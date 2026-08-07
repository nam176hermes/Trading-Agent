from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from test_launcher_protocol import _simulation_fixture
from test_launcher_protocol import _simulation_request


_GOLDENS = {
    "long-accounting": ("9a348cbd911852be08644345fd9c095b657c694280064c9b6d935eee16f07e81", "2", "0.2", "2", "1", "2", "1"),
    "short-accounting": ("f446c7ef6c7b65d2a016f6a156b7468dfd40ea452a18c4f3bd79f521531e51de", "-2", "0.198", "-4", "1", "2", "1"),
    "partial-fill": ("7c1a628d952b38fd4fff3df2e954e30ee70f43a769d48a51d771c39522d0c7f3", "1", "0.1", "1", "1", "2", "1"),
    "same-bar-stop-take-profit": ("31631b6e27d8441d69999436bc564e44f2e88279eb1fce87e5db1b84e09c04c0", "0", "0.198", "0", "2", "5", "2"),
    "stale-quote": ("27d1ef994a0081cbf2b0839a4624ca1f020911af7a88ab1683adace3a0a91460", "0", "0", "0", "0", "2", "0"),
    "zero-liquidity": ("b9e2e9af09fa6cfb58912de49b7502180d7dd9f63c1c263c1c4c3baa21b5147f", "0", "0", "0", "0", "2", "0"),
    "session-boundary": ("4d345ad6a4130a8394912579c1f1aa1766895778dade42d5ea565ccd7b5a529c", "1", "0.102", "0", "1", "3", "1"),
    "event-digest": ("00e144a68cf61d8e0c0bfc6ee413a139403c583e4a4eea4628cf3b8ede6b320b", "1", "0.1", "1", "1", "2", "1"),
}
_START = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
_END = datetime(2026, 8, 5, 12, 30, tzinfo=UTC)
_LAUNCHER = Path("engines/nautilus/launcher/nautilus_backtest.py")


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
    artifacts = _simulation_fixture(scenario_id)
    request = _simulation_request(artifacts)
    raw_request = canonical_json_bytes(request)
    accepted = launcher._validate_request(
        json.loads(raw_request), raw_request, profile="execution-simulation"
    )
    fixture = launcher.validate_simulation_fixture_inputs(accepted, artifacts)
    result = launcher.run_execution_simulation(fixture)
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
