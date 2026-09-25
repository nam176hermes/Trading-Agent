"""Legacy bar chronology, gap exits and per-signal stops (Codex audit A02-A04; synthetic data only)."""
import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import backtest_engine as b

T0 = datetime(2020, 1, 1, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


def _bar(i, open_, high, low, close):
    return b.Bar(T0 + timedelta(hours=i), open_, high, low, close, 1)


class Scripted(b.Strategy):
    def __init__(self, config, actions, sl=0.0, tp=0.0):
        super().__init__(config)
        self.actions, self.sl, self.tp = actions, sl, tp

    def next(self, i, bar, positions):
        return b.Signal(self.actions.get(i, 'HOLD'), 1.0, self.sl, self.tp)


def _run(bars, actions, **overrides):
    values = dict(initial_capital=1000, commission_pct=0, slippage_pct=0, funding_rate_pct=0,
                  stop_loss_pct=0.05, take_profit_pct=0.10, position_size_pct=0.1)
    values.update(overrides.pop('config', {}))
    config = b.BacktestConfig(**values)
    portfolio, broker = b.Portfolio(config), b.Broker(config)
    strategy = Scripted(config, actions, **overrides)
    for i, bar in enumerate(bars):
        b.step_bar(portfolio, broker, strategy, bar, i, 'SYNTH/TEST')
    return portfolio


def test_pending_sell_fills_at_open_before_the_bar_reaches_take_profit():
    bars = [_bar(0, 100, 100, 100, 100), _bar(1, 100, 100, 100, 100), _bar(2, 100, 111, 99, 105)]
    portfolio = _run(bars, {0: 'BUY', 1: 'SELL'})
    (trade,) = portfolio.trade_log
    assert trade['exit_reason'] == 'signal_exit' and trade['exit_price'] == 100 and trade['pnl'] == 0


def test_a_position_opened_at_the_open_is_stopped_by_the_same_bar():
    bars = [_bar(0, 100, 100, 100, 100), _bar(1, 100, 100, 90, 92)]
    portfolio = _run(bars, {0: 'BUY'})
    (trade,) = portfolio.trade_log
    assert trade['exit_reason'] == 'stop_loss' and trade['exit_price'] == 95


@pytest.mark.parametrize(('side', 'bar', 'price', 'reason'), [
    ('BUY', (80, 85, 75, 80), 80, 'stop_loss'),        # gap below the stop: the open is the first tradable price
    ('BUY', (97, 99, 94, 96), 95, 'stop_loss'),        # touched inside the bar
    ('BUY', (120, 125, 118, 121), 120, 'take_profit'),  # resting limit fills at the better open
    ('SELL', (120, 125, 118, 121), 120, 'stop_loss'),
    ('SELL', (80, 85, 75, 80), 80, 'take_profit'),
])
def test_gap_exits_fill_at_prices_the_bar_could_trade(side, bar, price, reason):
    config = b.BacktestConfig(initial_capital=1000, commission_pct=0, slippage_pct=0, funding_rate_pct=0,
                              stop_loss_pct=0.05, take_profit_pct=0.10)
    portfolio = b.Portfolio(config)
    portfolio.apply_fill(b.Fill(T0, 'SYNTH/TEST', side, 1, 100, 0, 0))
    portfolio.check_exits(_bar(1, *bar), 1)
    (trade,) = portfolio.trade_log
    assert (trade['exit_price'], trade['exit_reason']) == (price, reason)
    assert _bar(1, *bar).low <= trade['exit_price'] <= _bar(1, *bar).high


@pytest.mark.parametrize(('sl', 'tp', 'stop', 'target'), [(0.02, 0.03, 98, 103), (0.0, 0.0, 95, 110)])
def test_signal_stops_reach_the_position_with_config_fallback(sl, tp, stop, target):
    bars = [_bar(0, 100, 100, 100, 100), _bar(1, 100, 100, 100, 100)]
    portfolio = _run(bars, {0: 'BUY'}, sl=sl, tp=tp)
    (position,) = portfolio.positions
    assert position.stop_loss_price == pytest.approx(stop) and position.take_profit_price == pytest.approx(target)


@pytest.mark.parametrize(('module', 'function'), [
    ('backtest_engine.py', 'run'), ('backtest_runner.py', '_run_window_backtest'),
    ('alpha_backtest.py', '_run_alpha_backtest'), ('alpha_backtest.py', 'run_alpha_walk_forward')])
def test_every_bar_loop_uses_the_shared_step(module, function):
    tree = ast.parse((ROOT / module).read_text())
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == function)
    calls = {c.func.attr if isinstance(c.func, ast.Attribute) else getattr(c.func, 'id', '')
             for c in ast.walk(node) if isinstance(c, ast.Call)}
    assert 'step_bar' in calls and not {'check_exits', 'process_orders'} & calls
