from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import pytest

from test_launcher_protocol import _simulation_fixture


_START = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
_END = datetime(2026, 8, 5, 12, 30, tzinfo=UTC)


def _canonical(value: object) -> bytes:
    return json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True).encode()


def _mounted(scenario_id: str = "event-digest") -> tuple[bytes, bytes, bytes, bytes]:
    _configuration, catalog, strategy, market, scenario = _simulation_fixture(scenario_id)
    return scenario, catalog, strategy, market


def test_root_scenario_reconstructs_exact_mounted_bytes_and_bindings() -> None:
    from packages.nautilus_backtest.scenarios import BacktestScenarioV1

    scenario_bytes, catalog, strategy, market = _mounted()
    scenario = BacktestScenarioV1.from_mounted_artifacts(
        scenario_bytes=scenario_bytes,
        catalog_bytes=catalog,
        strategy_bytes=strategy,
        market_data_bytes=market,
        start_time=_START,
        end_time=_END,
    )

    assert scenario.mounted_bytes == scenario_bytes
    assert scenario.scenario_digest == hashlib.sha256(scenario_bytes).hexdigest()
    assert scenario.to_mounted_bytes() == scenario_bytes


@pytest.mark.parametrize("mutation", ["float", "unknown", "binding", "identity", "duplicate"])
def test_root_scenario_fails_closed_on_unsealed_semantics(mutation: str) -> None:
    from packages.nautilus_backtest.scenarios import BacktestScenarioError, BacktestScenarioV1

    scenario_bytes, catalog, strategy, market = _mounted()
    if mutation == "duplicate":
        scenario_bytes = scenario_bytes.replace(b'{"catalog_sha256":', b'{"scenario_id":"event-digest","catalog_sha256":')
    else:
        value = json.loads(scenario_bytes)
        if mutation == "float":
            value["fee_rate"] = 0.001
        elif mutation == "unknown":
            value["unexpected"] = True
        elif mutation == "binding":
            value["strategy_sha256"] = "0" * 64
        else:
            value["schema_version"] = "nautilus-execution-scenario-v9"
        scenario_bytes = _canonical(value)

    with pytest.raises(BacktestScenarioError):
        BacktestScenarioV1.from_mounted_artifacts(
            scenario_bytes=scenario_bytes,
            catalog_bytes=catalog,
            strategy_bytes=strategy,
            market_data_bytes=market,
            start_time=_START,
            end_time=_END,
        )


@pytest.mark.parametrize("mutation", ("sub-cent-price", "unordered-time", "forbidden-trigger"))
def test_root_scenario_rejects_launcher_invalid_semantics(mutation: str) -> None:
    """Changing this grammar check would admit inputs the launcher rejects."""
    from packages.nautilus_backtest.scenarios import BacktestScenarioError, BacktestScenarioV1

    configuration, catalog, strategy, market, scenario_bytes = _simulation_fixture("event-digest")
    del configuration
    scenario = json.loads(scenario_bytes)
    if mutation == "sub-cent-price":
        scenario["events"][0]["ask"] = "100.001"
    elif mutation == "unordered-time":
        scenario["events"].append({**scenario["events"][0], "sequence": 2, "event_time": "2026-08-05T11:59:00Z"})
    else:
        scenario["stop_price"] = "99"
    scenario_bytes = _canonical(scenario)

    with pytest.raises(BacktestScenarioError):
        BacktestScenarioV1.from_mounted_artifacts(
            scenario_bytes=scenario_bytes,
            catalog_bytes=catalog,
            strategy_bytes=strategy,
            market_data_bytes=market,
            start_time=_START,
            end_time=_END,
        )


def test_root_scenario_rejects_slippage_derived_price_outside_nautilus_bound() -> None:
    """Removing the derived bound would admit a scenario the launcher rejects."""
    from packages.nautilus_backtest.scenarios import BacktestScenarioError, BacktestScenarioV1

    _configuration, catalog, strategy, market, scenario_bytes = _simulation_fixture(
        "event-digest"
    )
    scenario = json.loads(scenario_bytes)
    scenario["events"][0]["ask"] = "17014118346046"
    scenario["slippage_bps"] = "1"

    with pytest.raises(BacktestScenarioError, match="executable entry price"):
        BacktestScenarioV1.from_mounted_artifacts(
            scenario_bytes=_canonical(scenario),
            catalog_bytes=catalog,
            strategy_bytes=strategy,
            market_data_bytes=market,
            start_time=_START,
            end_time=_END,
        )


@pytest.mark.parametrize("artifact", ("catalog", "market"))
def test_root_scenario_rejects_launcher_invalid_catalog_or_market_contract(
    artifact: str,
) -> None:
    """Replacing a mounted data contract must fail beyond its hash binding."""
    from packages.nautilus_backtest.scenarios import BacktestScenarioError, BacktestScenarioV1

    _configuration, catalog, strategy, market, scenario_bytes = _simulation_fixture(
        "event-digest"
    )
    scenario = json.loads(scenario_bytes)
    if artifact == "catalog":
        catalog = _canonical({"catalog": "fixed"})
        scenario["catalog_sha256"] = hashlib.sha256(catalog).hexdigest()
    else:
        market = b'{"close":"1"}\n'
        scenario["market_data_sha256"] = hashlib.sha256(market).hexdigest()

    with pytest.raises(BacktestScenarioError):
        BacktestScenarioV1.from_mounted_artifacts(
            scenario_bytes=_canonical(scenario),
            catalog_bytes=catalog,
            strategy_bytes=strategy,
            market_data_bytes=market,
            start_time=_START,
            end_time=_END,
        )


def test_root_scenario_rejects_event_outside_command_window() -> None:
    """Ignoring the command window would admit an unlaunchable fixture."""
    from packages.nautilus_backtest.scenarios import BacktestScenarioError, BacktestScenarioV1

    scenario_bytes, catalog, strategy, market = _mounted()

    with pytest.raises(BacktestScenarioError, match="command window"):
        BacktestScenarioV1.from_mounted_artifacts(
            scenario_bytes=scenario_bytes,
            catalog_bytes=catalog,
            strategy_bytes=strategy,
            market_data_bytes=market,
            start_time=datetime(2026, 8, 5, 12, 1, tzinfo=UTC),
            end_time=_END,
        )


def test_root_scenario_rejects_more_market_rows_than_scenario_events() -> None:
    """Dropping cardinality parity would admit input the launcher rejects."""
    from packages.nautilus_backtest.scenarios import BacktestScenarioError, BacktestScenarioV1

    _configuration, catalog_bytes, strategy, market, scenario_bytes = (
        _simulation_fixture("event-digest")
    )
    market_rows = [json.loads(market), json.loads(market)]
    market = b"".join(_canonical(row) + b"\n" for row in market_rows)
    catalog = json.loads(catalog_bytes)
    catalog["row_count"] = 2
    catalog["canonical_rows_sha256"] = hashlib.sha256(
        _canonical(market_rows)
    ).hexdigest()
    catalog_bytes = _canonical(catalog)
    scenario = json.loads(scenario_bytes)
    scenario["catalog_sha256"] = hashlib.sha256(catalog_bytes).hexdigest()
    scenario["market_data_sha256"] = hashlib.sha256(market).hexdigest()

    with pytest.raises(BacktestScenarioError, match="events do not match market data"):
        BacktestScenarioV1.from_mounted_artifacts(
            scenario_bytes=_canonical(scenario),
            catalog_bytes=catalog_bytes,
            strategy_bytes=strategy,
            market_data_bytes=market,
            start_time=_START,
            end_time=_END,
        )
