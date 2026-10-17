from __future__ import annotations

from pathlib import Path

import psycopg
import pytest

from trading_control.db import DatabaseSettings
from trading_control.real_import import build_real_plan
from tests.jobs._postgres import apply_disposable_real_plan, disposable_database, disposable_role_settings, upgrade_to_head


REAL_ROOT = Path("/home/thenam176/.hermes/crypto-research")
_TEST_DATABASE: DatabaseSettings | None = None


def role_settings(filename: str) -> DatabaseSettings:
    if _TEST_DATABASE is None:
        raise RuntimeError("Control API test database fixture is not active")
    if filename != "postgres-migrator.env":
        raise ValueError("Control API test role is not allowed")
    return disposable_role_settings(_TEST_DATABASE, "trading_migrator")


@pytest.fixture(autouse=True)
def clean_test_database() -> None:
    global _TEST_DATABASE
    with disposable_database(
        operation_id="control-api-real-data-apply-v1"
    ) as owner:
        _TEST_DATABASE = owner
        try:
            upgrade_to_head(owner)
            yield owner
        finally:
            _TEST_DATABASE = None


def test_reviewed_real_plan_first_and_second_apply_are_idempotent(clean_test_database) -> None:
    settings = role_settings("postgres-migrator.env")
    plan = build_real_plan(REAL_ROOT)

    first = apply_disposable_real_plan(plan, clean_test_database, apply=True, code_commit="test")
    second = apply_disposable_real_plan(plan, clean_test_database, apply=True, code_commit="test")

    assert first.inserted == 43055
    assert first.skipped == 0
    assert first.updated == 0
    assert first.invalid == 222
    assert second.inserted == 0
    assert second.skipped == 43055
    assert second.updated == 0
    assert second.invalid == 222
    with psycopg.connect(settings.conninfo()) as connection:
        counts = {
            table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in (
                "assets", "market_reports", "market_asset_snapshots", "decisions",
                "signals", "capability_evidence", "cost_summaries", "cost_sessions",
                "migration_errors", "audit_events",
            )
        }
        assert counts == {
            **plan.domain_counts,
            "migration_errors": 222,
            "audit_events": 2349,
        }
        assert connection.execute(
            "SELECT count(*) FROM migration_source_files"
        ).fetchone()[0] == 4590
        assert connection.execute(
            "SELECT count(*) FROM migration_source_chunks WHERE status='COMMITTED'"
        ).fetchone()[0] == 4656
        assert connection.execute(
            "SELECT count(*) FROM migration_source_chunks WHERE status<>'COMMITTED'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM migration_errors WHERE legacy_value='WATCH'"
        ).fetchone()[0] == 122
        assert connection.execute(
            "SELECT count(*) FROM migration_errors WHERE legacy_value='WATCH FOR EXIT'"
        ).fetchone()[0] == 14
