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


_CATALOG_ROWS = {                                        # the diagnostics' multi-column catalog reads
    "p.prosrc": ("BEGIN RETURN; END;", "trading_owner", None, None, True, "v", "u", "", "TABLE(x uuid)"),
    "pg_constraint": ("engine_run_projections_pkey", "PRIMARY KEY (engine_run_id)"),
}


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
    monkeypatch.setattr(proof, "provision_p3_roles", lambda _owner: None, raising=False)
    return directory


def _recorded(directory: Path) -> str:
    return "\n".join(path.read_text() for path in sorted(directory.iterdir()))


def test_diagnostics_read_scalars_on_a_dict_row_connection() -> None:
    values = {"pg_get_functiondef": "CREATE FUNCTION stub()", "alembic_version": "0032_p3_enqueue_freshness",
              "server_version": "16.15", "variable_conflict": "error"}

    def value_for(query: str) -> str:
        return next(value for key, value in values.items() if key in query)

    def row_for(query: str) -> tuple:
        key = next((key for key in _CATALOG_ROWS if key in query), None)
        return _CATALOG_ROWS[key] if key is not None else (value_for(query),)

    class DictRowConnection:                              # the proof opens its connections with dict_row
        def execute(self, query, *_args):
            return SimpleNamespace(fetchone=lambda: {"column": value_for(query)})

        def cursor(self, *, row_factory):
            assert row_factory is psycopg.rows.tuple_row
            return SimpleNamespace(execute=lambda query, *_a: SimpleNamespace(fetchone=lambda: row_for(query)))

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


_TABLES = {"engine_run_projections": [], "engine_events": [{"engine_run_id": RUN_ID}],
           "engine_event_batch_receipts": [{"batch_sha256": AUTHORITY[0]}]}
_FENCE_ROLLBACK = {"result": "ROLLED_BACK", "message": "fence", **proof.FENCE}


def _missing_projection_case(monkeypatch, *, recovery, snapshots=None, ledger=None):
    monkeypatch.setattr(proof.psycopg, "connect", lambda *_args, **_kwargs: _DeleteConnection())
    monkeypatch.setattr(proof, "_ingest_completed_p1", lambda *_args: _batch())
    tables = iter(snapshots) if snapshots is not None else None
    monkeypatch.setattr(proof, "_table_rows", lambda _owner: next(tables) if tables is not None else _TABLES)
    monkeypatch.setattr(proof, "_recover_directly", lambda _owner: recovery)
    monkeypatch.setattr(proof, "_owner_ledger", lambda _owner: (_Pool(), ledger or _Ledger()))


def test_fence_rejection_with_every_table_unchanged_passes_and_never_appends(evidence, tmp_path, monkeypatch) -> None:
    ledger = _Ledger()
    _missing_projection_case(monkeypatch, recovery=_FENCE_ROLLBACK, ledger=ledger)

    proof.test_recover_missing_completed_projection_cannot_lose_completion_fence(_Owner(), tmp_path)

    assert ledger.ingested == []
    assert '"reason=P1_COMPLETED_PROJECTION_MISSING"' in _recorded(evidence)


@pytest.mark.parametrize("recovery", [
    {"result": "ROLLED_BACK", "sqlstate": "42702", "message_primary": "ambiguous", "detail": None},   # the defect
    {**_FENCE_ROLLBACK, "message_primary": "durable engine-event sequence is not recoverable", "detail": None},
    {**_FENCE_ROLLBACK, "detail": None},                                                    # P2D01 without reason
    {"result": "COMMITTED", "rows": []},                                                    # recreated, fenced or not
], ids=["42702", "other-p2d01", "no-reason", "committed"])
def test_only_the_fence_rejection_is_evidence_for_the_missing_projection_case(
    evidence, tmp_path, monkeypatch, recovery,
) -> None:
    _missing_projection_case(monkeypatch, recovery=recovery, ledger=_Ledger(projection=_projection()))

    with pytest.raises(AssertionError):
        proof.test_recover_missing_completed_projection_cannot_lose_completion_fence(_Owner(), tmp_path)


def test_fence_rejection_must_leave_every_table_unchanged(evidence, tmp_path, monkeypatch) -> None:
    changed = {**_TABLES, "engine_run_projections": [{"engine_run_id": RUN_ID}]}
    _missing_projection_case(monkeypatch, recovery=_FENCE_ROLLBACK, snapshots=(_TABLES, _TABLES, changed))

    with pytest.raises(AssertionError):
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
_DEFECT = {"result": "ROLLED_BACK", "sqlstate": "42702", "message": "ambiguous"}


def _defect_then_repair(_n):
    return ({"result": "COMMITTED", "rows": _GOOD_ROWS} if _OBSERVED.get("revision") == proof.REPAIR_REVISION
            else _DEFECT)


def test_required_revisions_pass_only_with_the_defect_before_and_a_commit_at_the_repair(evidence, monkeypatch) -> None:
    monkeypatch.setattr(proof, "_ingest_generic", lambda *_args: None)
    calls = _count_recoveries(monkeypatch, _defect_then_repair)

    proof.test_recover_engine_projections_at_required_revisions(_Owner())

    assert calls == list(proof.REQUIRED_REVISIONS)


@pytest.mark.parametrize("outcome", [
    lambda n: _DEFECT,                                                    # the repair did not repair
    lambda n: {"result": "ROLLED_BACK", "sqlstate": "P2D08", "message": "x"} if n == 4 else _DEFECT,
    lambda n: {"result": "COMMITTED", "rows": _GOOD_ROWS},                # no defect before: not the RED schema
    lambda n: {"result": "COMMITTED", "rows": []} if n == 4 else _DEFECT,  # committed without the run
], ids=["unrepaired", "other-error", "no-defect-before", "wrong-rows"])
def test_required_revisions_fail_on_any_other_outcome(evidence, monkeypatch, outcome) -> None:
    monkeypatch.setattr(proof, "_ingest_generic", lambda *_args: None)
    calls = _count_recoveries(monkeypatch, outcome)

    with pytest.raises(AssertionError):
        proof.test_recover_engine_projections_at_required_revisions(_Owner())

    assert calls == list(proof.REQUIRED_REVISIONS)                        # every revision recorded first


def test_revision_mismatch_stops_before_recovery_at_required_revisions(evidence, monkeypatch) -> None:
    monkeypatch.setattr(proof, "_ingest_generic", lambda *_args: None)
    monkeypatch.setattr(proof, "_metadata", lambda _owner: {"revision": proof.CURRENT_HEAD})   # always reports 0032
    calls = _count_recoveries(monkeypatch, lambda _n: {"result": "COMMITTED", "rows": _GOOD_ROWS})

    with pytest.raises(AssertionError, match="revision"):
        proof.test_recover_engine_projections_at_required_revisions(_Owner())

    assert calls == []                                       # no recovery ran against the wrong schema


@pytest.mark.parametrize("case", ("idempotent", "completed_p1", "missing_projection", "non_p1", "closure",
                                  "parent_catalog"))
def test_current_head_cases_reject_a_wrong_revision_before_recovery(evidence, tmp_path, monkeypatch, case) -> None:
    monkeypatch.setattr(proof, "_metadata", lambda _owner: {"revision": "0019_p2_security_master"})
    deletes = []
    connection = _DeleteConnection()
    monkeypatch.setattr(connection, "execute", lambda *args: deletes.append(args) or SimpleNamespace(rowcount=1))
    monkeypatch.setattr(proof.psycopg, "connect", lambda *_args, **_kwargs: connection)
    monkeypatch.setattr(proof, "_ingest_generic", lambda *_args, **_kwargs: None)
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
        "non_p1": lambda: proof.test_recover_missing_non_p1_completion_projection_is_not_fenced(_Owner(), tmp_path),
        "closure": lambda: proof.test_repair_revision_accepts_c_new_and_rejects_the_prior_closure(_Owner(), tmp_path),
        "parent_catalog": lambda: proof.test_repair_refuses_a_drifted_parent_catalog_and_changes_nothing(_Owner()),
    }[case]

    with pytest.raises(AssertionError, match="revision"):
        run()

    assert calls == [] and recovered == []
    if case in ("missing_projection", "non_p1", "parent_catalog"):   # no synthetic mutation on an unverified schema
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


def test_diagnostics_load_plpgsql_before_reading_its_setting() -> None:
    """SV01 issue 1: a fresh session has no `plpgsql.variable_conflict` until plpgsql is loaded (UndefinedObject)."""
    executed: list[str] = []
    values = {"pg_get_functiondef": "CREATE FUNCTION stub()", "alembic_version": "0032_p3_enqueue_freshness",
              "server_version": "16.15", "variable_conflict": "error"}

    def execute(query, *_args):
        executed.append(query)
        if "variable_conflict" in query and not any(item.lstrip().upper().startswith("DO ") for item in executed):
            raise psycopg.errors.UndefinedObject('unrecognized configuration parameter "plpgsql.variable_conflict"')
        catalog = next((row for key, row in _CATALOG_ROWS.items() if key in query), None)
        value = next((value for key, value in values.items() if key in query), None)
        return SimpleNamespace(fetchone=lambda: catalog or (value,))

    class Connection:
        def cursor(self, *, row_factory):
            return SimpleNamespace(execute=execute)

    assert proof._diagnostics(Connection())["plpgsql_variable_conflict"] == "error"



def test_diagnostics_capture_the_catalog_inputs_a_guarded_rotation_binds() -> None:
    """R5: pg_get_functiondef alone is not the rotation guard; record prosrc and the function/constraint catalog."""
    import hashlib

    prosrc = "\nBEGIN\n  RETURN;\nEND;\n"
    rows = {
        "pg_get_functiondef": ("CREATE FUNCTION stub()",),
        "alembic_version": ("0032_p3_enqueue_freshness",),
        "server_version": ("16.15",),
        "variable_conflict": ("error",),
        "p.prosrc": (prosrc, "trading_owner", "{trading_owner=X/trading_owner}", "{search_path=pg_catalog}",
                     True, "v", "u", "", "TABLE(engine_run_id uuid)"),
        "pg_constraint": ("engine_run_projections_pkey", "PRIMARY KEY (engine_run_id)"),
    }

    def execute(query, *_args):
        row = next((value for key, value in rows.items() if key in query), (None,))
        return SimpleNamespace(fetchone=lambda: row)

    class Connection:
        def cursor(self, *, row_factory):
            return SimpleNamespace(execute=execute)

    result = proof._diagnostics(Connection())
    assert result["function_prosrc_sha256"] == hashlib.sha256(prosrc.encode()).hexdigest()
    assert result["function_catalog"] == {
        "owner": "trading_owner", "acl": "{trading_owner=X/trading_owner}", "config": "{search_path=pg_catalog}",
        "security_definer": True, "volatility": "v", "parallel": "u", "identity_arguments": "",
        "result": "TABLE(engine_run_id uuid)",
    }
    assert result["projection_primary_key"] == {"name": "engine_run_projections_pkey",
                                                "definition": "PRIMARY KEY (engine_run_id)"}



# i03: the P3 role bootstrap must run at 0019, after 0007's catalog digest, and before any upgrade past 0019.
def _record_order(monkeypatch) -> list[str]:
    order: list[str] = []

    def upgrade(owner, revision):
        order.append(f"upgrade:{revision}")
        _observe(owner, revision)

    monkeypatch.setattr(proof, "_upgrade_to_revision", upgrade)
    monkeypatch.setattr(proof, "provision_p3_roles", lambda _owner: order.append("provision"), raising=False)
    return order


def test_current_head_cases_provision_p3_roles_at_0019_before_the_head(evidence, monkeypatch) -> None:
    order = _record_order(monkeypatch)
    monkeypatch.setattr(proof, "_ingest_generic", lambda *_args: None)
    monkeypatch.setattr(proof, "_table_rows", lambda _owner: {})
    monkeypatch.setattr(proof, "_owner_ledger", lambda _owner: (_Pool(), _Ledger(recover_error=_ambiguous())))

    with pytest.raises(EngineEventConflictError):
        proof.test_recover_engine_projections_is_idempotent_after_reconnect(_Owner())

    assert order == ["upgrade:0019_p2_security_master", "provision", "upgrade:0033_p3_core_closure_repair"]


def test_revision_case_provisions_once_between_0019_and_0032_and_not_again_for_0033(evidence, monkeypatch) -> None:
    order = _record_order(monkeypatch)
    monkeypatch.setattr(proof, "_ingest_generic", lambda *_args: None)
    _count_recoveries(monkeypatch, _defect_then_repair)

    proof.test_recover_engine_projections_at_required_revisions(_Owner())

    assert order == ["upgrade:0018_p1_paper_closure_rotation", "upgrade:0019_p2_security_master",
                     "upgrade:0019_p2_security_master", "provision", "upgrade:0032_p3_enqueue_freshness",
                     "upgrade:0033_p3_core_closure_repair"]


def test_sequential_databases_each_get_their_own_p3_bootstrap(evidence, monkeypatch) -> None:
    """A4 REVISE: no process-wide memory of provisioned owners. The same object identity reused by a later
    database (as CPython may reuse id()) must still be provisioned; the harness's one-shot guard is the only guard."""
    order = _record_order(monkeypatch)
    reused = _Owner()                                          # database A, then database B with the same identity
    for _database in ("A", "B"):
        proof._upgrade(reused, proof.CURRENT_HEAD)

    assert order == ["upgrade:0019_p2_security_master", "provision", f"upgrade:{proof.CURRENT_HEAD}"] * 2


# 0033: the non-P1 control must commit (an over-broad fence fails it); the closure case must see the prior closure
# refused with the exact metadata rejection and nothing persisted.
def test_non_p1_control_fails_when_recovery_is_refused(evidence, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(proof, "_metadata", lambda _owner: {"revision": proof.CURRENT_HEAD})
    monkeypatch.setattr(proof, "_ingest_generic", lambda *_args, **_kwargs: None)
    _missing_projection_case(monkeypatch, recovery=_FENCE_ROLLBACK)

    with pytest.raises(AssertionError, match="ROLLED_BACK"):
        proof.test_recover_missing_non_p1_completion_projection_is_not_fenced(_Owner(), tmp_path)


@pytest.mark.parametrize("prior_error", [
    None,                                                                         # the prior closure was accepted
    psycopg.errors.RaiseException("P1 event stream is incomplete"),               # refused for another reason
], ids=["accepted", "other-reason"])
def test_closure_case_needs_the_exact_refusal_of_the_prior_closure(evidence, tmp_path, monkeypatch, prior_error) -> None:
    monkeypatch.setattr(proof, "_metadata", lambda _owner: {
        "revision": proof.CURRENT_HEAD, "ingest_closure_pins": {"c_prior": 0, "c_new": 2}})
    batches = {proof.C_PRIOR: SimpleNamespace(name="prior"), proof.C_NEW: _batch()}
    monkeypatch.setattr(proof, "_p1_batch", lambda _root, closure: batches[closure])
    monkeypatch.setattr(proof, "_table_rows", lambda _owner: {table: [] for table in proof.PROJECTION_TABLES})
    monkeypatch.setattr(proof, "_owner_ledger", lambda _owner: (_Pool(), _Ledger(projection=_projection())))

    def ingest_for_job(batch, *, claimed):
        if getattr(batch, "name", None) == "prior" and prior_error is not None:
            raise EngineEventConflictError("rejected") from prior_error
        return SimpleNamespace(batch_sha256=getattr(batch, "sha256", None))

    @contextmanager
    def worker(_owner):
        yield SimpleNamespace(engine_event_ingestor=lambda: SimpleNamespace(ingest_for_job=ingest_for_job)), None

    monkeypatch.setattr(proof, "_p1_worker", worker)

    with pytest.raises(AssertionError):
        proof.test_repair_revision_accepts_c_new_and_rejects_the_prior_closure(_Owner(), tmp_path)


def test_parent_catalog_control_fails_when_the_repair_applies_despite_drift(evidence, monkeypatch) -> None:
    """Codex P2: the drift control passes only on the exact P2D08 refusal with nothing changed."""
    parent = {"revision": proof.PRE_REPAIR_REVISIONS[-1], "session_catalog_sha256": proof.PARENT_SESSION_CATALOG_SHA256,
              "function_definition_sha256": proof.RECOVERY_PRIOR_DEFINITION_SHA256,
              "ingest_closure_pins": {"c_prior": 2, "c_new": 0}}
    drifted = {**parent, "session_catalog_sha256": "d" * 64}
    snapshots = iter((parent, drifted, drifted))
    monkeypatch.setattr(proof, "_metadata", lambda _owner: next(snapshots))
    monkeypatch.setattr(proof.psycopg, "connect", lambda *_args, **_kwargs: _DeleteConnection())

    with pytest.raises(AssertionError, match="UPGRADED"):                 # the stub upgrade accepted the drift
        proof.test_repair_refuses_a_drifted_parent_catalog_and_changes_nothing(_Owner())
    assert '"UPGRADED"' in _recorded(evidence)
