import json
import shutil
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from control_api.contracts import (
    CapabilityStatus,
    CostEvidenceQuality,
    ExecutionCapability,
    ExecutionMode,
    FreshnessStatus,
    KillSwitchState,
)
from control_api.repositories.capabilities import LegacyCapabilityRepository
from control_api.repositories.costs import LegacyCostRepository
from control_api.repositories.status import LegacyOperationalStatusRepository


def create_database(path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        "CREATE TABLE orders (id INTEGER PRIMARY KEY);"
        "CREATE TABLE trades (id INTEGER PRIMARY KEY);"
    )
    connection.executemany("INSERT INTO orders DEFAULT VALUES", [(), ()])
    connection.commit()
    connection.close()


def test_status_separates_runtime_liveness_from_research_freshness(tmp_path) -> None:
    (tmp_path / "memory").mkdir()
    create_database(tmp_path / "memory" / "trading.db")
    (tmp_path / ".mode").write_text("live\n", encoding="utf-8")
    (tmp_path / "live_prices.json").write_text(
        json.dumps({"updated_at": "2026-07-11T12:00:00Z"}), encoding="utf-8"
    )

    status = LegacyOperationalStatusRepository(
        tmp_path,
        stale_after_seconds=1800,
        env={"LIVE_EXECUTION_ENABLED": "false", "LIVE_TRADING_APPROVED": "false"},
        clock=lambda: datetime(2026, 7, 11, 12, 1, tzinfo=UTC),
        latest_research_at=lambda: datetime(2026, 6, 25, tzinfo=UTC),
    ).get()

    assert status.backend_service_liveness == "ALIVE"
    assert status.research_data_freshness.status is FreshnessStatus.STALE
    assert status.requested_mode is ExecutionMode.LIVE
    assert status.effective_mode is ExecutionMode.PAPER
    assert status.execution_capability is ExecutionCapability.LIVE_BLOCKED
    assert status.kill_switch_state is KillSwitchState.INACTIVE
    assert (status.orders_count, status.trades_count) == (2, 0)


def test_invalid_kill_switch_is_unknown(tmp_path) -> None:
    (tmp_path / "memory").mkdir()
    create_database(tmp_path / "memory" / "trading.db")
    (tmp_path / ".mode").write_text("paper", encoding="utf-8")
    (tmp_path / ".kill_switch").write_text("invalid", encoding="utf-8")

    status = LegacyOperationalStatusRepository(tmp_path).get()

    assert status.kill_switch_state is KillSwitchState.UNKNOWN


def test_control_api_and_worker_share_canonical_kill_switch_semantics(tmp_path, monkeypatch) -> None:
    from services.job_worker.safety import KillSwitchState as WorkerState, SafetyProvider, validate_data_root

    root = Path(tempfile.mkdtemp(prefix="task7-kill-parity-", dir="/home/thenam176/.cache"))
    try:
        (root / "memory").mkdir()
        create_database(root / "memory" / "trading.db")
        (root / ".mode").write_text("paper", encoding="utf-8")
        (root / ".mode").chmod(0o600)
        root.chmod(0o700)
        monkeypatch.setattr("services.job_worker.safety.APPROVED_DATA_ROOT", root)
        source = {"LIVE_EXECUTION_ENABLED": "false", "LIVE_TRADING_APPROVED": "false"}
        worker = SafetyProvider(validate_data_root(), source=source)

        for content, expected in ((None, "INACTIVE"), ("2026-07-12T00:00:00Z: drill", "ACTIVE"), ("INACTIVE", "UNKNOWN")):
            sentinel = root / ".kill_switch"
            sentinel.unlink(missing_ok=True)
            if content is not None:
                sentinel.write_text(content, encoding="utf-8")
                sentinel.chmod(0o600)
            control = LegacyOperationalStatusRepository(root, env=source).get().kill_switch_state
            assert control.value == expected
            assert worker.snapshot().kill_switch_state is WorkerState(expected)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_fresh_research_maps_to_healthy_business_state(tmp_path) -> None:
    (tmp_path / "memory").mkdir()
    create_database(tmp_path / "memory" / "trading.db")
    (tmp_path / ".mode").write_text("paper", encoding="utf-8")
    now = datetime(2026, 7, 11, 12, tzinfo=UTC)

    status = LegacyOperationalStatusRepository(
        tmp_path,
        clock=lambda: now,
        latest_research_at=lambda: now,
    ).get()

    assert status.research_pipeline_health == "HEALTHY"


def test_capabilities_default_to_unknown_without_current_evidence(tmp_path) -> None:
    capabilities = LegacyCapabilityRepository(tmp_path).list()

    assert len(capabilities) == 9
    assert all(item.status is CapabilityStatus.UNKNOWN for item in capabilities)


def test_costs_are_unknown_when_token_accounting_is_missing(tmp_path) -> None:
    (tmp_path / ".dexter" / "scratchpad").mkdir(parents=True)
    (tmp_path / ".dexter" / "scratchpad" / "session.jsonl").write_text(
        json.dumps({"type": "tool_result"}) + "\n", encoding="utf-8"
    )

    summary = LegacyCostRepository(tmp_path).get()

    assert summary.evidence_quality is CostEvidenceQuality.UNKNOWN
    assert summary.amount is None
    assert summary.total_sessions == 1


def test_cost_repository_skips_symlinked_and_oversized_jsonl_evidence(tmp_path, monkeypatch) -> None:
    from control_api.repositories import costs

    scratchpad = tmp_path / ".dexter" / "scratchpad"
    scratchpad.mkdir(parents=True)
    target = tmp_path / "outside.jsonl"
    target.write_text(json.dumps({"type": "llm_call"}) + "\n", encoding="utf-8")
    (scratchpad / "linked.jsonl").symlink_to(target)

    assert LegacyCostRepository(tmp_path).get().total_sessions == 0

    (scratchpad / "linked.jsonl").unlink()
    (scratchpad / "large.jsonl").write_text(json.dumps({"type": "llm_call"}) + "\n", encoding="utf-8")
    monkeypatch.setattr(costs, "MAX_COST_JSONL_BYTES", 16)

    assert LegacyCostRepository(tmp_path).get().total_sessions == 0


def test_cost_repository_keeps_newest_sessions_within_bounded_scan(tmp_path, monkeypatch) -> None:
    from control_api.repositories import costs

    scratchpad = tmp_path / ".dexter" / "scratchpad"
    scratchpad.mkdir(parents=True)
    for name in ("session-a.jsonl", "session-z.jsonl"):
        (scratchpad / name).write_text(json.dumps({"type": "llm_call"}) + "\n", encoding="utf-8")
    monkeypatch.setattr(costs, "MAX_COST_CANDIDATES", 3, raising=False)
    monkeypatch.setattr(costs, "MAX_COST_SESSIONS", 1, raising=False)

    summary = LegacyCostRepository(tmp_path).get()

    assert [item.session for item in summary.sessions] == ["session-z"]


def test_status_repository_rejects_symlinked_or_oversized_text_evidence(tmp_path, monkeypatch) -> None:
    from control_api.repositories import status as status_module

    (tmp_path / "memory").mkdir()
    create_database(tmp_path / "memory" / "trading.db")
    target = tmp_path / "mode-target"
    target.write_text("live", encoding="utf-8")
    (tmp_path / ".mode").symlink_to(target)
    (tmp_path / "live_prices.json").write_text(
        json.dumps({"updated_at": "2026-07-11T12:00:00Z"}), encoding="utf-8"
    )

    repository = LegacyOperationalStatusRepository(tmp_path)
    assert repository.get().requested_mode is ExecutionMode.PAPER

    monkeypatch.setattr(status_module, "MAX_LIVE_PRICE_BYTES", 16)
    assert repository.get().live_price_freshness.status is FreshnessStatus.NO_DATA
