from __future__ import annotations

import hashlib
import json

import pytest


def _canonical(value: object) -> bytes:
    return json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True).encode()


def _mounted(scenario_id: str = "event-digest") -> tuple[bytes, bytes, bytes, bytes]:
    catalog = _canonical({"catalog": "fixed"})
    strategy = _canonical(
        {
            "effective_at": "2026-08-05T12:00:00Z",
            "positions": [{"instrument": {"product_type": "crypto_spot", "symbol": "BTCUSDT", "venue": "BINANCE"}, "target_quantity": "1"}],
            "schema_version": "nautilus-execution-target-v1",
        }
    )
    market = b'{"close":"101"}\n'
    scenario = _canonical(
        {
            "catalog_sha256": hashlib.sha256(catalog).hexdigest(),
            "events": [
                {
                    "ask": "100", "bid": "99", "close": "101", "event_time": "2026-08-05T12:00:00Z",
                    "high": "102", "low": "98", "open": "100", "quote_time": "2026-08-05T12:00:00Z",
                    "sequence": 1, "session_open": True, "volume": "1",
                }
            ],
            "fee_rate": "0.001",
            "instrument": {"product_type": "crypto_spot", "symbol": "BTCUSDT", "venue": "BINANCE"},
            "liquidity_limit": "1", "market_data_sha256": hashlib.sha256(market).hexdigest(),
            "scenario_id": scenario_id, "schema_version": "nautilus-execution-scenario-v1",
            "session_policy": "explicit-open-flag-v1", "slippage_bps": "0",
            "stale_quote_threshold_seconds": 30, "stop_price": None,
            "stop_take_profit_precedence": "stop-first", "strategy_sha256": hashlib.sha256(strategy).hexdigest(),
            "take_profit_price": None,
        }
    )
    return scenario, catalog, strategy, market


def test_root_scenario_reconstructs_exact_mounted_bytes_and_bindings() -> None:
    from packages.nautilus_backtest.scenarios import BacktestScenarioV1

    scenario_bytes, catalog, strategy, market = _mounted()
    scenario = BacktestScenarioV1.from_mounted_artifacts(
        scenario_bytes=scenario_bytes,
        catalog_bytes=catalog,
        strategy_bytes=strategy,
        market_data_bytes=market,
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
        )
