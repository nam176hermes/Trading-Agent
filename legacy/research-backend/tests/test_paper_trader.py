"""
tests/test_paper_trader.py — Unit tests for paper_trader core logic.
Uses tmp_memory fixture from conftest.py to isolate all file I/O.
"""

import json
import sys
import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import paper_trader as pt


# ── Portfolio initialisation ──────────────────────────────────────────────────

class TestPortfolioInit:
    def test_init_portfolio_has_expected_keys(self, empty_portfolio):
        pf = empty_portfolio
        assert "cash" in pf
        assert "positions" in pf
        assert "pnl" in pf

    def test_initial_cash_is_100k(self, empty_portfolio):
        assert empty_portfolio["cash"] == pytest.approx(100_000.0)

    def test_no_positions_on_init(self, empty_portfolio):
        assert empty_portfolio["positions"] == {}

    def test_save_and_load_roundtrip(self, empty_portfolio):
        pf = empty_portfolio
        pf["cash"] = 55_000.0
        pt.save_portfolio(pf)
        loaded = pt.load_portfolio()
        assert loaded["cash"] == pytest.approx(55_000.0)


# ── Execute signal (buy/sell) ─────────────────────────────────────────────────

class TestExecuteSignal:
    def test_buy_creates_position(self, empty_portfolio):
        signal = {"symbol": "BTC", "action": "BUY", "confidence": 0.80}
        prices = {"BTC": 50_000.0}
        result = pt.execute_signal(signal, prices)
        pf = pt.load_portfolio()
        assert "BTC" in pf["positions"]
        assert result["side"] == "BUY"

    def test_buy_reduces_cash(self, empty_portfolio):
        signal = {"symbol": "ETH", "action": "BUY", "confidence": 0.70}
        prices = {"ETH": 3_000.0}
        pt.execute_signal(signal, prices)
        pf = pt.load_portfolio()
        assert pf["cash"] < 100_000.0

    def test_buy_sets_default_stop_loss(self, empty_portfolio):
        signal = {"symbol": "SOL", "action": "BUY", "confidence": 0.80}
        prices = {"SOL": 100.0}
        pt.execute_signal(signal, prices)
        pf = pt.load_portfolio()
        pos = pf["positions"]["SOL"]
        assert pos.get("stop_loss") is not None
        # default stop-loss is 5% below fill price (~100 * 1.001 * 0.95)
        assert pos["stop_loss"] < prices["SOL"]

    def test_buy_uses_signal_stop_loss_when_provided(self, empty_portfolio):
        signal = {"symbol": "SOL", "action": "BUY", "confidence": 0.80, "stop_loss": 85.0}
        prices = {"SOL": 100.0}
        pt.execute_signal(signal, prices)
        pf = pt.load_portfolio()
        assert pf["positions"]["SOL"]["stop_loss"] == pytest.approx(85.0)

    def test_sell_closes_position(self, empty_portfolio):
        buy_sig = {"symbol": "BTC", "action": "BUY", "confidence": 0.80}
        pt.execute_signal(buy_sig, {"BTC": 50_000.0})

        sell_sig = {"symbol": "BTC", "action": "SELL", "confidence": 0.80}
        pt.execute_signal(sell_sig, {"BTC": 52_000.0})
        pf = pt.load_portfolio()
        assert "BTC" not in pf["positions"]

    def test_sell_increases_cash(self, empty_portfolio):
        buy_sig = {"symbol": "ETH", "action": "BUY", "confidence": 0.80}
        pt.execute_signal(buy_sig, {"ETH": 3_000.0})
        cash_after_buy = pt.load_portfolio()["cash"]

        sell_sig = {"symbol": "ETH", "action": "SELL", "confidence": 0.80}
        pt.execute_signal(sell_sig, {"ETH": 3_300.0})
        pf = pt.load_portfolio()
        assert pf["cash"] > cash_after_buy

    def test_sell_without_position_is_rejected(self, empty_portfolio):
        signal = {"symbol": "XRP", "action": "SELL", "confidence": 0.80}
        result = pt.execute_signal(signal, {"XRP": 0.50})
        assert result is not None
        assert result.get("status") == "rejected"


# ── Stop-loss enforcement ─────────────────────────────────────────────────────

class TestCheckStops:
    def _open_position(self, symbol: str, avg_cost: float, shares: float = 10.0,
                       stop_loss: float | None = None):
        """Directly write a position into the portfolio file."""
        pf = pt.load_portfolio()
        pf["cash"] -= avg_cost * shares
        pf["positions"][symbol] = {
            "shares": shares,
            "avg_cost": avg_cost,
            "stop_loss": stop_loss,
            "highest_price": avg_cost,
            "trail_active": False,
            "trail_stop": None,
        }
        pt.save_portfolio(pf)

    def test_no_hits_when_price_above_stop(self, empty_portfolio):
        self._open_position("BTC", avg_cost=50_000.0, stop_loss=45_000.0)
        closed = pt.check_stops({"BTC": 51_000.0})
        assert closed == []

    def test_stop_loss_triggers_when_price_below(self, empty_portfolio):
        self._open_position("BTC", avg_cost=50_000.0, stop_loss=45_000.0)
        closed = pt.check_stops({"BTC": 44_000.0})
        assert len(closed) == 1
        assert closed[0]["symbol"] == "BTC"
        assert closed[0]["exit_reason"] == "stop-loss"

    def test_stop_loss_removes_position(self, empty_portfolio):
        self._open_position("ETH", avg_cost=3_000.0, stop_loss=2_700.0)
        pt.check_stops({"ETH": 2_500.0})
        pf = pt.load_portfolio()
        assert "ETH" not in pf["positions"]

    def test_default_stop_loss_applied_when_none_set(self, empty_portfolio):
        # No stop_loss set — should default to 5% below avg_cost
        self._open_position("SOL", avg_cost=100.0, stop_loss=None)
        # Price 6% below avg_cost should trigger
        closed = pt.check_stops({"SOL": 93.0})
        assert len(closed) == 1
        assert closed[0]["exit_reason"] == "stop-loss"

    def test_trailing_stop_activates_at_10pct_gain(self, empty_portfolio):
        self._open_position("BTC", avg_cost=50_000.0, stop_loss=40_000.0)
        # Price rises 11% — trailing stop should activate
        pt.check_stops({"BTC": 55_500.0})
        pf = pt.load_portfolio()
        pos = pf["positions"]["BTC"]
        assert pos.get("trail_active") is True
        assert pos.get("trail_stop") is not None

    def test_trailing_stop_triggers_on_pullback(self, empty_portfolio):
        self._open_position("BTC", avg_cost=50_000.0, stop_loss=40_000.0)
        # Push price up to activate trailing stop
        pt.check_stops({"BTC": 56_000.0})
        # Now drop 6% from peak — trailing stop (5% below 56k = 53_200) should fire
        closed = pt.check_stops({"BTC": 52_900.0})
        assert len(closed) == 1
        assert closed[0]["exit_reason"] == "trailing-stop"

    def test_missing_symbol_in_prices_is_skipped(self, empty_portfolio):
        self._open_position("BTC", avg_cost=50_000.0, stop_loss=45_000.0)
        # BTC not in prices — should not crash
        closed = pt.check_stops({"ETH": 3_000.0})
        assert closed == []


# ── Slippage stats ────────────────────────────────────────────────────────────

class TestSlippageStats:
    def test_returns_default_when_no_orders(self, tmp_memory):
        stats = pt.get_slippage_stats()
        assert stats["samples"] == 0
        assert stats["avg"] == pt.SLIPPAGE
        assert stats["recommended"] == pt.SLIPPAGE

    def test_reads_slippage_from_order_records(self, tmp_memory):
        orders = [
            {"symbol": "BTC", "side": "BUY", "fill_price": 50050, "cost": 5000, "slippage": 0.001},
            {"symbol": "ETH", "side": "BUY", "fill_price": 3003, "cost": 3000, "slippage": 0.001},
            {"symbol": "SOL", "side": "BUY", "fill_price": 100.1, "cost": 1000, "slippage": 0.001},
        ]
        pt.ORDERS_FILE.write_text("\n".join(json.dumps(o) for o in orders))
        stats = pt.get_slippage_stats()
        assert stats["samples"] == 3
        assert stats["avg"] == pytest.approx(0.001)
        assert stats["recommended"] >= 0.001

    def test_recommended_is_higher_than_avg(self, tmp_memory):
        orders = [{"slippage": 0.002} for _ in range(5)]
        pt.ORDERS_FILE.write_text("\n".join(json.dumps(o) for o in orders))
        stats = pt.get_slippage_stats()
        assert stats["recommended"] >= stats["avg"]
