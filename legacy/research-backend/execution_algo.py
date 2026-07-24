"""
execution_algo.py — VWAP execution slicer (Barry Johnson style).

Splits a large position into N child orders distributed across T minutes
to minimize market impact. Used by execute_live.py before sending to broker.

VWAP slicer logic:
  - Divides total quantity into N equal slices
  - Places each slice at the start of each time bucket
  - Tracks VWAP of fills vs market VWAP to measure execution quality
  - Supports paper mode (logs only) and live mode (calls broker)

Usage:
    from execution_algo import VWAPSlicer
    slicer = VWAPSlicer(symbol="BTC", total_qty=0.5, side="buy", duration_minutes=30)
    plan = slicer.build_plan(current_price=65000)
"""

import json
import logging
import math
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger("execution_algo")

DEFAULT_SLICES      = 10     # split order into 10 child orders
DEFAULT_DURATION    = 30     # minutes to execute over
MIN_CHILD_QTY_USD   = 10.0   # minimum child order size in USD


class VWAPSlicer:
    """
    Splits a large order into N time-weighted child orders.

    Args:
        symbol:           Trading symbol (e.g. "BTC")
        total_qty:        Total quantity to buy/sell (in base asset units)
        side:             "buy" or "sell"
        duration_minutes: Total time window to execute over
        n_slices:         Number of child orders
        entry_price:      Reference price for size calculation
    """

    def __init__(
        self,
        symbol:           str,
        total_qty:        float,
        side:             str,
        duration_minutes: int   = DEFAULT_DURATION,
        n_slices:         int   = DEFAULT_SLICES,
        entry_price:      Optional[float] = None,
    ):
        self.symbol           = symbol
        self.total_qty        = total_qty
        self.side             = side.lower()
        self.duration_minutes = duration_minutes
        self.n_slices         = n_slices
        self.entry_price      = entry_price
        self.fills: list[dict] = []

    def build_plan(self, current_price: Optional[float] = None) -> dict:
        """
        Build execution plan — returns list of child orders with timing.

        Returns:
            {
                symbol, side, total_qty, n_slices, duration_minutes,
                child_qty, interval_minutes, children: [...],
                estimated_market_impact_bps: float
            }
        """
        ref_price = current_price or self.entry_price or 0
        child_qty = self.total_qty / self.n_slices

        if ref_price > 0 and child_qty * ref_price < MIN_CHILD_QTY_USD:
            n = max(1, int(self.total_qty * ref_price / MIN_CHILD_QTY_USD))
            child_qty = self.total_qty / n
            self.n_slices = n
            log.debug("[%s] Reduced to %d slices (min size $%.0f)",
                      self.symbol, n, MIN_CHILD_QTY_USD)

        interval = self.duration_minutes / max(self.n_slices, 1)

        children = []
        for i in range(self.n_slices):
            children.append({
                "slice_idx":       i + 1,
                "qty":             round(child_qty, 8),
                "delay_minutes":   round(i * interval, 1),
                "status":          "pending",
                "fill_price":      None,
                "fill_time":       None,
            })

        # Rough market impact estimate: sqrt(participation_rate) × 10bps
        participation = (self.total_qty / self.n_slices) / max(ref_price, 1)
        impact_bps = math.sqrt(max(participation, 0)) * 10

        plan = {
            "symbol":            self.symbol,
            "side":              self.side,
            "total_qty":         round(self.total_qty, 8),
            "n_slices":          self.n_slices,
            "duration_minutes":  self.duration_minutes,
            "child_qty":         round(child_qty, 8),
            "interval_minutes":  round(interval, 1),
            "ref_price":         ref_price,
            "estimated_notional_usd": round(self.total_qty * ref_price, 2) if ref_price else None,
            "estimated_market_impact_bps": round(impact_bps, 2),
            "children":          children,
            "created_at":        datetime.now(timezone.utc).isoformat(),
        }
        return plan

    def record_fill(self, slice_idx: int, fill_price: float, fill_qty: float) -> None:
        """Record a child order fill for VWAP quality tracking."""
        self.fills.append({
            "slice_idx":  slice_idx,
            "fill_price": fill_price,
            "fill_qty":   fill_qty,
            "timestamp":  datetime.now(timezone.utc).isoformat(),
        })

    def execution_quality(self, market_vwap: Optional[float] = None) -> dict:
        """
        Compute execution quality vs market VWAP.
        Returns slippage in bps (negative = better than VWAP).
        """
        if not self.fills:
            return {"status": "no_fills"}

        total_qty   = sum(f["fill_qty"] for f in self.fills)
        total_cost  = sum(f["fill_price"] * f["fill_qty"] for f in self.fills)
        avg_fill    = total_cost / total_qty if total_qty > 0 else 0

        result = {
            "avg_fill_price": round(avg_fill, 4),
            "total_qty":      round(total_qty, 8),
            "n_fills":        len(self.fills),
        }

        if market_vwap and market_vwap > 0:
            slippage_bps = ((avg_fill - market_vwap) / market_vwap) * 10_000
            if self.side == "buy":
                slippage_bps = -slippage_bps   # negative = paid less than VWAP = good
            result["market_vwap"]    = market_vwap
            result["slippage_bps"]   = round(slippage_bps, 2)
            result["quality_rating"] = (
                "EXCELLENT" if slippage_bps > 0 else
                "GOOD"      if slippage_bps > -5 else
                "FAIR"      if slippage_bps > -15 else
                "POOR"
            )

        return result


def build_vwap_plan(
    symbol:           str,
    qty:              float,
    side:             str,
    current_price:    float,
    duration_minutes: int = DEFAULT_DURATION,
    n_slices:         int = DEFAULT_SLICES,
) -> dict:
    """Convenience function — build and return a VWAP execution plan."""
    slicer = VWAPSlicer(
        symbol           = symbol,
        total_qty        = qty,
        side             = side,
        duration_minutes = duration_minutes,
        n_slices         = n_slices,
        entry_price      = current_price,
    )
    return slicer.build_plan(current_price)


def twap_plan(
    symbol:           str,
    qty:              float,
    side:             str,
    current_price:    float,
    duration_minutes: int = DEFAULT_DURATION,
    n_slices:         int = DEFAULT_SLICES,
) -> dict:
    """
    TWAP variant — equal-sized slices at equal time intervals.
    Identical to VWAP plan structure; actual TWAP vs VWAP differs only in
    whether slices are volume-weighted (requires live volume data).
    Returns same plan format as build_vwap_plan.
    """
    return build_vwap_plan(symbol, qty, side, current_price, duration_minutes, n_slices)


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)

    plan = build_vwap_plan(
        symbol           = "BTC",
        qty              = 0.5,
        side             = "buy",
        current_price    = 65_000.0,
        duration_minutes = 30,
        n_slices         = 6,
    )
    print(json.dumps(plan, indent=2))
    print(f"\nImpact estimate: {plan['estimated_market_impact_bps']} bps")
    print(f"Notional: ${plan['estimated_notional_usd']:,.0f}")
