"""
Monte Carlo Simulation for VaR / CVaR and Strategy Robustness.

Pure Python; depends on numpy and scipy. No other external dependencies.

Usage
-----
>>> import numpy as np
>>> from monte_carlo import simulate_returns, var_cvar_simulation, strategy_robustness

>>> rets = np.random.randn(500) * 0.02          # daily log-returns
>>> paths = simulate_returns(rets, n_simulations=10000, horizon_days=30, method='bootstrap')
>>> risk = var_cvar_simulation(paths, confidence=0.95)

>>> equity = np.cumprod(1 + rets) * 10000       # fake equity curve
>>> ci = strategy_robustness(equity, n_simulations=1000)
"""

from __future__ import annotations

from typing import Dict, Optional

import numpy as np
from scipy import stats


# ---------------------------------------------------------------------------
# 1. Return simulation
# ---------------------------------------------------------------------------

def simulate_returns(
    returns: np.ndarray,
    n_simulations: int = 10_000,
    horizon_days: int = 30,
    method: str = "bootstrap",
    random_state: Optional[int] = None,
) -> np.ndarray:
    """
    Simulate *cumulative* log-return paths.

    Parameters
    ----------
    returns : np.ndarray, shape (n_obs,)
        Historical daily (log) returns to bootstrap or fit from.
    n_simulations : int
        Number of Monte-Carlo paths to generate.
    horizon_days : int
        Number of days projected into the future for each path.
    method : {'bootstrap', 'parametric'}
        - 'bootstrap' — draw single-day returns with replacement, then
          cumsum to build each path.
        - 'parametric' — fit a normal (or t-location-scale) distribution to
          *returns*, sample from it, then cumsum.
    random_state : int or None
        Seed for reproducibility.

    Returns
    -------
    paths : np.ndarray, shape (n_simulations, horizon_days)
        Each row is a cumulative log-return path.  paths[i, t] is the
        cumulative return from day 0 through day t (0-indexed).  The final
        column is the total horizon log-return.
    """
    rng = np.random.default_rng(random_state)
    returns = np.asarray(returns, dtype=float)

    if method == "bootstrap":
        # Draw (n_simulations * horizon_days) single-day returns
        draws = rng.choice(returns, size=(n_simulations, horizon_days))
        paths = np.cumsum(draws, axis=1)

    elif method == "parametric":
        # Fit a t-distribution (more robust for financial returns)
        df, loc, scale = stats.t.fit(returns)
        draws = stats.t.rvs(df, loc, scale, size=(n_simulations, horizon_days),
                            random_state=rng)
        paths = np.cumsum(draws, axis=1)

    else:
        raise ValueError(f"Unknown method '{method}'; choose 'bootstrap' or 'parametric'")

    return paths


# ---------------------------------------------------------------------------
# 2. VaR / CVaR from simulated paths
# ---------------------------------------------------------------------------

def var_cvar_simulation(
    simulated_paths: np.ndarray,
    confidence: float = 0.95,
) -> Dict[str, float]:
    """
    Compute Value-at-Risk, Conditional VaR, and max-drawdown from simulated
    terminal outcomes.

    Parameters
    ----------
    simulated_paths : np.ndarray, shape (n_simulations, horizon_days)
        Cumulative log-return paths (as returned by `simulate_returns`).
    confidence : float
        Confidence level, e.g. 0.95 → 95% VaR/CVaR.

    Returns
    -------
    dict with keys:
        'var_95'          — Value-at-Risk at the given confidence (as a
                            negative return, so a 5% loss is returned as -0.05).
        'cvar_95'         — Conditional VaR (expected shortfall) at the same
                            confidence.
        'max_drawdown_95' — Worst maximum-drawdown within the worst (1-α)
                            fraction of paths.

    Notes
    -----
    - Paths are assumed to be *cumulative log-returns*.  A value of -0.20 in
      the final column means the portfolio lost ~18.1% (1 - exp(-0.20)).
    - VaR / CVaR are reported as *log-return* values (not simple returns).
      Convert via `1 - np.exp(risk['var_95'])` if needed.
    """
    terminal = np.asarray(simulated_paths[:, -1], dtype=float)
    n = len(terminal)
    alpha = 1.0 - confidence

    # Value-at-Risk: the α-quantile of terminal log-returns
    var = float(np.quantile(terminal, alpha))

    # Conditional VaR (expected shortfall): mean of returns ≤ VaR
    tail = terminal[terminal <= var]
    cvar = float(tail.mean()) if len(tail) > 0 else var

    # Max drawdown within the worst tail
    # For each path compute the peak-to-trough drawdown
    # drawdown[t] = 1 - exp(cumret[t] - running_max[t])
    # but in log-return space: peak log-cumret so far minus current cumret.
    peak = np.maximum.accumulate(simulated_paths, axis=1)
    drawdowns = peak - simulated_paths  # positive when below peak
    max_dd_per_path = drawdowns.max(axis=1)

    # Worst max-drawdown among the worst (1-α)% of terminal outcomes
    worst_idx = np.argsort(terminal)[: max(1, int(alpha * n))]
    max_dd_95 = float(max_dd_per_path[worst_idx].mean())

    c_label = str(int(confidence * 100))
    return {
        f"var_{c_label}": var,
        f"cvar_{c_label}": cvar,
        f"max_drawdown_{c_label}": max_dd_95,
    }


# ---------------------------------------------------------------------------
# 3. Strategy robustness (bootstrap equity curve)
# ---------------------------------------------------------------------------

def strategy_robustness(
    equity_curve: np.ndarray,
    n_simulations: int = 1_000,
    random_state: Optional[int] = None,
) -> Dict[str, Dict[str, float]]:
    """
    Bootstrap an equity curve to produce confidence intervals for key
    performance metrics.

    Parameters
    ----------
    equity_curve : np.ndarray, shape (n_periods,)
        Cumulative equity (or NAV) values over time.  Must be positive and
        length ≥ 2.
    n_simulations : int
        Number of bootstrap resamples.
    random_state : int or None
        Seed for reproducibility.

    Returns
    -------
    dict of the form:
        {
            'sharpe':      {'median': ..., 'ci_lower': ..., 'ci_upper': ...},
            'cvar_95':     {'median': ..., 'ci_lower': ..., 'ci_upper': ...},
            'max_drawdown':{'median': ..., 'ci_lower': ..., 'ci_upper': ...},
        }

    Notes
    -----
    - Bootstrapping is done on *period returns* (equity[t] / equity[t-1] - 1).
    - Sharpe is annualised assuming daily returns (252 trading days).
    - CI bounds are 2.5% and 97.5% percentiles of the bootstrap distribution.
    """
    equity = np.asarray(equity_curve, dtype=float)
    if len(equity) < 2:
        raise ValueError("equity_curve must have at least 2 observations")
    if np.any(equity <= 0):
        raise ValueError("equity_curve must be strictly positive")

    # Period returns (simple)
    returns = equity[1:] / equity[:-1] - 1.0
    n_days = len(returns)
    rng = np.random.default_rng(random_state)

    # Storage
    sharpe_samples = np.empty(n_simulations)
    cvar_samples = np.empty(n_simulations)
    maxdd_samples = np.empty(n_simulations)

    for i in range(n_simulations):
        # Bootstrap returns (with replacement)
        idx = rng.integers(0, n_days, size=n_days)
        boot_ret = returns[idx]

        # Reconstruct equity curve from bootstrapped returns
        boot_eq = np.cumprod(1 + boot_ret)

        # ---- Sharpe ratio (annualised) ----
        mu = boot_ret.mean()
        sigma = boot_ret.std(ddof=1)
        if sigma > 0:
            sharpe_samples[i] = (mu / sigma) * np.sqrt(252)
        else:
            sharpe_samples[i] = 0.0

        # ---- CVaR 95 ----
        alpha = 0.05
        var_val = np.quantile(boot_ret, alpha)
        tail = boot_ret[boot_ret <= var_val]
        cvar_samples[i] = tail.mean() if len(tail) > 0 else var_val

        # ---- Max drawdown ----
        peak = np.maximum.accumulate(boot_eq)
        drawdowns = (peak - boot_eq) / peak  # fractional drawdowns
        maxdd_samples[i] = drawdowns.max()

    # Build confidence-interval dicts
    def _ci(arr: np.ndarray) -> Dict[str, float]:
        return {
            "median": float(np.median(arr)),
            "ci_lower": float(np.quantile(arr, 0.025)),
            "ci_upper": float(np.quantile(arr, 0.975)),
        }

    return {
        "sharpe": _ci(sharpe_samples),
        "cvar_95": _ci(cvar_samples),
        "max_drawdown": _ci(maxdd_samples),
    }


# ---------------------------------------------------------------------------
# NovaTrade — trade-P&L resampling robustness check
# ---------------------------------------------------------------------------

import json as _json
import math as _math
import random as _random
from datetime import datetime as _datetime, timezone as _timezone
from typing import Optional as _Optional
from runtime_paths import data_root

_MEMORY_DIR = data_root() / "memory"
_JOURNAL_PATH = _MEMORY_DIR / "trade_journal.jsonl"
_REPORT_PATH = _MEMORY_DIR / "monte_carlo_report.json"
_ANNUALIZE = _math.sqrt(252)


def _load_closed_pnls() -> list:
    """Load realized P&L (%) from closed trades in trade_journal.jsonl."""
    if not _JOURNAL_PATH.exists():
        return []
    pnls = []
    with open(_JOURNAL_PATH) as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line:
                continue
            try:
                _r = _json.loads(_line)
                if _r.get("status") == "closed":
                    _pnl = _r.get("pnl") or _r.get("realized_pnl")
                    if _pnl is not None:
                        pnls.append(float(_pnl))
            except (ValueError, KeyError):
                continue
    return pnls


def _sharpe_from_trade_pnls(pnls: list) -> float:
    if len(pnls) < 3:
        return 0.0
    n = len(pnls)
    mean = sum(pnls) / n
    var = sum((p - mean) ** 2 for p in pnls) / (n - 1)
    std = _math.sqrt(var) if var > 0 else 0.0
    return (mean / std * _ANNUALIZE) if std > 0 else 0.0


def _max_drawdown_from_trade_pnls(pnls: list) -> float:
    equity, peak, max_dd = 1.0, 1.0, 0.0
    for p in pnls:
        equity *= (1 + p / 100)
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak * 100
        if dd > max_dd:
            max_dd = dd
    return round(max_dd, 4)


def _monthly_return_from_trade_pnls(pnls: list, trades_per_month: int = 20) -> float:
    buckets = [pnls[i:i + trades_per_month]
               for i in range(0, len(pnls), trades_per_month)
               if pnls[i:i + trades_per_month]]
    if not buckets:
        return 0.0
    return round(sum(sum(b) for b in buckets) / len(buckets), 4)


def run_trade_simulation(pnls: list, n_simulations: int = 1_000, seed: int = 42) -> dict:
    """
    Resample closed trade P&Ls n_simulations× with replacement.
    Returns p10/p50/p90 distributions for Sharpe, drawdown, monthly return.
    """
    if not pnls:
        return {"error": "no_closed_trades"}
    n = len(pnls)
    rng = _random.Random(seed)
    sharpes, drawdowns, monthlies = [], [], []
    for _ in range(n_simulations):
        sample = [rng.choice(pnls) for _ in range(n)]
        sharpes.append(_sharpe_from_trade_pnls(sample))
        drawdowns.append(_max_drawdown_from_trade_pnls(sample))
        monthlies.append(_monthly_return_from_trade_pnls(sample))

    def _pct(data, p):
        s = sorted(data)
        return round(s[max(0, min(int(len(s) * p / 100), len(s) - 1))], 4)

    return {
        "n_trades": n, "n_simulations": n_simulations,
        "sharpe":            {"p10": _pct(sharpes, 10),   "p50": _pct(sharpes, 50),   "p90": _pct(sharpes, 90)},
        "max_drawdown_pct":  {"p10": _pct(drawdowns, 10), "p50": _pct(drawdowns, 50), "p90": _pct(drawdowns, 90)},
        "monthly_return_pct":{"p10": _pct(monthlies, 10), "p50": _pct(monthlies, 50), "p90": _pct(monthlies, 90)},
    }


def check_strategy_robustness(sim: dict, phase1_targets: _Optional[dict] = None) -> dict:
    """
    Check robustness against Phase 1 targets.
    Returns {robust: bool, checks: {metric: {pass, value, threshold}}}.
    """
    if "error" in sim:
        return {"robust": False, "error": sim["error"]}
    if phase1_targets is None:
        try:
            from config import PHASE1
            phase1_targets = PHASE1
        except ImportError:
            phase1_targets = {"min_sharpe": 1.5, "max_drawdown_pct": 15.0, "min_monthly_return": 3.0}

    checks = {
        "sharpe_p10": {
            "pass": sim["sharpe"]["p10"] >= phase1_targets.get("min_sharpe", 1.5) * 0.7,
            "value": sim["sharpe"]["p10"],
            "threshold": phase1_targets.get("min_sharpe", 1.5) * 0.7,
        },
        "drawdown_p90": {
            "pass": sim["max_drawdown_pct"]["p90"] <= phase1_targets.get("max_drawdown_pct", 15.0) * 1.3,
            "value": sim["max_drawdown_pct"]["p90"],
            "threshold": phase1_targets.get("max_drawdown_pct", 15.0) * 1.3,
        },
        "monthly_p10": {
            "pass": sim["monthly_return_pct"]["p10"] >= phase1_targets.get("min_monthly_return", 3.0) * 0.5,
            "value": sim["monthly_return_pct"]["p10"],
            "threshold": phase1_targets.get("min_monthly_return", 3.0) * 0.5,
        },
    }
    return {"robust": all(c["pass"] for c in checks.values()), "checks": checks}


def run_trade_robustness_report(seed: int = 42) -> dict:
    """Load trades, simulate, check robustness, persist to memory/."""
    pnls = _load_closed_pnls()
    sim = run_trade_simulation(pnls, seed=seed)
    robustness = check_strategy_robustness(sim)
    report = {
        "generated_at": _datetime.now(_timezone.utc).isoformat(),
        "simulation": sim,
        "robustness": robustness,
    }
    _MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    with open(_REPORT_PATH, "w") as _f:
        _json.dump(report, _f, indent=2)
    return report


# ---------------------------------------------------------------------------
# Quick smoke-test  (run with `python monte_carlo.py`)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Generate synthetic daily returns
    rng = np.random.default_rng(42)
    true_returns = rng.normal(0.0005, 0.02, size=500)

    print("=" * 60)
    print("1. simulate_returns (bootstrap)")
    paths_bs = simulate_returns(true_returns, n_simulations=5000, horizon_days=20,
                                method="bootstrap", random_state=1)
    print(f"   shape: {paths_bs.shape}")

    print("\n2. simulate_returns (parametric)")
    paths_pm = simulate_returns(true_returns, n_simulations=5000, horizon_days=20,
                                method="parametric", random_state=1)
    print(f"   shape: {paths_pm.shape}")

    print("\n3. var_cvar_simulation")
    risk = var_cvar_simulation(paths_bs, confidence=0.95)
    for k, v in risk.items():
        print(f"   {k:>20s}: {v:+.4f}  (simple return: {1 - np.exp(v):+.4f})")

    print("\n4. strategy_robustness")
    equity = np.cumprod(1 + true_returns) * 10000
    ci = strategy_robustness(equity, n_simulations=1000, random_state=2)
    for metric, vals in ci.items():
        print(f"   {metric}:")
        for k, v in vals.items():
            print(f"      {k:>10s}: {v:+.6f}")

    print("\nAll smoke-tests passed.")
