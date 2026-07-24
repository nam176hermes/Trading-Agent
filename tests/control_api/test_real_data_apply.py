from __future__ import annotations

from pathlib import Path

import psycopg
import pytest

from trading_control.db import DatabaseSettings
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


@pytest.fixture(autouse=True)
def clean_test_database() -> None:
    global _TEST_DATABASE_NAME
    with disposable_database(
        operation_id="control-api-real-data-apply-v1"
    ) as owner:
        _TEST_DATABASE_NAME = owner.database
        try:
            upgrade_to_head(owner)
            yield
        finally:
            _TEST_DATABASE_NAME = None


def test_reviewed_real_plan_first_and_second_apply_are_idempotent() -> None:
    settings = role_settings("postgres-migrator.env")
    plan = build_real_plan(REAL_ROOT)

    first = apply_real_plan(plan, settings, apply=True, code_commit="test")
    second = apply_real_plan(plan, settings, apply=True, code_commit="test")

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
