import ast
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
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


def _sizing():
    return {
        "position_pct": 0.1,
        "cash_to_use": 100.0,
        "shares": 1.0,
        "cost": 100.0,
        "quote_balance": 1_000.0,
    }


def _filled_order():
    return {
        "id": "synthetic-order-1",
        "symbol": "BTC/USD",
        "side": "buy",
        "type": "market",
        "amount": 1.0,
        "filled": 1.0,
        "avg_fill_price": 100.0,
        "status": "filled",
        "fee": 0.0,
        "fee_asset": "USD",
    }


def _report():
    return {
        "assets": [
            {
                "symbol": "BTC",
                "suggestion": "BUY",
                "price": 100.0,
                "confidence": 0.8,
                "rationale": "synthetic test signal",
            }
        ]
    }


@pytest.fixture
def execution_module(monkeypatch, tmp_path):
    import execute_live

    alert_manager = ModuleType("alert_manager")
    alert_manager.send_telegram_text = lambda _message: True
    monkeypatch.setitem(sys.modules, "alert_manager", alert_manager)
    monkeypatch.setattr(execute_live, "get_mode", lambda: "dryrun")
    monkeypatch.setattr(execute_live, "LIVE_POSITIONS_FILE", tmp_path / "live_positions.json")
    monkeypatch.setattr(execute_live, "LIVE_ORDERS_FILE", tmp_path / "live_orders.jsonl")
    monkeypatch.setattr(execute_live, "_HAS_ALLOCATION_ENGINE", False)
    monkeypatch.setattr(execute_live, "_HAS_GARCH", False)
    return execute_live


def test_public_taxonomy_has_safe_stable_failure_envelopes():
    import execute_live

    spec = importlib.util.find_spec("execute_live")
    assert spec is not None, "execute_live taxonomy is missing"

    failure = execute_live.ExecutionSubmissionFailed(
        operation="place_order",
        symbol="BTC",
        cause=RuntimeError("provider-secret-must-not-escape"),
    )

    assert failure.status == "UNAVAILABLE"
    assert failure.reason_code == "ORDER_SUBMISSION_FAILED"
    assert failure.trace_id
    assert failure.to_dict() == {
        "status": "UNAVAILABLE",
        "reason_code": "ORDER_SUBMISSION_FAILED",
        "trace_id": failure.trace_id,
        "operation": "place_order",
        "symbol": "BTC",
        "error_type": "RuntimeError",
    }
    assert "provider-secret-must-not-escape" not in str(failure.to_dict())


def test_execute_signals_dependency_exception_fails_closed(execution_module, monkeypatch):
    def unavailable_balances(_exchange_id):
        raise RuntimeError("provider payload must stay redacted")

    monkeypatch.setattr(execution_module, "fetch_balances", unavailable_balances)

    result = execution_module.execute_signals(_report())

    assert result["status"] == "UNAVAILABLE"
    assert result["reason_code"] == "EXECUTION_DEPENDENCY_UNAVAILABLE"
    assert result["trace_id"]
    assert result["executed"] == []
    assert result["errors"] == [
        {
            "status": "UNAVAILABLE",
            "reason_code": "EXECUTION_DEPENDENCY_UNAVAILABLE",
            "trace_id": result["trace_id"],
            "operation": "fetch_balances",
            "symbol": "BTC",
            "error_type": "RuntimeError",
            "action": "BUY",
        }
    ]
    assert "provider payload" not in str(result)


def test_execute_signals_submission_exception_has_no_success_inference(
    execution_module, monkeypatch
):
    monkeypatch.setattr(execution_module, "_compute_position_size", lambda *_args: _sizing())
    monkeypatch.setattr(execution_module, "preflight", lambda *_args: (True, None))

    def failed_submission(*_args, **_kwargs):
        raise TimeoutError("uncontrolled order response")

    monkeypatch.setattr(execution_module, "place_order", failed_submission)

    result = execution_module.execute_signals(_report())

    assert result["status"] == "UNAVAILABLE"
    assert result["reason_code"] == "ORDER_SUBMISSION_FAILED"
    assert result["executed"] == []
    assert result["errors"][0]["operation"] == "place_order"
    assert result["errors"][0]["error_type"] == "TimeoutError"
    assert result["errors"][0]["trace_id"] == result["trace_id"]
    assert "uncontrolled order response" not in str(result)


def test_execute_signal_rejects_malformed_order_result(execution_module, monkeypatch):
    monkeypatch.setattr(execution_module, "_compute_position_size", lambda *_args: _sizing())
    monkeypatch.setattr(execution_module, "preflight", lambda *_args: (True, None))
    monkeypatch.setattr(execution_module, "place_order", lambda *_args, **_kwargs: {})

    result = execution_module.execute_signal(
        {"asset": "BTC", "action": "BUY", "confidence": 0.8},
        {"BTC": 100.0},
    )

    assert result["status"] == "UNAVAILABLE"
    assert result["reason_code"] == "ORDER_RESULT_INVALID"
    assert result["trace_id"]
    assert result["operation"] == "place_order"
    assert result["symbol"] == "BTC"
    assert "fill_price" not in result


@pytest.mark.parametrize("field", ["filled", "avg_fill_price"])
@pytest.mark.parametrize(
    "invalid_value",
    [
        pytest.param(float("nan"), id="nan"),
        pytest.param(float("inf"), id="positive-infinity"),
        pytest.param(float("-inf"), id="negative-infinity"),
        pytest.param(5e-324, id="subnormal"),
        pytest.param(True, id="bool"),
    ],
)
def test_execute_signal_rejects_non_finite_or_bool_fill_evidence(
    execution_module, monkeypatch, field, invalid_value
):
    monkeypatch.setattr(execution_module, "_compute_position_size", lambda *_args: _sizing())
    monkeypatch.setattr(execution_module, "preflight", lambda *_args: (True, None))

    malformed_order = _filled_order()
    malformed_order[field] = invalid_value
    monkeypatch.setattr(
        execution_module, "place_order", lambda *_args, **_kwargs: malformed_order
    )

    result = execution_module.execute_signal(
        {"asset": "BTC", "action": "BUY", "confidence": 0.8},
        {"BTC": 100.0},
    )

    assert result["status"] == "UNAVAILABLE"
    assert result["reason_code"] == "ORDER_RESULT_INVALID"
    assert result["trace_id"]
    assert result["operation"] == "place_order"
    assert result["symbol"] == "BTC"
    assert result["error_type"] == "TypeError"
    assert "order_id" not in result
    assert "nan" not in str(result).lower()
    assert "inf" not in str(result).lower()


def test_live_summary_state_failure_keeps_public_keys(execution_module):
    execution_module.LIVE_POSITIONS_FILE.write_text("not-json")

    result = execution_module.get_live_summary()

    assert result["status"] == "UNAVAILABLE"
    assert result["reason_code"] == "EXECUTION_STATE_UNAVAILABLE"
    assert result["trace_id"]
    assert result["mode"] == "dryrun"
    assert result["live_execution_enabled"] is False
    assert result["positions"] == {}
    assert result["updated_at"] is None
    assert result["errors"][0]["trace_id"] == result["trace_id"]


def test_exit_monitor_names_symbol_when_price_fetch_fails(execution_module, monkeypatch):
    import exchange.ccxt_bridge as bridge

    monkeypatch.setattr(execution_module, "_HAS_EXIT_STRATEGIES", True)
    monkeypatch.setattr(
        execution_module,
        "_load_live_positions",
        lambda: {
            "positions": {"BTC": {"shares": 1.0, "avg_cost": 100.0}},
            "tiered_exits": {},
        },
    )
    monkeypatch.setattr(execution_module, "_save_live_positions", lambda _data: None)

    def unavailable_ticker(*_args):
        raise ConnectionError("provider response body")

    monkeypatch.setattr(bridge, "fetch_ticker", unavailable_ticker)

    result = execution_module.monitor_positions_for_exits()

    assert result["status"] == "UNAVAILABLE"
    assert result["reason_code"] == "EXECUTION_DEPENDENCY_UNAVAILABLE"
    assert result["unavailable_symbols"] == ["BTC"]
    assert result["actions"] == []
    assert result["errors"][0]["operation"] == "fetch_ticker"
    assert result["errors"][0]["trace_id"] == result["trace_id"]
    assert "provider response body" not in str(result)


@pytest.mark.parametrize(
    ("ticker_result", "sensitive_value"),
    [
        pytest.param({"last": "ticker-provider-secret"}, "ticker-provider-secret", id="string"),
        pytest.param({"last": float("nan")}, "nan", id="nan"),
        pytest.param({"last": float("inf")}, "inf", id="positive-infinity"),
        pytest.param({"last": float("-inf")}, "inf", id="negative-infinity"),
        pytest.param({"last": 5e-324}, None, id="subnormal"),
        pytest.param({"last": True}, "True", id="bool"),
        pytest.param({"last": 0}, None, id="zero"),
        pytest.param({"last": -1.0}, None, id="negative"),
        pytest.param({"close": "fallback-provider-secret"}, "fallback-provider-secret", id="fallback-close-string"),
    ],
)
def test_exit_monitor_rejects_malformed_ticker_result_without_exit_action(
    execution_module, monkeypatch, ticker_result, sensitive_value
):
    import exchange.ccxt_bridge as bridge

    submitted = []
    monkeypatch.setattr(execution_module, "_HAS_EXIT_STRATEGIES", True)
    monkeypatch.setattr(
        execution_module,
        "_load_live_positions",
        lambda: {
            "positions": {"BTC": {"shares": 1.0, "avg_cost": 100.0}},
            "tiered_exits": {},
        },
    )
    monkeypatch.setattr(execution_module, "_save_live_positions", lambda _data: None)
    monkeypatch.setattr(bridge, "fetch_ticker", lambda *_args: ticker_result)
    monkeypatch.setattr(
        execution_module,
        "place_order",
        lambda *_args, **_kwargs: submitted.append("submitted") or _filled_order(),
    )

    result = execution_module.monitor_positions_for_exits()

    assert submitted == []
    assert result["status"] == "UNAVAILABLE"
    assert result["reason_code"] == "EXECUTION_DEPENDENCY_RESULT_INVALID"
    assert result["trace_id"]
    assert result["actions"] == []
    assert result["unavailable_symbols"] == ["BTC"]
    assert result["errors"] == [
        {
            "status": "UNAVAILABLE",
            "reason_code": "EXECUTION_DEPENDENCY_RESULT_INVALID",
            "trace_id": result["trace_id"],
            "operation": "fetch_ticker",
            "symbol": "BTC",
            "error_type": "TypeError",
        }
    ]
    assert sensitive_value is None or sensitive_value not in str(result)


def test_exit_order_exception_is_typed_and_has_no_action(execution_module, monkeypatch):
    import exchange.ccxt_bridge as bridge

    monkeypatch.setattr(execution_module, "_HAS_EXIT_STRATEGIES", True)
    monkeypatch.setattr(
        execution_module,
        "_load_live_positions",
        lambda: {
            "positions": {"BTC": {"shares": 1.0, "avg_cost": 100.0}},
            "tiered_exits": {},
        },
    )
    monkeypatch.setattr(execution_module, "_save_live_positions", lambda _data: None)
    monkeypatch.setattr(bridge, "fetch_ticker", lambda *_args: {"last": 120.0})
    monkeypatch.setattr(
        execution_module,
        "tiered_profit_exit",
        lambda **_kwargs: {"action": "partial_close", "close_pct": 0.5, "reason": "tier-1"},
    )

    def failed_exit_order(*_args, **_kwargs):
        raise TimeoutError("provider order detail")

    monkeypatch.setattr(execution_module, "place_order", failed_exit_order)

    result = execution_module.monitor_positions_for_exits()

    assert result["status"] == "UNAVAILABLE"
    assert result["reason_code"] == "ORDER_SUBMISSION_FAILED"
    assert result["actions"] == []
    assert result["errors"][0]["symbol"] == "BTC"
    assert result["errors"][0]["trace_id"] == result["trace_id"]
    assert "provider order detail" not in str(result)


def test_post_order_persistence_failure_preserves_evidence_without_resubmission(
    execution_module, monkeypatch
):
    submissions = []
    monkeypatch.setattr(execution_module, "_compute_position_size", lambda *_args: _sizing())
    monkeypatch.setattr(execution_module, "preflight", lambda *_args: (True, None))

    def filled_order(*_args, **_kwargs):
        submissions.append("submitted")
        return _filled_order()

    monkeypatch.setattr(execution_module, "place_order", filled_order)
    monkeypatch.setattr(execution_module.cra_tracker, "log_trade", lambda **_kwargs: None)
    monkeypatch.setattr(
        execution_module,
        "_load_live_positions",
        lambda: {"positions": {}, "updated_at": None},
    )

    def failed_position_write(_data):
        raise OSError("local path detail")

    monkeypatch.setattr(execution_module, "_save_live_positions", failed_position_write)

    result = execution_module.execute_signals(_report())

    assert submissions == ["submitted"]
    assert result["status"] == "PARTIAL"
    assert result["reason_code"] == "EXECUTION_STATE_PERSISTENCE_FAILED"
    assert result["executed"][0]["status"] == "PARTIAL"
    assert result["executed"][0]["result"]["order_id"] == "synthetic-order-1"
    assert result["errors"][0]["operation"] == "save_live_positions"
    assert result["errors"][0]["trace_id"] == result["trace_id"]
    assert "local path detail" not in str(result)


def test_alert_failure_preserves_fill_and_exposes_observability_failure(
    execution_module, monkeypatch
):
    monkeypatch.setattr(execution_module, "_compute_position_size", lambda *_args: _sizing())
    monkeypatch.setattr(execution_module, "preflight", lambda *_args: (True, None))
    monkeypatch.setattr(execution_module, "place_order", lambda *_args, **_kwargs: _filled_order())
    monkeypatch.setattr(execution_module, "_log_journal", lambda _entry: None)
    monkeypatch.setattr(
        execution_module,
        "_load_live_positions",
        lambda: {"positions": {}, "updated_at": None},
    )
    monkeypatch.setattr(execution_module, "_save_live_positions", lambda _data: None)

    alert_manager = ModuleType("alert_manager")

    def failed_alert(_message):
        raise ConnectionError("telegram provider payload")

    alert_manager.send_telegram_text = failed_alert
    monkeypatch.setitem(sys.modules, "alert_manager", alert_manager)

    result = execution_module.execute_signal(
        {"asset": "BTC", "action": "BUY", "confidence": 0.8},
        {"BTC": 100.0},
    )

    assert result["status"] == "PARTIAL"
    assert result["aggregate_status"] == "PARTIAL"
    assert result["order_id"] == "synthetic-order-1"
    assert result["execution_evidence"]["order_id"] == "synthetic-order-1"
    assert result["observability_failures"] == [
        {
            "status": "PARTIAL",
            "reason_code": "EXECUTION_OBSERVABILITY_FAILED",
            "trace_id": result["observability_failures"][0]["trace_id"],
            "operation": "send_telegram_text",
            "symbol": "BTC",
            "error_type": "ConnectionError",
        }
    ]
    assert "telegram provider payload" not in str(result)


@pytest.mark.parametrize(
    "entrypoint",
    ["execute_signal", "execute_signals", "get_live_summary", "monitor_positions_for_exits"],
)
def test_public_live_entrypoints_type_mode_dependency_failure(
    execution_module, monkeypatch, entrypoint
):
    def unavailable_mode():
        raise OSError("mode-path-detail-must-not-escape")

    monkeypatch.setattr(execution_module, "get_mode", unavailable_mode)
    monkeypatch.setattr(
        execution_module,
        "place_order",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("order submission must not run")
        ),
    )

    if entrypoint == "execute_signal":
        result = execution_module.execute_signal(
            {"asset": "BTC", "action": "BUY", "confidence": 0.8},
            {"BTC": 100.0},
        )
    elif entrypoint == "execute_signals":
        result = execution_module.execute_signals(_report())
    else:
        result = getattr(execution_module, entrypoint)()

    assert result["status"] == "UNAVAILABLE"
    assert result["reason_code"] == "EXECUTION_DEPENDENCY_UNAVAILABLE"
    assert result["trace_id"]
    assert "mode-path-detail" not in str(result)


def test_preflight_types_mode_dependency_failure(execution_module, monkeypatch):
    def unavailable_mode():
        raise OSError("mode-path-detail-must-not-escape")

    monkeypatch.setattr(execution_module, "get_mode", unavailable_mode)

    with pytest.raises(execution_module.ExecutionDependencyUnavailable) as captured:
        execution_module.preflight("coinbase", "BTC/USD", "buy", 1.0)

    assert captured.value.operation == "get_mode"
    assert captured.value.reason_code == "EXECUTION_DEPENDENCY_UNAVAILABLE"
    assert "mode-path-detail" not in str(captured.value)


def test_unbounded_balance_is_rejected_before_sizing_or_submission(
    execution_module, monkeypatch, tmp_path
):
    submitted = []
    monkeypatch.setattr(execution_module, "data_root", lambda: tmp_path)
    monkeypatch.setattr(
        execution_module,
        "fetch_balances",
        lambda _exchange_id: {"USD": {"free": float.fromhex("0x1.fffffffffffffp+1023")}},
    )
    monkeypatch.setattr(execution_module, "preflight", lambda *_args: (True, None))
    monkeypatch.setattr(
        execution_module,
        "place_order",
        lambda *_args, **_kwargs: submitted.append("submitted") or _filled_order(),
    )

    result = execution_module.execute_signal(
        {"asset": "BTC", "action": "BUY", "confidence": 0.8},
        {"BTC": float.fromhex("0x1.0000000000000p-1022")},
    )

    assert submitted == []
    assert result["status"] == "UNAVAILABLE"
    assert result["reason_code"] == "EXECUTION_DEPENDENCY_RESULT_INVALID"
    assert result["operation"] == "fetch_balances"
    assert result["symbol"] == "BTC"


def test_execute_signals_mixed_dependency_failure_and_fill_is_partial(
    execution_module, monkeypatch
):
    report = {
        "assets": [
            {
                "symbol": "BTC",
                "suggestion": "BUY",
                "price": 100.0,
                "confidence": 0.8,
            },
            {
                "symbol": "ETH",
                "suggestion": "BUY",
                "price": 100.0,
                "confidence": 0.8,
            },
        ]
    }

    def sizing(symbol, *_args):
        if symbol == "BTC":
            raise execution_module.ExecutionDependencyUnavailable(
                operation="load_symbol_returns", symbol=symbol, cause=OSError()
            )
        return _sizing()

    monkeypatch.setattr(execution_module, "_compute_position_size", sizing)
    monkeypatch.setattr(execution_module, "preflight", lambda *_args: (True, None))
    monkeypatch.setattr(
        execution_module, "place_order", lambda *_args, **_kwargs: _filled_order()
    )
    monkeypatch.setattr(execution_module.cra_tracker, "log_trade", lambda **_kwargs: None)

    result = execution_module.execute_signals(report)

    assert result["status"] == "PARTIAL"
    assert result["reason_code"] == "EXECUTION_DEPENDENCY_UNAVAILABLE"
    assert result["errors"] == [
        {
            "status": "UNAVAILABLE",
            "reason_code": "EXECUTION_DEPENDENCY_UNAVAILABLE",
            "trace_id": result["trace_id"],
            "operation": "load_symbol_returns",
            "symbol": "BTC",
            "error_type": "OSError",
            "action": "BUY",
        }
    ]
    assert result["executed"][0]["symbol"] == "ETH"
    assert result["executed"][0]["result"]["order_id"] == "synthetic-order-1"


def test_unreadable_returns_input_fails_closed_without_order_submission(
    execution_module, monkeypatch, tmp_path
):
    returns_file = tmp_path / "memory" / "backtest" / "returns" / "BTC_returns.json"
    returns_file.parent.mkdir(parents=True)
    returns_file.write_text("not-json")
    submitted = []

    monkeypatch.setattr(execution_module, "data_root", lambda: tmp_path)
    monkeypatch.setattr(
        execution_module,
        "fetch_balances",
        lambda _exchange_id: {"USD": {"free": 1_000.0}},
    )
    monkeypatch.setattr(
        execution_module,
        "place_order",
        lambda *_args, **_kwargs: submitted.append("submitted") or _filled_order(),
    )

    result = execution_module.execute_signals(_report())

    assert submitted == []
    assert result["status"] == "UNAVAILABLE"
    assert result["reason_code"] == "EXECUTION_DEPENDENCY_UNAVAILABLE"
    assert result["errors"][0] == {
        "status": "UNAVAILABLE",
        "reason_code": "EXECUTION_DEPENDENCY_UNAVAILABLE",
        "trace_id": result["trace_id"],
        "operation": "load_symbol_returns",
        "symbol": "BTC",
        "error_type": "JSONDecodeError",
        "action": "BUY",
    }
    assert "not-json" not in str(result)


@pytest.mark.parametrize(
    ("operation", "configure_enrichment"),
    [
        (
            "forecast_volatility",
            lambda module, monkeypatch: (
                monkeypatch.setattr(module, "_HAS_GARCH", True),
                monkeypatch.setattr(
                    module,
                    "forecast_volatility",
                    lambda _returns: (_ for _ in ()).throw(ValueError("garch detail")),
                ),
            ),
        ),
        (
            "allocation_alignment",
            lambda module, monkeypatch: (
                monkeypatch.setattr(module, "_HAS_ALLOCATION_ENGINE", True),
                monkeypatch.setattr(
                    module,
                    "get_target_weight_for_symbol",
                    lambda _symbol: (_ for _ in ()).throw(KeyError("allocation detail")),
                ),
            ),
        ),
    ],
)
def test_optional_sizing_enrichment_failure_is_returned_as_partial(
    execution_module, monkeypatch, tmp_path, operation, configure_enrichment
):
    returns_file = tmp_path / "memory" / "backtest" / "returns" / "BTC_returns.json"
    returns_file.parent.mkdir(parents=True)
    returns_file.write_text("{\"daily_returns\": [" + ", ".join(["0.01"] * 30) + "]}")

    monkeypatch.setattr(execution_module, "data_root", lambda: tmp_path)
    monkeypatch.setattr(
        execution_module,
        "fetch_balances",
        lambda _exchange_id: {"USD": {"free": 1_000.0}},
    )
    monkeypatch.setattr(execution_module, "cvar_position_size", lambda *_args: type(
        "Sizing", (), {"notional": 100.0, "quantity": 1.0, "capital_used_pct": 10.0,
                       "risk_amount": 1.0, "cvar_95": 0.01}
    )())
    monkeypatch.setattr(execution_module, "preflight", lambda *_args: (True, None))
    monkeypatch.setattr(
        execution_module, "place_order", lambda *_args, **_kwargs: _filled_order()
    )
    monkeypatch.setattr(execution_module.cra_tracker, "log_trade", lambda **_kwargs: None)
    configure_enrichment(execution_module, monkeypatch)

    result = execution_module.execute_signals(_report())

    assert result["status"] == "PARTIAL"
    assert result["executed"][0]["result"]["order_id"] == "synthetic-order-1"
    assert result["observability_failures"] == [
        {
            "status": "PARTIAL",
            "reason_code": "EXECUTION_OBSERVABILITY_FAILED",
            "trace_id": result["trace_id"],
            "operation": operation,
            "symbol": "BTC",
            "error_type": "ValueError" if operation == "forecast_volatility" else "KeyError",
        }
    ]
    assert "detail" not in str(result)


@pytest.mark.parametrize(
    ("malformed_forecast", "sensitive_value"),
    [
        pytest.param("garch-provider-secret", "garch-provider-secret", id="string"),
        pytest.param(float("nan"), "nan", id="nan"),
        pytest.param(float("inf"), "inf", id="positive-infinity"),
        pytest.param(float("-inf"), "inf", id="negative-infinity"),
        pytest.param(5e-324, None, id="positive-subnormal"),
        pytest.param(True, "True", id="bool"),
        pytest.param(-0.01, None, id="negative"),
    ],
)
def test_malformed_garch_forecast_is_typed_partial_with_fill_evidence(
    execution_module, monkeypatch, tmp_path, malformed_forecast, sensitive_value
):
    returns_file = tmp_path / "memory" / "backtest" / "returns" / "BTC_returns.json"
    returns_file.parent.mkdir(parents=True)
    returns_file.write_text(
        "{\"daily_returns\": [" + ", ".join(["0.01"] * 30) + "]}"
    )
    submitted = []

    monkeypatch.setattr(execution_module, "data_root", lambda: tmp_path)
    monkeypatch.setattr(execution_module, "_HAS_GARCH", True)
    monkeypatch.setattr(
        execution_module, "forecast_volatility", lambda _returns: malformed_forecast
    )
    monkeypatch.setattr(
        execution_module,
        "fetch_balances",
        lambda _exchange_id: {"USD": {"free": 1_000.0}},
    )
    monkeypatch.setattr(execution_module, "cvar_position_size", lambda *_args: type(
        "Sizing", (), {"notional": 100.0, "quantity": 1.0, "capital_used_pct": 10.0,
                       "risk_amount": 1.0, "cvar_95": 0.01}
    )())
    monkeypatch.setattr(execution_module, "preflight", lambda *_args: (True, None))
    monkeypatch.setattr(
        execution_module,
        "place_order",
        lambda *_args, **_kwargs: submitted.append("submitted") or _filled_order(),
    )
    monkeypatch.setattr(execution_module.cra_tracker, "log_trade", lambda **_kwargs: None)

    result = execution_module.execute_signals(_report())

    assert submitted == ["submitted"]
    assert result["status"] == "PARTIAL"
    assert result["reason_code"] == "EXECUTION_OBSERVABILITY_FAILED"
    assert result["trace_id"]
    assert result["executed"][0]["result"]["order_id"] == "synthetic-order-1"
    assert result["observability_failures"] == [
        {
            "status": "PARTIAL",
            "reason_code": "EXECUTION_OBSERVABILITY_FAILED",
            "trace_id": result["trace_id"],
            "operation": "forecast_volatility",
            "symbol": "BTC",
            "error_type": "TypeError",
        }
    ]
    assert sensitive_value is None or sensitive_value not in str(result)


def test_zero_garch_forecast_keeps_normal_no_adjustment_semantics(
    execution_module, monkeypatch, tmp_path
):
    returns_file = tmp_path / "memory" / "backtest" / "returns" / "BTC_returns.json"
    returns_file.parent.mkdir(parents=True)
    returns_file.write_text(
        "{\"daily_returns\": [" + ", ".join(["0.01"] * 30) + "]}"
    )
    preflight_shares = []

    monkeypatch.setattr(execution_module, "data_root", lambda: tmp_path)
    monkeypatch.setattr(execution_module, "_HAS_GARCH", True)
    monkeypatch.setattr(execution_module, "forecast_volatility", lambda _returns: 0)
    monkeypatch.setattr(
        execution_module,
        "fetch_balances",
        lambda _exchange_id: {"USD": {"free": 1_000.0}},
    )
    monkeypatch.setattr(execution_module, "cvar_position_size", lambda *_args: type(
        "Sizing", (), {"notional": 100.0, "quantity": 1.0, "capital_used_pct": 10.0,
                       "risk_amount": 1.0, "cvar_95": 0.01}
    )())
    monkeypatch.setattr(
        execution_module,
        "preflight",
        lambda _exchange_id, _symbol, _side, shares: preflight_shares.append(shares) or (True, None),
    )
    monkeypatch.setattr(
        execution_module, "place_order", lambda *_args, **_kwargs: _filled_order()
    )
    monkeypatch.setattr(execution_module.cra_tracker, "log_trade", lambda **_kwargs: None)

    result = execution_module.execute_signals(_report())

    assert preflight_shares == [1.0]
    assert result["status"] == "COMPLETED"
    assert result["observability_failures"] == []
    assert result["executed"][0]["result"]["order_id"] == "synthetic-order-1"


def test_allocation_runtime_error_preserves_execution_evidence_as_partial(
    execution_module, monkeypatch
):
    monkeypatch.setattr(execution_module, "_compute_position_size", lambda *_args: _sizing())
    monkeypatch.setattr(execution_module, "preflight", lambda *_args: (True, None))
    monkeypatch.setattr(
        execution_module, "place_order", lambda *_args, **_kwargs: _filled_order()
    )
    monkeypatch.setattr(execution_module.cra_tracker, "log_trade", lambda **_kwargs: None)
    monkeypatch.setattr(execution_module, "_HAS_ALLOCATION_ENGINE", True)

    def unavailable_allocation(_symbol):
        raise RuntimeError("allocation observer detail")

    monkeypatch.setattr(
        execution_module, "get_target_weight_for_symbol", unavailable_allocation
    )

    result = execution_module.execute_signals(_report())

    assert result["status"] == "PARTIAL"
    assert result["reason_code"] == "EXECUTION_OBSERVABILITY_FAILED"
    assert result["trace_id"]
    assert result["executed"][0]["result"]["order_id"] == "synthetic-order-1"
    assert result["observability_failures"] == [
        {
            "status": "PARTIAL",
            "reason_code": "EXECUTION_OBSERVABILITY_FAILED",
            "trace_id": result["trace_id"],
            "operation": "allocation_alignment",
            "symbol": "BTC",
            "error_type": "RuntimeError",
        }
    ]
    assert "allocation observer detail" not in str(result)


@pytest.mark.parametrize(
    "invalid_target_weight",
    [
        pytest.param("allocation-provider-secret", id="sensitive-string"),
        pytest.param(float("nan"), id="nan"),
        pytest.param(float("inf"), id="positive-infinity"),
        pytest.param(float("-inf"), id="negative-infinity"),
        pytest.param(5e-324, id="positive-subnormal"),
        pytest.param(True, id="bool"),
    ],
)
def test_malformed_allocation_target_is_typed_partial_without_value_leakage(
    execution_module, monkeypatch, invalid_target_weight
):
    submissions = []
    preflight_shares = []
    monkeypatch.setattr(execution_module, "_compute_position_size", lambda *_args: _sizing())
    monkeypatch.setattr(
        execution_module,
        "preflight",
        lambda _exchange_id, _symbol, _side, shares: preflight_shares.append(shares)
        or (True, None),
    )
    monkeypatch.setattr(execution_module, "_HAS_ALLOCATION_ENGINE", True)
    monkeypatch.setattr(
        execution_module,
        "get_target_weight_for_symbol",
        lambda _symbol: invalid_target_weight,
    )
    monkeypatch.setattr(
        execution_module,
        "place_order",
        lambda *_args, **_kwargs: submissions.append("submitted") or _filled_order(),
    )
    monkeypatch.setattr(execution_module.cra_tracker, "log_trade", lambda **_kwargs: None)

    result = execution_module.execute_signals(_report())

    assert submissions == ["submitted"]
    assert preflight_shares == [1.0]
    assert result["status"] == "PARTIAL"
    assert result["reason_code"] == "EXECUTION_OBSERVABILITY_FAILED"
    assert result["trace_id"]
    assert result["executed"][0]["result"]["order_id"] == "synthetic-order-1"
    assert result["observability_failures"] == [
        {
            "status": "PARTIAL",
            "reason_code": "EXECUTION_OBSERVABILITY_FAILED",
            "trace_id": result["trace_id"],
            "operation": "allocation_alignment",
            "symbol": "BTC",
            "error_type": "TypeError",
        }
    ]
    assert "allocation-provider-secret" not in str(result)


def test_zero_allocation_target_remains_valid_normal_semantics(
    execution_module, monkeypatch
):
    monkeypatch.setattr(execution_module, "_compute_position_size", lambda *_args: _sizing())
    monkeypatch.setattr(execution_module, "preflight", lambda *_args: (True, None))
    monkeypatch.setattr(execution_module, "_HAS_ALLOCATION_ENGINE", True)
    monkeypatch.setattr(
        execution_module, "get_target_weight_for_symbol", lambda _symbol: 0
    )
    monkeypatch.setattr(
        execution_module, "place_order", lambda *_args, **_kwargs: _filled_order()
    )
    monkeypatch.setattr(execution_module.cra_tracker, "log_trade", lambda **_kwargs: None)

    result = execution_module.execute_signals(_report())

    assert result["status"] == "COMPLETED"
    assert result["observability_failures"] == []
    assert result["executed"][0]["result"]["order_id"] == "synthetic-order-1"


def test_preflight_rejects_nested_non_dict_balance_with_typed_single_envelope(
    execution_module, monkeypatch, tmp_path
):
    balance_calls = []
    submissions = []
    monkeypatch.setattr(execution_module, "data_root", lambda: tmp_path)

    def balances(_exchange_id):
        balance_calls.append("fetch")
        if len(balance_calls) == 1:
            return {"USD": {"free": 1_000.0}}
        return {"USD": []}

    monkeypatch.setattr(execution_module, "fetch_balances", balances)
    monkeypatch.setattr(
        execution_module,
        "place_order",
        lambda *_args, **_kwargs: submissions.append("submitted") or _filled_order(),
    )

    result = execution_module.execute_signal(
        {"asset": "BTC", "action": "BUY", "confidence": 0.8}, {"BTC": 100.0}
    )

    assert balance_calls == ["fetch", "fetch"]
    assert submissions == []
    assert result["status"] == "UNAVAILABLE"
    assert result["reason_code"] == "EXECUTION_DEPENDENCY_RESULT_INVALID"
    assert result["trace_id"]
    assert result["operation"] == "fetch_balances"
    assert result["symbol"] == "BTC"
    assert result["error_type"] == "TypeError"


@pytest.mark.parametrize(
    "malformed_balances",
    [
        pytest.param({"USD": []}, id="nested-non-dict"),
        pytest.param({"USD": {"free": "quote-provider-secret"}}, id="string"),
        pytest.param({"USDT": {"free": float("nan")}}, id="nan"),
        pytest.param({"USDC": {"free": float("inf")}}, id="positive-infinity"),
        pytest.param({"USD": {"free": float("-inf")}}, id="negative-infinity"),
        pytest.param({"USD": {"free": 5e-324}}, id="subnormal"),
        pytest.param({"USD": {"free": True}}, id="bool"),
    ],
)
def test_buy_sizing_rejects_malformed_quote_balances_in_aggregate_path(
    execution_module, monkeypatch, tmp_path, malformed_balances
):
    submissions = []
    monkeypatch.setattr(execution_module, "data_root", lambda: tmp_path)
    monkeypatch.setattr(
        execution_module, "fetch_balances", lambda _exchange_id: malformed_balances
    )
    monkeypatch.setattr(
        execution_module,
        "place_order",
        lambda *_args, **_kwargs: submissions.append("submitted") or _filled_order(),
    )

    result = execution_module.execute_signals(_report())

    assert submissions == []
    assert result["status"] == "UNAVAILABLE"
    assert result["reason_code"] == "EXECUTION_DEPENDENCY_RESULT_INVALID"
    assert result["trace_id"]
    assert result["executed"] == []
    assert result["errors"] == [
        {
            "status": "UNAVAILABLE",
            "reason_code": "EXECUTION_DEPENDENCY_RESULT_INVALID",
            "trace_id": result["trace_id"],
            "operation": "fetch_balances",
            "symbol": "BTC",
            "error_type": "TypeError",
            "action": "BUY",
        }
    ]
    assert "quote-provider-secret" not in str(result)


@pytest.mark.parametrize(
    "malformed_balances",
    [
        pytest.param({"BTC": []}, id="nested-non-dict"),
        pytest.param(
            {"BTC": {"free": "base-provider-secret", "total": 0}}, id="string"
        ),
        pytest.param({"BTC": {"free": float("nan"), "total": 0}}, id="nan"),
        pytest.param({"BTC": {"free": float("inf"), "total": 0}}, id="positive-infinity"),
        pytest.param({"BTC": {"free": float("-inf"), "total": 0}}, id="negative-infinity"),
        pytest.param({"BTC": {"free": 5e-324, "total": 0}}, id="subnormal-free"),
        pytest.param({"BTC": {"free": True, "total": 0}}, id="bool-free"),
        pytest.param({"BTC": {"free": 1, "total": True}}, id="bool-total"),
    ],
)
def test_sell_sizing_rejects_malformed_base_balances_in_single_path(
    execution_module, monkeypatch, malformed_balances
):
    submissions = []
    monkeypatch.setattr(
        execution_module, "fetch_balances", lambda _exchange_id: malformed_balances
    )
    monkeypatch.setattr(
        execution_module,
        "place_order",
        lambda *_args, **_kwargs: submissions.append("submitted") or _filled_order(),
    )

    result = execution_module.execute_signal(
        {"asset": "BTC", "action": "SELL", "confidence": 0.8}, {"BTC": 100.0}
    )

    assert submissions == []
    assert result["status"] == "UNAVAILABLE"
    assert result["reason_code"] == "EXECUTION_DEPENDENCY_RESULT_INVALID"
    assert result["trace_id"]
    assert result["operation"] == "fetch_balances"
    assert result["symbol"] == "BTC"
    assert result["error_type"] == "TypeError"
    assert "base-provider-secret" not in str(result)


def _is_broad_exception_type(exception_type):
    if exception_type is None:
        return True
    if isinstance(exception_type, ast.Name):
        return exception_type.id in {"Exception", "BaseException"}
    if isinstance(exception_type, ast.Tuple):
        return any(_is_broad_exception_type(element) for element in exception_type.elts)
    return False


@pytest.mark.parametrize(
    "handler_source",
    [
        "try:\n    pass\nexcept:\n    pass\n",
        "try:\n    pass\nexcept BaseException:\n    pass\n",
        "try:\n    pass\nexcept (ValueError, Exception):\n    pass\n",
        "try:\n    pass\nexcept ((KeyError, BaseException), TypeError):\n    pass\n",
    ],
)
def test_broad_handler_classifier_recognizes_all_broad_forms(handler_source):
    handler = ast.parse(handler_source).body[0].handlers[0]
    assert _is_broad_exception_type(handler.type)


@pytest.mark.parametrize(
    "handler_source",
    [
        "try:\n    pass\nexcept ValueError:\n    pass\n",
        "try:\n    pass\nexcept (ValueError, KeyError):\n    pass\n",
    ],
)
def test_broad_handler_classifier_rejects_narrow_forms(handler_source):
    handler = ast.parse(handler_source).body[0].handlers[0]
    assert not _is_broad_exception_type(handler.type)


def test_broad_handlers_are_only_immediate_typed_boundary_conversions():
    source_path = Path(__file__).parents[1] / "execute_live.py"
    tree = ast.parse(source_path.read_text())
    parents = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    broad_handlers = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler)
        and _is_broad_exception_type(node.type)
    ]

    classifications = {}
    for handler in broad_handlers:
        parent = parents[handler]
        while parent is not None and not isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            parent = parents.get(parent)
        classifications.setdefault(parent.name if parent else "<module>", []).append(handler)

        assert isinstance(handler.type, ast.Name)
        assert handler.type.id == "Exception"
        assert len(handler.body) <= 3
        assert not any(isinstance(node, ast.Pass) for node in ast.walk(handler))
        assert any(
            isinstance(node, (ast.Raise, ast.Return))
            for statement in handler.body
            for node in ast.walk(statement)
        )

    assert classifications == {
        "_call_execution_dependency": [classifications["_call_execution_dependency"][0]],
        "_call_order_submission": [classifications["_call_order_submission"][0]],
        "_call_observability_boundary": [classifications["_call_observability_boundary"][0]],
    }


@pytest.mark.parametrize(
    "malformed_id",
    [
        pytest.param({}, id="mapping"),
        pytest.param([], id="list"),
        pytest.param(True, id="bool"),
        pytest.param("", id="empty"),
        pytest.param("   ", id="whitespace"),
        pytest.param("x" * 129, id="oversized"),
        pytest.param("unsafe\x00id", id="control"),
    ],
)
def test_malformed_order_ids_fail_closed_in_aggregate_and_single_paths(
    execution_module, monkeypatch, malformed_id
):
    submitted = []
    order = _filled_order()
    order["id"] = malformed_id
    monkeypatch.setattr(execution_module, "_compute_position_size", lambda *_args: _sizing())
    monkeypatch.setattr(execution_module, "preflight", lambda *_args: (True, None))
    monkeypatch.setattr(
        execution_module,
        "place_order",
        lambda *_args, **_kwargs: submitted.append("submitted") or order,
    )

    aggregate = execution_module.execute_signals(_report())
    single = execution_module.execute_signal(
        {"asset": "BTC", "action": "BUY", "confidence": 0.8}, {"BTC": 100.0}
    )

    assert submitted == ["submitted", "submitted"]
    assert aggregate["executed"] == []
    assert aggregate["reason_code"] == "ORDER_RESULT_INVALID"
    assert aggregate["errors"][0]["trace_id"] == aggregate["trace_id"]
    assert single["status"] == "UNAVAILABLE"
    assert single["reason_code"] == "ORDER_RESULT_INVALID"
    assert single["trace_id"]
    if isinstance(malformed_id, str) and malformed_id in {"x" * 129, "unsafe\x00id"}:
        assert malformed_id not in str(aggregate)


@pytest.mark.parametrize("safe_id", ["ccxt-safe-123", 123])
def test_safe_order_ids_are_normalized_before_persistence(execution_module, monkeypatch, safe_id):
    order = _filled_order()
    order["id"] = safe_id
    monkeypatch.setattr(execution_module, "_compute_position_size", lambda *_args: _sizing())
    monkeypatch.setattr(execution_module, "preflight", lambda *_args: (True, None))
    monkeypatch.setattr(execution_module, "place_order", lambda *_args, **_kwargs: order)
    monkeypatch.setattr(execution_module.cra_tracker, "log_trade", lambda **_kwargs: None)

    result = execution_module.execute_signals(_report())

    assert result["status"] == "COMPLETED"
    assert result["executed"][0]["result"]["order_id"] == str(safe_id)


def test_malformed_order_id_in_exit_path_does_not_mutate_exit_state(
    execution_module, monkeypatch
):
    import exchange.ccxt_bridge as bridge

    saves = []
    monkeypatch.setattr(execution_module, "_HAS_EXIT_STRATEGIES", True)
    monkeypatch.setattr(
        execution_module,
        "_load_live_positions",
        lambda: {"positions": {"BTC": {"shares": 1.0, "avg_cost": 100.0}}},
    )
    monkeypatch.setattr(execution_module, "_save_live_positions", lambda data: saves.append(data))
    monkeypatch.setattr(bridge, "fetch_ticker", lambda *_args: {"last": 120.0})
    monkeypatch.setattr(
        execution_module,
        "tiered_profit_exit",
        lambda **_kwargs: {"action": "partial_close", "close_pct": 0.5, "reason": "tier"},
    )
    order = _filled_order()
    order["id"] = []
    monkeypatch.setattr(execution_module, "place_order", lambda *_args, **_kwargs: order)

    result = execution_module.monitor_positions_for_exits()

    assert result["actions"] == []
    assert result["reason_code"] == "ORDER_RESULT_INVALID"
    assert saves == []


@pytest.mark.parametrize("bad_return", ["bad", True, float("nan"), float("inf"), -1.1])
def test_malformed_symbol_returns_fail_closed_before_cvar_or_submission(
    execution_module, monkeypatch, tmp_path, bad_return
):
    returns_file = tmp_path / "memory" / "backtest" / "returns" / "BTC_returns.json"
    returns_file.parent.mkdir(parents=True)
    returns_file.write_text(json.dumps({"daily_returns": [bad_return] * 30}))
    submitted = []
    monkeypatch.setattr(execution_module, "data_root", lambda: tmp_path)
    monkeypatch.setattr(execution_module, "fetch_balances", lambda *_args: {"USD": {"free": 1000.0}})
    monkeypatch.setattr(execution_module, "cvar_position_size", lambda *_args: pytest.fail("CVaR must not run"))
    monkeypatch.setattr(execution_module, "place_order", lambda *_args, **_kwargs: submitted.append(True))

    result = execution_module.execute_signals(_report())

    assert submitted == []
    assert result["reason_code"] == "EXECUTION_DEPENDENCY_RESULT_INVALID"
    assert result["errors"][0]["operation"] == "load_symbol_returns"
    assert result["errors"][0]["trace_id"] == result["trace_id"]


@pytest.mark.parametrize(
    "bad_sizing",
    [
        {},
        type("Sizing", (), {"quantity": True, "notional": 100.0, "capital_used_pct": 10.0, "risk_amount": 1.0, "cvar_95": 0.01})(),
        type("Sizing", (), {"quantity": 1.0, "notional": 100.0, "capital_used_pct": 10.0, "risk_amount": 0.0, "cvar_95": 0.01})(),
    ],
)
def test_malformed_cvar_result_fails_closed_in_single_path(
    execution_module, monkeypatch, tmp_path, bad_sizing
):
    returns_file = tmp_path / "memory" / "backtest" / "returns" / "BTC_returns.json"
    returns_file.parent.mkdir(parents=True)
    returns_file.write_text(json.dumps({"daily_returns": [0.01] * 30}))
    submitted = []
    monkeypatch.setattr(execution_module, "data_root", lambda: tmp_path)
    monkeypatch.setattr(execution_module, "fetch_balances", lambda *_args: {"USD": {"free": 1000.0}})
    monkeypatch.setattr(execution_module, "cvar_position_size", lambda *_args: bad_sizing)
    monkeypatch.setattr(execution_module, "place_order", lambda *_args, **_kwargs: submitted.append(True))

    aggregate = execution_module.execute_signals(_report())
    result = execution_module.execute_signal(
        {"asset": "BTC", "action": "BUY", "confidence": 0.8}, {"BTC": 100.0}
    )

    assert submitted == []
    assert aggregate["reason_code"] == "EXECUTION_DEPENDENCY_RESULT_INVALID"
    assert aggregate["errors"][0]["operation"] == "cvar_position_size"
    assert aggregate["errors"][0]["trace_id"] == aggregate["trace_id"]
    assert result["reason_code"] == "EXECUTION_DEPENDENCY_RESULT_INVALID"
    assert result["operation"] == "cvar_position_size"


def test_malformed_equity_returns_fail_closed_before_fallback_cvar_submission(
    execution_module, monkeypatch
):
    submitted = []
    tracker = ModuleType("performance_tracker")
    tracker._equity_returns = lambda: [0.01] * 29 + [True]
    monkeypatch.setitem(sys.modules, "performance_tracker", tracker)
    monkeypatch.setattr(execution_module, "fetch_balances", lambda *_args: {"USD": {"free": 1000.0}})
    monkeypatch.setattr(execution_module, "place_order", lambda *_args, **_kwargs: submitted.append(True))

    result = execution_module.execute_signals(_report())

    assert submitted == []
    assert result["reason_code"] == "EXECUTION_DEPENDENCY_RESULT_INVALID"
    assert result["errors"][0]["operation"] == "load_equity_returns"


@pytest.mark.parametrize(
    "asset",
    [
        {"symbol": "BTC", "suggestion": "HOLD", "price": 100.0, "confidence": 0.8},
        {"symbol": "BTC", "suggestion": "BUY", "price": True, "confidence": 0.8},
        {"symbol": "BTC", "suggestion": "BUY", "price": float("nan"), "confidence": 0.8},
        {"symbol": "BTC", "suggestion": "BUY", "price": 100.0, "confidence": float("inf")},
        {"symbol": "BTC", "suggestion": "BUY", "price": 100.0, "confidence": -0.1},
        {"symbol": "BTC", "suggestion": "BUY", "price": 100.0, "confidence": 0.8, "_gate_modifier": 2},
        {"symbol": "bad\x00symbol", "suggestion": "BUY", "price": 100.0, "confidence": 0.8},
    ],
)
def test_invalid_aggregate_execution_inputs_never_reach_sizing_or_submission(
    execution_module, monkeypatch, asset
):
    monkeypatch.setattr(execution_module, "_compute_position_size", lambda *_args: pytest.fail("sizing must not run"))
    monkeypatch.setattr(execution_module, "place_order", lambda *_args, **_kwargs: pytest.fail("submission must not run"))
    result = execution_module.execute_signals({"assets": [asset]})
    assert result["status"] == "UNAVAILABLE"
    assert result["reason_code"] == "EXECUTION_INPUT_INVALID"
    assert result["errors"][0]["status"] == "REJECTED"
    assert result["errors"][0]["trace_id"] == result["trace_id"]


@pytest.mark.parametrize("value", ["0.8", True, float("nan"), float("inf"), -1, 2])
def test_invalid_single_confidence_never_reaches_sizing_or_submission(
    execution_module, monkeypatch, value
):
    monkeypatch.setattr(execution_module, "_compute_position_size", lambda *_args: pytest.fail("sizing must not run"))
    monkeypatch.setattr(execution_module, "place_order", lambda *_args, **_kwargs: pytest.fail("submission must not run"))
    result = execution_module.execute_signal(
        {"asset": "BTC", "action": "BUY", "confidence": value}, {"BTC": 100.0}
    )
    assert result["status"] == "REJECTED"
    assert result["reason_code"] == "EXECUTION_INPUT_INVALID"
    assert result["trace_id"]


def test_zero_position_modifier_is_a_non_trading_rejection(execution_module, monkeypatch):
    monkeypatch.setattr(execution_module, "_compute_position_size", lambda *_args: pytest.fail("sizing must not run"))
    aggregate = execution_module.execute_signals({"assets": [{**_report()["assets"][0], "_gate_modifier": 0}]})
    single = execution_module.execute_signal(
        {"asset": "BTC", "action": "BUY", "confidence": 0.8, "position_modifier": 0}, {"BTC": 100.0}
    )
    assert aggregate["skipped"][0]["reason_code"] == "POSITION_MODIFIER_ZERO"
    assert single["reason_code"] == "POSITION_MODIFIER_ZERO"
    assert single["trace_id"]


@pytest.mark.parametrize(
    "durable_state",
    [
        {"positions": []},
        {"positions": {"BTC": {"shares": "bad", "avg_cost": 100.0}}},
        {"positions": {"BTC": {"shares": True, "avg_cost": 100.0}}},
        {"positions": {"BTC": {"shares": 1.0, "avg_cost": float("nan")}}},
        {"positions": {"BTC": {"shares": 1.0, "avg_cost": 100.0}}, "tiered_exits": []},
        {"positions": {"BTC": {"shares": 1.0, "avg_cost": 100.0}}, "tiered_exits": {"BTC": {"cumulative_closed": 1.0}}},
    ],
)
def test_malformed_durable_position_state_preserves_bytes_and_blocks_execution(
    execution_module, monkeypatch, durable_state
):
    execution_module.LIVE_POSITIONS_FILE.write_text(json.dumps(durable_state, default=str))
    original = execution_module.LIVE_POSITIONS_FILE.read_bytes()
    monkeypatch.setattr(execution_module, "_compute_position_size", lambda *_args: pytest.fail("sizing must not run"))
    monkeypatch.setattr(execution_module, "place_order", lambda *_args, **_kwargs: pytest.fail("submission must not run"))

    result = execution_module.execute_signal(
        {"asset": "BTC", "action": "BUY", "confidence": 0.8}, {"BTC": 100.0}
    )

    assert result["status"] == "UNAVAILABLE"
    assert result["reason_code"] == "EXECUTION_STATE_UNAVAILABLE"
    assert execution_module.LIVE_POSITIONS_FILE.read_bytes() == original


@pytest.mark.parametrize(
    "bad_exit",
    [
        {},
        {"action": "sell", "close_pct": 0.5, "reason": "bad"},
        {"action": "partial_close", "close_pct": True, "reason": "bad"},
        {"action": "partial_close", "close_pct": 0.5, "reason": "bad\x00reason"},
        {"action": "full_close", "close_pct": 0.5, "reason": "bad"},
    ],
)
def test_malformed_tiered_exit_result_blocks_sell_and_state_mutation(
    execution_module, monkeypatch, bad_exit
):
    import exchange.ccxt_bridge as bridge

    saved = []
    submitted = []
    monkeypatch.setattr(execution_module, "_HAS_EXIT_STRATEGIES", True)
    monkeypatch.setattr(
        execution_module,
        "_load_live_positions",
        lambda: {"positions": {"BTC": {"shares": 1.0, "avg_cost": 100.0}}},
    )
    monkeypatch.setattr(execution_module, "_save_live_positions", lambda data: saved.append(data))
    monkeypatch.setattr(bridge, "fetch_ticker", lambda *_args: {"last": 120.0})
    monkeypatch.setattr(execution_module, "tiered_profit_exit", lambda **_kwargs: bad_exit)
    monkeypatch.setattr(execution_module, "place_order", lambda *_args, **_kwargs: submitted.append(True))

    result = execution_module.monitor_positions_for_exits()

    assert submitted == []
    assert saved == []
    assert result["reason_code"] == "EXECUTION_DEPENDENCY_RESULT_INVALID"
    assert result["errors"][0]["operation"] == "tiered_profit_exit"


def test_oversized_durable_position_blocks_tiered_sell(
    execution_module, monkeypatch
):
    import exchange.ccxt_bridge as bridge

    submitted = []
    monkeypatch.setattr(execution_module, "_HAS_EXIT_STRATEGIES", True)
    monkeypatch.setattr(
        execution_module,
        "_load_live_positions",
        lambda: {
            "positions": {
                "BTC": {
                    "shares": execution_module._MAX_CVAR_QUANTITY * 2.0,
                    "avg_cost": 100.0,
                }
            }
        },
    )
    monkeypatch.setattr(bridge, "fetch_ticker", lambda *_args: {"last": 120.0})
    monkeypatch.setattr(
        execution_module,
        "tiered_profit_exit",
        lambda **_kwargs: {
            "action": "full_close",
            "close_pct": 1.0,
            "reason": "close",
        },
    )
    monkeypatch.setattr(
        execution_module,
        "place_order",
        lambda *_args, **_kwargs: submitted.append(True),
    )

    result = execution_module.monitor_positions_for_exits()

    assert submitted == []
    assert result["status"] == "UNAVAILABLE"
    assert result["reason_code"] == "EXECUTION_STATE_UNAVAILABLE"


def test_subnormal_durable_position_blocks_tiered_sell(
    execution_module, monkeypatch
):
    submitted = []
    monkeypatch.setattr(execution_module, "_HAS_EXIT_STRATEGIES", True)
    monkeypatch.setattr(
        execution_module,
        "_load_live_positions",
        lambda: {
            "positions": {
                "BTC": {
                    "shares": float.fromhex("0x0.0000000000001p-1022"),
                    "avg_cost": 100.0,
                }
            }
        },
    )
    monkeypatch.setattr(
        execution_module,
        "place_order",
        lambda *_args, **_kwargs: submitted.append(True),
    )

    result = execution_module.monitor_positions_for_exits()

    assert submitted == []
    assert result["status"] == "UNAVAILABLE"
    assert result["reason_code"] == "EXECUTION_STATE_UNAVAILABLE"


def test_tiered_exit_below_minimum_notional_never_submits_or_mutates_state(
    execution_module, monkeypatch
):
    import exchange.ccxt_bridge as bridge

    submitted = []
    saved = []
    monkeypatch.setattr(execution_module, "_HAS_EXIT_STRATEGIES", True)
    monkeypatch.setattr(
        execution_module,
        "_load_live_positions",
        lambda: {
            "positions": {"BTC": {"shares": 0.01, "avg_cost": 100.0}},
            "tiered_exits": {},
        },
    )
    monkeypatch.setattr(
        execution_module, "_save_live_positions", lambda data: saved.append(data)
    )
    monkeypatch.setattr(bridge, "fetch_ticker", lambda *_args: {"last": 100.0})
    monkeypatch.setattr(
        execution_module,
        "tiered_profit_exit",
        lambda **_kwargs: {
            "action": "partial_close",
            "close_pct": 0.5,
            "reason": "tiny tier",
        },
    )
    monkeypatch.setattr(
        execution_module,
        "place_order",
        lambda *_args, **_kwargs: submitted.append(True),
    )

    result = execution_module.monitor_positions_for_exits()

    assert submitted == []
    assert saved == []
    assert result["status"] == "PARTIAL"
    assert result["reason_code"] == "EXECUTION_DEPENDENCY_RESULT_INVALID"
    assert result["errors"][0]["operation"] == "validate_order_notional"


@pytest.mark.parametrize(
    "amount",
    [
        float("nan"),
        float("inf"),
        1e308,
        float.fromhex("0x0.0000000000001p-1022"),
    ],
)
def test_preflight_rejects_nonfinite_or_unbounded_amount(
    execution_module, monkeypatch, amount
):
    monkeypatch.setattr(
        execution_module,
        "fetch_balances",
        lambda *_args: {"USD": {"free": 1000.0}},
    )

    ok, reason = execution_module.preflight("binance", "BTC/USD", "buy", amount)

    assert ok is False
    assert reason == "Invalid order amount"


def test_execute_signal_rejects_subnormal_price_before_sizing(
    execution_module, monkeypatch
):
    sizing_calls = []
    monkeypatch.setattr(
        execution_module,
        "_compute_position_size",
        lambda *_args, **_kwargs: sizing_calls.append(True),
    )

    result = execution_module.execute_signal(
        {"asset": "BTC", "action": "BUY", "confidence": 0.8},
        {"BTC": float.fromhex("0x0.0000000000001p-1022")},
    )

    assert sizing_calls == []
    assert result["status"] == "REJECTED"
    assert result["reason_code"] == "EXECUTION_INPUT_INVALID"


def test_execute_signals_rejects_subnormal_price_before_sizing(
    execution_module, monkeypatch
):
    sizing_calls = []
    report = _report()
    report["assets"][0]["price"] = float.fromhex(
        "0x0.0000000000001p-1022"
    )
    monkeypatch.setattr(
        execution_module,
        "_compute_position_size",
        lambda *_args, **_kwargs: sizing_calls.append(True),
    )

    result = execution_module.execute_signals(report)

    assert sizing_calls == []
    assert result["reason_code"] == "EXECUTION_INPUT_INVALID"


def test_non_object_symbol_returns_are_typed_as_invalid_result(
    execution_module, monkeypatch, tmp_path
):
    returns_file = tmp_path / "memory" / "backtest" / "returns" / "BTC_returns.json"
    returns_file.parent.mkdir(parents=True)
    returns_file.write_text(json.dumps([0.01] * 30))
    submitted = []
    monkeypatch.setattr(execution_module, "data_root", lambda: tmp_path)
    monkeypatch.setattr(
        execution_module, "fetch_balances", lambda *_args: {"USD": {"free": 1000.0}}
    )
    monkeypatch.setattr(
        execution_module, "place_order", lambda *_args, **_kwargs: submitted.append(True)
    )

    result = execution_module.execute_signals(_report())

    assert submitted == []
    assert result["reason_code"] == "EXECUTION_DEPENDENCY_RESULT_INVALID"
    assert result["errors"][0]["operation"] == "load_symbol_returns"
    assert result["errors"][0]["trace_id"] == result["trace_id"]


def test_cvar_result_attribute_failure_is_typed_invalid_result(
    execution_module, monkeypatch, tmp_path
):
    class MalformedSizing:
        @property
        def quantity(self):
            raise RuntimeError("sizing-provider-secret")

        notional = 100.0
        capital_used_pct = 10.0
        risk_amount = 1.0
        cvar_95 = 0.01

    returns_file = tmp_path / "memory" / "backtest" / "returns" / "BTC_returns.json"
    returns_file.parent.mkdir(parents=True)
    returns_file.write_text(json.dumps({"daily_returns": [0.01] * 30}))
    monkeypatch.setattr(execution_module, "data_root", lambda: tmp_path)
    monkeypatch.setattr(
        execution_module, "fetch_balances", lambda *_args: {"USD": {"free": 1000.0}}
    )
    monkeypatch.setattr(
        execution_module, "cvar_position_size", lambda *_args: MalformedSizing()
    )
    monkeypatch.setattr(
        execution_module,
        "place_order",
        lambda *_args, **_kwargs: pytest.fail("submission must not run"),
    )

    result = execution_module.execute_signal(
        {"asset": "BTC", "action": "BUY", "confidence": 0.8}, {"BTC": 100.0}
    )

    assert result["status"] == "UNAVAILABLE"
    assert result["reason_code"] == "EXECUTION_DEPENDENCY_RESULT_INVALID"
    assert result["operation"] == "cvar_position_size"
    assert "sizing-provider-secret" not in str(result)


@pytest.mark.parametrize(
    ("route", "field"),
    [("aggregate", "rationale"), ("single", "reasoning")],
)
def test_unbounded_execution_metadata_is_rejected_before_sizing(
    execution_module, monkeypatch, route, field
):
    monkeypatch.setattr(
        execution_module,
        "_compute_position_size",
        lambda *_args: pytest.fail("sizing must not run"),
    )
    monkeypatch.setattr(
        execution_module,
        "place_order",
        lambda *_args, **_kwargs: pytest.fail("submission must not run"),
    )
    if route == "aggregate":
        asset = {**_report()["assets"][0], field: {"secret": "metadata-secret"}}
        result = execution_module.execute_signals({"assets": [asset]})
        failure = result["errors"][0]
    else:
        signal = {
            "asset": "BTC",
            "action": "BUY",
            "confidence": 0.8,
            field: {"secret": "metadata-secret"},
        }
        result = execution_module.execute_signal(signal, {"BTC": 100.0})
        failure = result

    assert failure["status"] == "REJECTED"
    assert failure["reason_code"] == "EXECUTION_INPUT_INVALID"
    assert failure["trace_id"]
    assert "metadata-secret" not in str(result)


def test_post_ack_state_revalidation_preserves_evidence_without_resubmission(
    execution_module, monkeypatch
):
    durable_state: dict = {
        "positions": {"BTC": {"shares": 1.0, "avg_cost": 100.0}}
    }
    submitted = []
    saved = []
    monkeypatch.setattr(execution_module, "_load_live_positions", lambda: durable_state)
    monkeypatch.setattr(execution_module, "_compute_position_size", lambda *_args: _sizing())
    monkeypatch.setattr(execution_module, "preflight", lambda *_args: (True, None))
    monkeypatch.setattr(
        execution_module,
        "_save_live_positions",
        lambda data: saved.append(json.loads(json.dumps(data))),
    )

    def submit_once(*_args, **_kwargs):
        submitted.append("submitted")
        durable_state["positions"]["BTC"]["shares"] = "post-ack-secret"
        return _filled_order()

    monkeypatch.setattr(execution_module, "place_order", submit_once)

    result = execution_module.execute_signal(
        {"asset": "BTC", "action": "BUY", "confidence": 0.8}, {"BTC": 100.0}
    )

    assert submitted == ["submitted"]
    assert saved == []
    assert result["status"] == "PARTIAL"
    assert result["reason_code"] == "EXECUTION_STATE_PERSISTENCE_FAILED"
    assert result["execution_evidence"]["order_id"] == "synthetic-order-1"
    assert result["persistence_failures"][0]["operation"] == "update_live_positions_after_fill"
    assert "post-ack-secret" not in str(result)


def test_machine_readable_broad_handler_inventory_has_exact_tracked_coverage():
    root = Path(__file__).resolve().parents[3]
    inventory_path = root / "docs" / "implementation" / "foundation-exception-inventory.md"
    specification = importlib.util.spec_from_file_location(
        "broad_handler_inventory",
        root / "scripts" / "check_broad_handler_inventory.py",
    )
    assert specification is not None and specification.loader is not None
    tool = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(tool)

    tool.check_inventory(root, inventory_path)
    documented = tool.read_documented_rows(inventory_path)
    assert {row[3] for row in documented} <= {
        "PRODUCTION_CRITICAL", "INTENTIONAL_CONTAINMENT", "TESTS", "TOOLING_MIGRATION"
    }
    assert {row[4] for row in documented} <= {
        "RAISE", "RETURN", "PASS", "CONTINUE", "OTHER"
    }
    execute_rows = [
        row for row in documented if row[0] == "legacy/research-backend/execute_live.py"
    ]
    assert len(execute_rows) == 3
    assert [row[4] for row in execute_rows] == ["RAISE", "RAISE", "RETURN"]
    assert {row[2:4] for row in execute_rows} == {
        ("Exception", "INTENTIONAL_CONTAINMENT")
    }
