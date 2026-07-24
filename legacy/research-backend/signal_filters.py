"""
Entry signal quality filters for crypto trading.

Multi-timeframe confirmation and volume-based filters
inspired by classic trading literature (Murphy, Elder, Minervini).
"""

from typing import Any


# ── Multi-Timeframe Confirmation ──────────────────────────────────────────────

def multi_timeframe_confirm(
    ta_4h: dict[str, Any],
    ta_1d: dict[str, Any],
    ta_1w: dict[str, Any],
    direction: str,
) -> tuple[bool, str]:
    """
    Confirm a trade signal by requiring alignment across three timeframes.

    The higher timeframes (1d, 1w) set the directional bias; the lowest
    timeframe (4h) provides the trigger.  A confirmed signal means all three
    are pointing the same way — trend, momentum, and entry timing align.

    Args:
        ta_4h: TA indicators for the 4-hour chart.  Expected keys:
               'rsi', 'macd_line', 'macd_signal', 'close', 'sma_20',
               'sma_50', 'ema_20', 'ema_50', 'volume'.
        ta_1d: Same shape as ta_4h for the daily chart.
        ta_1w: Same shape as ta_4h for the weekly chart.
        direction: 'BUY' or 'SELL'.

    Returns:
        (confirmed, reason) — confirmed is True when all checks pass;
        reason is a human-readable breakdown.
    """
    direction = direction.upper()
    if direction not in ("BUY", "SELL"):
        return False, f"Invalid direction '{direction}'. Must be BUY or SELL."

    is_bull = direction == "BUY"

    # ── Helper ────────────────────────────────────────────────────────────
    def _get(d: dict[str, Any], key: str, default: float | None = None) -> float | None:
        return d.get(key, default)

    def _check_rsi(d: dict[str, Any], bullish: bool) -> tuple[bool, str]:
        """Check RSI: above 50 for bulls, below 50 for bears."""
        rsi = _get(d, "rsi")
        if rsi is None:
            return True, "RSI missing (skipped)"
        if bullish:
            ok = rsi > 50
            return ok, f"RSI={rsi:.1f} {'>' if ok else '<='}50"
        else:
            ok = rsi < 50
            return ok, f"RSI={rsi:.1f} {'<' if ok else '>='}50"

    def _check_macd(d: dict[str, Any], bullish: bool) -> tuple[bool, str]:
        """Check MACD: line above signal for bulls, below for bears."""
        macd_line = _get(d, "macd_line")
        macd_signal = _get(d, "macd_signal")
        if macd_line is None or macd_signal is None:
            return True, "MACD missing (skipped)"
        if bullish:
            ok = macd_line > macd_signal
            return ok, f"MACD line ({macd_line:.4f}) {'>' if ok else '<='} signal ({macd_signal:.4f})"
        else:
            ok = macd_line < macd_signal
            return ok, f"MACD line ({macd_line:.4f}) {'<' if ok else '>='} signal ({macd_signal:.4f})"

    def _check_price_vs_sma50(d: dict[str, Any], bullish: bool) -> tuple[bool, str]:
        """Check price vs SMA-50: above for bulls, below for bears."""
        close = _get(d, "close")
        sma50 = _get(d, "sma_50")
        if close is None or sma50 is None:
            return True, "SMA-50 missing (skipped)"
        if bullish:
            ok = close > sma50
            return ok, f"Close ({close:.2f}) {'>' if ok else '<='} SMA-50 ({sma50:.2f})"
        else:
            ok = close < sma50
            return ok, f"Close ({close:.2f}) {'<' if ok else '>='} SMA-50 ({sma50:.2f})"

    def _check_trigger_rsi(ta: dict[str, Any], bullish: bool) -> tuple[bool, str]:
        """Check trigger: RSI oversold for buys, overbought for sells."""
        rsi = _get(ta, "rsi")
        if rsi is None:
            return True, "Trigger RSI missing (skipped)"
        if bullish:
            ok = rsi < 40  # oversold / pullback zone
            return ok, f"Trigger RSI={rsi:.1f} {'<' if ok else '>='}40 (needs oversold pullback)"
        else:
            ok = rsi > 60  # overbought / rally zone
            return ok, f"Trigger RSI={rsi:.1f} {'>' if ok else '<='}60 (needs overbought rally)"

    # ── Build the multi-timeframe check ───────────────────────────────────

    checks: list[tuple[bool, str]] = []

    # --- Weekly bias (heaviest weight) ---
    rsi_w, msg_w = _check_rsi(ta_1w, is_bull)
    checks.append((rsi_w, f"[1W]  {msg_w}"))

    macd_w, msg_w_m = _check_macd(ta_1w, is_bull)
    checks.append((macd_w, f"[1W]  {msg_w_m}"))

    price_w, msg_w_p = _check_price_vs_sma50(ta_1w, is_bull)
    checks.append((price_w, f"[1W]  {msg_w_p}"))

    # --- Daily bias ---
    macd_d, msg_d = _check_macd(ta_1d, is_bull)
    checks.append((macd_d, f"[1D]  {msg_d}"))

    price_d, msg_d_p = _check_price_vs_sma50(ta_1d, is_bull)
    checks.append((price_d, f"[1D]  {msg_d_p}"))

    # --- 4h trigger ---
    trigger_ok, trigger_msg = _check_trigger_rsi(ta_4h, is_bull)
    checks.append((trigger_ok, f"[4H]  {trigger_msg}"))

    # A bonus: 4h MACD should agree with direction too
    macd_4h, msg_4h = _check_macd(ta_4h, is_bull)
    checks.append((macd_4h, f"[4H]  {msg_4h}"))

    # ── Scoring ───────────────────────────────────────────────────────────
    passed = sum(1 for ok, _ in checks if ok)
    total = len(checks)
    grade = "STRONG" if passed == total else "GOOD" if passed >= total - 1 else "WEAK" if passed >= total - 2 else "MISMATCH"

    lines: list[str] = []
    lines.append(f"{direction} signal — {grade} multi-TF alignment ({passed}/{total})")
    for ok, msg in checks:
        mark = "✓" if ok else "✗"
        lines.append(f"  {mark} {msg}")

    reason = "\n".join(lines)

    # Require 4h trigger + at least one higher-TF bias signal
    if not trigger_ok:
        return False, reason + "\n→ REJECTED: 4H trigger condition not met"

    higher_tf_passed = sum(ok for ok, _ in checks[:5] if ok)  # first 5 = 1W+1D
    if higher_tf_passed < 2:
        return False, reason + f"\n→ REJECTED: insufficient higher-TF support ({higher_tf_passed}/5)"

    return True, reason + "\n→ CONFIRMED"


# ── Volume Confirmation ──────────────────────────────────────────────────────

def volume_confirm(
    volume: float,
    volume_avg_20: float,
    multiplier: float = 1.5,
) -> tuple[bool, str]:
    """
    Confirm that a breakout is backed by above-average volume.

    Classic rule: a breakout on thin volume is suspect.  Require volume
    to be at least `multiplier` times the 20-period average.

    Args:
        volume:         Current period volume.
        volume_avg_20: 20-period simple moving average of volume.
        multiplier:     Minimum ratio (default 1.5).

    Returns:
        (confirmed, reason) — confirmed is True when volume exceeds
        the threshold.
    """
    threshold = volume_avg_20 * multiplier
    ratio = volume / volume_avg_20 if volume_avg_20 > 0 else 0.0

    if ratio >= multiplier:
        return True, (
            f"Volume confirmed: {volume:.2f} ≥ {multiplier:.1f}× avg "
            f"(ratio={ratio:.2f}, threshold={threshold:.2f})"
        )
    else:
        return False, (
            f"Volume rejected: {volume:.2f} < {multiplier:.1f}× avg "
            f"(ratio={ratio:.2f}, threshold={threshold:.2f})"
        )


# ── Signal Filter Pipeline ───────────────────────────────────────────────────

def apply_signal_filters(
    signal: str,
    ta_context: dict[str, Any],
    filters: list[str],
) -> tuple[str, str]:
    """
    Run a signal through multiple quality filters.

    Each filter in `filters` is applied in order.  If any filter rejects
    the signal the output becomes 'HOLD', regardless of subsequent filters.

    Args:
        signal:     Initial signal — 'BUY', 'SELL', or 'HOLD'.
        ta_context: Dictionary of all TA data needed by the filters.
                    Expected structure:

                        {
                            '4h': { ... 4h indicators ... },
                            '1d': { ... 1d indicators ... },
                            '1w': { ... 1w indicators ... },
                            'current_volume': float,
                            'volume_avg_20':   float,
                        }

        filters:    Ordered list of filter names to apply.  Recognised values:
                    'multi_timeframe'  → multi_timeframe_confirm
                    'volume'           → volume_confirm

    Returns:
        (filtered_signal, filter_log) — filtered_signal is 'HOLD' if any
        filter rejects, otherwise the original signal.  filter_log is a
        multi-line summary of every filter's decision.
    """
    if signal.upper() == "HOLD":
        return "HOLD", "Signal is already HOLD — no filters applied."

    direction = signal.upper()
    if direction not in ("BUY", "SELL"):
        return "HOLD", f"Unknown signal '{signal}' → HOLD"

    lines: list[str] = [f"Applying filters to {direction} signal: {', '.join(filters)}", ""]
    all_passed = True

    for name in filters:
        name_lower = name.lower().strip()

        if name_lower == "multi_timeframe":
            ta_4h = ta_context.get("4h", {})
            ta_1d = ta_context.get("1d", {})
            ta_1w = ta_context.get("1w", {})
            ok, reason = multi_timeframe_confirm(ta_4h, ta_1d, ta_1w, direction)
            lines.append(f"── multi_timeframe ──")
            lines.append(reason)
            lines.append("")
            if not ok:
                all_passed = False
                break

        elif name_lower == "volume":
            vol = ta_context.get("current_volume")
            vol_avg = ta_context.get("volume_avg_20")
            if vol is None or vol_avg is None:
                lines.append("── volume ──")
                lines.append("SKIPPED: missing current_volume or volume_avg_20 in context")
                lines.append("")
                continue
            ok, reason = volume_confirm(float(vol), float(vol_avg))
            lines.append(f"── volume ──")
            lines.append(reason)
            lines.append("")
            if not ok:
                all_passed = False
                break

        else:
            lines.append(f"── {name} ──")
            lines.append(f"UNKNOWN filter — skipped")
            lines.append("")

    if all_passed:
        result = direction
        lines.append(f"✓ ALL FILTERS PASSED → {result}")
    else:
        result = "HOLD"
        lines.append(f"✗ FILTER REJECTED → downgraded to HOLD")

    return result, "\n".join(lines)
