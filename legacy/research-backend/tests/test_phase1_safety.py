import os
import ast
from pathlib import Path
from unittest.mock import Mock

import pytest


def test_live_execution_gate_defaults_to_disabled(monkeypatch):
    monkeypatch.delenv("LIVE_EXECUTION_ENABLED", raising=False)
    import trading_agent

    assert trading_agent.is_live_execution_enabled() is False


def test_live_execution_gate_requires_exact_true(monkeypatch):
    import trading_agent

    monkeypatch.setenv("LIVE_EXECUTION_ENABLED", "true")
    assert trading_agent.is_live_execution_enabled() is True
    monkeypatch.setenv("LIVE_EXECUTION_ENABLED", "1")
    assert trading_agent.is_live_execution_enabled() is True


def test_execute_live_does_not_touch_executor_when_gate_is_disabled(monkeypatch):
    import trading_agent

    monkeypatch.setenv("LIVE_EXECUTION_ENABLED", "false")
    agent = object.__new__(trading_agent.TradingAgent)
    agent.executor = Mock()

    agent._execute_live("BTC", "BUY", 0.01, 100_000, {"id": "test"})

    agent.executor.execute.assert_not_called()


def test_kill_switch_defaults_to_external_runtime_root(monkeypatch, tmp_path):
    import kill_switch

    monkeypatch.delenv("TRADING_DATA_ROOT", raising=False)
    monkeypatch.delenv("TRADING_KILL_SWITCH_PATH", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert kill_switch.resolve_kill_switch_path() == (
        tmp_path / ".local" / "share" / "trading-agent" / ".kill_switch"
    )


def test_kill_switch_path_can_be_overridden(monkeypatch, tmp_path):
    import kill_switch

    expected = tmp_path / "canonical.kill"
    monkeypatch.setenv("TRADING_KILL_SWITCH_PATH", str(expected))
    assert kill_switch.resolve_kill_switch_path() == expected


def test_invalid_kill_switch_state_fails_closed(tmp_path, monkeypatch):
    import kill_switch

    sentinel = tmp_path / ".kill_switch"
    sentinel.write_text("not-a-valid-state")
    monkeypatch.setenv("TRADING_KILL_SWITCH_PATH", str(sentinel))
    state = kill_switch.read_kill_switch_state()
    assert state.state.value == "UNKNOWN"
    assert kill_switch.is_kill_switch_active() is True


def test_unreadable_kill_switch_state_fails_closed(monkeypatch, tmp_path):
    import kill_switch

    sentinel = tmp_path / ".kill_switch"
    sentinel.write_text("placeholder")
    monkeypatch.setenv("TRADING_KILL_SWITCH_PATH", str(sentinel))
    monkeypatch.setattr(Path, "read_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("denied")))
    state = kill_switch.read_kill_switch_state()
    assert state.state.value == "UNKNOWN"
    assert kill_switch.is_kill_switch_active() is True


def test_kill_switch_toggle_roundtrip_is_canonical(tmp_path, monkeypatch):
    import kill_switch

    sentinel = tmp_path / ".kill_switch"
    monkeypatch.setenv("TRADING_KILL_SWITCH_PATH", str(sentinel))
    kill_switch.activate_kill_switch("phase-1-drill")
    assert kill_switch.is_kill_switch_active() is True
    assert "phase-1-drill" in sentinel.read_text()
    kill_switch.deactivate_kill_switch()
    assert kill_switch.is_kill_switch_active() is False


def test_safety_engine_has_one_circuit_breaker_definition():
    source = Path(__file__).parent.parent / "safety_engine.py"
    tree = ast.parse(source.read_text())
    definitions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "check_circuit_breaker"]
    assert len(definitions) == 1


def test_circuit_breaker_preserves_default_thresholds(tmp_path, monkeypatch):
    import json
    import safety_engine

    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "report_fixture.json").write_text(json.dumps({"assets": [
        {"symbol": "BTC", "price_change_24h_pct": -31},
        {"symbol": "ETH", "price_change_24h_pct": 0},
    ]}))
    monkeypatch.setenv("TRADING_REPORTS_DIR", str(reports))
    result = safety_engine.check_circuit_breaker()
    assert result["triggered"] is True
    assert "Single-asset circuit breaker" in result["detail"]
    assert "threshold -30%" in result["detail"]


@pytest.mark.parametrize("symbol", ["BTC", "ETH", "SOL", "TON", "DOGE", "ADA", "AVAX", "DOT", "LINK", "MATIC"])
def test_all_watchlist_cryptos_are_never_routed_to_alpaca(symbol):
    import broker

    assert broker.execution_route(symbol) == "crypto"


def test_unknown_asset_is_rejected(monkeypatch):
    import broker

    monkeypatch.setenv("ALPACA_API_KEY", "test_key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test_secret")
    result = broker.execute("UNKNOWN", 1.0, "buy")

    assert result["status"] == "rejected"
    assert result["reason"] == "REJECT_UNKNOWN_ASSET"
