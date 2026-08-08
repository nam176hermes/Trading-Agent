from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from packages.nautilus_backtest import (
    build_canonical_simulation_fixture,
    build_simulation_envelope,
)


def _simulation_fixture(scenario_id: str) -> tuple[bytes, bytes, bytes, bytes, bytes]:
    return build_canonical_simulation_fixture(scenario_id).artifacts


_GOLDENS = {
    "long-accounting": ("c05decc60280b4394e641392dd10a96e72c82da906b7a7a4abb70de1c9f46c78", "2", "0.2", "2", "1", "2", "1"),
    "short-accounting": ("b13ee6e0432aac3079c3df4cc9447fda37c11bd0adb49b18ef200998941233e6", "-2", "0.198", "-4", "1", "2", "1"),
    "partial-fill": ("10719ca90e0d153ac8d12c4000e679136a7cef0042672ba07deee3c6272b0724", "1", "0.1", "1", "1", "2", "1"),
    "same-bar-stop-take-profit": ("4267c0354ac5b8a03a73c40a39c830f77b33972171a24bfd4db2adc617d1a916", "0", "0.198", "0", "2", "5", "2"),
    "stale-quote": ("27d1ef994a0081cbf2b0839a4624ca1f020911af7a88ab1683adace3a0a91460", "0", "0", "0", "0", "2", "0"),
    "zero-liquidity": ("b9e2e9af09fa6cfb58912de49b7502180d7dd9f63c1c263c1c4c3baa21b5147f", "0", "0", "0", "0", "2", "0"),
    "session-boundary": ("bbb7995229d7286592cacabf34e6e24117accc4993f7e12b879b621a01d58e48", "1", "0.102", "0", "1", "3", "1"),
    "event-digest": ("31ca501f78a3ac250c0fc7d7d8d38d9fb4acbb51bae7c3b15d39c311082c6baa", "1", "0.1", "1", "1", "2", "1"),
}
_START = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
_END = datetime(2026, 8, 5, 12, 30, tzinfo=UTC)
_LAUNCHER = Path("engines/nautilus/launcher/nautilus_backtest.py")


class _Amount:
    def __init__(self, value: str) -> None:
        self._value = Decimal(value)

    def as_decimal(self) -> Decimal:
        return self._value


def _balance(value: str) -> SimpleNamespace:
    amount = _Amount(value)
    return SimpleNamespace(total=amount, locked=_Amount("0"), free=amount)


_SEALED_RESULT_FACTS = {
    "long-accounting": {
        "balances": {"BTC": "2", "USDT": "999799.8"},
        "events": [
            {"event_type": "order-created", "quantity": "2", "sequence": 0},
            {"event_time": "2026-08-05T12:00:00Z", "event_type": "fill", "price": "100", "quantity": "2", "sequence": 1},
        ],
        "iterations": 1, "orders": 1, "fills": 1, "positions": 1,
        "filled": "2", "remaining": "0", "position": "2", "average": "100",
        "fees": "0.2", "realized": "0", "unrealized": "2",
    },
    "short-accounting": {
        "balances": {"BTC": "0", "USDT": "1000197.802"},
        "events": [
            {"event_type": "order-created", "quantity": "-2", "sequence": 0},
            {"event_time": "2026-08-05T12:00:00Z", "event_type": "fill", "price": "99", "quantity": "-2", "sequence": 1},
        ],
        "iterations": 1, "orders": 1, "fills": 1, "positions": 1,
        "filled": "-2", "remaining": "0", "position": "-2", "average": "99",
        "fees": "0.198", "realized": "0", "unrealized": "-4",
    },
    "partial-fill": {
        "balances": {"BTC": "1", "USDT": "999899.9"},
        "events": [
            {"event_type": "order-created", "quantity": "3", "sequence": 0},
            {"event_time": "2026-08-05T12:00:00Z", "event_type": "fill", "price": "100", "quantity": "1", "sequence": 1},
        ],
        "iterations": 1, "orders": 1, "fills": 1, "positions": 1,
        "filled": "1", "remaining": "2", "position": "1", "average": "100",
        "fees": "0.1", "realized": "0", "unrealized": "1",
    },
    "same-bar-stop-take-profit": {
        "balances": {"BTC": "0", "USDT": "999997.802"},
        "events": [
            {"event_type": "order-created", "quantity": "1", "sequence": 0},
            {"event_time": "2026-08-05T12:00:00Z", "event_type": "fill", "price": "100", "quantity": "1", "sequence": 1},
            {"event_type": "exit-order-created", "reason": "stop", "sequence": 2},
            {"event_time": "2026-08-05T12:00:00Z", "event_type": "fill", "price": "98", "quantity": "-1", "sequence": 3},
            {"event_type": "position-closed", "sequence": 4},
        ],
        "iterations": 1, "orders": 2, "fills": 2, "positions": 1,
        "filled": "1", "remaining": "0", "position": "0", "average": "100",
        "fees": "0.198", "realized": "-2", "unrealized": "0",
    },
    "stale-quote": {
        "balances": {"USDT": "1000000"},
        "events": [
            {"event_type": "order-created", "quantity": "1", "sequence": 0},
            {"event_type": "quote-rejected", "market_sequence": 1, "reason": "stale", "sequence": 1},
        ],
        "iterations": 1, "orders": 0, "fills": 0, "positions": 0,
        "filled": "0", "remaining": "1", "position": "0", "average": "0",
        "fees": "0", "realized": "0", "unrealized": "0",
    },
    "zero-liquidity": {
        "balances": {"USDT": "1000000"},
        "events": [
            {"event_type": "order-created", "quantity": "1", "sequence": 0},
            {"event_type": "liquidity-rejected", "market_sequence": 1, "reason": "zero", "sequence": 1},
        ],
        "iterations": 1, "orders": 0, "fills": 0, "positions": 0,
        "filled": "0", "remaining": "1", "position": "0", "average": "0",
        "fees": "0", "realized": "0", "unrealized": "0",
    },
    "session-boundary": {
        "balances": {"BTC": "1", "USDT": "999897.898"},
        "events": [
            {"event_type": "order-created", "quantity": "1", "sequence": 0},
            {"event_type": "session-closed", "market_sequence": 1, "sequence": 1},
            {"event_time": "2026-08-05T12:01:00Z", "event_type": "fill", "price": "102", "quantity": "1", "sequence": 2},
        ],
        "iterations": 2, "orders": 1, "fills": 1, "positions": 1,
        "filled": "1", "remaining": "0", "position": "1", "average": "102",
        "fees": "0.102", "realized": "0", "unrealized": "0",
    },
    "event-digest": {
        "balances": {"BTC": "1", "USDT": "999899.9"},
        "events": [
            {"event_type": "order-created", "quantity": "1", "sequence": 0},
            {"event_time": "2026-08-05T12:00:00Z", "event_type": "fill", "price": "100", "quantity": "1", "sequence": 1},
        ],
        "iterations": 1, "orders": 1, "fills": 1, "positions": 1,
        "filled": "1", "remaining": "0", "position": "1", "average": "100",
        "fees": "0.1", "realized": "0", "unrealized": "1",
    },
}


def _launcher_module():
    spec = importlib.util.spec_from_file_location("reference_boundary_launcher", _LAUNCHER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _scenario(scenario_id: str):
    from packages.nautilus_backtest.scenarios import BacktestScenarioV1

    scenario, catalog, strategy, market = _simulation_fixture(scenario_id)[4], *_simulation_fixture(scenario_id)[1:4]
    return BacktestScenarioV1.from_mounted_artifacts(
        scenario_bytes=scenario, catalog_bytes=catalog, strategy_bytes=strategy, market_data_bytes=market
        , start_time=_START, end_time=_END
    )


@pytest.mark.parametrize("scenario_id", tuple(_GOLDENS))
def test_reference_oracle_has_fixed_golden_outcome_for_each_scenario(scenario_id: str) -> None:
    from packages.nautilus_backtest.reference import calculate_reference_outcome

    expected_digest, position, fees, unrealized, fills, events, orders = _GOLDENS[scenario_id]
    outcome = calculate_reference_outcome(_scenario(scenario_id))

    assert outcome.event_digest == expected_digest
    assert str(outcome.position_quantity) == position
    assert outcome.fees == Decimal(fees)
    assert outcome.unrealized_pnl == Decimal(unrealized)
    assert outcome.total_fills == int(fills)
    assert outcome.total_events == int(events)
    assert outcome.total_orders == int(orders)


@pytest.mark.parametrize("scenario_id", tuple(_GOLDENS))
def test_launcher_event_equals_independent_reference_and_root_validator(
    scenario_id: str,
) -> None:
    from packages.engine_contracts import EngineEventEnvelope, canonical_json_bytes
    from packages.nautilus_backtest.reference import calculate_reference_outcome
    from packages.nautilus_backtest.result import validate_isolated_simulation_result

    launcher = _launcher_module()
    canonical_fixture = build_canonical_simulation_fixture(scenario_id)
    artifacts = canonical_fixture.artifacts
    request = build_simulation_envelope(canonical_fixture)
    raw_request = canonical_json_bytes(request)
    accepted = launcher._validate_request(
        json.loads(raw_request), raw_request, profile="execution-simulation"
    )
    facts = _SEALED_RESULT_FACTS[scenario_id]
    account = SimpleNamespace(
        balances=lambda: {
            currency: _balance(value)
            for currency, value in facts["balances"].items()
        }
    )
    balance_count = launcher._account_balance_count(account)
    record = launcher._canonical_nautilus_result_record(
        strategy_events=facts["events"],
        iterations=facts["iterations"],
        order_count=facts["orders"],
        fill_count=facts["fills"],
        filled_quantity=Decimal(facts["filled"]),
        position_count=facts["positions"],
        position_quantity=Decimal(facts["position"]),
        average_entry_price=Decimal(facts["average"]),
        realized_pnl=Decimal(facts["realized"]),
        unrealized_pnl=Decimal(facts["unrealized"]),
        account_balance_count=balance_count,
        commissions=Decimal(facts["fees"]),
    )
    result = {
        "scenario_id": scenario_id,
        "event_digest": hashlib.sha256(
            launcher._canonical_json_bytes(record)
        ).hexdigest(),
        "iterations": facts["iterations"],
        "total_events": len(facts["events"]),
        "total_orders": facts["orders"],
        "total_fills": facts["fills"],
        "total_positions": facts["positions"],
        "filled_quantity": facts["filled"],
        "remaining_quantity": facts["remaining"],
        "position_quantity": facts["position"],
        "average_entry_price": facts["average"],
        "fees": facts["fees"],
        "realized_pnl": facts["realized"],
        "unrealized_pnl": facts["unrealized"],
        "stop_take_profit_precedence": "stop-first",
    }
    event = EngineEventEnvelope.model_validate_json(
        launcher._canonical_json_bytes(
            launcher._simulation_event(accepted, artifacts, result)
        )
    )
    expected = calculate_reference_outcome(_scenario(scenario_id))

    validated = validate_isolated_simulation_result(request, event, expected)

    assert validated.event_digest == result["event_digest"]
    assert validated.total_orders == result["total_orders"]
    assert validated.realized_pnl == Decimal(str(result["realized_pnl"]))


@pytest.mark.parametrize("scenario_id", ("stale-quote", "zero-liquidity"))
def test_reference_counts_no_order_when_strategy_never_submits(
    scenario_id: str,
) -> None:
    from packages.nautilus_backtest.reference import calculate_reference_outcome

    assert calculate_reference_outcome(_scenario(scenario_id)).total_orders == 0


@pytest.mark.parametrize("mutation", ("fee", "slippage"))
def test_reference_oracle_changes_deterministically_when_execution_input_changes(mutation: str) -> None:
    from packages.nautilus_backtest.reference import calculate_reference_outcome

    baseline = _scenario("event-digest")
    raw = json.loads(baseline.mounted_bytes)
    if mutation == "fee":
        raw["fee_rate"] = "0.002"
    else:
        raw["slippage_bps"] = "1"
    changed = json.dumps(raw, separators=(",", ":"), sort_keys=True).encode()
    scenario = type(baseline).from_mounted_artifacts(
        scenario_bytes=changed,
        catalog_bytes=_simulation_fixture("event-digest")[1],
        strategy_bytes=_simulation_fixture("event-digest")[2],
        market_data_bytes=_simulation_fixture("event-digest")[3],
        start_time=_START,
        end_time=_END,
    )

    assert calculate_reference_outcome(scenario) != calculate_reference_outcome(baseline)
