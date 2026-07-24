from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier

import psycopg
import pytest
from psycopg.errors import UniqueViolation
from psycopg.pq import DiagnosticField

from packages.job_contracts import (
    ActorIdentity,
    ActorType,
    EnqueueJobRequest,
    payload_fingerprint,
)
from services.job_store import (
    EnqueueOutcome,
    IdempotencyConflict,
    JobRepository,
    JobStoreSettings,
)
from tests.jobs._postgres import (
    disposable_database,
    disposable_role_settings,
    upgrade_to_head,
)


def _request(
    *,
    job_type: str = "SNAPSHOT",
    payload: dict[str, object] | None = None,
    key: str = "manual:snapshot:one",
    actor_type: str = "OPERATOR",
    actor_id: str = "operator-7",
    priority: int = 8,
) -> EnqueueJobRequest:
    return EnqueueJobRequest.model_validate(
        {
            "job_type": job_type,
            "payload": payload
            or {"scope": "default", "requested_as_of": None},
            "idempotency_key": key,
            "actor": {"actor_type": actor_type, "actor_id": actor_id},
            "priority": priority,
        }
    )


def _store_settings(database_settings) -> JobStoreSettings:
    return JobStoreSettings(
        host=database_settings.host,
        port=database_settings.port,
        database=database_settings.database,
        user=database_settings.user,
        password=database_settings.password,
        pool_min=1,
        pool_max=4,
        statement_timeout_ms=5_000,
    )


@pytest.fixture
def repository_database():
    with disposable_database(
        operation_id="jobs-repository-enqueue-v1"
    ) as database_settings:
        upgrade_to_head(database_settings)
        settings = _store_settings(
            disposable_role_settings(database_settings, "trading_job_api")
        )
        with JobRepository(settings) as repository:
            yield repository, database_settings


def test_first_enqueue_persists_queued_job_and_event(repository_database) -> None:
    repository, _ = repository_database

    result = repository.enqueue(_request(), trace_id="trace-enqueue-1")

    assert result.outcome is EnqueueOutcome.ENQUEUED
    assert result.job.state.value == "QUEUED"
    assert result.job.actor == ActorIdentity(
        actor_type=ActorType.OPERATOR, actor_id="operator-7"
    )
    detail = repository.get_job(result.job.job_id)
    assert detail is not None
    assert [(event.sequence, event.reason_code, event.trace_id) for event in detail.events] == [
        (1, "ENQUEUED", "trace-enqueue-1")
    ]


def test_canonical_same_payload_deduplicates_to_same_job(repository_database) -> None:
    repository, _ = repository_database
    first = repository.enqueue(
        _request(
            payload={"scope": "default", "requested_as_of": None},
            key="manual:snapshot:canonical",
        ),
        trace_id="trace-canonical-1",
    )

    second = repository.enqueue(
        _request(
            payload={"requested_as_of": None, "scope": "default"},
            key="manual:snapshot:canonical",
        ),
        trace_id="trace-canonical-2",
    )

    assert second.outcome is EnqueueOutcome.DEDUPLICATED
    assert second.job.job_id == first.job.job_id
    assert repository.get_job(first.job.job_id).events[0].trace_id == "trace-canonical-1"


def test_same_key_with_different_actor_is_conflict(repository_database) -> None:
    repository, _ = repository_database
    repository.enqueue(
        _request(
            key="manual:snapshot:conflict",
            actor_id="operator-first",
        ),
        trace_id="trace-conflict-1",
    )

    with pytest.raises(IdempotencyConflict) as caught:
        repository.enqueue(
            _request(
                key="manual:snapshot:conflict",
                actor_id="operator-other",
            ),
            trace_id="trace-conflict-2",
        )

    assert caught.value.code == "IDEMPOTENCY_CONFLICT"
    assert "payload" not in str(caught.value).lower()


def test_two_concurrent_enqueues_create_one_job_and_one_event(repository_database) -> None:
    repository, database_settings = repository_database
    request = _request(key="manual:snapshot:concurrent")
    barrier = Barrier(2)

    def enqueue(index: int):
        barrier.wait(timeout=5)
        return repository.enqueue(request, trace_id=f"trace-concurrent-{index}")

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(enqueue, (1, 2)))

    assert {result.outcome for result in results} == {
        EnqueueOutcome.ENQUEUED,
        EnqueueOutcome.DEDUPLICATED,
    }
    assert len({result.job.job_id for result in results}) == 1
    with psycopg.connect(database_settings.conninfo()) as connection:
        assert connection.execute(
            "SELECT count(*) FROM jobs WHERE idempotency_key = %s",
            (request.idempotency_key,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM job_events WHERE job_id = %s",
            (results[0].job.job_id,),
        ).fetchone()[0] == 1


def test_actor_and_trace_are_attributed_without_auth_material(repository_database) -> None:
    repository, database_settings = repository_database
    result = repository.enqueue(
        _request(key="manual:snapshot:attribution", actor_id="operator-safe"),
        trace_id="trace-attribution",
    )

    with psycopg.connect(database_settings.conninfo()) as connection:
        job = connection.execute(
            "SELECT actor_type, actor_id, payload FROM jobs WHERE job_id = %s",
            (result.job.job_id,),
        ).fetchone()
        event = connection.execute(
            "SELECT actor_type, actor_id, trace_id, metadata "
            "FROM job_events WHERE job_id = %s",
            (result.job.job_id,),
        ).fetchone()
    assert job == ("OPERATOR", "operator-safe", {"scope": "default", "requested_as_of": None})
    assert event == ("OPERATOR", "operator-safe", "trace-attribution", {})


def test_enqueue_rolls_back_job_when_database_rejects_event_insert(
    repository_database,
) -> None:
    repository, database_settings = repository_database
    with psycopg.connect(database_settings.conninfo()) as owner:
        owner.execute(
            """
            CREATE FUNCTION test_reject_enqueued_event()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
              IF NEW.trace_id = 'trace-rollback' THEN
                RAISE EXCEPTION 'synthetic event failure';
              END IF;
              RETURN NEW;
            END;
            $$
            """
        )
        owner.execute(
            """
            CREATE TRIGGER trg_test_reject_enqueued_event
            BEFORE INSERT ON job_events
            FOR EACH ROW EXECUTE FUNCTION test_reject_enqueued_event()
            """
        )

    with pytest.raises(psycopg.Error, match="synthetic event failure"):
        repository.enqueue(
            _request(key="manual:snapshot:rollback"),
            trace_id="trace-rollback",
        )

    with psycopg.connect(database_settings.conninfo()) as connection:
        assert connection.execute(
            "SELECT count(*) FROM jobs WHERE idempotency_key = %s",
            ("manual:snapshot:rollback",),
        ).fetchone()[0] == 0


class _Cursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _ExistingJobConnection:
    def __init__(self, row):
        self._row = row
        self.statements: list[str] = []

    def execute(self, statement, parameters):
        self.statements.append(str(statement))
        if len(self.statements) == 1:
            return _Cursor(
                {"job_id": self._row["job_id"], "outcome": "DEDUPLICATED"}
            )
        return _Cursor(self._row)


class _ConflictConnection:
    def __init__(self):
        self.statements: list[str] = []

    def execute(self, statement, parameters):
        self.statements.append(str(statement))
        raise UniqueViolation(
            "idempotency identity conflict",
            info={
                DiagnosticField.SQLSTATE: b"23505",
                DiagnosticField.MESSAGE_PRIMARY: b"idempotency identity conflict",
                DiagnosticField.CONSTRAINT_NAME: b"job_plane_idempotency_identity",
            },
        )


def _existing_job_row(request: EnqueueJobRequest) -> dict[str, object]:
    return {
        "job_id": "job_existing",
        "job_type": request.job_type.value,
        "state": "QUEUED",
        "payload": request.payload.model_dump(mode="json"),
        "payload_fingerprint": payload_fingerprint(request.payload),
        "idempotency_key": request.idempotency_key,
        "actor_type": request.actor.actor_type.value,
        "actor_id": request.actor.actor_id,
        "priority": request.priority,
        "requested_at": datetime(2026, 7, 16, tzinfo=UTC),
        "updated_at": datetime(2026, 7, 16, tzinfo=UTC),
        "attempt_count": 0,
        "max_attempts": 2,
        "reason_code": "ENQUEUED",
        "result_hash": None,
        "cancel_requested_at": None,
        "cancel_actor_type": None,
        "cancel_actor_id": None,
    }


def test_exact_retry_deduplicates_without_appending_an_event() -> None:
    request = _request(key="manual:snapshot:complete-identity")
    connection = _ExistingJobConnection(_existing_job_row(request))
    repository = object.__new__(JobRepository)

    result = repository._enqueue(
        connection,
        request=request,
        trace_id="trace-dropped-response-retry",
    )

    assert result.outcome is EnqueueOutcome.DEDUPLICATED
    assert result.job.job_id == "job_existing"
    assert len(connection.statements) == 2
    assert "job_plane.api_enqueue_snapshot" in connection.statements[0]
    assert all(
        "INSERT INTO job_events" not in statement
        for statement in connection.statements
    )


def test_inactive_actor_is_rejected_before_enqueue_capability() -> None:
    changed_request = _request(
        key="manual:snapshot:complete-identity",
        actor_type="RECOVERY",
        actor_id="operator-7",
    )
    connection = _ExistingJobConnection(_existing_job_row(changed_request))
    repository = object.__new__(JobRepository)

    with pytest.raises(ValueError, match="actor authority"):
        repository._enqueue(
            connection,
            request=changed_request,
            trace_id="trace-mismatched-retry",
        )

    assert connection.statements == []


@pytest.mark.parametrize(
    "changed_request",
    (
        _request(
            key="manual:snapshot:complete-identity",
            actor_id="operator-other",
        ),
        _request(
            key="manual:snapshot:complete-identity",
            priority=9,
        ),
    ),
)
def test_capability_identity_conflict_maps_to_domain_error(
    changed_request: EnqueueJobRequest,
) -> None:
    connection = _ConflictConnection()
    repository = object.__new__(JobRepository)

    with pytest.raises(IdempotencyConflict):
        repository._enqueue(
            connection,
            request=changed_request,
            trace_id="trace-mismatched-retry",
        )

    assert len(connection.statements) == 1
    assert "job_plane.api_enqueue_snapshot" in connection.statements[0]
