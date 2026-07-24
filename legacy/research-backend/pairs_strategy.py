#!/usr/bin/env python3
"""
Pairs Trading Strategy — wraps pairs_trader into a backtest_engine Strategy.

Detects cointegrated pairs and trades mean-reversion on the spread.
Uses z-score thresholds for entry/exit with configurable lookback.

Integration: pairs_trader.generate_pair_signal() drives the signal logic.
"""

from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd

from backtest_engine import BacktestConfig, Bar, Position, Signal, Strategy
from pairs_trader import (
    test_cointegration,
    find_pairs,
    generate_pair_signal,
)

# ── Helpers ──────────────────────────────────────────────────────────────────────

def scan_pairs(price_dict: dict[str, np.ndarray], max_pairs: int = 50) -> list[dict]:
    """
    Scan for cointegrated pairs and return ranked candidates.

    Parameters
    ----------
    price_dict : dict[str, np.ndarray]
        Symbol → close price array (all same length).
    max_pairs : int
        Maximum pairs to test.

    Returns
    -------
    list[dict]
        Cointegrated pairs sorted by p-value (ascending).
    """
    all_results = find_pairs(price_dict, max_pairs=max_pairs)
    # Filter to only cointegrated pairs
    return [r for r in all_results if r["cointegrated"]]


def print_candidates(candidates: list[dict]) -> str:
    """Format candidate pairs for display."""
    if not candidates:
        return "No cointegrated pairs found."
    lines = [f"Found {len(candidates)} cointegrated pair(s):", ""]
    for i, c in enumerate(candidates[:10], 1):
        lines.append(
            f"  {i:2d}. {c['symbol_a']}/{c['symbol_b']}  "
            f"p={c['p_value']:.4f}  "
            f"hedge={c['hedge_ratio']:.4f}  "
            f"half-life={c['half_life']:.1f} bars"
        )
    if len(candidates) > 10:
        lines.append(f"  ... and {len(candidates) - 10} more")
    return "\n".join(lines)


# ── Strategy ─────────────────────────────────────────────────────────────────────

class PairsStrategy(Strategy):
    """
    Mean-reversion pairs trading strategy.

    Trades the spread between two cointegrated assets:
      - LONG_SPREAD when z-score < -threshold  (buy A, sell B)
      - SHORT_SPREAD when z-score > +threshold (sell A, buy B)
      - CLOSE when z-score crosses zero

    Parameters
    ----------
    config : BacktestConfig
        Backtest engine configuration.
    symbol_a, symbol_b : str
        The two symbols in the pair.
    hedge_ratio : float
        OLS beta from cointegration regression.
    intercept : float
        OLS intercept (alpha). Default 0.
    z_score_threshold : float
        Entry threshold in z-score units. Default 2.0.
    lookback : int or None
        Rolling window for z-score mean/std. None = expanding.
    """

    def __init__(
        self,
        config: BacktestConfig,
        symbol_a: str,
        symbol_b: str,
        hedge_ratio: float,
        intercept: float = 0.0,
        z_score_threshold: float = 2.0,
        lookback: Optional[int] = None,
    ):
        super().__init__(config)
        self.symbol_a = symbol_a
        self.symbol_b = symbol_b
        self.hedge_ratio = hedge_ratio
        self.intercept = intercept
        self.z_score_threshold = z_score_threshold
        self.lookback = lookback
        self._prices_a: list[float] = []
        self._prices_b: list[float] = []
        self._current_action = "HOLD"

    def next(self, i: int, bar: Bar, positions: list[Position]) -> Signal:
        """
        Called on each bar. Accumulates prices and generates pair signal.

        The bar represents asset A; asset B prices are accumulated from
        the joint DataFrame via set_data (see set_pair_data).
        """
        # Accumulate price A from the bar
        self._prices_a.append(bar.close)

        # Get price B from the shared data
        if self._df is not None and i < len(self._df):
            self._prices_b.append(float(self._df.iloc[i]["close"]))

        # Need at least 30 bars for reliable z-score
        if len(self._prices_a) < 30:
            return Signal(action="HOLD", confidence=0.0, stop_loss_pct=0.0, take_profit_pct=0.0)

        prices_a_arr = np.array(self._prices_a)
        prices_b_arr = np.array(self._prices_b)

        sig = generate_pair_signal(
            symbol_a=self.symbol_a,
            symbol_b=self.symbol_b,
            prices_a=prices_a_arr,
            prices_b=prices_b_arr,
            hedge_ratio=self.hedge_ratio,
            intercept=self.intercept,
            z_score_threshold=self.z_score_threshold,
            lookback=self.lookback,
        )

        action = sig["action"]
        self._current_action = action

        if action in ("LONG_SPREAD", "SHORT_SPREAD"):
            confidence = min(abs(sig["z_score"]) / (self.z_score_threshold * 2), 1.0)
            return Signal(
                action="BUY",  # LONG_SPREAD → BUY A (SHORT B handled by pair bookkeeping)
                confidence=round(confidence, 4),
                stop_loss_pct=self.config.stop_loss_pct,
                take_profit_pct=self.config.take_profit_pct,
            )
        elif action == "CLOSE":
            return Signal(
                action="SELL",
                confidence=0.8,
                stop_loss_pct=0.0,
                take_profit_pct=0.0,
            )
        else:
            return Signal(action="HOLD", confidence=0.0, stop_loss_pct=0.0, take_profit_pct=0.0)

    def set_pair_data(self, df_a: pd.DataFrame, df_b: pd.DataFrame):
        """Set data for both legs of the pair."""
        self.set_data(df_a)
        self._df_b = df_b


# ── Smoke test ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Build synthetic cointegrated price series
    np.random.seed(42)
    n = 500

    rw = np.cumsum(np.random.randn(n) * 0.5) + 100.0
    noise = np.random.randn(n) * 0.3
    prices_a = rw + noise
    prices_b = rw * 0.8 + np.random.randn(n) * 0.2 + 20.0

    # Test cointegration on synthetic data
    result = test_cointegration(prices_a, prices_b)
    print("Cointegration test:")
    print(f"  Cointegrated: {result['cointegrated']}")
    print(f"  p-value:      {result['p_value']:.6f}")
    print(f"  Hedge ratio:  {result['hedge_ratio']:.4f}")
    print(f"  Half-life:    {result['half_life']:.1f} bars")

    # Generate a signal
    sig = generate_pair_signal(
        symbol_a="SYNTH_A",
        symbol_b="SYNTH_B",
        prices_a=prices_a,
        prices_b=prices_b,
        hedge_ratio=result["hedge_ratio"],
        intercept=result["intercept"],
    )
    print(f"\nLatest signal: {sig['action']} (z={sig['z_score']:.2f})")
