from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path

import psycopg
import pytest
from psycopg.types.json import Jsonb

from packages.job_contracts import EnqueueJobRequest
from services.job_store import JobRepository, JobStoreSettings
from services.job_store.worker_repository import ProcessIdentity, WorkerRepository
from tests.jobs._postgres import (
    _upgrade_to_revision,
    disposable_database,
    disposable_role_settings,
)
from trading_control.db import DatabaseSettings


ROOT = Path(__file__).parents[2]
MIGRATION_0005 = ROOT / "alembic" / "versions" / "0005_job_plane_role_split.py"
MIGRATION_0006 = (
    ROOT
    / "alembic"
    / "versions"
    / "0006_job_transition_database_authority.py"
)
MIGRATION_0007 = ROOT / "alembic" / "versions" / "0007_job_event_chain_authority.py"
EXACT_0007_HEAD = "0007_job_event_chain_authority"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_migration(path: Path):
    spec = importlib.util.spec_from_file_location(f"frozen_{path.stem}", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_frozen_migration_identity_is_exact_and_forward_only() -> None:
    assert _sha256(MIGRATION_0005) == (
        "7b77d9abe0b5cfe84bf69ea60e47441179c99bcb533a6776f629cab103698f4e"
    )
    assert _sha256(MIGRATION_0006) == (
        "f4cadfc5683ff49038790afc7fac2632fe207073b1b0eecbf296147fdcceb2fd"
    )
    assert _sha256(MIGRATION_0007) == (
        "ff692b50f2c49fb9a2e277ab39a558ac07d16afbcf17c176eb9517d2aae036fd"
    )

    migration = _load_migration(MIGRATION_0006)
    assert migration.revision == "0006_job_transition_database_authority"
    assert migration.down_revision == "0005_job_plane_role_split"
    with pytest.raises(RuntimeError, match="forward-only"):
        migration.downgrade()

    forward_repair = _load_migration(MIGRATION_0007)
    assert forward_repair.revision == "0007_job_event_chain_authority"
    assert forward_repair.down_revision == "0006_job_transition_database_authority"
    assert forward_repair.PRODUCTION_DATABASE_NAME == "trading_agent"
    assert (
        forward_repair.DISPOSABLE_DATABASE_NAME
        == "trading_agent_disposable_test"
    )
    for snapshot_name, digest_name in (
        (
            "catalog-0006-v1.snapshot",
            "REVIEWED_DISPOSABLE_0006_CATALOG_SHA256",
        ),
        (
            "catalog-0007-v1.snapshot",
            "REVIEWED_DISPOSABLE_0007_CATALOG_SHA256",
        ),
    ):
        snapshot = (
            ROOT / "ops/postgres/job-plane-authority" / snapshot_name
        ).read_bytes()
        disposable_snapshot = snapshot.replace(
            b'"name": "trading_agent"',
            b'"name": "trading_agent_disposable_test"',
        ).replace(
            b'"database": "trading_agent"',
            b'"database": "trading_agent_disposable_test"',
        )
        assert hashlib.sha256(disposable_snapshot).hexdigest() == getattr(
            forward_repair,
            digest_name,
        )
    with pytest.raises(RuntimeError, match="forward-only"):
        forward_repair.downgrade()


@pytest.mark.parametrize(
    ("database_name", "digest_name"),
    (
        ("trading_agent", "REVIEWED_0006_CATALOG_SHA256"),
        (
            "trading_agent_disposable_test",
            "REVIEWED_DISPOSABLE_0006_CATALOG_SHA256",
        ),
    ),
)
def test_0007_catalog_gate_selects_only_the_exact_database_identity_digest(
    monkeypatch: pytest.MonkeyPatch,
    database_name: str,
    digest_name: str,
) -> None:
    migration = _load_migration(MIGRATION_0007)
    expected = getattr(migration, digest_name)
    monkeypatch.setattr(migration, "_catalog_database_name", lambda: database_name)
    monkeypatch.setattr(migration, "_catalog_digest", lambda: expected)

    migration._require_catalog_digest(
        migration.REVIEWED_0006_CATALOG_SHA256,
        migration.REVIEWED_DISPOSABLE_0006_CATALOG_SHA256,
        "preflight",
    )


def test_0007_catalog_gate_rejects_other_database_identity_and_digest_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration(MIGRATION_0007)
    monkeypatch.setattr(
        migration,
        "_catalog_database_name",
        lambda: "unreviewed_database",
    )
    monkeypatch.setattr(
        migration,
        "_catalog_digest",
        lambda: migration.REVIEWED_0006_CATALOG_SHA256,
    )
    with pytest.raises(RuntimeError, match="database identity does not match"):
        migration._require_catalog_digest(
            migration.REVIEWED_0006_CATALOG_SHA256,
            migration.REVIEWED_DISPOSABLE_0006_CATALOG_SHA256,
            "preflight",
        )

    monkeypatch.setattr(
        migration,
        "_catalog_database_name",
        lambda: migration.DISPOSABLE_DATABASE_NAME,
    )
    monkeypatch.setattr(migration, "_catalog_digest", lambda: "0" * 64)
    with pytest.raises(RuntimeError, match="catalog digest does not match"):
        migration._require_catalog_digest(
            migration.REVIEWED_0006_CATALOG_SHA256,
            migration.REVIEWED_DISPOSABLE_0006_CATALOG_SHA256,
            "preflight",
        )


RUNTIME_ROLES = (
    "trading_job_api",
    "trading_job_worker",
    "trading_job_scheduler",
)

EXPECTED_FUNCTION_ROLES = {
    "api_enqueue_snapshot": "trading_job_api",
    "api_cancel_snapshot": "trading_job_api",
    "scheduler_enqueue_snapshot": "trading_job_scheduler",
    "worker_claim_snapshot": "trading_job_worker",
    "worker_start_snapshot": "trading_job_worker",
    "worker_control_snapshot_lease": "trading_job_worker",
    "worker_finalize_snapshot": "trading_job_worker",
    "worker_recover_expired_snapshot": "trading_job_worker",
}

EXPECTED_FUNCTION_ARGUMENTS = {
    "api_enqueue_snapshot": "text, jsonb, text, text, text, smallint, text, text",
    "api_cancel_snapshot": "text, text, text, text",
    "scheduler_enqueue_snapshot": "text, jsonb, text, text, text, text, text",
    "worker_claim_snapshot": "text, text, text, integer, text, text",
    "worker_start_snapshot": (
        "text, text, text, text, bigint, bigint, bigint, text, text, text"
    ),
    "worker_control_snapshot_lease": "text, text, text, text, integer, text",
    "worker_finalize_snapshot": (
        "text, text, text, text, text, text, text, text, text, text, integer, "
        "text, text, jsonb, text, text, boolean, text, jsonb"
    ),
    "worker_recover_expired_snapshot": (
        "text, text, text, text, text, text, bigint, bigint, bigint, text, "
        "text, text, text, text, text"
    ),
}

EXPECTED_FUNCTION_RESULTS = {
    "api_enqueue_snapshot": "TABLE(job_id text, outcome text)",
    "api_cancel_snapshot": "TABLE(job_id text, state text, changed boolean)",
    "scheduler_enqueue_snapshot": "TABLE(job_id text, outcome text)",
    "worker_claim_snapshot": (
        "TABLE(job_id text, job_type text, payload jsonb, "
        "attempt_number integer, max_attempts smallint, "
        "lease_expires_at timestamp with time zone)"
    ),
    "worker_start_snapshot": "boolean",
    "worker_control_snapshot_lease": "text",
    "worker_finalize_snapshot": "boolean",
    "worker_recover_expired_snapshot": "text",
}

EXPECTED_REMAINING_POLICIES = {
    "job_plane_api_jobs_select",
    "job_plane_worker_jobs_select",
    "job_plane_scheduler_jobs_select",
    "job_plane_api_attempts_select",
    "job_plane_worker_attempts_select",
    "job_plane_api_events_select",
    "job_plane_worker_events_select",
    "job_plane_scheduler_events_select",
    "job_plane_scheduler_heartbeats_insert",
    "job_plane_api_artifacts_select",
    "job_plane_worker_artifacts_insert",
    "job_plane_worker_heartbeats_select",
    "job_plane_worker_heartbeats_insert",
    "job_plane_worker_heartbeats_update",
}

SNAPSHOT_PAYLOAD = {"scope": "default", "requested_as_of": None}
SNAPSHOT_FINGERPRINT = (
    "dc993577d7fe81a0fc6b23e281e0b7e2a182d557143cfa312d21078271b4091a"
)


def _required_null_fuzz_cases():
    specs = (
        (
            "api_enqueue_snapshot",
            "trading_job_api",
            "SELECT * FROM job_plane.api_enqueue_snapshot("
            "%s::text, %s::jsonb, %s::text, %s::text, %s::text, "
            "%s::smallint, %s::text, %s::text)",
            (
                "job_null_api",
                Jsonb(SNAPSHOT_PAYLOAD),
                SNAPSHOT_FINGERPRINT,
                "manual:snapshot:" + "0" * 32,
                "null-fuzz-api",
                0,
                "trace-null-fuzz-api",
                "event_null_fuzz_api",
            ),
            range(8),
        ),
        (
            "api_cancel_snapshot",
            "trading_job_api",
            "SELECT * FROM job_plane.api_cancel_snapshot("
            "%s::text, %s::text, %s::text, %s::text)",
            (
                "job_null_cancel",
                "null-fuzz-api",
                "trace-null-fuzz-cancel",
                "event_null_fuzz_cancel",
            ),
            range(4),
        ),
        (
            "scheduler_enqueue_snapshot",
            "trading_job_scheduler",
            "SELECT * FROM job_plane.scheduler_enqueue_snapshot("
            "%s::text, %s::jsonb, %s::text, %s::text, %s::text, "
            "%s::text, %s::text)",
            (
                "job_null_scheduler",
                Jsonb(SNAPSHOT_PAYLOAD),
                SNAPSHOT_FINGERPRINT,
                "schedule:snapshot:2026-07-16T12:30Z",
                "null-fuzz-scheduler",
                "trace-null-fuzz-scheduler",
                "event_null_fuzz_scheduler",
            ),
            range(7),
        ),
        (
            "worker_claim_snapshot",
            "trading_job_worker",
            "SELECT * FROM job_plane.worker_claim_snapshot("
            "%s::text, %s::text, %s::text, %s::integer, %s::text, %s::text)",
            (
                "attempt_null_claim",
                "null-fuzz-worker",
                "t" * 64,
                30,
                "trace-null-fuzz-claim",
                "event_null_fuzz_claim",
            ),
            range(6),
        ),
        (
            "worker_start_snapshot",
            "trading_job_worker",
            "SELECT job_plane.worker_start_snapshot("
            "%s::text, %s::text, %s::text, %s::text, %s::bigint, "
            "%s::bigint, %s::bigint, %s::text, %s::text, %s::text)",
            (
                "job_null_start",
                "attempt_null_start",
                "null-fuzz-worker",
                "t" * 64,
                41001,
                41001,
                9001,
                "d" * 64,
                "trace-null-fuzz-start",
                "event_null_fuzz_start",
            ),
            range(10),
        ),
        (
            "worker_finalize_snapshot",
            "trading_job_worker",
            "SELECT job_plane.worker_finalize_snapshot("
            "%s::text, %s::text, %s::text, %s::text, %s::text, %s::text, "
            "%s::text, %s::text, %s::text, %s::text, %s::integer, %s::text, "
            "%s::text, %s::jsonb, %s::text, %s::text, %s::boolean, "
            "%s::text, %s::jsonb)",
            (
                "job_null_finalize",
                "attempt_null_finalize",
                "null-fuzz-worker",
                "t" * 64,
                "RUNNING",
                "RUNNING",
                "FAILED",
                "EXECUTION_FAILED",
                "trace-null-fuzz-finalize",
                "event_null_fuzz_finalize",
                1,
                "PROCESS_EXIT",
                None,
                Jsonb({}),
                "PROCESS_FAILED",
                "sanitized failure",
                False,
                None,
                Jsonb({}),
            ),
            (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 13, 16, 18),
        ),
        (
            "worker_recover_expired_snapshot",
            "trading_job_worker",
            "SELECT job_plane.worker_recover_expired_snapshot("
            "%s::text, %s::text, %s::text, %s::text, %s::text, %s::text, "
            "%s::bigint, %s::bigint, %s::bigint, %s::text, %s::text, "
            "%s::text, %s::text, %s::text, %s::text)",
            (
                "job_null_recovery",
                "attempt_null_recovery",
                "RUNNING",
                "RUNNING",
                "null-fuzz-worker",
                "t" * 64,
                41001,
                41001,
                9001,
                "d" * 64,
                "UNVERIFIABLE",
                "trace-null-fuzz-recovery",
                "lease-recovery",
                "event_null_fuzz_recovery",
                "event_null_fuzz_recovery_retry",
            ),
            (0, 1, 2, 3, 4, 5, 10, 11, 12, 13),
        ),
        (
            "worker_control_snapshot_lease",
            "trading_job_worker",
            "SELECT job_plane.worker_control_snapshot_lease("
            "%s::text, %s::text, %s::text, %s::text, %s::integer, %s::text)",
            (
                "job_null_lease",
                "attempt_null_lease",
                "null-fuzz-worker",
                "t" * 64,
                30,
                "RUNNING",
            ),
            range(6),
        ),
    )
    return tuple(
        (f"{name}-arg-{index}", role, query, arguments, index)
        for name, role, query, arguments, required_indexes in specs
        for index in required_indexes
    )


REQUIRED_NULL_FUZZ_CASES = _required_null_fuzz_cases()


@pytest.fixture(scope="module")
def authority_database():
    if (
        os.environ.get("TRADING_TEST_ALLOW_DISPOSABLE_POSTGRES") != "YES"
        or os.environ.get("TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE")
        != "DISPOSABLE_PG_GREEN"
    ):
        pytest.skip("exact disposable PostgreSQL GREEN authority is not present")
    with disposable_database(
        operation_id="jobs-transition-authority-green-v1"
    ) as owner:
        _upgrade_to_revision(owner, EXACT_0007_HEAD)
        roles = {
            role: disposable_role_settings(owner, role) for role in RUNTIME_ROLES
        }
        yield owner, roles


def _store(settings: DatabaseSettings) -> JobStoreSettings:
    return JobStoreSettings(
        host=settings.host,
        port=settings.port,
        database=settings.database,
        user=settings.user,
        password=settings.password,
        pool_max=2,
    ).require_user(settings.user)


def _request(key: str) -> EnqueueJobRequest:
    return EnqueueJobRequest.model_validate(
        {
            "job_type": "SNAPSHOT",
            "payload": {"scope": "default", "requested_as_of": None},
            "idempotency_key": key,
            "actor": {
                "actor_type": "OPERATOR",
                "actor_id": "transition-authority-operator",
            },
            "priority": 0,
        }
    )


def _enqueue(roles: dict[str, DatabaseSettings], key: str) -> str:
    with JobRepository(_store(roles["trading_job_api"])) as repository:
        return repository.enqueue(_request(key), trace_id=f"trace-{key}").job.job_id


def test_runtime_roles_have_no_direct_state_or_event_dml(authority_database) -> None:
    owner, _ = authority_database
    with psycopg.connect(owner.conninfo()) as connection:
        assert not connection.execute(
            "SELECT has_column_privilege("
            "'trading_job_api', 'public.jobs', 'state', 'UPDATE')"
        ).fetchone()[0]
        assert not connection.execute(
            "SELECT has_any_column_privilege("
            "'trading_job_api', 'public.jobs', 'INSERT')"
        ).fetchone()[0]
        assert not connection.execute(
            "SELECT has_column_privilege("
            "'trading_job_worker', 'public.jobs', 'state', 'UPDATE')"
        ).fetchone()[0]
        assert not connection.execute(
            "SELECT has_column_privilege("
            "'trading_job_worker', 'public.job_attempts', 'outcome', 'UPDATE')"
        ).fetchone()[0]
        assert not connection.execute(
            "SELECT has_any_column_privilege("
            "'trading_job_worker', 'public.job_attempts', 'INSERT')"
        ).fetchone()[0]
        assert not connection.execute(
            "SELECT has_any_column_privilege("
            "'trading_job_scheduler', 'public.jobs', 'INSERT')"
        ).fetchone()[0]
        for role in RUNTIME_ROLES:
            assert not connection.execute(
                "SELECT has_any_column_privilege(%s, 'public.job_events', 'INSERT')",
                (role,),
            ).fetchone()[0]


@pytest.mark.parametrize(
    "case_id,role,query,base_arguments,null_index",
    REQUIRED_NULL_FUZZ_CASES,
    ids=[case[0] for case in REQUIRED_NULL_FUZZ_CASES],
)
def test_capabilities_reject_null_required_arguments(
    authority_database,
    case_id: str,
    role: str,
    query: str,
    base_arguments: tuple[object, ...],
    null_index: int,
) -> None:
    del case_id
    owner, roles = authority_database
    with psycopg.connect(owner.conninfo()) as connection:
        before = connection.execute(
            "SELECT (SELECT count(*) FROM public.jobs), "
            "(SELECT count(*) FROM public.job_attempts), "
            "(SELECT count(*) FROM public.job_events)"
        ).fetchone()

    arguments = list(base_arguments)
    arguments[null_index] = None
    with psycopg.connect(roles[role].conninfo(), autocommit=True) as connection:
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            connection.execute(query, tuple(arguments))

    with psycopg.connect(owner.conninfo()) as connection:
        after = connection.execute(
            "SELECT (SELECT count(*) FROM public.jobs), "
            "(SELECT count(*) FROM public.job_attempts), "
            "(SELECT count(*) FROM public.job_events)"
        ).fetchone()
    assert after == before


def test_api_cannot_commit_trigger_valid_cancel_without_event(
    authority_database,
) -> None:
    owner, roles = authority_database
    job_id = _enqueue(roles, "manual:snapshot:" + "a" * 32)

    with psycopg.connect(
        roles["trading_job_api"].conninfo(), autocommit=True
    ) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(
                """
                UPDATE public.jobs
                SET state = 'CANCELLED',
                    updated_at = now(),
                    reason_code = 'CANCEL_REQUESTED',
                    cancel_requested_at = now(),
                    cancel_actor_type = 'OPERATOR',
                    cancel_actor_id = 'transition-authority-operator'
                WHERE job_id = %s AND state = 'QUEUED'
                """,
                (job_id,),
            )

    with psycopg.connect(owner.conninfo()) as connection:
        state = connection.execute(
            "SELECT state FROM public.jobs WHERE job_id = %s", (job_id,)
        ).fetchone()[0]
        events = connection.execute(
            "SELECT count(*) FROM public.job_events WHERE job_id = %s", (job_id,)
        ).fetchone()[0]
    assert (state, events) == ("QUEUED", 1)


def test_worker_cannot_commit_state_without_event(authority_database) -> None:
    owner, roles = authority_database
    job_id = _enqueue(roles, "manual:snapshot:" + "b" * 32)

    with psycopg.connect(
        roles["trading_job_worker"].conninfo(), autocommit=True
    ) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(
                """
                UPDATE public.jobs
                SET state = 'BLOCKED', reason_code = 'UNAUTHORIZED_DIRECT_WRITE',
                    updated_at = now(), finished_at = now()
                WHERE job_id = %s
                """,
                (job_id,),
            )

    with psycopg.connect(owner.conninfo()) as connection:
        assert connection.execute(
            "SELECT state FROM public.jobs WHERE job_id = %s", (job_id,)
        ).fetchone()[0] == "QUEUED"
        assert connection.execute(
            "SELECT count(*) FROM public.job_events WHERE job_id = %s", (job_id,)
        ).fetchone()[0] == 1


@pytest.mark.parametrize(
    "case_id,tamper_statement",
    (
        (
            "partial",
            "UPDATE public.job_attempts SET command_fingerprint = NULL "
            "WHERE attempt_id = %s",
        ),
        (
            "all-null",
            "UPDATE public.job_attempts "
            "SET child_pid = NULL, process_group_id = NULL, "
            "process_start_ticks = NULL, command_fingerprint = NULL "
            "WHERE attempt_id = %s",
        ),
    ),
)
def test_recovery_capability_blocks_incomplete_stored_identity(
    authority_database,
    case_id: str,
    tamper_statement: str,
) -> None:
    owner, roles = authority_database
    key_character = "c" if case_id == "partial" else "d"
    job_id = _enqueue(roles, "manual:snapshot:" + key_character * 32)
    identity = ProcessIdentity(41001, 41001, 9001, "d" * 64)
    with psycopg.connect(owner.conninfo()) as connection:
        connection.execute(
            "UPDATE public.jobs SET priority = 100 WHERE job_id = %s",
            (job_id,),
        )
    with WorkerRepository(_store(roles["trading_job_worker"])) as workers:
        claim = workers.claim_next(
            "transition-authority-worker",
            30,
            "trace-claim-incomplete-identity",
        )
        assert claim is not None
        assert claim.job_id == job_id
        assert workers.start_attempt(
            job_id,
            claim.attempt_id,
            "transition-authority-worker",
            claim.lease_token,
            identity,
            "trace-start-incomplete-identity",
        )

    with psycopg.connect(owner.conninfo()) as connection:
        connection.execute(tamper_statement, (claim.attempt_id,))
        connection.execute(
            "UPDATE public.jobs "
            "SET lease_expires_at = now() - interval '1 second' "
            "WHERE job_id = %s",
            (job_id,),
        )
        expected_identity = connection.execute(
            "SELECT child_pid, process_group_id, process_start_ticks, "
            "command_fingerprint FROM public.job_attempts "
            "WHERE attempt_id = %s",
            (claim.attempt_id,),
        ).fetchone()

    event_suffix = case_id.replace("-", "_")
    with psycopg.connect(
        roles["trading_job_worker"].conninfo(), autocommit=True
    ) as connection:
        outcome = connection.execute(
            """
            SELECT job_plane.worker_recover_expired_snapshot(
              %s, %s, 'RUNNING', 'RUNNING', %s, %s,
              %s::bigint, %s::bigint, %s::bigint, %s::text,
              'ABSENT', %s, 'lease-recovery', %s, %s
            )
            """,
            (
                job_id,
                claim.attempt_id,
                "transition-authority-worker",
                claim.lease_token,
                *expected_identity,
                f"trace-direct-incomplete-{event_suffix}",
                f"event_direct_incomplete_{event_suffix}",
                f"event_direct_incomplete_retry_{event_suffix}",
            ),
        ).fetchone()[0]
    assert outcome == "LEASE_EXPIRED_CHILD_IDENTITY_UNVERIFIABLE"

    with psycopg.connect(owner.conninfo()) as connection:
        assert connection.execute(
            "SELECT state FROM public.jobs WHERE job_id = %s", (job_id,)
        ).fetchone()[0] == "BLOCKED"
        assert connection.execute(
            "SELECT reason_code FROM public.job_events "
            "WHERE job_id = %s ORDER BY sequence DESC LIMIT 1",
            (job_id,),
        ).fetchone()[0] == "LEASE_EXPIRED_CHILD_IDENTITY_UNVERIFIABLE"
        assert connection.execute(
            "SELECT count(*) FROM public.job_events "
            "WHERE job_id = %s "
            "AND reason_code = 'LEASE_EXPIRED_RETRY_SCHEDULED'",
            (job_id,),
        ).fetchone()[0] == 0


def test_recovery_capability_rejects_null_observation_without_mutation(
    authority_database,
) -> None:
    owner, roles = authority_database
    job_id = _enqueue(roles, "manual:snapshot:" + "f" * 32)
    identity = ProcessIdentity(42001, 42001, 9002, "e" * 64)
    with psycopg.connect(owner.conninfo()) as connection:
        connection.execute(
            "UPDATE public.jobs SET priority = 100 WHERE job_id = %s",
            (job_id,),
        )
    with WorkerRepository(_store(roles["trading_job_worker"])) as workers:
        claim = workers.claim_next(
            "transition-authority-worker",
            30,
            "trace-claim-null-observation",
        )
        assert claim is not None
        assert claim.job_id == job_id
        assert workers.start_attempt(
            job_id,
            claim.attempt_id,
            "transition-authority-worker",
            claim.lease_token,
            identity,
            "trace-start-null-observation",
        )

    with psycopg.connect(owner.conninfo()) as connection:
        connection.execute(
            "UPDATE public.jobs "
            "SET lease_expires_at = now() - interval '1 second' "
            "WHERE job_id = %s",
            (job_id,),
        )
        before = connection.execute(
            "SELECT state, (SELECT count(*) FROM public.job_events "
            "WHERE job_id = %s) FROM public.jobs WHERE job_id = %s",
            (job_id, job_id),
        ).fetchone()

    with psycopg.connect(
        roles["trading_job_worker"].conninfo(), autocommit=True
    ) as connection:
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            connection.execute(
                """
                SELECT job_plane.worker_recover_expired_snapshot(
                  %s, %s, 'RUNNING', 'RUNNING', %s, %s,
                  %s::bigint, %s::bigint, %s::bigint, %s,
                  NULL::text, %s, 'lease-recovery', %s, %s
                )
                """,
                (
                    job_id,
                    claim.attempt_id,
                    "transition-authority-worker",
                    claim.lease_token,
                    identity.pid,
                    identity.process_group,
                    identity.start_ticks,
                    identity.command_fingerprint,
                    "trace-direct-null-observation",
                    "event_direct_null_observation",
                    "event_direct_null_observation_retry",
                ),
            )

    with psycopg.connect(owner.conninfo()) as connection:
        after = connection.execute(
            "SELECT state, (SELECT count(*) FROM public.job_events "
            "WHERE job_id = %s) FROM public.jobs WHERE job_id = %s",
            (job_id, job_id),
        ).fetchone()
        assert connection.execute(
            "SELECT count(*) FROM public.job_events "
            "WHERE job_id = %s "
            "AND reason_code = 'LEASE_EXPIRED_RETRY_SCHEDULED'",
            (job_id,),
        ).fetchone()[0] == 0
    assert before == after == ("RUNNING", 3)


@pytest.mark.parametrize("invalid_payload,invalid_fingerprint", (
    ({"scope": "poisoned", "requested_as_of": None}, SNAPSHOT_FINGERPRINT),
    (SNAPSHOT_PAYLOAD, "f" * 64),
))
def test_api_capability_rejects_noncanonical_snapshot_identity(
    authority_database,
    invalid_payload: dict[str, object],
    invalid_fingerprint: str,
) -> None:
    owner, roles = authority_database
    with psycopg.connect(
        roles["trading_job_api"].conninfo(), autocommit=True
    ) as connection:
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            connection.execute(
                """
                SELECT * FROM job_plane.api_enqueue_snapshot(
                  'job_bad_api', %s::jsonb, %s,
                  'manual:snapshot:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee',
                  'transition-authority-operator', 0::smallint,
                  'trace-bad-api', 'event_bad_api'
                )
                """,
                (Jsonb(invalid_payload), invalid_fingerprint),
            )
    with psycopg.connect(owner.conninfo()) as connection:
        assert connection.execute(
            "SELECT count(*) FROM public.jobs WHERE job_id = 'job_bad_api'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM public.job_events WHERE event_id = 'event_bad_api'"
        ).fetchone()[0] == 0


@pytest.mark.parametrize("invalid_payload,invalid_fingerprint", (
    ({"scope": "poisoned", "requested_as_of": None}, SNAPSHOT_FINGERPRINT),
    (SNAPSHOT_PAYLOAD, "f" * 64),
))
def test_scheduler_capability_rejects_noncanonical_snapshot_identity(
    authority_database,
    invalid_payload: dict[str, object],
    invalid_fingerprint: str,
) -> None:
    owner, roles = authority_database
    with psycopg.connect(
        roles["trading_job_scheduler"].conninfo(), autocommit=True
    ) as connection:
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            connection.execute(
                """
                SELECT * FROM job_plane.scheduler_enqueue_snapshot(
                  'job_bad_scheduler', %s::jsonb, %s,
                  'schedule:snapshot:2026-07-16T12:30Z',
                  'transition-authority-scheduler',
                  'trace-bad-scheduler', 'event_bad_scheduler'
                )
                """,
                (Jsonb(invalid_payload), invalid_fingerprint),
            )
    with psycopg.connect(owner.conninfo()) as connection:
        assert connection.execute(
            "SELECT count(*) FROM public.jobs WHERE job_id = 'job_bad_scheduler'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM public.job_events "
            "WHERE event_id = 'event_bad_scheduler'"
        ).fetchone()[0] == 0


def test_transition_authority_function_catalog_is_exact(authority_database) -> None:
    owner, _ = authority_database
    with psycopg.connect(owner.conninfo()) as connection:
        schema = connection.execute(
            """
            SELECT pg_get_userbyid(namespace_row.nspowner),
                   EXISTS (
                     SELECT 1
                     FROM aclexplode(
                       coalesce(
                         namespace_row.nspacl,
                         acldefault('n', namespace_row.nspowner)
                       )
                     ) acl
                     WHERE acl.grantee = 0
                       AND acl.privilege_type = 'USAGE'
                   ),
                   EXISTS (
                     SELECT 1
                     FROM aclexplode(
                       coalesce(
                         namespace_row.nspacl,
                         acldefault('n', namespace_row.nspowner)
                       )
                     ) acl
                     WHERE acl.grantee = 0
                       AND acl.privilege_type = 'CREATE'
                   )
            FROM pg_namespace namespace_row
            WHERE namespace_row.nspname = 'job_plane'
            """
        ).fetchone()
        assert schema == ("trading_owner", False, False)
        for role in RUNTIME_ROLES:
            assert connection.execute(
                "SELECT has_schema_privilege(%s, 'job_plane', 'USAGE')",
                (role,),
            ).fetchone()[0]
            assert not connection.execute(
                "SELECT has_schema_privilege(%s, 'job_plane', 'CREATE')",
                (role,),
            ).fetchone()[0]

        functions = connection.execute(
            """
            SELECT procedure_row.proname,
                   pg_get_userbyid(procedure_row.proowner),
                   procedure_row.prosecdef,
                   procedure_row.proleakproof,
                   procedure_row.provolatile,
                   procedure_row.proparallel,
                   procedure_row.proconfig,
                   pg_get_function_identity_arguments(procedure_row.oid),
                   oidvectortypes(procedure_row.proargtypes),
                   language_row.lanname,
                   pg_get_function_result(procedure_row.oid)
            FROM pg_proc procedure_row
            JOIN pg_language language_row
              ON language_row.oid = procedure_row.prolang
            WHERE procedure_row.pronamespace = 'job_plane'::regnamespace
            ORDER BY procedure_row.proname
            """
        ).fetchall()
        assert {row[0] for row in functions} == set(EXPECTED_FUNCTION_ROLES)
        assert len(functions) == len(EXPECTED_FUNCTION_ROLES)
        for (
            name,
            owner_name,
            security_definer,
            leakproof,
            volatility,
            parallel,
            config,
            _arguments,
            identity_types,
            language,
            result_type,
        ) in functions:
            assert owner_name == "trading_owner"
            assert security_definer is True
            assert leakproof is False
            assert volatility == "v"
            assert parallel == "u"
            assert config == ["search_path=pg_catalog"]
            assert language == "plpgsql"
            assert identity_types == EXPECTED_FUNCTION_ARGUMENTS[name]
            assert result_type == EXPECTED_FUNCTION_RESULTS[name]
            assert "EXECUTE" not in connection.execute(
                "SELECT prosrc FROM pg_proc "
                "WHERE pronamespace = 'job_plane'::regnamespace AND proname = %s",
                (name,),
            ).fetchone()[0].upper()

        for name, allowed_role in EXPECTED_FUNCTION_ROLES.items():
            oid = connection.execute(
                "SELECT oid FROM pg_proc "
                "WHERE pronamespace = 'job_plane'::regnamespace AND proname = %s",
                (name,),
            ).fetchone()[0]
            assert not connection.execute(
                """
                SELECT EXISTS (
                  SELECT 1
                  FROM pg_proc procedure_row
                  CROSS JOIN LATERAL aclexplode(
                    coalesce(
                      procedure_row.proacl,
                      acldefault('f', procedure_row.proowner)
                    )
                  ) acl
                  WHERE procedure_row.oid = %s
                    AND acl.grantee = 0
                    AND acl.privilege_type = 'EXECUTE'
                )
                """,
                (oid,),
            ).fetchone()[0]
            for role in RUNTIME_ROLES:
                assert connection.execute(
                    "SELECT has_function_privilege(%s, %s, 'EXECUTE')",
                    (role, oid),
                ).fetchone()[0] is (role == allowed_role)
            assert connection.execute(
                """
                SELECT count(*)
                FROM pg_proc procedure_row
                CROSS JOIN LATERAL aclexplode(procedure_row.proacl) acl
                WHERE procedure_row.oid = %s AND acl.is_grantable
                """,
                (oid,),
            ).fetchone()[0] == 0
            assert connection.execute(
                """
                SELECT array_agg(grantee_role.rolname ORDER BY grantee_role.rolname)
                FROM pg_proc procedure_row
                CROSS JOIN LATERAL aclexplode(
                  coalesce(
                    procedure_row.proacl,
                    acldefault('f', procedure_row.proowner)
                  )
                ) acl
                LEFT JOIN pg_roles grantee_role ON grantee_role.oid = acl.grantee
                WHERE procedure_row.oid = %s
                  AND acl.privilege_type = 'EXECUTE'
                  AND acl.grantee <> procedure_row.proowner
                """,
                (oid,),
            ).fetchone()[0] == [allowed_role]

        policies = connection.execute(
            """
            SELECT policy.polname
            FROM pg_policy policy
            JOIN pg_class relation ON relation.oid = policy.polrelid
            WHERE relation.relnamespace = 'public'::regnamespace
              AND relation.relname = ANY(%s)
            """,
            (
                [
                    "jobs",
                    "job_attempts",
                    "job_events",
                    "scheduler_heartbeats",
                    "job_artifacts",
                    "worker_heartbeats",
                ],
            ),
        ).fetchall()
        assert {row[0] for row in policies} == EXPECTED_REMAINING_POLICIES


def test_runtime_head_is_transition_authority_revision(authority_database) -> None:
    owner, _ = authority_database
    with psycopg.connect(owner.conninfo()) as connection:
        assert connection.execute(
            "SELECT version_num FROM public.alembic_version"
        ).fetchone()[0] == "0007_job_event_chain_authority"
