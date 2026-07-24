from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest

from trading_control.db import DatabaseSettings
from trading_control.writer import (
    ApplyPlan,
    ApplyRecord,
    ApplyReport,
    ApplyReportAsset,
    ApplyRejected,
    PlannedQuarantine,
    apply_plan,
)
from tests.jobs._postgres import disposable_database, upgrade_to_head


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
        operation_id="control-api-fixture-importer-v1"
    ) as owner:
        _TEST_DATABASE_NAME = owner.database
        try:
            upgrade_to_head(owner)
            yield
        finally:
            _TEST_DATABASE_NAME = None


def record(index: int, *, fingerprint: str | None = None) -> ApplyRecord:
    return ApplyRecord(
        record_id=f"decision-{index}",
        asset_id="crypto:spot:BTC/USDT",
        symbol="BTC",
        action="STRONG_SELL" if index == 1 else "BUY",
        confidence=0.5,
        as_of=datetime(2026, 6, 25, tzinfo=UTC),
        source_path="memory/decisions.jsonl",
        source_hash="a" * 64,
        source_record_index=index,
        payload_hash=f"{index:064x}"[-64:],
        canonical_fingerprint=fingerprint or f"{index + 1000:064x}"[-64:],
        audit_codes=("NORMALIZED_ACTION_ALIAS",) if index == 1 else (),
    )


def quarantine(index: int, legacy_action: str, code: str = "INVALID_ENUM") -> PlannedQuarantine:
    return PlannedQuarantine(
        source_path="memory/decisions.jsonl",
        source_hash="a" * 64,
        source_record_index=index,
        payload_hash=f"{index + 9000:064x}"[-64:],
        legacy_value=legacy_action,
        error_code=code,
        sanitized_message="legacy decision action is not canonical",
    )


def plan(*, total: int = 503, inventory_hash: str = "b" * 64) -> ApplyPlan:
    return ApplyPlan(
        source_root="/synthetic/fixture",
        source_inventory_hash=inventory_hash,
        normalization_version="phase3-v1",
        schema_revision="0002_quarantine_lineage",
        code_commit="fixture",
        records=tuple(record(index) for index in range(1, total + 1)),
        quarantines=(
            quarantine(total + 1, "WATCH"),
            quarantine(total + 2, "WATCH FOR EXIT"),
            quarantine(total + 3, "secret-token=must-not-persist", "INVALID_CONFIDENCE"),
        ),
        reports=tuple(
            ApplyReport(
                report_id=f"report-{index}",
                as_of=datetime(2026, 6, 20 + index, tzinfo=UTC),
                source_path=f"reports/report_{index}.json",
                source_hash=str(index) * 64,
                canonical_fingerprint=str(index + 2) * 64,
                assets=(
                    ApplyReportAsset(
                        snapshot_id=f"snapshot-{index}",
                        asset_id="crypto:spot:BTC/USDT",
                        symbol="BTC",
                        price=100.0 + index,
                        action="BUY",
                        confidence=0.5,
                    ),
                ),
            )
            for index in (1, 2)
        ),
    )


def counts(connection: psycopg.Connection) -> dict[str, int]:
    result = {
        table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in ("decisions", "migration_errors", "audit_events")
    }
    result["migration_source_chunks"] = connection.execute(
        "SELECT count(*) FROM migration_source_chunks WHERE domain='decisions'"
    ).fetchone()[0]
    return result


def test_explicit_apply_gate_and_idempotent_second_run() -> None:
    settings = role_settings("postgres-migrator.env")
    with pytest.raises(ApplyRejected):
        apply_plan(plan(), settings, apply=False)
    first = apply_plan(plan(), settings, apply=True)
    second = apply_plan(plan(), settings, apply=True)
    assert (first.inserted, first.skipped, first.updated) == (503, 0, 0)
    assert (second.inserted, second.skipped, second.updated) == (0, 503, 0)
    with psycopg.connect(settings.conninfo()) as connection:
        assert counts(connection) == {
            "decisions": 503, "migration_errors": 6,
            "audit_events": 2, "migration_source_chunks": 4,
        }
        assert connection.execute("SELECT count(*) FROM market_reports").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM market_asset_snapshots").fetchone()[0] == 2
        run_counters = {
            tuple(row)
            for row in connection.execute(
                "SELECT records_seen,records_inserted,records_skipped,records_invalid "
                "FROM migration_runs"
            )
        }
        assert run_counters == {(508, 505, 0, 3), (508, 0, 505, 3)}
        assert connection.execute(
            "SELECT count(*) FROM migration_source_files"
        ).fetchone()[0] == 6
        rows = connection.execute(
            "SELECT source_path, source_hash, source_record_index, payload_hash, "
            "legacy_value, error_code, error_message_sanitized, normalization_version "
            "FROM migration_errors "
            "ORDER BY source_record_index"
        ).fetchall()
        assert {row[4] for row in rows} >= {"WATCH", "WATCH FOR EXIT"}
        assert all(row[0] == "memory/decisions.jsonl" for row in rows)
        assert all(row[1] == "a" * 64 for row in rows)
        assert all(row[2] is not None and len(row[3]) == 64 for row in rows)
        assert all(row[5] in {"INVALID_ENUM", "INVALID_CONFIDENCE"} for row in rows)
        assert all(row[6] == "legacy record rejected by strict normalization" for row in rows)
        assert all(row[7] == "phase3-v1" for row in rows)
        assert "secret-token" not in repr(rows)


def test_failed_chunk_rolls_back_then_resume_retries_without_duplicates() -> None:
    settings = role_settings("postgres-migrator.env")
    with pytest.raises(RuntimeError):
        apply_plan(plan(), settings, apply=True, fail_chunk_first_index=501)
    with psycopg.connect(settings.conninfo()) as connection:
        run_id = connection.execute("SELECT run_id FROM migration_runs").fetchone()[0]
        assert connection.execute("SELECT count(*) FROM decisions").fetchone()[0] == 500
        assert connection.execute("SELECT count(*) FROM migration_errors").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM audit_events").fetchone()[0] == 1
        assert connection.execute("SELECT status FROM migration_runs").fetchone()[0] == "FAILED"
        assert connection.execute(
            "SELECT status FROM migration_source_chunks WHERE first_record_index=501"
        ).fetchone()[0] == "FAILED"
    resumed = apply_plan(plan(), settings, apply=True, resume_run_id=run_id)
    assert resumed.inserted == 3
    with psycopg.connect(settings.conninfo()) as connection:
        assert connection.execute("SELECT count(*) FROM decisions").fetchone()[0] == 503
        assert connection.execute(
            "SELECT count(*) FROM migration_source_chunks "
            "WHERE status='COMMITTED' AND domain='decisions'"
        ).fetchone()[0] == 2


def test_resume_rejects_changed_inventory_or_normalization() -> None:
    settings = role_settings("postgres-migrator.env")
    with pytest.raises(RuntimeError):
        apply_plan(plan(), settings, apply=True, fail_chunk_first_index=501)
    with psycopg.connect(settings.conninfo()) as connection:
        run_id = connection.execute("SELECT run_id FROM migration_runs").fetchone()[0]
    with pytest.raises(ApplyRejected):
        apply_plan(plan(inventory_hash="c" * 64), settings, apply=True, resume_run_id=run_id)
    with pytest.raises(ApplyRejected):
        apply_plan(replace(plan(), normalization_version="phase3-v2"), settings, apply=True, resume_run_id=run_id)
    with pytest.raises(ApplyRejected):
        apply_plan(replace(plan(), schema_revision="different-head"), settings, apply=True, resume_run_id=run_id)


def test_mid_chunk_failure_rolls_back_every_row_and_checkpoint() -> None:
    settings = role_settings("postgres-migrator.env")
    with pytest.raises(RuntimeError):
        apply_plan(
            replace(plan(total=10), reports=()), settings, apply=True,
            fail_after_records_in_chunk=5,
        )
    with psycopg.connect(settings.conninfo()) as connection:
        assert connection.execute("SELECT count(*) FROM decisions").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM migration_errors").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM audit_events").fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM migration_source_chunks WHERE status='COMMITTED'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM migration_source_chunks WHERE status='FAILED'"
        ).fetchone()[0] == 1


def test_collision_is_quarantined_without_overwrite() -> None:
    settings = role_settings("postgres-migrator.env")
    apply_plan(plan(total=1), settings, apply=True)
    changed = replace(plan(total=1), records=(record(1, fingerprint="f" * 64),))
    result = apply_plan(changed, settings, apply=True)
    assert result.inserted == 0 and result.updated == 0 and result.invalid >= 1
    with psycopg.connect(settings.conninfo()) as connection:
        stored = connection.execute(
            "SELECT source_record_fingerprint FROM decisions WHERE decision_id='decision-1'"
        ).fetchone()[0]
        assert stored != "f" * 64
        assert connection.execute(
            "SELECT count(*) FROM migration_errors WHERE error_code='DUPLICATE_SOURCE_RECORD'"
        ).fetchone()[0] == 1


def test_changed_source_content_requires_new_run_and_preserves_old_provenance() -> None:
    settings = role_settings("postgres-migrator.env")
    original = replace(plan(total=1), reports=())
    first = apply_plan(original, settings, apply=True)
    changed_record = replace(
        record(1), record_id="decision-new-source", source_hash="d" * 64,
        canonical_fingerprint="e" * 64,
    )
    changed = replace(
        original, source_inventory_hash="f" * 64, records=(changed_record,),
        quarantines=(),
    )
    second = apply_plan(changed, settings, apply=True)
    assert first.run_id != second.run_id
    with psycopg.connect(settings.conninfo()) as connection:
        assert connection.execute("SELECT count(*) FROM decisions").fetchone()[0] == 2
        hashes = {
            row[0] for row in connection.execute("SELECT source_hash FROM decisions")
        }
        assert hashes == {"a" * 64, "d" * 64}
