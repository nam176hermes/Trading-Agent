from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import psycopg
import pytest

from trading_control.db import DatabaseSettings
from trading_control.phase3b_backfill import build_phase3b_backfill_plan
from trading_control.phase3b_sources import make_asset_lineage_evidence
from trading_control.phase3b_writer import (
    Phase3BApplyError,
    apply_phase3b_plan,
    inspect_phase3b_dry_run,
    phase3b_run_id,
)
from trading_control.real_import import apply_real_plan, build_real_plan
from tests.jobs._postgres import disposable_database, upgrade_to_head


REAL_ROOT = Path("/home/thenam176/.hermes/crypto-research")
_TEST_DATABASE_NAME: str | None = None


def role_settings(filename: str) -> DatabaseSettings:
    if _TEST_DATABASE_NAME is None:
        raise RuntimeError("Control API test database fixture is not active")
    values = dict(
        line.split("=", 1)
        for line in (Path.home() / ".config" / "trading-agent" / filename)
        .read_text(encoding="utf-8")
        .splitlines()
        if line
    )
    values["TRADING_DATABASE_NAME"] = _TEST_DATABASE_NAME
    return DatabaseSettings.from_env(values)


@pytest.fixture(scope="module")
def phase3b_database() -> tuple[DatabaseSettings, object]:
    global _TEST_DATABASE_NAME
    with disposable_database(
        operation_id="control-api-phase3b-transactions-v1"
    ) as owner:
        _TEST_DATABASE_NAME = owner.database
        try:
            upgrade_to_head(owner)
            settings = role_settings("postgres-migrator.env")
            apply_real_plan(
                build_real_plan(REAL_ROOT), settings, apply=True, code_commit="test"
            )
            yield settings, build_phase3b_backfill_plan(REAL_ROOT)
        finally:
            _TEST_DATABASE_NAME = None


def test_real_phase3b_apply_and_second_apply_are_idempotent(
    phase3b_database: tuple[DatabaseSettings, object],
) -> None:
    settings, plan = phase3b_database

    with psycopg.connect(settings.conninfo()) as connection:
        before = {
            table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in (
                "decision_field_lineage", "cost_session_assets",
                "asset_source_lineage", "phase3b_backfill_runs",
            )
        }
    dry_run = inspect_phase3b_dry_run(plan, settings)
    with psycopg.connect(settings.conninfo()) as connection:
        after = {
            table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in before
        }
    assert before == after == {
        "decision_field_lineage": 0,
        "cost_session_assets": 0,
        "asset_source_lineage": 0,
        "phase3b_backfill_runs": 0,
    }
    assert dry_run["decision-price"] == {
        "total": 16517, "already_exact": 0, "backfillable_exact": 16517,
        "backfillable_derived": 0, "unknown": 0, "conflicts": 0,
    }
    assert dry_run["decision-snippet"] == {
        "total": 16517, "already_populated": 0, "backfillable": 16516,
        "unknown": 1, "conflicts": 0,
    }
    assert dry_run["cost-symbols"] == {
        "sessions": 20, "sessions_with_evidenced_symbols": 20,
        "sessions_with_no_evidence": 0, "unknown_assets": 0, "conflicts": 0,
    }
    assert dry_run["asset-lineage"] == {
        "assets": 17, "source_lineage_rows_planned": 41039,
        "distinct_source_files": 2209, "conflicts": 0,
    }

    first = apply_phase3b_plan(plan, settings, apply=True, code_commit="test")
    second = apply_phase3b_plan(plan, settings, apply=True, code_commit="test")

    assert first["decision-price"].updated == 16517
    assert first["decision-snippet"].updated == 16516
    assert first["decision-snippet"].unknown == 1
    assert first["cost-symbols"].updated == 20
    assert first["cost-symbols"].lineage_inserted == 200
    assert first["asset-lineage"].lineage_inserted == 41039
    assert all(item.updated == 0 and item.lineage_inserted == 0 for item in second.values())
    with psycopg.connect(settings.conninfo()) as connection:
        assert connection.execute("SELECT count(*) FROM decision_field_lineage").fetchone()[0] == 33034
        assert connection.execute("SELECT count(*) FROM cost_session_assets").fetchone()[0] == 200
        assert connection.execute("SELECT count(*) FROM asset_source_lineage").fetchone()[0] == 41039
        assert connection.execute("SELECT count(*) FROM phase3b_backfill_runs").fetchone()[0] == 4
        assert connection.execute(
            "SELECT count(*) FROM decisions WHERE price_at_decision IS NULL"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM decisions WHERE report_snippet IS NULL"
        ).fetchone()[0] == 1
        canonical = sum(
            connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in (
                "assets", "market_reports", "market_asset_snapshots", "decisions",
                "signals", "capability_evidence", "cost_summaries", "cost_sessions",
            )
        )
        assert canonical == 43055


def test_chunk_failure_rolls_back_and_resume_completes(
    phase3b_database: tuple[DatabaseSettings, object],
) -> None:
    settings, plan = phase3b_database
    valid = make_asset_lineage_evidence(
        asset_id=plan.asset_ids[0], symbol="AAPL", source_type="ASSET_REGISTRY",
        source_path="synthetic-registry", source_hash="f" * 64,
        source_record_index=1, source_field="ASSET_REGISTRY[AAPL]",
    )
    invalid = replace(
        make_asset_lineage_evidence(
            asset_id=plan.asset_ids[0], symbol="AAPL", source_type="ASSET_REGISTRY",
            source_path="synthetic-registry", source_hash="f" * 64,
            source_record_index=2, source_field="ASSET_REGISTRY[UNKNOWN]",
        ),
        asset_id="missing-asset",
    )
    failing = replace(
        plan, inventory_hash="e" * 64, asset_lineage=(valid, invalid)
    )
    run_id = phase3b_run_id("asset-lineage", failing.inventory_hash)

    with pytest.raises(Phase3BApplyError):
        apply_phase3b_plan(
            failing, settings, domains=("asset-lineage",), apply=True,
            code_commit="test", chunk_size=2,
        )

    with psycopg.connect(settings.conninfo()) as connection:
        assert connection.execute(
            "SELECT count(*) FROM asset_source_lineage WHERE source_hash=%s",
            ("f" * 64,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT status FROM phase3b_backfill_runs WHERE backfill_run_id=%s",
            (run_id,),
        ).fetchone()[0] == "FAILED"

    corrected = replace(failing, asset_lineage=(valid,))
    result = apply_phase3b_plan(
        corrected, settings, domains=("asset-lineage",), apply=True,
        code_commit="test", resume_run_id=run_id,
    )
    assert result["asset-lineage"].lineage_inserted == 1
    with psycopg.connect(settings.conninfo()) as connection:
        assert connection.execute(
            "SELECT status FROM phase3b_backfill_runs WHERE backfill_run_id=%s",
            (run_id,),
        ).fetchone()[0] == "COMPLETED"
