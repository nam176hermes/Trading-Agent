"""
risk_engine.py — Production Risk Management
Implements CVaR-based position sizing, funding rate tracking,
and walk-forward validation gate. Replaces naive Kelly/fixed-fraction sizing.

Patterns from: 19 trading books research (May 2026)
- CVaR sizing: replaces Kelly (crypto kurtosis 15+ kills Kelly)
- Funding tracking: 100%+ APR cost completely absent from traditional books
- WF validation: rejects overfit strategies automatically
"""
import numpy as np
import logging
from typing import Optional, Dict, Tuple
from dataclasses import dataclass, field

log = logging.getLogger("risk_engine")

# ── Constants ────────────────────────────────────────────────────────────────
DEFAULT_CONFIDENCE_LEVEL = 0.95        # CVaR confidence level
MAX_POSITION_RISK_PCT = 0.02           # Max 2% risk per position
MAX_PORTFOLIO_RISK_PCT = 0.06          # Max 6% total portfolio risk
DAILY_LOSS_LIMIT_PCT = 0.03            # 3% daily loss circuit breaker
MAX_CONSECUTIVE_LOSSES = 3             # Pause after 3 consecutive losses
FUNDING_INTERVAL_HOURS = 8             # Standard perpetual funding interval
MIN_RETURNS_FOR_CVAR = 10             # Minimum return observations for CVaR
WF_CONSISTENCY_MIN = 0.30              # Minimum OOS consistency to pass gate
WF_SHARPE_MIN = 0.0                    # Minimum aggregate Sharpe
WF_SORTINO_MIN = 0.0                   # Minimum aggregate Sortino
WF_PROFITABLE_WINDOWS_MIN_PCT = 0.30   # At least 30% of windows profitable


@dataclass
class PositionSize:
    """CVaR-computed position size result."""
    symbol: str
    action: str  # BUY / SELL
    price: float
    quantity: float
    notional: float           # quantity * price
    risk_amount: float        # max dollar loss at this size
    capital_used_pct: float   # % of total capital
    cvar_95: float            # 95% CVaR of returns
    volatility_30d: float     # 30-day annualized volatility
    method: str = "cvar"


@dataclass
class FundingCost:
    """Funding rate cost estimate for a perpetual swap position."""
    symbol: str
    hourly_rate_pct: float    # funding rate per 8h interval, as percentage
    annualized_pct: float     # annualized cost
    cost_per_day_pct: float   # daily cost as % of position value
    is_expensive: bool        # True if >50% annualized
    note: str = ""


@dataclass
class ValidationGate:
    """Walk-forward validation result."""
    passed: bool
    consistency: float        # fraction of profitable windows
    aggregate_sharpe: float
    aggregate_sortino: float
    profitable_windows: int
    total_windows: int
    max_drawdown_pct: float
    rejection_reasons: list = field(default_factory=list)


# ── CVaR Position Sizing ─────────────────────────────────────────────────────

def compute_cvar(returns: np.ndarray, confidence: float = DEFAULT_CONFIDENCE_LEVEL) -> float:
    """
    Conditional Value at Risk (Expected Shortfall).
    The average loss in the worst (1-confidence)% of cases.
    Much more robust than Kelly/fixed-fraction for fat-tailed crypto returns.
    """
    if len(returns) < 10:
        return abs(np.nanmean(returns)) * 3 if not np.all(np.isnan(returns)) else 0.05

    var_cutoff = np.percentile(returns, (1 - confidence) * 100)
    tail_losses = returns[returns <= var_cutoff]
    if len(tail_losses) == 0:
        return abs(var_cutoff)
    return abs(np.mean(tail_losses))


def cvar_position_size(
    symbol: str,
    price: float,
    action: str,
    capital: float,
    returns_30d: np.ndarray,
    confidence: float = DEFAULT_CONFIDENCE_LEVEL,
    max_risk_pct: float = MAX_POSITION_RISK_PCT,
) -> Optional[PositionSize]:
    """
    Compute position size using CVaR.

    Formula: quantity = (capital * max_risk_pct) / (price * cvar_95 * safety_multiplier)

    The safety_multiplier (3.0) accounts for the fact that crypto tail events
    are worse than what 30-day historical data captures. This was the #1 finding
    from the adversarial book audit: standard formulas underestimate crypto risk
    by 3-5x.
    """
    if returns_30d is None or len(returns_30d) < 10:
        log.warning("[%s] Insufficient returns data for CVaR sizing (%d obs)", symbol, len(returns_30d) if returns_30d is not None else 0)
        return None

    cvar_95 = compute_cvar(returns_30d, confidence)
    vol_30d = float(np.std(returns_30d) * np.sqrt(365))  # annualized

    if cvar_95 <= 0 or np.isnan(cvar_95):
        log.warning("[%s] CVaR is zero or NaN — cannot size position", symbol)
        return None

    # Safety multiplier: crypto tails are 3-5x worse than historical
    # Source: ScienceDirect paper on crypto tail risk
    SAFETY_MULTIPLIER = 3.0

    risk_dollars = capital * max_risk_pct
    risk_per_unit = price * cvar_95 * SAFETY_MULTIPLIER
    quantity = risk_dollars / risk_per_unit

    notional = quantity * price

    # Cap at max portfolio risk
    if notional > capital * MAX_PORTFOLIO_RISK_PCT:
        quantity = (capital * MAX_PORTFOLIO_RISK_PCT) / price
        notional = quantity * price

    if quantity <= 0:
        return None

    return PositionSize(
        symbol=symbol,
        action=action,
        price=price,
        quantity=round(quantity, 8),
        notional=round(notional, 2),
        risk_amount=round(risk_dollars, 2),
        capital_used_pct=round(notional / capital * 100, 2),
        cvar_95=round(cvar_95, 6),
        volatility_30d=round(vol_30d, 4),
        method="cvar",
    )


# ── Funding Rate Tracking ────────────────────────────────────────────────────

def estimate_funding_cost(
    symbol: str,
    funding_rate_8h: float,   # e.g. 0.0001 = 0.01%
    position_notional: float,
    expected_hold_hours: int = 168,  # default 1 week
) -> FundingCost:
    """
    Estimate funding rate cost for a perpetual swap position.
    This is the #1 hidden cost absent from all traditional trading books.
    During bull runs, funding can hit 0.1-0.15% per 8h = 109-164% annualized.
    """
    hourly_rate_pct = funding_rate_8h * 100
    intervals_per_day = 24 / FUNDING_INTERVAL_HOURS  # 3 funding events/day
    annualized = funding_rate_8h * intervals_per_day * 365 * 100
    cost_per_day_pct = funding_rate_8h * intervals_per_day * 100

    intervals_held = expected_hold_hours / FUNDING_INTERVAL_HOURS
    total_cost = funding_rate_8h * position_notional * intervals_held

    is_expensive = annualized > 50.0

    note = ""
    if is_expensive:
        note = f"HIGH FUNDING: {annualized:.0f}% APR — consider shorting instead"
    elif annualized > 20:
        note = f"Moderate funding: {annualized:.0f}% APR"

    return FundingCost(
        symbol=symbol,
        hourly_rate_pct=round(hourly_rate_pct, 4),
        annualized_pct=round(annualized, 1),
        cost_per_day_pct=round(cost_per_day_pct, 2),
        is_expensive=is_expensive,
        note=note,
    )


def funding_aware_pnl(
    raw_pnl: float,
    symbol: str,
    funding_rate_8h: float,
    position_notional: float,
    bars_held: int,
    bar_interval_hours: float = 1.0,
) -> Tuple[float, float]:
    """
    Adjust raw P&L for funding rate costs.
    Returns (adjusted_pnl, funding_cost).

    Every 8 hours, a funding payment occurs. This function approximates
    the total funding cost over the position's holding period.
    """
    if funding_rate_8h is None or funding_rate_8h == 0:
        return raw_pnl, 0.0

    hours_held = bars_held * bar_interval_hours
    funding_events = hours_held / FUNDING_INTERVAL_HOURS
    funding_cost = funding_rate_8h * position_notional * funding_events

    adjusted_pnl = raw_pnl - funding_cost

    if funding_cost > 0 and abs(funding_cost) > abs(raw_pnl) * 0.1:
        log.info("[%s] Funding cost: $%.2f over %d bars (%.1fh) — P&L adjusted from $%.2f to $%.2f",
                 symbol, funding_cost, bars_held, hours_held, raw_pnl, adjusted_pnl)

    return adjusted_pnl, funding_cost


# ── Walk-Forward Validation Gate ─────────────────────────────────────────────

def validate_walk_forward(
    sharpe_per_window: list,
    sortino_per_window: list,
    pnl_per_window: list,
    max_drawdown: float,
) -> ValidationGate:
    """
    Gate that rejects overfit strategies.
    A strategy must pass ALL checks to be considered valid for live deployment.

    Source: Algo Trading Cheat Codes (Davey), Building Winning Algo Systems (Davey)
    Adversarial finding: most retail strategies fail at least one of these.
    """
    reasons = []
    total_windows = len(sharpe_per_window)

    if total_windows == 0:
        return ValidationGate(
            passed=False, consistency=0.0, aggregate_sharpe=0.0,
            aggregate_sortino=0.0, profitable_windows=0, total_windows=0,
            max_drawdown_pct=max_drawdown, rejection_reasons=["No windows to validate"]
        )

    profitable_windows = sum(1 for s in sharpe_per_window if s > 0)
    profitable_pnl = sum(1 for p in pnl_per_window if p > 0)
    consistency = profitable_pnl / total_windows if total_windows > 0 else 0.0
    aggregate_sharpe = float(np.mean(sharpe_per_window)) if sharpe_per_window else 0.0
    aggregate_sortino = float(np.mean(sortino_per_window)) if sortino_per_window else 0.0

    # Check 1: Consistency threshold
    if consistency < WF_CONSISTENCY_MIN:
        reasons.append(
            f"Consistency {consistency:.0%} < {WF_CONSISTENCY_MIN:.0%} minimum "
            f"({profitable_pnl}/{total_windows} profitable windows)"
        )

    # Check 2: Sharpe threshold
    if aggregate_sharpe < WF_SHARPE_MIN:
        reasons.append(f"Aggregate Sharpe {aggregate_sharpe:.2f} < {WF_SHARPE_MIN} minimum")

    # Check 3: Sortino threshold
    if aggregate_sortino < WF_SORTINO_MIN:
        reasons.append(f"Aggregate Sortino {aggregate_sortino:.2f} < {WF_SORTINO_MIN} minimum")

    # Check 4: Profitable windows threshold
    profitable_pct = profitable_windows / total_windows
    if profitable_pct < WF_PROFITABLE_WINDOWS_MIN_PCT:
        reasons.append(
            f"Profitable Sharpe windows {profitable_pct:.0%} < "
            f"{WF_PROFITABLE_WINDOWS_MIN_PCT:.0%} minimum"
        )

    # Check 5: Max drawdown
    if max_drawdown > 0.20:  # 20% max acceptable drawdown
        reasons.append(f"Max drawdown {max_drawdown:.1%} exceeds 20% limit")

    passed = len(reasons) == 0

    result = ValidationGate(
        passed=passed,
        consistency=round(consistency, 4),
        aggregate_sharpe=round(aggregate_sharpe, 4),
        aggregate_sortino=round(aggregate_sortino, 4),
        profitable_windows=profitable_windows,
        total_windows=total_windows,
        max_drawdown_pct=round(max_drawdown, 4),
        rejection_reasons=reasons,
    )

    if not passed:
        log.warning("VALIDATION GATE: REJECTED — %s", "; ".join(reasons))
    else:
        log.info("VALIDATION GATE: PASSED — consistency=%.0f%% Sharpe=%.2f Sortino=%.2f "
                 "profitable=%d/%d DD=%.1f%%",
                 consistency * 100, aggregate_sharpe, aggregate_sortino,
                 profitable_windows, total_windows, max_drawdown * 100)

    return result


# ── Daily Loss Circuit Breaker ────────────────────────────────────────────────

def check_circuit_breaker(
    daily_pnl: float,
    starting_capital: float,
    consecutive_losses: int,
    daily_loss_limit_pct: float = DAILY_LOSS_LIMIT_PCT,
    max_consecutive: int = MAX_CONSECUTIVE_LOSSES,
) -> Tuple[bool, str]:
    """
    Circuit breaker: halt trading if daily limits breached.
    Pattern from: Algo Trading Cheat Codes (Davey), Entry/Exit Confessions (Davey)
    """
    reasons = []

    if starting_capital > 0:
        daily_loss_pct = abs(daily_pnl) / starting_capital
        if daily_pnl < 0 and daily_loss_pct >= daily_loss_limit_pct:
            reasons.append(
                f"Daily loss {daily_loss_pct:.1%} >= {daily_loss_limit_pct:.1%} limit"
            )

    if consecutive_losses >= max_consecutive:
        reasons.append(
            f"Consecutive losses ({consecutive_losses}) >= max ({max_consecutive})"
        )

    halted = len(reasons) > 0
    return halted, "; ".join(reasons) if reasons else ""
