from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from threading import Event, Thread
from types import MappingProxyType

import psycopg
import pytest

from packages.job_contracts import (
    ActorIdentity,
    EnqueueJobRequest,
    canonical_payload_json,
    payload_fingerprint,
)
from services.job_store import (
    InvalidJobFilters,
    JobFilters,
    JobRepository,
    JobStoreSettings,
)
from services.job_store.worker_repository import WorkerRepository
from services.job_worker.recovery import ProcessIdentity
from tests.jobs._postgres import (
    disposable_database,
    disposable_role_settings,
    upgrade_to_head,
)


def _settings(database_settings) -> JobStoreSettings:
    return JobStoreSettings(
        host=database_settings.host,
        port=database_settings.port,
        database=database_settings.database,
        user=database_settings.user,
        password=database_settings.password,
        pool_max=3,
    )


def _request(
    key: str,
    *,
    job_type: str = "SNAPSHOT",
    actor_type: str = "OPERATOR",
    actor_id: str = "operator-list",
) -> EnqueueJobRequest:
    payloads = {
        "SNAPSHOT": {"scope": "default", "requested_as_of": None},
        "DEBATE": {"asset": "BTC", "horizon": "1d"},
        "REPLAY": {"session_id": "session_list"},
        "BACKTEST": {
            "asset": "ETH",
            "strategy_id": "legacy-binary-report-v1",
            "date_from": None,
            "date_to": None,
        },
    }
    return EnqueueJobRequest.model_validate(
        {
            "job_type": job_type,
            "payload": payloads[job_type],
            "idempotency_key": key,
            "actor": {"actor_type": actor_type, "actor_id": actor_id},
        }
    )


@pytest.fixture
def repository_database():
    with disposable_database(
        operation_id="jobs-repository-queries-v1"
    ) as database_settings:
        upgrade_to_head(database_settings)
        api_settings = disposable_role_settings(
            database_settings, "trading_job_api"
        )
        worker_settings = disposable_role_settings(
            database_settings, "trading_job_worker"
        )
        with (
            JobRepository(_settings(api_settings)) as repository,
            WorkerRepository(_settings(worker_settings)) as workers,
        ):
            yield repository, workers, database_settings


def _owner_seed_queued_historical_job(
    database_settings,
    *,
    request: EnqueueJobRequest,
    job_id: str,
    requested_at: datetime,
) -> str:
    """Seed one internally consistent legacy row outside runtime capabilities."""

    with psycopg.connect(database_settings.conninfo()) as owner:
        owner.execute(
            """
            INSERT INTO jobs (
              job_id, job_type, state, payload, payload_fingerprint,
              idempotency_key, actor_type, actor_id, priority, requested_at,
              updated_at, max_attempts, reason_code
            ) VALUES (
              %s, %s, 'QUEUED', %s::jsonb, %s, %s, %s, %s, %s, %s, %s, 2,
              'ENQUEUED'
            )
            """,
            (
                job_id,
                request.job_type.value,
                canonical_payload_json(request.payload),
                payload_fingerprint(request.payload),
                request.idempotency_key,
                request.actor.actor_type.value,
                request.actor.actor_id,
                request.priority,
                requested_at,
                requested_at,
            ),
        )
        owner.execute(
            """
            INSERT INTO job_events (
              event_id, job_id, sequence, from_state, to_state, reason_code,
              actor_type, actor_id, trace_id, metadata
            ) VALUES (
              %s, %s, 1, NULL, 'QUEUED', 'ENQUEUED', %s, %s, %s,
              '{}'::jsonb
            )
            """,
            (
                f"event_{job_id}",
                job_id,
                request.actor.actor_type.value,
                request.actor.actor_id,
                f"seed:{job_id}",
            ),
        )
    return job_id


def _start_claimed_job(
    workers: WorkerRepository,
    *,
    worker_id: str,
    claim_trace: str,
    start_trace: str,
):
    claimed = workers.claim_next(worker_id, 30, claim_trace)
    assert claimed is not None
    assert workers.start_attempt(
        claimed.job_id,
        claimed.attempt_id,
        worker_id,
        claimed.lease_token,
        ProcessIdentity(7001, 7001, 11, "d" * 64),
        start_trace,
    )
    return claimed


def test_list_jobs_has_stable_newest_first_order_with_job_id_tiebreak(
    repository_database,
) -> None:
    repository, _, database_settings = repository_database
    ids = [
        repository.enqueue(_request(f"list:stable:{index}"), trace_id=f"trace-{index}").job.job_id
        for index in range(3)
    ]
    tied_at = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    with psycopg.connect(database_settings.conninfo()) as connection:
        connection.execute(
            "UPDATE jobs SET requested_at = %s WHERE job_id = ANY(%s)",
            (tied_at, ids),
        )

    listed = repository.list_jobs(JobFilters())

    assert [job.job_id for job in listed] == sorted(ids, reverse=True)


def test_list_jobs_applies_type_state_actor_and_time_filters(repository_database) -> None:
    repository, _, database_settings = repository_database
    snapshot = repository.enqueue(
        _request("list:filter:snapshot", actor_id="alice"), trace_id="trace-filter-1"
    ).job
    debate_id = _owner_seed_queued_historical_job(
        database_settings,
        request=_request(
            "list:filter:debate",
            job_type="DEBATE",
            actor_id="bob",
        ),
        job_id="job_historical_debate",
        requested_at=datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc),
    )
    repository.request_cancel(
        snapshot.job_id,
        ActorIdentity.model_validate(
            {"actor_type": "OPERATOR", "actor_id": "alice"}
        ),
        "trace-filter-cancel",
    )
    before = datetime(2026, 7, 12, 11, 0, tzinfo=timezone.utc)
    after = datetime(2026, 7, 12, 13, 0, tzinfo=timezone.utc)
    with psycopg.connect(database_settings.conninfo()) as connection:
        connection.execute(
            "UPDATE jobs SET requested_at = %s WHERE job_id = %s",
            (datetime(2026, 7, 12, 10, 0, tzinfo=timezone.utc), snapshot.job_id),
        )

    assert [job.job_id for job in repository.list_jobs(JobFilters(job_type="DEBATE"))] == [
        debate_id
    ]
    assert [job.job_id for job in repository.list_jobs(JobFilters(state="CANCELLED"))] == [
        snapshot.job_id
    ]
    assert [job.job_id for job in repository.list_jobs(
        JobFilters(actor_type="OPERATOR", actor_id="bob")
    )] == [debate_id]
    assert [job.job_id for job in repository.list_jobs(
        JobFilters(requested_from=before, requested_to=after)
    )] == [debate_id]


def test_list_jobs_applies_bounded_limit_and_offset(repository_database) -> None:
    repository, _, _ = repository_database
    for index in range(4):
        repository.enqueue(_request(f"list:page:{index}"), trace_id=f"trace-page-{index}")
    all_jobs = repository.list_jobs(JobFilters(limit=4))

    page = repository.list_jobs(JobFilters(limit=2, offset=1))

    assert page == all_jobs[1:3]
    for filters in (
        JobFilters(limit=0),
        JobFilters(limit=101),
        JobFilters(offset=-1),
        JobFilters(actor_id="missing-type"),
        JobFilters(
            requested_from=datetime(2026, 7, 13, tzinfo=timezone.utc),
            requested_to=datetime(2026, 7, 12, tzinfo=timezone.utc),
        ),
    ):
        with pytest.raises(InvalidJobFilters):
            repository.list_jobs(filters)


@pytest.mark.parametrize(
    "filters",
    [
        JobFilters(limit="10"),
        JobFilters(limit=None),
        JobFilters(offset="0"),
        JobFilters(offset=None),
        JobFilters(actor_type=1),
        JobFilters(actor_type="OPERATOR", actor_id=1),
        JobFilters(requested_from="2026-07-12T00:00:00Z"),
        JobFilters(requested_to=1),
        JobFilters(requested_from=datetime(2026, 7, 12)),
    ],
)
def test_list_jobs_normalizes_all_malformed_filter_types(
    repository_database, filters
) -> None:
    repository, _, _ = repository_database

    with pytest.raises(InvalidJobFilters):
        repository.list_jobs(filters)


def test_get_job_returns_events_in_sequence_order(repository_database) -> None:
    repository, workers, _ = repository_database
    job = repository.enqueue(_request("detail:events"), trace_id="trace-detail-1").job
    claimed = _start_claimed_job(
        workers,
        worker_id="worker-detail",
        claim_trace="trace-detail-2",
        start_trace="trace-detail-3",
    )
    assert claimed.job_id == job.job_id

    detail = repository.get_job(job.job_id)

    assert detail is not None
    assert [event.sequence for event in detail.events] == [1, 2, 3]
    assert [event.trace_id for event in detail.events] == [
        "trace-detail-1",
        "trace-detail-2",
        "trace-detail-3",
    ]


def test_get_job_uses_one_repeatable_read_snapshot_during_concurrent_transition(
    repository_database, monkeypatch
) -> None:
    repository, workers, _ = repository_database
    job = repository.enqueue(_request("detail:snapshot"), trace_id="trace-snapshot-1").job
    first_job_read = Event()
    transition_finished = Event()
    real_connection_context = repository._pool.connection

    class GatedConnection:
        def __init__(self, connection):
            self._connection = connection

        def execute(self, query, params=None):
            result = self._connection.execute(query, params)
            if "FROM jobs WHERE job_id" in str(query) and not first_job_read.is_set():
                first_job_read.set()
                assert transition_finished.wait(timeout=5)
            return result

        def __getattr__(self, name):
            return getattr(self._connection, name)

    @contextmanager
    def gated_connection(*args, **kwargs):
        with real_connection_context(*args, **kwargs) as connection:
            yield GatedConnection(connection)

    monkeypatch.setattr(repository._pool, "connection", gated_connection)

    transition_errors: list[BaseException] = []

    def transition() -> None:
        try:
            assert first_job_read.wait(timeout=5)
            claimed = workers.claim_next(
                "worker-snapshot", 30, "trace-snapshot-2"
            )
            assert claimed is not None and claimed.job_id == job.job_id
        except BaseException as error:
            transition_errors.append(error)
        finally:
            transition_finished.set()

    writer_thread = Thread(target=transition)
    writer_thread.start()
    detail = repository.get_job(job.job_id)
    writer_thread.join(timeout=5)

    assert not writer_thread.is_alive()
    assert transition_errors == []
    assert detail is not None
    assert detail.job.state.value == "QUEUED"
    assert [event.trace_id for event in detail.events] == ["trace-snapshot-1"]


def test_get_job_recursively_freezes_event_and_artifact_metadata(
    repository_database,
) -> None:
    repository, _, database_settings = repository_database
    job = repository.enqueue(_request("detail:immutable"), trace_id="trace-immutable-1").job
    with psycopg.connect(database_settings.conninfo()) as owner:
        owner.execute(
            """
            UPDATE jobs
            SET state = 'CLAIMED', attempt_count = 1,
                lease_owner = 'worker-immutable',
                lease_token = 'lease-token-immutable-00000001',
                lease_expires_at = now() + interval '1 minute',
                reason_code = 'CLAIMED', updated_at = now()
            WHERE job_id = %s
            """,
            (job.job_id,),
        )
        owner.execute(
            """
            INSERT INTO job_attempts (
              attempt_id, job_id, attempt_number, worker_id, outcome,
              lease_token, lease_expires_at, claimed_at
            ) VALUES (
              'attempt-immutable', %s, 1, 'worker-immutable', 'CLAIMED',
              'lease-token-immutable-00000001',
              now() + interval '1 minute', now()
            )
            """,
            (job.job_id,),
        )
        owner.execute(
            """
            INSERT INTO job_events (
              event_id, job_id, attempt_id, sequence, from_state, to_state,
              reason_code, actor_type, actor_id, trace_id, metadata
            ) VALUES (
              'event-immutable', %s, 'attempt-immutable', 2, 'QUEUED', 'CLAIMED',
              'CLAIMED', 'WORKER', 'worker-immutable', 'trace-immutable-2',
              '{"nested":{"values":[1,{"flag":true}]}}'::jsonb
            )
            """,
            (job.job_id,),
        )
        owner.execute(
            """
            INSERT INTO job_artifacts (
              artifact_id, job_id, attempt_id, artifact_type, relative_ref,
              sha256, size_bytes, media_type, validator_id, validation_metadata
            ) VALUES (
              'artifact-immutable', %s, 'attempt-immutable', 'REPORT',
              'jobs/immutable/report.json', %s, 1, 'application/json',
              'report-v1', '{"checks":[{"name":"shape","ok":true}]}'::jsonb
            )
            """,
            (job.job_id, "a" * 64),
        )

    detail = repository.get_job(job.job_id)

    assert detail is not None
    event_metadata = detail.events[-1].metadata
    artifact_metadata = detail.artifacts[0].validation_metadata
    assert isinstance(event_metadata, MappingProxyType)
    assert isinstance(event_metadata["nested"], MappingProxyType)
    assert isinstance(event_metadata["nested"]["values"], tuple)
    assert isinstance(event_metadata["nested"]["values"][1], MappingProxyType)
    assert isinstance(artifact_metadata, MappingProxyType)
    assert isinstance(artifact_metadata["checks"], tuple)
    with pytest.raises(TypeError):
        event_metadata["new"] = "forbidden"
    with pytest.raises(TypeError):
        artifact_metadata["checks"][0]["ok"] = False


def test_get_job_returns_none_for_unknown_id(repository_database) -> None:
    repository, _, _ = repository_database
    assert repository.get_job("job_unknown") is None
