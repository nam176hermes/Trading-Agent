"""Legacy backtest accounting, data window and ML probability contracts (synthetic data only)."""
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import backtest_engine as b

NOW = datetime(2020, 1, 1, tzinfo=timezone.utc)


def _bar(price, ts=NOW):
    return b.Bar(ts, price, price, price, price, 1)


def _portfolio(**overrides):
    values = dict(initial_capital=1000, commission_pct=0, slippage_pct=0, funding_rate_pct=0)
    values.update(overrides)
    return b.Portfolio(b.BacktestConfig(**values))


@pytest.mark.parametrize("side,exit_price,expected_pnl", [
    ("BUY", 110, 10), ("BUY", 90, -10), ("SELL", 90, 10), ("SELL", 110, -10),
])
def test_round_trip_cash_equals_trade_pnl_for_long_and_short(side, exit_price, expected_pnl):
    portfolio = _portfolio()
    portfolio.apply_fill(b.Fill(NOW, "SYNTH/TEST", side, 1, 100, 0, 0))
    assert portfolio.compute_equity(_bar(100)) == 1000
    assert portfolio.compute_equity(_bar(exit_price)) == 1000 + expected_pnl
    portfolio.close_all(_bar(exit_price), 1)
    assert portfolio.trade_log[0]["pnl"] == expected_pnl
    assert portfolio.cash == 1000 + expected_pnl


@pytest.mark.parametrize("exit_path", ["close_all", "check_exits"])
def test_funding_reduces_cash_exactly_once(exit_path):
    portfolio = _portfolio(funding_rate_pct=0.01, stop_loss_pct=0.05)
    portfolio.apply_fill(b.Fill(NOW, "SYNTH/TEST", "BUY", 1, 100, 0, 0))
    if exit_path == "close_all":
        portfolio.close_all(_bar(100), 8)
    else:
        portfolio.check_exits(b.Bar(NOW, 94, 94, 94, 94, 1), 8)     # stop at 95
    pnl = portfolio.trade_log[0]["pnl"]
    assert portfolio.total_funding_paid == pytest.approx(1.0)
    assert portfolio.cash == pytest.approx(1000 + pnl)


def test_final_equity_is_reported_after_end_of_test_settlement():
    config = b.BacktestConfig(initial_capital=1000, commission_pct=0.01, slippage_pct=0, funding_rate_pct=0)
    frame = pd.DataFrame({"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1.0},
                         index=pd.date_range(NOW, periods=50, freq="h"))
    handler = b.DataHandler("SYNTH/TEST", "2020-01-01", "2020-01-03")
    handler.load = lambda: frame
    engine = b.BacktestEngine("SYNTH/TEST", b.BaselineStrategy(config), config, handler)
    report = engine.run()
    assert not engine.portfolio.positions
    assert report["aggregate"]["final_equity"] == pytest.approx(engine.portfolio.cash)
    assert engine.portfolio.equity_curve[-1][1] == pytest.approx(engine.portfolio.cash)
    assert len(engine.portfolio.equity_curve) == 50                 # settlement adds no period
    assert report["aggregate"]["final_equity"] == pytest.approx(1000 + report["aggregate"]["total_pnl"])


def test_loader_trims_provider_pages_and_stale_cache_to_the_requested_window(tmp_path, monkeypatch):
    monkeypatch.setattr(b, "CACHE_DIR", tmp_path)
    index = pd.DatetimeIndex(["2019-12-31T23:00:00Z", "2020-01-01T00:00:00Z",
                              "2020-01-01T23:00:00Z", "2020-01-03T00:00:00Z"])
    page = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}, index=index)
    page.index.name = "timestamp"
    handler = b.DataHandler("SYNTH/TEST", "2020-01-01", "2020-01-01")
    monkeypatch.setattr(handler, "_fetch", lambda: page)
    loaded = handler.load()
    assert list(loaded.index) == list(index[1:3])
    # A cache written before the fix may hold rows outside the window; a hit is trimmed too.
    cache = next(tmp_path.iterdir())
    page.to_csv(cache)
    assert list(b.DataHandler("SYNTH/TEST", "2020-01-01", "2020-01-01").load().index) == list(index[1:3])


@pytest.mark.parametrize("probability,action", [(0.30, "HOLD"), (0.54, "HOLD"), (0.56, "BUY")])
def test_ml_strategy_uses_booster_probability_without_a_second_sigmoid(probability, action):
    config = b.BacktestConfig()
    strategy = b.MLStrategy(config, model=SimpleNamespace(predict=lambda x: np.array([probability])))
    strategy._features = pd.DataFrame({"feature": [1.0]})
    strategy._feature_cols = ["feature"]
    assert strategy.prob_threshold == 0.55
    signal = strategy.next(0, _bar(100), [])
    assert signal.action == action
    if action == "BUY":
        assert signal.confidence == pytest.approx(probability, abs=1e-4)


def test_ml_strategy_refuses_a_non_probability_output():
    strategy = b.MLStrategy(b.BacktestConfig(), model=SimpleNamespace(predict=lambda x: np.array([2.5])))
    strategy._features = pd.DataFrame({"feature": [1.0]})
    strategy._feature_cols = ["feature"]
    assert strategy.next(0, _bar(100), []).action == "HOLD"
