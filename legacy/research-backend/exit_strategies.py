"""
exit_strategies.py — Tiered profit taking, time-based exits, and ATR trailing stops.

Inspired by Kevin Davey's tiered exit framework from
"Entry and Exit Confessions of a Champion Trader."  Davey's core insight:
most traders let winners turn into losers by exiting all at once or holding
too long.  Tiered exits lock in partial profits at pre-planned R-multiples
while keeping a runner for the big move — the "let your winners run but
don't let them run away" pattern.

Provides three pure functions with numpy, type hints, and zero side effects.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

log = logging.getLogger("exit_strategies")


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Tiered Profit-Taking Exit
# ═══════════════════════════════════════════════════════════════════════════════

def tiered_profit_exit(
    entry_price: float,
    current_price: float,
    atr: float,
    tier_config: Optional[list[dict]] = None,
    direction: str = "long",
    high_watermark: Optional[float] = None,
) -> dict:
    """
    Tiered profit-taking exit a la Kevin Davey.

    Partitions the position into slices that exit at pre-defined R-multiples.
    The runner slice (last tier) is protected by a trailing ATR stop instead
    of a fixed target, so it can capture outsized moves.

    R is defined as ATR × 2 (the initial risk taken on the trade).  This
    matches Davey's convention: if your stop is 2 ATR from entry, then 1R
    is 2 ATR (move equal to your initial risk).

    Args:
        entry_price:   Entry fill price.
        current_price: Current market price.
        atr:           Average True Range (same period used for entry sizing).
        tier_config:   Optional list of tier dicts.  Each dict has:
                         r_multiple  — R multiple for this tier (float)
                         close_pct   — fraction of original position to close
                                       at this tier (0.0–1.0)
                         runner      — if True, this tier uses a trailing stop
                                       instead of a fixed limit (bool, optional)
                       Default (matching Davey's typical structure):
                         [{'r_multiple': 1.0, 'close_pct': 0.50},       # 50% at 1R
                          {'r_multiple': 2.0, 'close_pct': 0.30},       # 30% at 2R
                          {'r_multiple': 3.0, 'close_pct': 0.20,        # 20% runner
                           'runner': True}]
        direction:     "long" or "short".
        high_watermark: Highest (long) or lowest (short) price reached so far.
                        Used for the runner trailing stop.  If None, defaults
                        to current_price.

    Returns:
        dict with keys:
          action         — "partial_close" | "full_close" | "hold"
          close_pct      — cumulative fraction of original position to close
                           (0.0–1.0).  Consumers should track which tiers have
                           already been executed and only close the delta.
          trailing_stop  — current trailing-stop price for the runner (0.0 if
                           no runner tier or runner already closed)
          reason         — human-readable explanation
    """
    # ── defaults ──────────────────────────────────────────────────────────
    if tier_config is None:
        tier_config = [
            {"r_multiple": 1.0, "close_pct": 0.50},
            {"r_multiple": 2.0, "close_pct": 0.30},
            {"r_multiple": 3.0, "close_pct": 0.20, "runner": True},
        ]

    if high_watermark is None:
        high_watermark = current_price

    r_value = atr * 2.0  # 1R = 2× ATR (risk distance)

    # ── price move in R units ─────────────────────────────────────────────
    if direction == "long":
        move_r = (current_price - entry_price) / r_value if r_value > 0 else 0.0
        runner_stop = _trailing_stop_long(high_watermark, atr, multiplier=2.0)
    elif direction == "short":
        move_r = (entry_price - current_price) / r_value if r_value > 0 else 0.0
        runner_stop = _trailing_stop_short(high_watermark, atr, multiplier=2.0)
    else:
        raise ValueError(f"direction must be 'long' or 'short', got '{direction}'")

    # ── walk tiers and compute cumulative close ───────────────────────────
    cumulative_close = 0.0
    runner_active = False
    reason_parts: list[str] = []

    for tier in tier_config:
        r_target = tier["r_multiple"]
        pct = tier["close_pct"]
        is_runner = tier.get("runner", False)

        if move_r >= r_target:
            if is_runner:
                # Runner is NOT closed at its R-multiple; it rides the
                # trailing stop.  Mark it active so we include the stop.
                runner_active = True
            else:
                cumulative_close += pct
                reason_parts.append(f"{pct*100:.0f}% at {r_target:.1f}R")

    # ── runner stop-out check ─────────────────────────────────────────────
    if runner_active:
        if direction == "long" and current_price <= runner_stop:
            cumulative_close += _runner_close_pct(tier_config)
            reason_parts.append("runner stopped out (trailing stop hit)")
            runner_active = False
        elif direction == "short" and current_price >= runner_stop:
            cumulative_close += _runner_close_pct(tier_config)
            reason_parts.append("runner stopped out (trailing stop hit)")
            runner_active = False

    # clamp
    cumulative_close = min(cumulative_close, 1.0)

    # ── determine action ──────────────────────────────────────────────────
    if cumulative_close >= 1.0:
        action = "full_close"
    elif cumulative_close > 0.0:
        action = "partial_close"
    else:
        action = "hold"

    trailing_stop_price = runner_stop if runner_active else 0.0
    reason = "; ".join(reason_parts) if reason_parts else "no tier reached"

    return {
        "action": action,
        "close_pct": round(cumulative_close, 6),
        "trailing_stop": round(trailing_stop_price, 2),
        "reason": reason,
    }


def _runner_close_pct(tier_config: list[dict]) -> float:
    """Return the close_pct of the runner tier, or 0.0 if none."""
    for tier in tier_config:
        if tier.get("runner"):
            return tier["close_pct"]
    return 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Time-Based Exit ("Time Is Risk")
# ═══════════════════════════════════════════════════════════════════════════════

def time_based_exit(
    bars_held: int,
    max_bars: int,
    pnl_pct: float,
    min_move_pct: float = 0.5,
    direction: str = "long",
) -> dict:
    """
    Time-based exit — "time is risk" pattern from Davey.

    If a position has been held longer than `max_bars` bars AND the P&L
    hasn't moved at least `min_move_pct` percent in the favourable direction,
    the market has failed to confirm the trade in a reasonable time window.
    Exit to free up capital and avoid bleed.

    This is a sanity-check exit, not a primary profit-taking mechanism.
    Davey uses it to kill trades that "aren't working" — they may not hit
    the stop, but they also aren't making money, and every bar held is
    another bar of risk.

    Args:
        bars_held:    Number of bars the position has been open.
        max_bars:     Maximum allowed bars before the time decay check triggers.
        pnl_pct:      Current unrealised P&L as a percentage (e.g. 0.02 = 2%).
                      Positive for gains, negative for losses.
        min_move_pct: Minimum favourable move (percentage) required to stay in.
                      Default 0.5% — if you're not up at least half a percent
                      after max_bars, the trade isn't working.
        direction:    "long" or "short".  For longs, favourable = positive PnL;
                      for shorts, favourable = positive PnL (pnl_pct should be
                      positive when the position is in profit).

    Returns:
        dict with keys:
          action    — "exit" | "hold"
          reason    — human-readable explanation
          bars_held — echo of input (for logging)
          pnl_pct   — echo of input
    """
    if bars_held <= max_bars:
        return {
            "action": "hold",
            "reason": f"within time window ({bars_held}/{max_bars} bars)",
            "bars_held": bars_held,
            "pnl_pct": pnl_pct,
        }

    if pnl_pct >= min_move_pct:
        return {
            "action": "hold",
            "reason": (
                f"time expired ({bars_held}/{max_bars} bars) but P&L "
                f"({pnl_pct*100:.2f}%) exceeds min move ({min_move_pct*100:.2f}%)"
            ),
            "bars_held": bars_held,
            "pnl_pct": pnl_pct,
        }

    return {
        "action": "exit",
        "reason": (
            f"time expired ({bars_held}/{max_bars} bars) and P&L "
            f"({pnl_pct*100:.2f}%) below min move ({min_move_pct*100:.2f}%) — "
            f"time is risk"
        ),
        "bars_held": bars_held,
        "pnl_pct": pnl_pct,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 3. ATR Trailing Stop
# ═══════════════════════════════════════════════════════════════════════════════

def trailing_stop(
    atr: float,
    high_watermark: float,
    current_price: float,
    multiplier: float = 2.0,
    direction: str = "long",
) -> float:
    """
    ATR-based trailing stop — the classic "chandelier exit".

    For a long position:
      stop = high_watermark - ATR × multiplier
    For a short position:
      stop = high_watermark + ATR × multiplier

    The stop only moves in the favourable direction (tightens on new
    highs for longs, new lows for shorts).  The caller is responsible
    for updating high_watermark before each invocation.

    Args:
        atr:            Current Average True Range value.
        high_watermark: Highest high (long) or lowest low (short) since entry.
        current_price:  Current market price (used to prevent the stop from
                        gapping beyond the current price).
        multiplier:     ATR multiplier.  Davey default is 2.0 (2 ATR from
                        the extreme).  Higher = wider = fewer whipsaws.
        direction:      "long" or "short".

    Returns:
        float: The trailing stop price.

    Example:
        >>> trailing_stop(atr=50, high_watermark=1100, current_price=1080,
        ...               multiplier=2.0, direction='long')
        1000.0
    """
    if direction == "long":
        stop = float(high_watermark - atr * multiplier)
        # Never let the stop exceed the current price (that would be a
        # guaranteed fill — the stop must be BELOW current for a long).
        stop = min(stop, current_price)
    elif direction == "short":
        stop = float(high_watermark + atr * multiplier)
        stop = max(stop, current_price)
    else:
        raise ValueError(f"direction must be 'long' or 'short', got '{direction}'")

    return round(stop, 2)


# ═══════════════════════════════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _trailing_stop_long(
    high_watermark: float, atr: float, multiplier: float = 2.0
) -> float:
    """Convenience: trailing stop for long positions."""
    return float(high_watermark - atr * multiplier)


def _trailing_stop_short(
    high_watermark: float, atr: float, multiplier: float = 2.0
) -> float:
    """Convenience: trailing stop for short positions."""
    return float(high_watermark + atr * multiplier)
