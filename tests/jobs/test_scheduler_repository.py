from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier

import psycopg
import pytest

from services.job_scheduler import main as scheduler_main
from services.job_scheduler.scheduler import (
    SchedulerHeartbeatOutcome,
    SchedulerIdentity,
    schedule_tick,
)
from services.job_store import JobRepository, JobStoreSettings
from tests.jobs._postgres import (
    disposable_database,
    disposable_role_settings,
    upgrade_to_head,
)


TICK = datetime(2026, 7, 12, 12, 30, 19, tzinfo=timezone.utc)
IDENTITY = SchedulerIdentity(
    scheduler_id="scheduler-test",
    code_commit="0123456789abcdef0123456789abcdef01234567",
    actor_id="scheduler-service",
    trace_id="trace-scheduler-test",
)


@pytest.fixture(scope="module")
def scheduler_database():
    with disposable_database(
        operation_id="jobs-scheduler-repository-v1"
    ) as owner:
        upgrade_to_head(owner)
        role = disposable_role_settings(owner, "trading_job_scheduler")
        settings = JobStoreSettings(
            host=role.host,
            port=role.port,
            database=role.database,
            user=role.user,
            password=role.password,
            pool_min=1,
            pool_max=4,
        ).require_user("trading_job_scheduler")
        yield settings, owner


def test_non_slot_tick_persists_skipped_heartbeat_without_job(scheduler_database) -> None:
    scheduler, owner = scheduler_database
    identity = SchedulerIdentity(
        scheduler_id=IDENTITY.scheduler_id, code_commit=IDENTITY.code_commit,
        actor_id=IDENTITY.actor_id, trace_id="trace-scheduler-skipped",
    )
    with JobRepository(scheduler) as repository:
        result = schedule_tick(TICK.replace(minute=29), repository, identity)

    assert result.outcome is SchedulerHeartbeatOutcome.SKIPPED_NOT_SLOT
    assert result.job_id is None
    with psycopg.connect(owner.conninfo()) as connection:
        row = connection.execute(
            "SELECT tick_at, slot_at, outcome, job_id, scheduler_id, code_commit, "
            "actor_id, trace_id, reason_code, metadata FROM scheduler_heartbeats "
            "WHERE trace_id = %s ORDER BY created_at DESC LIMIT 1",
            (identity.trace_id,),
        ).fetchone()
    assert row == (
        TICK.replace(minute=29), None, "SKIPPED_NOT_SLOT", None,
        identity.scheduler_id, identity.code_commit, identity.actor_id,
        identity.trace_id, "SKIPPED_NOT_SLOT", {},
    )


def test_repeat_tick_enqueues_once_and_persists_both_transactional_outcomes(
    scheduler_database,
) -> None:
    scheduler, owner = scheduler_database
    identity_one = IDENTITY
    identity_two = SchedulerIdentity(
        scheduler_id=IDENTITY.scheduler_id, code_commit=IDENTITY.code_commit,
        actor_id=IDENTITY.actor_id, trace_id="trace-scheduler-repeat",
    )
    with JobRepository(scheduler) as repository:
        first = schedule_tick(TICK, repository, identity_one)
        second = schedule_tick(TICK, repository, identity_two)

    assert first.outcome is SchedulerHeartbeatOutcome.ENQUEUED
    assert second.outcome is SchedulerHeartbeatOutcome.DEDUPLICATED
    assert first.job_id == second.job_id
    with psycopg.connect(owner.conninfo()) as connection:
        job = connection.execute(
            "SELECT job_type, idempotency_key, payload, actor_type, actor_id "
            "FROM jobs WHERE job_id = %s", (first.job_id,),
        ).fetchone()
        heartbeats = connection.execute(
            "SELECT outcome, job_id, slot_at, actor_id, trace_id FROM scheduler_heartbeats "
            "WHERE trace_id IN (%s, %s) ORDER BY created_at",
            (identity_one.trace_id, identity_two.trace_id),
        ).fetchall()
    assert job == (
        "SNAPSHOT", "schedule:snapshot:2026-07-12T12:30Z",
        {"scope": "default", "requested_as_of": None},
        "SCHEDULER", IDENTITY.actor_id,
    )
    assert heartbeats == [
        ("ENQUEUED", first.job_id, TICK.replace(second=0), IDENTITY.actor_id, identity_one.trace_id),
        ("DEDUPLICATED", first.job_id, TICK.replace(second=0), IDENTITY.actor_id, identity_two.trace_id),
    ]


def test_concurrent_scheduler_processes_create_one_job(scheduler_database) -> None:
    scheduler, owner = scheduler_database
    tick = TICK.replace(hour=13, minute=0)
    barrier = Barrier(2)

    def run(index: int):
        identity = SchedulerIdentity(
            scheduler_id=f"scheduler-{index}", code_commit=IDENTITY.code_commit,
            actor_id=IDENTITY.actor_id, trace_id=f"trace-scheduler-concurrent-{index}",
        )
        with JobRepository(scheduler) as repository:
            barrier.wait(timeout=5)
            return schedule_tick(tick, repository, identity)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(run, (1, 2)))

    assert {item.outcome for item in outcomes} == {
        SchedulerHeartbeatOutcome.ENQUEUED,
        SchedulerHeartbeatOutcome.DEDUPLICATED,
    }
    assert len({item.job_id for item in outcomes}) == 1
    with psycopg.connect(owner.conninfo()) as connection:
        assert connection.execute(
            "SELECT count(*) FROM jobs WHERE idempotency_key = %s",
            ("schedule:snapshot:2026-07-12T13:00Z",),
        ).fetchone()[0] == 1


def test_enqueue_failure_records_failed_without_process_fallback() -> None:
    class FailingRepository:
        def schedule_snapshot(self, **kwargs):
            raise RuntimeError("database password=do-not-leak")

        def record_scheduler_heartbeat(self, **kwargs):
            self.heartbeat = kwargs

    repository = FailingRepository()

    result = schedule_tick(TICK, repository, IDENTITY)

    assert result.outcome is SchedulerHeartbeatOutcome.FAILED
    assert result.job_id is None
    assert repository.heartbeat["outcome"] is SchedulerHeartbeatOutcome.FAILED
    assert repository.heartbeat["reason_code"] == "DATABASE_ERROR"
    assert "do-not-leak" not in repr(repository.heartbeat)

    imports = {
        alias.name
        for file in Path("services/job_scheduler").glob("*.py")
        for node in ast.walk(ast.parse(file.read_text(encoding="utf-8")))
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module or ""
        for file in Path("services/job_scheduler").glob("*.py")
        for node in ast.walk(ast.parse(file.read_text(encoding="utf-8")))
        if isinstance(node, ast.ImportFrom)
    }
    assert not any("job_worker" in name or "process" in name for name in imports)


def test_cli_returns_nonzero_and_sanitizes_database_failure(monkeypatch, capsys) -> None:
    class BrokenRepository:
        def __init__(self, settings):
            raise RuntimeError("postgres://" + "admin:credential@localhost/runtime")

    monkeypatch.setattr(scheduler_main, "JobRepository", BrokenRepository)
    monkeypatch.setattr(
        scheduler_main.JobStoreSettings,
        "from_env",
        classmethod(
            lambda cls, *, expected_user: (
                object()
                if expected_user == "trading_job_scheduler"
                else pytest.fail("scheduler selected the wrong database role")
            )
        ),
    )

    assert scheduler_main.main(now=TICK, identity=IDENTITY) == 1
    captured = capsys.readouterr()
    assert "scheduler tick failed" in captured.err
    assert "secret" not in captured.err
    assert "runtime" not in captured.err


def test_cli_requires_scheduler_role_before_repository_construction(monkeypatch) -> None:
    store_calls = []
    repository_calls = []

    class RepositoryContext:
        def __init__(self, configured):
            repository_calls.append(configured)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

    monkeypatch.setattr(
        scheduler_main.JobStoreSettings,
        "from_env",
        classmethod(
            lambda cls, *, expected_user: store_calls.append(expected_user) or object()
        ),
    )
    monkeypatch.setattr(scheduler_main, "JobRepository", RepositoryContext)
    monkeypatch.setattr(
        scheduler_main,
        "schedule_tick",
        lambda now, repository, identity: type(
            "TickResult", (), {"outcome": SchedulerHeartbeatOutcome.SKIPPED_NOT_SLOT}
        )(),
    )

    assert scheduler_main.main(now=TICK, identity=IDENTITY) == 0
    assert store_calls == ["trading_job_scheduler"]
    assert len(repository_calls) == 1


def test_cli_rejects_shared_role_before_repository_construction(
    monkeypatch, capsys
) -> None:
    store_calls = []
    repository_calls = []

    def reject_shared_role(cls, *, expected_user):
        store_calls.append(expected_user)
        raise ValueError("job database user does not match expected service role")

    monkeypatch.setattr(
        scheduler_main.JobStoreSettings,
        "from_env",
        classmethod(reject_shared_role),
    )
    monkeypatch.setattr(
        scheduler_main,
        "JobRepository",
        lambda configured: repository_calls.append(configured),
    )

    assert scheduler_main.main(now=TICK, identity=IDENTITY) == 1
    assert store_calls == ["trading_job_scheduler"]
    assert repository_calls == []
    assert "scheduler tick failed" in capsys.readouterr().err
