from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from test_launcher_protocol import _simulation_fixture


_GOLDENS = {
    "long-accounting": ("39258ec4143a8f789d7cda7e1c709e72df49fd38b228e88ece9f79d12083eab2", "2", "0.2", "2", "1", "2"),
    "short-accounting": ("95329534a5527307dc1a941bdec6cbffd0aeefde2c688bddd598a8a4c30ca8ba", "-2", "0.198", "-4", "1", "2"),
    "partial-fill": ("5b732a9c8e5430ae9f4774fb217c19146a9a36da6dbb475fa36b229d83a8931f", "1", "0.1", "1", "1", "2"),
    "same-bar-stop-take-profit": ("2ff844069040c8cffeee31d02d1d3ee0bd0aeffd1e86ccb33fa1e59745af4d8a", "0", "0.198", "0", "2", "5"),
    "stale-quote": ("7ed8afc414a08884f1880a0fa6641cb3330f19876a404fa17300424e82167371", "0", "0", "0", "0", "2"),
    "zero-liquidity": ("803c9a26d5ab16800f1743696ca1929639730ae6c84f26c3bf5565d4147cb06c", "0", "0", "0", "0", "2"),
    "session-boundary": ("e358a04095cab4ddc9fe6fd14e120d588ee47377fa859fa751e4855a0bf8e1bb", "1", "0.102", "0", "1", "3"),
    "event-digest": ("30b6de71b9d7a69f8e1038d1584efecd3c2bdfe4a944303a479c4680f078cd33", "1", "0.1", "1", "1", "2"),
}
_START = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
_END = datetime(2026, 8, 5, 12, 30, tzinfo=UTC)


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

    expected_digest, position, fees, unrealized, fills, events = _GOLDENS[scenario_id]
    outcome = calculate_reference_outcome(_scenario(scenario_id))

    assert outcome.event_digest == expected_digest
    assert str(outcome.position_quantity) == position
    assert outcome.fees == Decimal(fees)
    assert outcome.unrealized_pnl == Decimal(unrealized)
    assert outcome.total_fills == int(fills)
    assert outcome.total_events == int(events)


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
