"""Offline contract for 0033: the guarded recovery repair and the guarded P1 closure rotation.

The prior hashes are the ones observed on the real PostgreSQL 16 catalog (SV01 issue 4) and by the 0018 rotation;
the rotated hashes are derived here from those bytes. Nothing here touches a database.
"""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace

from tests.jobs.test_p1_paper_closure_rotation_migration import (
    NEW_CLOSURE as PRIOR_CLOSURE,
    OLD_CLOSURE as ORIGINAL_0018_CLOSURE,
    ROTATED_DEFINITION_SHA256 as INGEST_PRIOR_DEFINITION_SHA256,
    ROTATED_SOURCE_SHA256 as INGEST_PRIOR_SOURCE_SHA256,
    _current_prosrc as _ingest_0017_prosrc,
    _postgresql_16_definition as _ingest_definition,
)

ROOT = Path(__file__).parents[2]
MIGRATION = ROOT / "alembic/versions/0033_p3_core_closure_repair.py"
LEDGER_MIGRATION = ROOT / "alembic/versions/0010_engine_event_ledger.py"
C_NEW = "5863b3c96c9bf31c8c6239cfedb9617b2f7418fff3cd7c697028b15392bf2155"
RECOVERY_PRIOR_SOURCE_SHA256 = "15f38af183293e48f32864ffdf5a55d2249bc2a566f527a49da8aa1f5c3ac982"      # SV01 i04
RECOVERY_PRIOR_DEFINITION_SHA256 = "4841af0563ed378dba8fcbd99448829ee4a6e3787cbfe7804df02a594ec94373"  # SV01 i04
RECOVERY_RESULT = ("TABLE(engine_run_id uuid, event_count bigint, event_type_counts jsonb, last_sequence bigint, "
                   "last_digest character)")


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("p3_core_closure_repair_0033", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _recovery_prosrc() -> str:
    delimiter = "$recover_engine_run_projections$"
    return LEDGER_MIGRATION.read_text(encoding="utf-8").split("AS " + delimiter, 1)[1].split(delimiter + ";", 1)[0]


def _recovery_definition(prosrc: str) -> str:
    """pg_get_functiondef on PostgreSQL 16, as observed for this function in SV01 issue 4."""
    return (
        "CREATE OR REPLACE FUNCTION public.recover_engine_run_projections()\n"
        f" RETURNS {RECOVERY_RESULT}\n LANGUAGE plpgsql\n SECURITY DEFINER\n"
        " SET search_path TO 'pg_catalog'\nAS $function$" + prosrc + "$function$\n"
    )


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _repaired_recovery(migration: ModuleType, prosrc: str) -> str:
    assert prosrc.count(migration.RECOVERY_OLD_CLAUSE) == 1 and prosrc.count(migration.RECOVERY_ANCHOR) == 1
    assert migration.RECOVERY_NEW_CLAUSE not in prosrc and migration.RECOVERY_FENCE_REASON not in prosrc
    return prosrc.replace(migration.RECOVERY_OLD_CLAUSE, migration.RECOVERY_NEW_CLAUSE).replace(
        migration.RECOVERY_ANCHOR, migration.RECOVERY_FENCE + migration.RECOVERY_ANCHOR)


def test_recovery_repair_binds_the_observed_prior_and_its_exact_result() -> None:
    migration = _load_migration()
    prior = _recovery_prosrc()
    assert _sha(prior) == RECOVERY_PRIOR_SOURCE_SHA256
    assert _sha(_recovery_definition(prior)) == RECOVERY_PRIOR_DEFINITION_SHA256
    repaired = _repaired_recovery(migration, prior)

    assert (migration.RECOVERY_PRIOR_SOURCE_SHA256, migration.RECOVERY_PRIOR_DEFINITION_SHA256) == (
        RECOVERY_PRIOR_SOURCE_SHA256, RECOVERY_PRIOR_DEFINITION_SHA256)
    assert migration.RECOVERY_REPAIRED_SOURCE_SHA256 == _sha(repaired)
    assert migration.RECOVERY_REPAIRED_DEFINITION_SHA256 == _sha(_recovery_definition(repaired))
    assert migration.RECOVERY_RESULT == RECOVERY_RESULT
    assert "ON CONFLICT (engine_run_id)" not in repaired
    assert repaired.count("ON CONFLICT ON CONSTRAINT engine_run_projections_pkey") == 1


def test_recovery_fence_runs_under_the_existing_lock_before_every_write() -> None:
    repaired = _repaired_recovery(_load_migration(), _recovery_prosrc())
    lock = repaired.index("LOCK TABLE public.engine_events IN SHARE MODE")
    sequence_check = repaired.index("durable engine-event sequence is not recoverable")
    fence = repaired.index("P1_COMPLETED_PROJECTION_MISSING")
    writes = [repaired.index(statement) for statement in ("INSERT INTO", "DELETE FROM")]

    assert lock < sequence_check < fence < min(writes)


def test_recovery_fence_is_scoped_to_durable_p1_completion_of_the_same_receipt_and_run() -> None:
    fence = _load_migration().RECOVERY_FENCE
    for clause in (
        "receipt.batch_sha256 = completed.batch_sha256",
        "receipt.engine_run_id = completed.engine_run_id",
        "receipt.last_sequence = completed.stream_sequence",
        "completed.event_type = 'RunCompleted'",
        '@.name == "schema_version" && @.value == "nautilus-p1-event-stream-v1"',
        '@.name == "semantic_digest"',
        "projection.engine_run_id = completed.engine_run_id",
        "USING ERRCODE = 'P2D01'",
        "DETAIL = 'reason=P1_COMPLETED_PROJECTION_MISSING'",
    ):
        assert fence.count(clause) == 1, clause
    # Every column reference is qualified: the RETURNS TABLE names are plpgsql variables (variable_conflict=error).
    for bare in (" engine_run_id", " event_count", " last_sequence", " last_digest", " event_type_counts"):
        assert bare not in fence, bare


def test_closure_rotation_binds_the_0018_result_and_rotates_both_pins_to_c_new() -> None:
    migration = _load_migration()
    prior = _ingest_0017_prosrc().replace(ORIGINAL_0018_CLOSURE, PRIOR_CLOSURE)
    rotated = prior.replace(PRIOR_CLOSURE, C_NEW)
    assert prior.count(PRIOR_CLOSURE) == 2 and C_NEW not in prior
    assert _sha(prior) == INGEST_PRIOR_SOURCE_SHA256
    assert _sha(_ingest_definition(prior)) == INGEST_PRIOR_DEFINITION_SHA256

    assert f" RETURNS {migration.INGEST_RESULT}\n" in _ingest_definition(prior)     # the catalog result it guards
    assert (migration.CLOSURE_OLD, migration.CLOSURE_NEW) == (PRIOR_CLOSURE, C_NEW)
    assert (migration.INGEST_PRIOR_SOURCE_SHA256, migration.INGEST_PRIOR_DEFINITION_SHA256) == (
        INGEST_PRIOR_SOURCE_SHA256, INGEST_PRIOR_DEFINITION_SHA256)
    assert migration.INGEST_ROTATED_SOURCE_SHA256 == _sha(rotated)
    assert migration.INGEST_ROTATED_DEFINITION_SHA256 == _sha(_ingest_definition(rotated))


def test_repair_is_one_guarded_forward_only_driver_statement() -> None:
    migration = _load_migration()
    observed: list[tuple[str, dict[str, object]]] = []

    class Bind:
        def exec_driver_sql(self, statement: str, *, execution_options: dict[str, object]) -> None:
            observed.append((statement, execution_options))

    migration.op = SimpleNamespace(get_bind=lambda: Bind())
    migration.upgrade()
    assert len(observed) == 1                                 # one DO block: any guard failure rolls back both edits
    statement, options = observed[0]
    assert options == {"no_parameters": True}
    assert (migration.revision, migration.down_revision) == (
        "0033_p3_core_closure_repair", "0032_p3_enqueue_freshness")
    assert statement.lstrip().startswith("DO $p3_core_closure_repair$")
    for guard in (
        "current_user", "session_user", "'0032_p3_enqueue_freshness'",       # exact parent and owner session
        "'engine_run_projections_pkey'", "'PRIMARY KEY (engine_run_id)'",    # the constraint the repair names
        "pg_catalog.pg_get_function_identity_arguments", "pg_catalog.pg_get_function_result",
    ):
        assert guard in statement, guard
    for digest in (
        migration.RECOVERY_PRIOR_SOURCE_SHA256, migration.RECOVERY_PRIOR_DEFINITION_SHA256,
        migration.RECOVERY_REPAIRED_SOURCE_SHA256, migration.RECOVERY_REPAIRED_DEFINITION_SHA256,
        migration.INGEST_PRIOR_SOURCE_SHA256, migration.INGEST_PRIOR_DEFINITION_SHA256,
        migration.INGEST_ROTATED_SOURCE_SHA256, migration.INGEST_ROTATED_DEFINITION_SHA256,
    ):
        assert statement.count(digest) == 1, digest
    # Each function is looked up with owner, SECURITY DEFINER, volatility, parallel, config and a sole owner
    # EXECUTE grant, before and after its own edit.
    assert statement.count("pg_catalog.aclexplode") == 4
    assert statement.count("EXECUTE v_") == 2
    assert "plpgsql.variable_conflict" not in statement

    try:
        migration.downgrade()
    except RuntimeError as exc:
        assert "forward-only" in str(exc)
    else:
        raise AssertionError("0033 downgrade was accepted")


PARENT_SESSION_CATALOG_SHA256 = "f5092b5f50e6f81aa504e8515d595e8a1e2cf1848c3e4375c8d9990dc2c83d9a"   # observed at 0032


def test_parent_session_catalog_is_pinned_by_literal_before_either_rewrite() -> None:
    """Codex P2: 0033 must not carry pre-existing P3 catalog drift into the catalog Step 2 will pin."""
    from services.job_store.p3_catalog import SESSION_CATALOG_SQL

    migration = _load_migration()
    source = MIGRATION.read_text(encoding="utf-8")
    assert f'PARENT_SESSION_CATALOG_SHA256 = "{PARENT_SESSION_CATALOG_SHA256}"' in source
    for moving_head_pin in ("SESSION_CATALOG_SHA256 as", "SESSION_CATALOGS", "SESSION_REVISION"):
        assert moving_head_pin not in source, moving_head_pin
    statement = migration.REPAIR_SQL
    catalog = statement.index(SESSION_CATALOG_SQL)
    check = statement.index(f"IS DISTINCT FROM '{PARENT_SESSION_CATALOG_SHA256}'")
    assert statement.count(SESSION_CATALOG_SQL) == 1 and statement.count(PARENT_SESSION_CATALOG_SHA256) == 1
    assert statement.index("PERFORM pg_catalog.set_config('search_path', 'pg_catalog', true)") < catalog < check
    assert check < statement.index("EXECUTE v_edited")        # before both rewrites
