"""Offline regressions for the SV01 proof harness (R5 REVISE): stubs only, never PostgreSQL.

They exercise the proof's own control flow: diagnostics on a dict-row connection, evidence written before a failing
recovery raises, the allowed rollback branch of the missing-projection case, and the evidence location. They prove
nothing about SQL behaviour.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import psycopg
import pytest

from packages.engine_event_ledger import EngineEventConflictError, EngineEventSequenceBlockedError
from tests.jobs import test_engine_projection_recovery_postgres_runtime as proof
from tests.jobs._postgres import ROOT

RUN_ID = UUID("70000000-0000-4000-8000-000000000009")
AUTHORITY = ("a" * 64, "b" * 64, UUID("80000000-0000-4000-8000-000000000001"))


def _ambiguous() -> EngineEventConflictError:
    try:
        raise psycopg.errors.AmbiguousColumn('column reference "engine_run_id" is ambiguous')
    except psycopg.Error as error:
        try:
            raise EngineEventConflictError("engine-event ledger rejected the operation") from error
        except EngineEventConflictError as wrapped:
            return wrapped


class _Owner:
    def conninfo(self) -> str:
        return "host=stub"


class _Pool:
    def close(self) -> None:
        pass


class _Ledger:
    def __init__(self, *, projection=None, recover_error=None, ingest_error=None) -> None:
        self.projection, self.recover_error, self.ingest_error = projection, recover_error, ingest_error
        self.ingested = []

    def load_projection(self, _run_id):
        return self.projection

    def recover_projections(self):
        if self.recover_error is not None:
            raise self.recover_error
        return (self.projection,)

    def ingest(self, batch):
        self.ingested.append(batch)
        if self.ingest_error is not None:
            raise self.ingest_error


class _DeleteConnection:
    """psycopg.connect stand-in for the synthetic projection DELETE: one row, inside a transaction."""

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        return None

    @contextmanager
    def transaction(self):
        yield

    def execute(self, *_args):
        return SimpleNamespace(rowcount=1)


def _batch():
    return SimpleNamespace(
        events=(SimpleNamespace(engine_run_id=RUN_ID, stream_sequence=3),),
        sha256=AUTHORITY[0],
        profile_result=SimpleNamespace(semantic_sha256=AUTHORITY[1]),
        validation_metadata={"request_message_id": str(AUTHORITY[2])},
    )


def _projection(*, fenced: bool = True):
    return SimpleNamespace(
        batch_sha256=AUTHORITY[0] if fenced else None,
        semantic_digest=AUTHORITY[1] if fenced else None,
        request_message_id=AUTHORITY[2] if fenced else None,
    )


_OBSERVED: dict[str, object] = {}          # the revision the stub database reports, set by the stub upgrade


def _observe(_owner, revision: str) -> None:
    _OBSERVED["revision"] = revision


@pytest.fixture
def evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "sv01-evidence"
    directory.mkdir()
    _OBSERVED.clear()
    monkeypatch.setenv("TRADING_TEST_SV01_EVIDENCE_DIR", str(directory))
    monkeypatch.setattr(proof, "_upgrade_to_revision", _observe)
    monkeypatch.setattr(proof, "_metadata", lambda _owner: {"revision": _OBSERVED.get("revision")}, raising=False)
    return directory


def _recorded(directory: Path) -> str:
    return "\n".join(path.read_text() for path in sorted(directory.iterdir()))


def test_diagnostics_read_scalars_on_a_dict_row_connection() -> None:
    values = {"pg_get_functiondef": "CREATE FUNCTION stub()", "alembic_version": "0032_p3_enqueue_freshness",
              "server_version": "16.15", "variable_conflict": "error"}

    def value_for(query: str) -> str:
        return next(value for key, value in values.items() if key in query)

    class DictRowConnection:                              # the proof opens its connections with dict_row
        def execute(self, query, *_args):
            return SimpleNamespace(fetchone=lambda: {"column": value_for(query)})

        def cursor(self, *, row_factory):
            assert row_factory is psycopg.rows.tuple_row
            return SimpleNamespace(execute=lambda query, *_a: SimpleNamespace(fetchone=lambda: (value_for(query),)))

    result = proof._diagnostics(DictRowConnection())

    assert result["revision"] == "0032_p3_enqueue_freshness"
    assert result["plpgsql_variable_conflict"] == "error"


def test_each_revision_is_recorded_as_soon_as_it_has_a_result(evidence, monkeypatch) -> None:
    upgrades = []

    def upgrade(owner, revision):
        upgrades.append(revision)
        if len(upgrades) == 2:
            raise RuntimeError("migration to the second revision failed")
        _observe(owner, revision)

    monkeypatch.setattr(proof, "_upgrade_to_revision", upgrade)
    monkeypatch.setattr(proof, "_ingest_generic", lambda *_args: None)
    rows = [{"engine_run_id": proof.RUN_A, "event_count": 2, "last_sequence": 3}]
    monkeypatch.setattr(proof, "_recover_directly", lambda _owner: {"result": "COMMITTED", "rows": rows})

    with pytest.raises(RuntimeError, match="second revision"):
        proof.test_recover_engine_projections_at_required_revisions(_Owner())

    assert proof.REQUIRED_REVISIONS[0] in _recorded(evidence)


def test_failed_idempotent_recovery_is_recorded_before_it_raises(evidence, monkeypatch) -> None:
    monkeypatch.setattr(proof, "_ingest_generic", lambda *_args: None)
    monkeypatch.setattr(proof, "_table_rows", lambda _owner: {})
    monkeypatch.setattr(proof, "_owner_ledger", lambda _owner: (_Pool(), _Ledger(recover_error=_ambiguous())))

    with pytest.raises(EngineEventConflictError):
        proof.test_recover_engine_projections_is_idempotent_after_reconnect(_Owner())

    assert '"42702"' in _recorded(evidence)


def test_failed_completed_p1_recovery_is_recorded_before_it_raises(evidence, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(proof, "_ingest_completed_p1", lambda *_args: _batch())
    monkeypatch.setattr(proof, "_owner_ledger",
                        lambda _owner: (_Pool(), _Ledger(projection=_projection(), recover_error=_ambiguous())))

    with pytest.raises(EngineEventConflictError):
        proof.test_recover_completed_p1_projection_preserves_authority(_Owner(), tmp_path)

    assert '"42702"' in _recorded(evidence)


@pytest.mark.parametrize("append", ("blocked", "succeeds"))
def test_rolled_back_recovery_records_tables_and_never_appends(evidence, tmp_path, monkeypatch, append) -> None:
    ledger = _Ledger(ingest_error=EngineEventSequenceBlockedError(engine_run_id=RUN_ID, expected_sequence=1, actual_sequence=4) if append == "blocked" else None)
    monkeypatch.setattr(proof.psycopg, "connect", lambda *_args, **_kwargs: _DeleteConnection())
    monkeypatch.setattr(proof, "_ingest_completed_p1", lambda *_args: _batch())
    monkeypatch.setattr(proof, "_table_rows", lambda _owner: {"engine_run_projections": []})
    monkeypatch.setattr(proof, "_recover_directly",
                        lambda _owner: {"result": "ROLLED_BACK", "sqlstate": "42702", "message": "ambiguous"})
    monkeypatch.setattr(proof, "_owner_ledger", lambda _owner: (_Pool(), ledger))

    proof.test_recover_missing_completed_projection_cannot_lose_completion_fence(_Owner(), tmp_path)

    assert ledger.ingested == []                               # the rollback branch never tries to append
    assert '"ROLLED_BACK"' in _recorded(evidence)


def test_rolled_back_recovery_must_leave_every_table_unchanged(evidence, tmp_path, monkeypatch) -> None:
    unchanged, changed = {"engine_run_projections": []}, {"engine_run_projections": [{"engine_run_id": RUN_ID}]}
    snapshots = iter((unchanged, unchanged, changed, changed))  # with row, before recovery, after recovery, spare
    monkeypatch.setattr(proof.psycopg, "connect", lambda *_args, **_kwargs: _DeleteConnection())
    monkeypatch.setattr(proof, "_ingest_completed_p1", lambda *_args: _batch())
    monkeypatch.setattr(proof, "_table_rows", lambda _owner: next(snapshots))
    monkeypatch.setattr(proof, "_recover_directly", lambda _owner: {"result": "ROLLED_BACK", "sqlstate": "42702"})
    monkeypatch.setattr(proof, "_owner_ledger", lambda _owner: (_Pool(), _Ledger()))

    with pytest.raises(AssertionError):
        proof.test_recover_missing_completed_projection_cannot_lose_completion_fence(_Owner(), tmp_path)


@pytest.mark.parametrize(("projection", "ingest_error"), [
    (_projection(fenced=False), EngineEventConflictError("completed")),   # recovered without authority
    (_projection(), None),                                                 # authority back, fence does not hold
])
def test_committed_recovery_needs_exact_authority_and_a_holding_fence(
    evidence, tmp_path, monkeypatch, projection, ingest_error,
) -> None:
    monkeypatch.setattr(proof.psycopg, "connect", lambda *_args, **_kwargs: _DeleteConnection())
    monkeypatch.setattr(proof, "_ingest_completed_p1", lambda *_args: _batch())
    monkeypatch.setattr(proof, "_table_rows", lambda _owner: {})
    monkeypatch.setattr(proof, "_recover_directly", lambda _owner: {"result": "COMMITTED", "rows": []})
    monkeypatch.setattr(proof, "_owner_ledger",
                        lambda _owner: (_Pool(), _Ledger(projection=projection, ingest_error=ingest_error)))

    with pytest.raises((AssertionError, pytest.fail.Exception)):
        proof.test_recover_missing_completed_projection_cannot_lose_completion_fence(_Owner(), tmp_path)


@pytest.mark.parametrize(("value", "message"), [
    (str(ROOT), "source checkout"),
    (str(ROOT / "docs"), "source checkout"),
    ("/tmp/phase4-postgres-sv01-probe/evidence", "cleanup root"),
    ("relative/evidence", "absolute"),
])
def test_evidence_directory_outside_checkout_and_cleanup_roots(monkeypatch, value: str, message: str) -> None:
    monkeypatch.setenv("TRADING_TEST_SV01_EVIDENCE_DIR", value)

    with pytest.raises(AssertionError, match=message):
        proof._evidence_dir()



# R5 REVISE-02: the observed revision is checked before each recovery; a rolled-back SQL error at one revision does
# not hide the later revisions, and the case still fails at the end.
def _count_recoveries(monkeypatch, outcomes):
    calls = []

    def recover(_owner):
        calls.append(_OBSERVED.get("revision"))
        return outcomes(len(calls))

    monkeypatch.setattr(proof, "_recover_directly", recover)
    return calls


_GOOD_ROWS = [{"engine_run_id": proof.RUN_A, "event_count": 2, "last_sequence": 3}]


def test_revision_mismatch_stops_before_recovery_at_required_revisions(evidence, monkeypatch) -> None:
    monkeypatch.setattr(proof, "_ingest_generic", lambda *_args: None)
    monkeypatch.setattr(proof, "_metadata", lambda _owner: {"revision": proof.CURRENT_HEAD})   # always reports 0032
    calls = _count_recoveries(monkeypatch, lambda _n: {"result": "COMMITTED", "rows": _GOOD_ROWS})

    with pytest.raises(AssertionError, match="revision"):
        proof.test_recover_engine_projections_at_required_revisions(_Owner())

    assert calls == []                                       # no recovery ran against the wrong schema


@pytest.mark.parametrize("case", ("idempotent", "completed_p1", "missing_projection"))
def test_current_head_cases_reject_a_wrong_revision_before_recovery(evidence, tmp_path, monkeypatch, case) -> None:
    monkeypatch.setattr(proof, "_metadata", lambda _owner: {"revision": "0019_p2_security_master"})
    deletes = []
    connection = _DeleteConnection()
    monkeypatch.setattr(connection, "execute", lambda *args: deletes.append(args) or SimpleNamespace(rowcount=1))
    monkeypatch.setattr(proof.psycopg, "connect", lambda *_args, **_kwargs: connection)
    monkeypatch.setattr(proof, "_ingest_generic", lambda *_args: None)
    monkeypatch.setattr(proof, "_ingest_completed_p1", lambda *_args: _batch())
    monkeypatch.setattr(proof, "_table_rows", lambda _owner: {})
    ledger = _Ledger(projection=_projection())
    monkeypatch.setattr(proof, "_owner_ledger", lambda _owner: (_Pool(), ledger))
    calls = _count_recoveries(monkeypatch, lambda _n: {"result": "COMMITTED", "rows": []})
    recovered = []
    monkeypatch.setattr(ledger, "recover_projections", lambda: recovered.append(1) or ())
    run = {
        "idempotent": lambda: proof.test_recover_engine_projections_is_idempotent_after_reconnect(_Owner()),
        "completed_p1": lambda: proof.test_recover_completed_p1_projection_preserves_authority(_Owner(), tmp_path),
        "missing_projection": lambda: proof.test_recover_missing_completed_projection_cannot_lose_completion_fence(
            _Owner(), tmp_path),
    }[case]

    with pytest.raises(AssertionError, match="revision"):
        run()

    assert calls == [] and recovered == []
    if case == "missing_projection":                         # no synthetic DELETE on an unverified schema either
        assert deletes == []


def test_rolled_back_first_revision_still_records_every_revision_then_fails(evidence, monkeypatch) -> None:
    monkeypatch.setattr(proof, "_ingest_generic", lambda *_args: None)
    calls = _count_recoveries(monkeypatch, lambda n: (
        {"result": "ROLLED_BACK", "sqlstate": "42702", "message": "ambiguous"} if n == 1
        else {"result": "COMMITTED", "rows": _GOOD_ROWS}))

    with pytest.raises(AssertionError, match="42702"):
        proof.test_recover_engine_projections_at_required_revisions(_Owner())

    assert calls == list(proof.REQUIRED_REVISIONS)            # all three revisions ran
    recorded = _recorded(evidence)
    assert all(f'"requested_revision": "{revision}"' in recorded for revision in proof.REQUIRED_REVISIONS)
