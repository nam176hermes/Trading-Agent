"""SV01 proof: `recover_engine_run_projections()` on a real disposable PostgreSQL 16 cluster.

Migration 0010 is not modified. Each test owns one planned disposable database (its own operation id and fixture
slot). Evidence is written create-only to TRADING_TEST_SV01_EVIDENCE_DIR, which must lie outside the source
checkout and every disposable cleanup root: metadata (revision, `plpgsql.variable_conflict`, function definition
hash) before each checked recovery, and that same invocation's result or SQLSTATE before any assertion or cleanup.
Skipped or unauthorized runs are not a PASS.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
import dataclasses
import hashlib
import json
import os
from pathlib import Path
from typing import TypeVar
from uuid import UUID

import psycopg
from psycopg.rows import dict_row, tuple_row
from psycopg_pool import ConnectionPool
import pytest

from packages.engine_event_ledger import EngineEventConflictError
from scripts.validate_disposable_postgres_approval import PGDATA_PREFIX
from services.job_store.config import JobStoreSettings
from services.job_store.engine_event_repository import PostgresEngineEventLedger
from services.job_store.worker_repository import WorkerRepository
from tests.control_api._disposable_runtime import require_disposable_green
from tests.engine_event_ledger.test_p1_stream_ingestion import _validated_p1_batch
from tests.jobs._postgres import (
    ROOT,
    _upgrade_to_revision,
    disposable_database,
    disposable_role_settings,
    provision_p3_roles,
)
from tests.jobs.test_engine_event_ledger import _batch, _event
from tests.jobs.test_engine_event_postgres_runtime import P1_PRODUCT_CLOSURE_SHA256, _seed_running_job
from tests.nautilus_runtime_contracts.test_result import CODE_COMMIT, _p1_claim

pytestmark = pytest.mark.runtime_postgres

REQUIRED_REVISIONS = (
    "0018_p1_paper_closure_rotation",
    "0019_p2_security_master",
    "0032_p3_enqueue_freshness",
)
CURRENT_HEAD = REQUIRED_REVISIONS[-1]
P3_ROLE_REVISION = "0019_p2_security_master"            # the P3 role bootstrap runs here, before 0020
RUN_A = UUID("70000000-0000-4000-8000-000000000001")
PROJECTION_TABLES = ("engine_run_projections", "engine_events", "engine_event_batch_receipts")
_T = TypeVar("_T")


def _evidence_dir() -> Path:
    """Resolve the evidence directory; reject the source checkout and disposable cleanup roots."""
    value = os.environ.get("TRADING_TEST_SV01_EVIDENCE_DIR", "")
    if not value or not Path(value).is_absolute():
        raise AssertionError("SV01 evidence directory must be an absolute path")
    path = Path(value).resolve()
    if path.is_relative_to(ROOT.resolve()):
        raise AssertionError("SV01 evidence directory must be outside the source checkout")
    if any(str(part).startswith(PGDATA_PREFIX) for part in (path, *path.parents)):
        raise AssertionError("SV01 evidence directory must be outside every disposable cleanup root")
    if not path.is_dir():
        raise AssertionError("SV01 evidence directory must already exist")
    return path


def _jsonable(value: object) -> object:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    return str(value)


def _record(name: str, document: object) -> None:
    with (_evidence_dir() / f"{name}.json").open("x", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2, sort_keys=True, default=_jsonable)
        handle.write("\n")


def _sqlstate(error: BaseException) -> str | None:
    """SQLSTATE of the error or of the database error it wraps (the repository re-raises typed errors)."""
    current: BaseException | None = error
    while current is not None:
        if isinstance(current, psycopg.Error):
            return current.sqlstate
        current = current.__cause__
    return None


def _checked(name: str, invocation: Callable[[], _T]) -> _T:
    """Run the checked recovery invocation and record its own outcome before returning or re-raising."""
    try:
        value = invocation()
    except Exception as error:
        _record(name, {"result": "FAILED", "error_type": type(error).__name__, "message": str(error),
                       "sqlstate": _sqlstate(error)})
        raise
    _record(name, {"result": "RETURNED", "value": value})
    return value


@pytest.fixture
def owner_database(request: pytest.FixtureRequest) -> Iterator[object]:
    require_disposable_green()
    _evidence_dir()                                          # before any cluster exists
    operation_id = f"sv01-{request.node.name.removeprefix('test_').replace('_', '-')}-v1"
    with disposable_database(operation_id=operation_id, planned=True, p3_roles=True) as owner:   # 0020-0032 need them
        yield owner


def _diagnostics(connection) -> dict[str, object]:
    cursor = connection.cursor(row_factory=tuple_row)        # scalar reads, whatever the connection's row factory

    def scalar(query: str) -> str:
        return cursor.execute(query).fetchone()[0]

    definition = scalar("SELECT pg_get_functiondef('public.recover_engine_run_projections()'::regprocedure)")
    catalog = cursor.execute(                               # what a guarded forward rotation binds
        "SELECT p.prosrc, pg_catalog.pg_get_userbyid(p.proowner), p.proacl::text, p.proconfig::text,"
        " p.prosecdef, p.provolatile::text, p.proparallel::text,"
        " pg_catalog.pg_get_function_identity_arguments(p.oid), pg_catalog.pg_get_function_result(p.oid)"
        " FROM pg_catalog.pg_proc AS p WHERE p.oid = 'public.recover_engine_run_projections()'::regprocedure"
    ).fetchone()
    key = cursor.execute(
        "SELECT c.conname, pg_catalog.pg_get_constraintdef(c.oid) FROM pg_catalog.pg_constraint AS c"
        " WHERE c.conrelid = 'public.engine_run_projections'::regclass AND c.contype = 'p'"
    ).fetchone()
    cursor.execute("DO $sv01$ BEGIN END $sv01$")             # load plpgsql so its setting exists; changes nothing
    return {
        "revision": scalar("SELECT version_num FROM public.alembic_version"),
        "server_version": scalar("SHOW server_version"),
        "plpgsql_variable_conflict": scalar("SHOW plpgsql.variable_conflict"),
        "function_definition_sha256": hashlib.sha256(definition.encode()).hexdigest(),
        "function_definition": definition,
        "function_prosrc_sha256": hashlib.sha256(catalog[0].encode()).hexdigest(),
        "function_prosrc": catalog[0],
        "function_catalog": dict(zip(
            ("owner", "acl", "config", "security_definer", "volatility", "parallel", "identity_arguments", "result"),
            catalog[1:],
        )),
        "projection_primary_key": {"name": key[0], "definition": key[1]},
    }


def _metadata(owner) -> dict[str, object]:
    with psycopg.connect(owner.conninfo(), row_factory=dict_row) as connection:
        return _diagnostics(connection)


def _require_revision(metadata: dict[str, object], revision: str) -> None:
    """The observed schema revision must be the requested one before any recovery runs against it."""
    if metadata.get("revision") != revision:
        raise AssertionError(f"observed revision {metadata.get('revision')!r} is not the requested {revision!r}")


def _recover_directly(owner) -> dict[str, object]:
    """Call the SQL function in its own transaction; return its rows or its exact SQLSTATE after rollback."""
    with psycopg.connect(owner.conninfo(), row_factory=dict_row) as connection:
        try:
            with connection.transaction():
                rows = connection.execute("SELECT * FROM public.recover_engine_run_projections()").fetchall()
            return {"result": "COMMITTED", "rows": rows}
        except psycopg.Error as error:
            return {"result": "ROLLED_BACK", "sqlstate": error.sqlstate, "message": str(error)}


def _table_rows(owner) -> dict[str, list[dict[str, object]]]:
    with psycopg.connect(owner.conninfo(), row_factory=dict_row) as connection:
        return {
            table: connection.execute(f"SELECT * FROM public.{table} ORDER BY 1").fetchall()  # noqa: S608 - fixed names
            for table in PROJECTION_TABLES
        }


def _owner_ledger(owner) -> tuple[ConnectionPool, PostgresEngineEventLedger]:
    pool = ConnectionPool(
        conninfo=owner.conninfo(), min_size=1, max_size=2, kwargs={"row_factory": dict_row}, open=True,
    )
    pool.wait()
    return pool, PostgresEngineEventLedger(pool)


def _upgrade(owner, revision: str) -> None:
    """Upgrade; past 0019, first stop at 0019 and run the bound P3 role bootstrap (0020-0032 need it).

    Each case upgrades past 0019 once. No state is kept here: the harness allows exactly one bootstrap per
    database context and rejects a second call.
    """
    if revision not in (REQUIRED_REVISIONS[0], P3_ROLE_REVISION):
        _upgrade_to_revision(owner, P3_ROLE_REVISION)
        provision_p3_roles(owner)
    _upgrade_to_revision(owner, revision)


def _ingest_generic(owner, run_id: UUID) -> None:
    pool, ledger = _owner_ledger(owner)
    try:
        ledger.ingest(_batch(_event(2, "BacktestStarted", engine_run_id=run_id),
                             _event(3, "BacktestCompleted", engine_run_id=run_id)))
    finally:
        pool.close()


def _ingest_completed_p1(owner, tmp_path: Path):
    claim = _p1_claim()
    batch = _validated_p1_batch(tmp_path, closure_digest=P1_PRODUCT_CLOSURE_SHA256)
    with psycopg.connect(owner.conninfo()) as connection:
        _seed_running_job(connection, claim, idempotency_key="sv01-p1-completed")
    worker = disposable_role_settings(owner, "trading_job_worker")
    settings = JobStoreSettings(host=worker.host, port=worker.port, database=worker.database,
                                user=worker.user, password=worker.password, pool_max=2)
    with WorkerRepository(settings) as repository:
        repository.worker_heartbeat(claim.worker_id, CODE_COMMIT, "IDLE", current_job_id=None, current_attempt_id=None)
        repository.worker_heartbeat(claim.worker_id, CODE_COMMIT, "BUSY",
                                    current_job_id=claim.job_id, current_attempt_id=claim.attempt_id)
        repository.engine_event_ingestor().ingest_for_job(batch, claimed=claim)
    return batch


def _authority(batch) -> tuple[object, object, UUID]:
    return batch.sha256, batch.profile_result.semantic_sha256, UUID(batch.validation_metadata["request_message_id"])


def _append_after_completion(ledger: PostgresEngineEventLedger, batch) -> None:
    last = batch.events[-1]
    ledger.ingest(_batch(_event(last.stream_sequence + 1, "BacktestContinued", engine_run_id=last.engine_run_id)))


def test_recover_engine_projections_at_required_revisions(owner_database) -> None:
    """A rolled-back SQL error is recorded and the later revisions still run; migration, authority or revision
    failures stop at once. The verdict is asserted only after every revision has a recorded result."""
    results = {}
    for index, revision in enumerate(REQUIRED_REVISIONS):
        _upgrade(owner_database, revision)
        if index == 0:
            _ingest_generic(owner_database, RUN_A)
        name = f"sv01-revision-{index + 1}-{revision}"
        metadata = _metadata(owner_database)
        _record(f"{name}-metadata", metadata)
        _require_revision(metadata, revision)
        result = _recover_directly(owner_database)
        _record(f"{name}-result", {"requested_revision": revision, **result})   # before the next revision
        results[revision] = result

    failures = {
        revision: (result.get("sqlstate"), result.get("message"))
        for revision, result in results.items() if result["result"] != "COMMITTED"
    }
    assert not failures, failures
    for revision, result in results.items():
        assert [(row["engine_run_id"], row["event_count"], row["last_sequence"]) for row in result["rows"]] == [
            (RUN_A, 2, 3)
        ], revision


def test_recover_engine_projections_is_idempotent_after_reconnect(owner_database) -> None:
    _upgrade(owner_database, CURRENT_HEAD)
    _ingest_generic(owner_database, RUN_A)
    before = _table_rows(owner_database)
    metadata = _metadata(owner_database)
    _record("sv01-idempotent-metadata", {"metadata": metadata, "tables_before": before})
    _require_revision(metadata, CURRENT_HEAD)
    outcomes = []
    for attempt in (1, 2):                                   # a fresh pool (reconnect) per recovery
        pool, ledger = _owner_ledger(owner_database)
        try:
            outcomes.append(_checked(f"sv01-idempotent-recovery-{attempt}", ledger.recover_projections))
        finally:
            pool.close()
    after = _table_rows(owner_database)
    _record("sv01-idempotent-tables-after", after)

    assert outcomes[0] == outcomes[1]
    for table in ("engine_events", "engine_event_batch_receipts"):
        assert after[table] == before[table]
    assert [{k: v for k, v in row.items() if k != "updated_at"} for row in after["engine_run_projections"]] == [
        {k: v for k, v in row.items() if k != "updated_at"} for row in before["engine_run_projections"]
    ]


def test_recover_completed_p1_projection_preserves_authority(owner_database, tmp_path: Path) -> None:
    _upgrade(owner_database, CURRENT_HEAD)
    batch = _ingest_completed_p1(owner_database, tmp_path)
    run_id = batch.events[0].engine_run_id
    pool, ledger = _owner_ledger(owner_database)
    try:
        before = ledger.load_projection(run_id)
        metadata = _metadata(owner_database)
        _record("sv01-completed-p1-metadata", {"metadata": metadata, "projection_before": before})
        _require_revision(metadata, CURRENT_HEAD)
        recovered = _checked("sv01-completed-p1-recovery", ledger.recover_projections)
        after = ledger.load_projection(run_id)
        _record("sv01-completed-p1-projection-after", {"projection_after": after})
        assert before is not None and before.semantic_digest is not None
        assert after == before and before in recovered
        assert (after.batch_sha256, after.semantic_digest, after.request_message_id) == _authority(batch)
        with pytest.raises(EngineEventConflictError):
            _append_after_completion(ledger, batch)             # the completion fence still holds
    finally:
        pool.close()


def test_recover_missing_completed_projection_cannot_lose_completion_fence(owner_database, tmp_path: Path) -> None:
    """Deleting the synthetic projection row is the one data mutation this operation's work order names."""
    _upgrade(owner_database, CURRENT_HEAD)
    batch = _ingest_completed_p1(owner_database, tmp_path)
    run_id = batch.events[0].engine_run_id
    tables_with_projection = _table_rows(owner_database)
    metadata = _metadata(owner_database)
    _record("sv01-missing-projection-metadata", {"metadata": metadata, "tables_with_projection": tables_with_projection})
    _require_revision(metadata, CURRENT_HEAD)                 # before the synthetic DELETE and before recovery
    with psycopg.connect(owner_database.conninfo()) as connection:
        with connection.transaction():                       # rowcount is checked before the commit
            deleted = connection.execute(
                "DELETE FROM public.engine_run_projections WHERE engine_run_id = %s", (run_id,)
            ).rowcount
            if deleted != 1:
                raise AssertionError(f"synthetic projection DELETE touched {deleted} rows; rolled back")
    before = _table_rows(owner_database)
    _record("sv01-missing-projection-before", {"deleted_rows": deleted, "tables_before_recovery": before})

    result = _recover_directly(owner_database)
    _record("sv01-missing-projection-recovery", result)
    after = _table_rows(owner_database)
    _record("sv01-missing-projection-tables-after", after)

    if result["result"] != "COMMITTED":                      # allowed: rejected and fully rolled back, nothing else
        assert after == before
        return
    pool, ledger = _owner_ledger(owner_database)             # committed: exact authority and a holding fence
    try:
        projection = ledger.load_projection(run_id)
        _record("sv01-missing-projection-projection-after", {"projection_after": projection})
        assert projection is not None
        assert (projection.batch_sha256, projection.semantic_digest, projection.request_message_id) == _authority(batch)
        with pytest.raises(EngineEventConflictError):
            _append_after_completion(ledger, batch)
    finally:
        pool.close()
