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
    later = NOW + pd.Timedelta(hours=8)                                # one 8h funding interval held
    if exit_path == "close_all":
        portfolio.close_all(_bar(100, later), 8)
    else:
        portfolio.check_exits(b.Bar(later, 94, 94, 94, 94, 1), 8)   # stop at 95
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


def _signal(action):
    return b.Signal(action=action, confidence=1.0, stop_loss_pct=0.0, take_profit_pct=0.0)


def test_sell_while_flat_never_opens_a_short_and_cannot_exceed_max_positions():
    config = b.BacktestConfig(initial_capital=1000, commission_pct=0, slippage_pct=0,
                              funding_rate_pct=0, max_positions=1)
    portfolio, broker = b.Portfolio(config), b.Broker(config)
    for index in range(6):
        assert broker.place_order(_signal("SELL"), _bar(100), index, portfolio, "SYNTH/TEST") is None
        broker.process_orders(_bar(100), index, portfolio, "SYNTH/TEST")
    assert portfolio.positions == []
    assert portfolio.cash == 1000


def test_sell_closes_the_long_instead_of_opening_a_hedge():
    config = b.BacktestConfig(initial_capital=1000, commission_pct=0, slippage_pct=0, funding_rate_pct=0)
    portfolio, broker = b.Portfolio(config), b.Broker(config)
    portfolio.apply_fill(b.Fill(NOW, "SYNTH/TEST", "BUY", 2, 100, 0, 0))
    broker.place_order(_signal("SELL"), _bar(110), 1, portfolio, "SYNTH/TEST")
    fills = broker.process_orders(_bar(110), 2, portfolio, "SYNTH/TEST")
    assert [(fill.side, fill.quantity) for fill in fills] == [("SELL", 2)]
    assert portfolio.positions == []
    assert portfolio.cash == pytest.approx(1020)
    assert portfolio.trade_log[-1]["exit_reason"] == "signal_exit"


def test_pending_sell_is_dropped_when_a_stop_closed_the_long_first():
    config = b.BacktestConfig(initial_capital=1000, commission_pct=0, slippage_pct=0,
                              funding_rate_pct=0, stop_loss_pct=0.05)
    portfolio, broker = b.Portfolio(config), b.Broker(config)
    portfolio.apply_fill(b.Fill(NOW, "SYNTH/TEST", "BUY", 1, 100, 0, 0))
    broker.place_order(_signal("SELL"), _bar(100), 1, portfolio, "SYNTH/TEST")
    portfolio.check_exits(_bar(90), 2)                                   # stop at 95 fires first
    assert broker.process_orders(_bar(90), 2, portfolio, "SYNTH/TEST") == []
    assert portfolio.positions == []


class _BuyOnce:
    """Stand-in for MLStrategy in the walk-forward window: buy on the first bar, then hold."""
    def __init__(self, config, symbol=None, model=None):
        self.config = config

    def _precompute_features(self, df):
        return None

    def next(self, i, bar, positions):
        return _signal("BUY" if i == 0 and not positions else "HOLD")


@pytest.mark.parametrize("timeframe", ["1h", "4h"])
def test_walk_forward_window_settles_final_equity_and_binds_metric_arguments(monkeypatch, timeframe):
    import backtest_runner
    monkeypatch.setattr(backtest_runner, "MLStrategy", _BuyOnce)
    config = b.BacktestConfig(initial_capital=1000, commission_pct=0.01, slippage_pct=0, funding_rate_pct=0)
    frame = pd.DataFrame({"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1.0},
                         index=pd.date_range(NOW, periods=50, freq="h"))
    result = backtest_runner._run_window_backtest(frame, "SYNTH/TEST", config, None, None, None, [], timeframe)
    assert result["final_equity"] == pytest.approx(1000 + result["total_pnl"])
    assert len(result["equity_curve"]) == 50
    metrics = result["metrics"]
    assert isinstance(metrics["total_funding_paid"], float)
    expected = b.compute_aggregate_metrics(result["equity_curve"], result["trades"], config,
                                           total_funding_paid=0.0, timeframe=timeframe)
    assert metrics == expected


def test_yfinance_request_covers_the_inclusive_end_day(tmp_path, monkeypatch):
    import sys
    from types import ModuleType
    requested = {}

    class Ticker:
        def __init__(self, symbol):
            pass

        def history(self, start, end, interval):
            requested.update(start=start, end=end)
            days = pd.date_range(start, end, freq="D", inclusive="left", tz="UTC")   # end-exclusive
            return pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0, "Volume": 1.0}, index=days)

    module = ModuleType("yfinance")
    module.Ticker = Ticker
    monkeypatch.setitem(sys.modules, "yfinance", module)
    monkeypatch.setattr(b, "CACHE_DIR", tmp_path)
    loaded = b.DataHandler("SYNTHETIC", "2020-01-01", "2020-01-02", timeframe="1d").load()
    assert requested == {"start": "2020-01-01", "end": "2020-01-03"}
    assert [ts.strftime("%Y-%m-%d") for ts in loaded.index] == ["2020-01-01", "2020-01-02"]


def test_queued_buys_cannot_open_beyond_max_positions_at_fill():
    config = b.BacktestConfig(initial_capital=1000, commission_pct=0, slippage_pct=0,
                              funding_rate_pct=0, max_positions=1)
    portfolio, broker = b.Portfolio(config), b.Broker(config)
    for index in range(2):                                   # both accepted while flat
        assert broker.place_order(_signal("BUY"), _bar(100), index, portfolio, "SYNTH/TEST") is not None
    fills = broker.process_orders(_bar(100), 2, portfolio, "SYNTH/TEST")
    assert len(fills) == 1
    assert portfolio.position_count == 1


def test_sell_order_quantity_matches_what_it_fills_across_several_longs():
    config = b.BacktestConfig(initial_capital=1000, commission_pct=0, slippage_pct=0, funding_rate_pct=0)
    portfolio, broker = b.Portfolio(config), b.Broker(config)
    portfolio.apply_fill(b.Fill(NOW, "SYNTH/TEST", "BUY", 1, 100, 0, 0))
    portfolio.apply_fill(b.Fill(NOW, "SYNTH/TEST", "BUY", 2, 100, 0, 1))
    order = broker.place_order(_signal("SELL"), _bar(100), 2, portfolio, "SYNTH/TEST")
    assert order is not None and order.quantity == 3
    fills = broker.process_orders(_bar(100), 3, portfolio, "SYNTH/TEST")
    assert sum(fill.quantity for fill in fills) == order.quantity
    assert portfolio.positions == []


def test_sell_never_closes_a_long_opened_after_it_was_placed():
    config = b.BacktestConfig(initial_capital=1000, commission_pct=0, slippage_pct=0, funding_rate_pct=0)
    portfolio, broker = b.Portfolio(config), b.Broker(config)
    portfolio.apply_fill(b.Fill(NOW, "SYNTH/TEST", "BUY", 1, 100, 0, 0))
    order = broker.place_order(_signal("SELL"), _bar(100), 1, portfolio, "SYNTH/TEST")
    later = b.Fill(NOW, "SYNTH/TEST", "BUY", 1, 100, 0, 2)    # an identical long opened afterwards
    portfolio.apply_fill(later)
    fills = broker.process_orders(_bar(100), 3, portfolio, "SYNTH/TEST")
    assert order is not None and sum(fill.quantity for fill in fills) == order.quantity == 1
    assert [(p.side, p.quantity, p.entry_bar) for p in portfolio.positions] == [("BUY", 1, 2)]
