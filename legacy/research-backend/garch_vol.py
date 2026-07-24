"""
GARCH(1,1) Volatility Forecasting with EWMA Fallback.

Priority order:
  1. `arch` library GARCH(1,1) maximum-likelihood estimation
  2. EWMA (Exponentially Weighted Moving Average) via pandas .ewm

Exposed API:
  - fit_garch(returns, p=1, q=1) -> dict
  - forecast_volatility(returns, horizon=1) -> float
  - compare_with_atr(returns, atr_14) -> dict
"""

import numpy as np
import warnings

# ---------------------------------------------------------------------------
# Optional `arch` import — only used when the package is installed
# ---------------------------------------------------------------------------
try:
    from arch import arch_model

    _HAS_ARCH = True
except ImportError:
    _HAS_ARCH = False


# ===========================================================================
# Public API
# ===========================================================================


def fit_garch(returns, p=1, q=1):
    """
    Fit GARCH(p,q) and return one-step-ahead conditional volatility.

    Returns a dict with GARCH parameters (omega, alpha, beta), the
    forecast daily standard deviation, annualised volatility, and a
    convergence flag.

    Falls back to EWMA (lambda=0.94) when:
      * The ``arch`` package is not installed, or
      * GARCH optimisation fails to converge / raises an exception.

    Parameters
    ----------
    returns : np.ndarray
        1-D array of log-returns or simple returns.
    p : int
        GARCH (symmetry) lag order (default 1).
    q : int
        ARCH (innovation) lag order (default 1).

    Returns
    -------
    dict
        ``omega``          — constant term (np.nan for EWMA)
        ``alpha``          — ARCH coefficient  (np.nan for EWMA)
        ``beta``           — GARCH coefficient  (np.nan for EWMA)
        ``forecast``       — one-step-ahead daily volatility (std dev)
        ``annualized_vol`` — forecast * sqrt(252)
        ``converged``      — bool (False for EWMA or failed GARCH)
        ``method``         — ``'garch'`` or ``'ewma'``
    """
    returns = np.asarray(returns, dtype=float).ravel()
    returns = returns[~np.isnan(returns)]

    if len(returns) < 30:
        raise ValueError(
            f"Need at least 30 observations for stable estimation, "
            f"got {len(returns)}"
        )

    if _HAS_ARCH:
        try:
            return _fit_with_arch(returns, p, q)
        except Exception:
            pass  # fall through to EWMA

    return _fit_with_ewma(returns)


def forecast_volatility(returns, horizon=1):
    """
    One-step-ahead daily volatility forecast.

    Convenience wrapper around ``fit_garch``.

    Parameters
    ----------
    returns : np.ndarray
        1-D array of returns.
    horizon : int
        Forecast horizon (currently only horizon=1 is computed).

    Returns
    -------
    float
        Forecast daily standard deviation.
    """
    result = fit_garch(returns)
    return result["forecast"]


def compare_with_atr(returns, atr_14):
    """
    Compare GARCH / EWMA volatility against a 14-period ATR for regime
    detection.

    The ATR is assumed to already be normalised to the same scale as the
    GARCH annualised vol (e.g. ATR expressed as a fraction of price).

    Parameters
    ----------
    returns : np.ndarray
        1-D array of returns.
    atr_14 : float
        14-period Average True Range (price-scale, annualised equivalent).

    Returns
    -------
    dict
        ``garch_annualized_vol`` — GARCH/EWMA annualised vol
        ``atr_14``               — input ATR
        ``ratio``               — garch_annualized_vol / atr_14
        ``regime``              — ``'low_vol'`` | ``'normal'`` | ``'high_vol'``
        ``interpretation``      — human-readable sentence
        ``method``              — ``'garch'`` or ``'ewma'``
    """
    result = fit_garch(returns)
    gav = result["annualized_vol"]
    atr = max(float(atr_14), 1e-12)
    ratio = gav / atr

    if ratio < 0.5:
        regime = "low_vol"
    elif ratio < 1.5:
        regime = "normal"
    else:
        regime = "high_vol"

    interpretations = {
        "low_vol": (
            f"GARCH vol ({gav:.4f}) << ATR ({atr:.4f}) — ratio={ratio:.3f}. "
            f"Low-volatility regime: trend-following may underperform."
        ),
        "normal": (
            f"GARCH vol ({gav:.4f}) ≈ ATR ({atr:.4f}) — ratio={ratio:.3f}. "
            f"Normal volatility regime."
        ),
        "high_vol": (
            f"GARCH vol ({gav:.4f}) >> ATR ({atr:.4f}) — ratio={ratio:.3f}. "
            f"High-volatility regime: consider reducing position size."
        ),
    }

    return {
        "garch_annualized_vol": float(gav),
        "atr_14": float(atr),
        "ratio": float(ratio),
        "regime": regime,
        "interpretation": interpretations[regime],
        "method": result["method"],
    }


# ===========================================================================
# Internal estimators
# ===========================================================================


def _fit_with_arch(returns, p, q):
    """Fit GARCH(p,q) via maximum likelihood (arch library)."""
    # Scale to percentages for numerical stability
    returns_pct = returns * 100.0

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = arch_model(
            returns_pct,
            mean="Zero",
            vol="GARCH",
            p=p,
            q=q,
            dist="normal",
        )
        res = model.fit(disp="off", show_warning=False)

    params = res.params
    omega = float(params.get("omega", np.nan))
    alpha = float(params.get("alpha[1]", np.nan))
    beta = float(params.get("beta[1]", np.nan))
    converged = bool(getattr(res, "convergence_flag", 1) == 0)

    # One-step-ahead variance forecast (de-scale from pct²)
    fc = res.forecast(horizon=1, reindex=False)
    var_fc = float(fc.variance.values[-1, 0])
    var_fc = max(var_fc, 0.0)  # guard negative
    vol_fc = np.sqrt(var_fc) / 100.0

    annualized = vol_fc * np.sqrt(252)

    return {
        "omega": omega,
        "alpha": alpha,
        "beta": beta,
        "forecast": vol_fc,
        "annualized_vol": annualized,
        "converged": converged,
        "method": "garch",
    }


def _fit_with_ewma(returns):
    """Fallback: EWMA volatility with RiskMetrics lambda=0.94."""
    import pandas as pd

    lam = 0.94
    r2 = returns ** 2

    ewm_var = pd.Series(r2).ewm(alpha=(1.0 - lam), adjust=False).mean()
    latest_var = float(ewm_var.iloc[-1])
    latest_var = max(latest_var, 0.0)
    vol_fc = np.sqrt(latest_var)
    annualized = vol_fc * np.sqrt(252)

    return {
        "omega": np.nan,
        "alpha": np.nan,
        "beta": np.nan,
        "forecast": vol_fc,
        "annualized_vol": annualized,
        "converged": False,
        "method": "ewma",
        "ewma_lambda": lam,
    }


# ===========================================================================
# Quick smoke-test
# ===========================================================================
if __name__ == "__main__":
    # Generate synthetic returns with volatility clustering
    rng = np.random.default_rng(42)
    n = 500
    shocks = rng.normal(0, 1, n)
    sigma = np.ones(n)
    for t in range(1, n):
        sigma[t] = np.sqrt(0.05 + 0.15 * shocks[t - 1] ** 2 + 0.75 * sigma[t - 1] ** 2)
    returns = shocks * sigma

    result = fit_garch(returns)
    print("fit_garch result:")
    for k, v in result.items():
        print(f"  {k}: {v}")

    fwd = forecast_volatility(returns)
    print(f"\nforecast_volatility: {fwd:.6f}")

    # Simulate an ATR value (annualised ≈ 30% vol)
    atr = 0.30
    comp = compare_with_atr(returns, atr)
    print(f"\ncompare_with_atr (atr={atr}):")
    for k, v in comp.items():
        print(f"  {k}: {v}")
