import importlib.util
from unittest.mock import Mock

import pytest


def policy_module():
    assert importlib.util.find_spec("live_execution_policy") is not None, "central live_execution_policy module is required"
    import live_execution_policy
    return live_execution_policy


def make_policy(env, state="INACTIVE"):
    module = policy_module()
    return module.LiveExecutionPolicy(env=env, kill_switch_reader=lambda: state)


@pytest.mark.parametrize("truthy", ["1", "true", "yes", "on", " TRUE "])
def test_two_independent_gates_accept_only_explicit_truthy_values(truthy):
    policy = make_policy({"LIVE_EXECUTION_ENABLED": truthy, "LIVE_TRADING_APPROVED": truthy})
    decision = policy.evaluate("live")
    assert decision.allowed is True
    assert decision.reason_code == "ALLOW"


def test_both_gates_false_blocks_live():
    decision = make_policy({}).evaluate("live")
    assert decision.allowed is False
    assert decision.reason_code == "LIVE_EXECUTION_DISABLED"
    assert decision.effective_mode == "paper"
    assert decision.execution_capability == "LIVE_BLOCKED"


def test_one_gate_true_one_false_blocks_live():
    decision = make_policy({"LIVE_EXECUTION_ENABLED": "true"}).evaluate("live")
    assert decision.allowed is False
    assert decision.reason_code == "LIVE_APPROVAL_MISSING"


@pytest.mark.parametrize("state", ["ACTIVE", "UNKNOWN"])
def test_kill_switch_active_or_unknown_blocks_live(state):
    decision = make_policy({"LIVE_EXECUTION_ENABLED": "true", "LIVE_TRADING_APPROVED": "true"}, state).evaluate("live")
    assert decision.allowed is False
    assert decision.reason_code == "KILL_SWITCH_ACTIVE"


def test_risk_preflight_failure_blocks_live():
    decision = make_policy({"LIVE_EXECUTION_ENABLED": "true", "LIVE_TRADING_APPROVED": "true"}).evaluate("live", risk_preflight_pass=False)
    assert decision.allowed is False
    assert decision.reason_code == "RISK_PREFLIGHT_FAILED"


def test_missing_adapter_or_credentials_blocks_live():
    policy = make_policy({"LIVE_EXECUTION_ENABLED": "true", "LIVE_TRADING_APPROVED": "true"})
    assert policy.evaluate("live", adapter_initialized=False).reason_code == "EXECUTION_ADAPTER_UNAVAILABLE"
    assert policy.evaluate("live", credentials_available=False).reason_code == "CREDENTIALS_UNAVAILABLE"


@pytest.mark.parametrize("mode", ["paper", "dryrun"])
def test_non_live_modes_never_gain_live_capability(mode):
    decision = make_policy({"LIVE_EXECUTION_ENABLED": "true", "LIVE_TRADING_APPROVED": "true"}).evaluate(mode)
    assert decision.allowed is False
    assert decision.reason_code == "MODE_NOT_LIVE"
    assert decision.effective_mode == mode


def test_blocked_agent_submission_never_calls_executor(monkeypatch):
    import trading_agent

    monkeypatch.setenv("LIVE_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("LIVE_TRADING_APPROVED", "false")
    agent = object.__new__(trading_agent.TradingAgent)
    agent.mode = "live"
    agent.halted = False
    agent.executor = Mock()
    agent.adapter = Mock()
    agent._credentials_available = True
    agent._execute_live("BTC", "BUY", 0.01, 100_000, {"id": "test"})
    agent.executor.execute.assert_not_called()


def test_exchange_adapter_boundary_never_calls_client_when_gates_are_false(monkeypatch):
    from exchange.adapter import ExchangeAdapter, ExchangeID, OrderRequest, OrderSide, OrderType

    monkeypatch.setenv("LIVE_EXECUTION_ENABLED", "false")
    monkeypatch.setenv("LIVE_TRADING_APPROVED", "false")
    adapter = object.__new__(ExchangeAdapter)
    adapter.sandbox = False
    adapter.exchange_id = ExchangeID.COINBASE
    adapter._client = Mock()
    request = OrderRequest(
        exchange=ExchangeID.COINBASE,
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=0.01,
    )
    with pytest.raises(PermissionError, match="LIVE_EXECUTION_DISABLED"):
        adapter.create_order(request)
    adapter._client.create_order.assert_not_called()


def test_exchange_adapter_dryrun_never_submits_sandbox_order():
    from exchange.adapter import ExchangeAdapter, ExchangeID, OrderRequest, OrderSide, OrderType

    adapter = object.__new__(ExchangeAdapter)
    adapter.sandbox = True
    adapter.exchange_id = ExchangeID.COINBASE
    adapter._client = Mock()
    request = OrderRequest(
        exchange=ExchangeID.COINBASE,
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=0.01,
    )
    with pytest.raises(PermissionError, match="DRYRUN_ORDER_SUBMISSION_DISABLED"):
        adapter.create_order(request)
    adapter._client.create_order.assert_not_called()
