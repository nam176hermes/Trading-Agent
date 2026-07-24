#!/usr/bin/env python3
"""
Statistical Arbitrage Engine — Cointegration-Based Pairs Trading

Core functions:
  test_cointegration()  — Engle-Granger cointegration test
  find_pairs()          — Scan all pairs, rank by p-value
  generate_pair_signal() — Z-score based entry/exit signals

Dependencies: numpy, scipy, statsmodels
"""

import itertools
from typing import Optional

import numpy as np

# ---------------------------------------------------------------------------
# Graceful ImportError handling for optional dependencies
# ---------------------------------------------------------------------------

_statsmodels_available = False
_adfuller = None
_OLS = None

try:
    from statsmodels.tsa.stattools import adfuller as _adfuller
    from statsmodels.regression.linear_model import OLS as _OLS
    _statsmodels_available = True
except ImportError:
    pass

# ---------------------------------------------------------------------------
# 1. Engle-Granger Cointegration Test
# ---------------------------------------------------------------------------

def test_cointegration(
    prices_a: np.ndarray,
    prices_b: np.ndarray,
    adf_maxlag: Optional[int] = None,
) -> dict:
    """
    Engle-Granger two-step cointegration test.

    Parameters
    ----------
    prices_a : np.ndarray
        Price series for asset A (the dependent variable).
    prices_b : np.ndarray
        Price series for asset B (the independent variable).
    adf_maxlag : int or None
        Maximum lags for the ADF test.  None lets statsmodels choose.

    Returns
    -------
    dict with keys:
        cointegrated  : bool      — True if p-value < 0.05
        p_value       : float     — MacKinnon approximate p-value from ADF
        hedge_ratio   : float     — OLS slope (beta)
        intercept     : float     — OLS intercept (alpha)
        spread        : np.array  — residuals = A - (alpha + beta * B)
        half_life     : float     — estimated half-life of mean reversion
        adf_stat      : float     — ADF test statistic
        adf_crit_5pct : float     — 5% critical value from ADF
    """
    if not _statsmodels_available:
        raise ImportError(
            "statsmodels is required for cointegration tests. "
            "Install it with: pip install statsmodels"
        )

    if len(prices_a) != len(prices_b):
        raise ValueError(
            f"Price series must have equal length, got {len(prices_a)} vs {len(prices_b)}"
        )

    if len(prices_a) < 30:
        raise ValueError(
            f"Need at least 30 observations for cointegration test, got {len(prices_a)}"
        )

    # Step 1: OLS regression A = alpha + beta * B
    X = np.column_stack([np.ones(len(prices_b)), prices_b])
    model = _OLS(prices_a, X).fit()
    intercept = model.params[0]
    hedge_ratio = model.params[1]
    spread = model.resid

    # Step 2: ADF test on residuals (no constant, no trend — Engle-Granger residuals
    #         are already detrended by construction)
    adf_kwargs = {"regression": "n"}
    if adf_maxlag is not None:
        adf_kwargs["maxlag"] = adf_maxlag
    adf_result = _adfuller(spread, **adf_kwargs)

    adf_stat = float(adf_result[0])
    p_value = float(adf_result[1])
    # adfuller returns critical values at 1%, 5%, 10%
    adf_crit_5pct = float(adf_result[4].get("5%", np.nan))

    cointegrated = p_value < 0.05

    # Half-life of mean reversion
    half_life = _estimate_half_life(spread)

    return {
        "cointegrated": cointegrated,
        "p_value": p_value,
        "hedge_ratio": float(hedge_ratio),
        "intercept": float(intercept),
        "spread": spread,
        "half_life": float(half_life),
        "adf_stat": adf_stat,
        "adf_crit_5pct": adf_crit_5pct,
    }


# ---------------------------------------------------------------------------
# 2. Find Cointegrated Pairs
# ---------------------------------------------------------------------------

def find_pairs(price_dict: dict, max_pairs: int = 50) -> list:
    """
    Test all unique pairs in *price_dict* for cointegration.

    Parameters
    ----------
    price_dict : dict[str, np.ndarray]
        Mapping from symbol → price array.  All arrays must be the same length
        and aligned to the same time index.

    max_pairs : int
        Maximum number of pairs to test (default 50).  For N symbols there
        are N*(N-1)/2 pairs, which can be large.

    Returns
    -------
    list[dict]
        Results sorted by p-value (ascending).  Each dict contains:
          symbol_a, symbol_b, cointegrated, p_value, hedge_ratio,
          intercept, half_life, adf_stat, adf_crit_5pct, spread
        Symbol pair order is determined by OLS direction (A regressed on B).
    """
    if not _statsmodels_available:
        raise ImportError(
            "statsmodels is required for cointegration tests. "
            "Install it with: pip install statsmodels"
        )

    symbols = list(price_dict.keys())
    n = len(symbols)

    if n < 2:
        return []

    total_pairs = n * (n - 1) // 2
    pairs_to_test = min(total_pairs, max_pairs)

    # Generate all pairs; if too many, sample a subset
    all_combos = list(itertools.combinations(symbols, 2))
    if total_pairs > max_pairs:
        rng = np.random.RandomState(42)
        indices = rng.choice(len(all_combos), size=max_pairs, replace=False)
        combos = [all_combos[i] for i in indices]
    else:
        combos = all_combos

    results = []
    for a, b in combos:
        try:
            res = test_cointegration(price_dict[a], price_dict[b])
        except Exception:
            continue

        results.append({
            "symbol_a": a,
            "symbol_b": b,
            "cointegrated": res["cointegrated"],
            "p_value": res["p_value"],
            "hedge_ratio": res["hedge_ratio"],
            "intercept": res["intercept"],
            "half_life": res["half_life"],
            "adf_stat": res["adf_stat"],
            "adf_crit_5pct": res["adf_crit_5pct"],
            "spread": res["spread"],
        })

    results.sort(key=lambda x: x["p_value"])
    return results


# ---------------------------------------------------------------------------
# 3. Pair Trading Signal
# ---------------------------------------------------------------------------

def generate_pair_signal(
    symbol_a: str,
    symbol_b: str,
    prices_a: np.ndarray,
    prices_b: np.ndarray,
    hedge_ratio: float,
    intercept: float = 0.0,
    z_score_threshold: float = 2.0,
    lookback: Optional[int] = None,
) -> dict:
    """
    Generate a trading signal from the current state of a pair.

    Signal logic (standard mean-reversion):
      - spread = price_A - (intercept + hedge_ratio * price_B)
      - z = (current_spread - mean(spread)) / std(spread)
      - If z > +threshold  → SHORT_SPREAD  (sell A, buy B)
      - If z < -threshold  → LONG_SPREAD   (buy A, sell B)
      - If cross through 0 → CLOSE         (neutralise position)
      - Otherwise          → HOLD

    Parameters
    ----------
    symbol_a, symbol_b : str
        Symbol names (for the return dict).
    prices_a, prices_b : np.ndarray
        Full price series (must include both lookback AND current price).
    hedge_ratio : float
        OLS slope (beta) from cointegration regression.
    intercept : float
        OLS intercept (alpha).  Default 0 (assumes pre-demeaned spread).
    z_score_threshold : float
        Entry threshold in z-score units (default 2.0).
    lookback : int or None
        Number of recent observations to use for mean/std estimation.
        None means use the entire series.

    Returns
    -------
    dict with keys:
        action   : 'LONG_SPREAD' | 'SHORT_SPREAD' | 'CLOSE' | 'HOLD'
        z_score  : float   — current z-score
        spread   : float   — current raw spread value
    """
    # Full spread series
    spread_series = prices_a - (intercept + hedge_ratio * prices_b)

    if len(spread_series) < 2:
        return {"action": "HOLD", "z_score": np.nan, "spread": np.nan}

    # Estimation window
    est = spread_series if lookback is None else spread_series[-lookback:]
    if len(est) < 2:
        return {"action": "HOLD", "z_score": np.nan, "spread": np.nan}

    spread_mean = np.mean(est)
    spread_std = np.std(est, ddof=1)
    current_spread = spread_series[-1]
    prev_spread = spread_series[-2]

    if spread_std < 1e-12:
        # Degenerate — zero-variance spread
        return {"action": "HOLD", "z_score": 0.0, "spread": current_spread}

    z_current = (current_spread - spread_mean) / spread_std
    z_prev = (prev_spread - spread_mean) / spread_std

    # Check for zero-crossing close signal: sign changed from previous bar
    if z_prev * z_current < 0:
        action = "CLOSE"
    elif z_current > z_score_threshold:
        action = "SHORT_SPREAD"
    elif z_current < -z_score_threshold:
        action = "LONG_SPREAD"
    else:
        action = "HOLD"

    return {
        "action": action,
        "z_score": float(z_current),
        "spread": float(current_spread),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _estimate_half_life(spread: np.ndarray) -> float:
    """
    Estimate the half-life of mean reversion from an OU-like process.

    Uses the discrete-time approximation:
        ΔS_t = γ + θ * S_{t-1} + ε_t
        θ = slope from OLS
        half_life = -ln(2) / θ
    """
    lag = spread[:-1]
    diff = np.diff(spread)
    if len(lag) < 10:
        return np.nan

    # OLS: diff ~ intercept + theta * lag
    X = np.column_stack([np.ones(len(lag)), lag])
    try:
        beta = np.linalg.lstsq(X, diff, rcond=None)[0]
        theta = beta[1]
    except np.linalg.LinAlgError:
        return np.nan

    if theta >= 0:
        return np.inf  # Not mean-reverting
    return -np.log(2) / theta


def rolling_zscore(spread: np.ndarray, window: int) -> np.ndarray:
    """
    Compute rolling z-score of the spread.

    Returns an array of the same length as *spread*.  The first
    (window - 1) entries are NaN.
    """
    z = np.full_like(spread, np.nan, dtype=float)
    for i in range(window - 1, len(spread)):
        chunk = spread[i - window + 1 : i + 1]
        m = np.mean(chunk)
        s = np.std(chunk, ddof=1)
        if s > 1e-12:
            z[i] = (spread[i] - m) / s
        else:
            z[i] = 0.0
    return z


# ---------------------------------------------------------------------------
# NovaTrade — BTC/ETH log-spread Z-score signal (simple, no statsmodels)
# ---------------------------------------------------------------------------

import json as _json
import logging as _logging
import math as _math
from typing import Optional as _Optional
from runtime_paths import data_root

_log = _logging.getLogger("pairs_trader")
_DATA_DIR = data_root() / "memory" / "data"
_HIST_DIR = data_root() / "memory" / "backtest" / "historical"

ENTRY_Z = 2.0
EXIT_Z = 0.5
MIN_CORR = 0.70
SPREAD_LOOKBACK = 100


def _load_btc_eth_prices(symbol: str) -> list:
    cache = _DATA_DIR / f"{symbol}_prices.json"
    if cache.exists():
        try:
            return [float(p) for p in _json.loads(cache.read_text()) if p is not None]
        except Exception:
            pass
    csv = _HIST_DIR / f"{symbol}_1h.csv"
    if csv.exists():
        try:
            import pandas as _pd
            return _pd.read_csv(csv, usecols=["close"])["close"].astype(float).tail(500).tolist()
        except Exception:
            pass
    return []


def _simple_pearson(x: list, y: list) -> float:
    n = len(x)
    if n < 10:
        return 0.0
    mx, my = sum(x) / n, sum(y) / n
    num = sum((a - mx) * (b - my) for a, b in zip(x, y))
    sx = _math.sqrt(sum((a - mx) ** 2 for a in x))
    sy = _math.sqrt(sum((b - my) ** 2 for b in y))
    return num / (sx * sy) if sx > 0 and sy > 0 else 0.0


def compute_log_spread(
    btc_prices: list,
    eth_prices: list,
    lookback: int = SPREAD_LOOKBACK,
) -> dict:
    """
    Compute log(BTC/ETH) spread Z-score over the lookback window.
    Returns {z_score, spread_current, spread_mean, spread_std, correlation, n_obs}.
    """
    n = min(len(btc_prices), len(eth_prices), lookback)
    if n < 20:
        return {"error": "insufficient_data", "n_obs": n}
    btc, eth = btc_prices[-n:], eth_prices[-n:]
    spread = [_math.log(b / e) for b, e in zip(btc, eth) if b > 0 and e > 0]
    if len(spread) < 20:
        return {"error": "insufficient_data", "n_obs": len(spread)}
    mean = sum(spread) / len(spread)
    std = _math.sqrt(sum((s - mean) ** 2 for s in spread) / len(spread))
    z = (spread[-1] - mean) / std if std > 0 else 0.0
    return {
        "z_score": round(z, 4), "spread_current": round(spread[-1], 6),
        "spread_mean": round(mean, 6), "spread_std": round(std, 6),
        "correlation": round(_simple_pearson(btc, eth), 4), "n_obs": len(spread),
    }


def get_pairs_signal(
    btc_prices: _Optional[list] = None,
    eth_prices: _Optional[list] = None,
) -> dict:
    """
    BTC/ETH pairs trading signal based on log-spread Z-score.

    Returns:
        {direction, z_score, correlation, reason}
    direction: LONG_ETH_SHORT_BTC | LONG_BTC_SHORT_ETH | EXIT | HOLD
    """
    btc = btc_prices or _load_btc_eth_prices("BTC")
    eth = eth_prices or _load_btc_eth_prices("ETH")
    if not btc or not eth:
        return {"direction": "HOLD", "z_score": 0.0, "correlation": 0.0, "reason": "no_price_data"}

    spread = compute_log_spread(btc, eth)
    if "error" in spread:
        return {"direction": "HOLD", "z_score": 0.0, "correlation": 0.0, "reason": spread["error"]}

    z, corr = spread["z_score"], spread["correlation"]
    if corr < MIN_CORR:
        return {"direction": "HOLD", "z_score": z, "correlation": corr,
                "reason": f"correlation {corr:.2f} < {MIN_CORR} — not cointegrated"}

    if abs(z) < EXIT_Z:
        direction, reason = "EXIT", f"spread converged (|Z|={abs(z):.2f} < {EXIT_Z})"
    elif z > ENTRY_Z:
        direction, reason = "LONG_ETH_SHORT_BTC", f"Z={z:.2f} > {ENTRY_Z}: BTC overextended vs ETH"
    elif z < -ENTRY_Z:
        direction, reason = "LONG_BTC_SHORT_ETH", f"Z={z:.2f} < -{ENTRY_Z}: ETH overextended vs BTC"
    else:
        direction, reason = "HOLD", f"Z={z:.2f} within ±{ENTRY_Z}"

    _log.info("Pairs signal: %s (Z=%.2f, corr=%.2f)", direction, z, corr)
    return {
        "direction": direction, "z_score": z, "correlation": corr,
        "spread_mean": spread["spread_mean"], "spread_std": spread["spread_std"],
        "n_obs": spread["n_obs"], "reason": reason,
    }


# ---------------------------------------------------------------------------
# Test / Demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # ── Helpers for synthetic data ──
    def _simulate_cointegrated(n: int = 500, seed: int = 42) -> tuple:
        rng = np.random.RandomState(seed)
        # Random walk (common trend)
        rw = np.cumsum(rng.randn(n)) * 0.5
        # Two price series with a shared trend + noise
        price_a = rw + rng.randn(n) * 0.3 + 10.0
        price_b = rw + rng.randn(n) * 0.3 + 5.0
        return price_a, price_b

    def _simulate_not_cointegrated(n: int = 500, seed: int = 99) -> tuple:
        rng = np.random.RandomState(seed)
        # Two independent random walks with different drift directions
        price_a = np.cumsum(rng.randn(n) + 0.02) * 0.5 + 10.0
        price_b = np.cumsum(rng.randn(n) - 0.02) * 0.5 + 5.0
        return price_a, price_b

    print("=" * 60)
    print("Pairs Trader — Self-Test")
    print("=" * 60)

    # --- test_cointegration on a cointegrated pair ---
    print("\n1. test_cointegration (cointegrated pair)")
    ca, cb = _simulate_cointegrated(500)
    result = test_cointegration(ca, cb)
    for k, v in result.items():
        if isinstance(v, float):
            print(f"   {k}: {v:.6f}")
        elif isinstance(v, np.ndarray):
            print(f"   {k}: array len={len(v)}")
        else:
            print(f"   {k}: {v}")
    assert result["cointegrated"], "Expected cointegrated=True"
    print("   ✓ PASS")

    # --- test_cointegration on a non-cointegrated pair ---
    print("\n2. test_cointegration (non-cointegrated pair)")
    na, nb = _simulate_not_cointegrated(500)
    result2 = test_cointegration(na, nb)
    print(f"   cointegrated: {result2['cointegrated']}, p_value: {result2['p_value']:.6f}")
    assert isinstance(result2["cointegrated"], bool)
    assert isinstance(result2["p_value"], float)
    print("   ✓ PASS")

    # --- find_pairs ---
    print("\n3. find_pairs (multi-symbol scan)")
    rng = np.random.RandomState(7)
    rw = np.cumsum(rng.randn(300)) * 0.5
    price_dict = {
        "COIN_A": rw + rng.randn(300) * 0.3 + 10.0,
        "COIN_B": rw + rng.randn(300) * 0.3 + 5.0,
        "COIN_C": np.cumsum(rng.randn(300)) * 0.5 + 12.0,
        "COIN_D": rw + rng.randn(300) * 0.3 + 7.0,
    }
    pairs = find_pairs(price_dict)
    print(f"   Tested {len(pairs)} pairs")
    for p in pairs[:3]:
        print(f"   {p['symbol_a']:>8} / {p['symbol_b']:<8}  "
              f"coint={p['cointegrated']}  p={p['p_value']:.6f}  hr={p['hedge_ratio']:.4f}")
    assert len(pairs) > 0, "Expected at least one pair"
    # Best pair should be cointegrated
    assert pairs[0]["cointegrated"], "Best pair should be cointegrated"
    print("   ✓ PASS")

    # --- generate_pair_signal ---
    print("\n4. generate_pair_signal")
    # Use the best pair from the scan
    best = pairs[0]
    sig_long = generate_pair_signal(
        best["symbol_a"], best["symbol_b"],
        price_dict[best["symbol_a"]], price_dict[best["symbol_b"]],
        hedge_ratio=best["hedge_ratio"],
        intercept=best["intercept"],
        z_score_threshold=0.1,  # artificially low to trigger
    )
    print(f"   Low-threshold signal: {sig_long}")

    # Normal threshold — should typically be HOLD
    sig_normal = generate_pair_signal(
        best["symbol_a"], best["symbol_b"],
        price_dict[best["symbol_a"]], price_dict[best["symbol_b"]],
        hedge_ratio=best["hedge_ratio"],
        intercept=best["intercept"],
        z_score_threshold=2.0,
    )
    print(f"   Normal-threshold signal: {sig_normal}")
    assert sig_normal["action"] in ("HOLD", "LONG_SPREAD", "SHORT_SPREAD", "CLOSE")
    print("   ✓ PASS")

    # --- rolling_zscore ---
    print("\n5. rolling_zscore")
    # Non-degenerate spread that crosses
    spread = np.sin(np.linspace(0, 4 * np.pi, 200))
    z = rolling_zscore(spread, 30)
    assert np.isnan(z[0])
    assert not np.isnan(z[-1])
    # A full sine cycle should produce some crossings above 1
    assert np.any(np.abs(z[~np.isnan(z)]) > 1.0), "Expected z-score extremes"
    print("   ✓ PASS")

    # --- Edge cases ---
    print("\n6. Edge cases")
    # Short series
    try:
        test_cointegration(np.array([1.0, 2.0]), np.array([3.0, 4.0]))
        assert False, "Should have raised"
    except ValueError:
        print("   Short series → ValueError ✓")

    # Unequal lengths
    try:
        test_cointegration(np.arange(100), np.arange(50))
        assert False, "Should have raised"
    except ValueError:
        print("   Unequal length → ValueError ✓")

    # Empty dict
    assert find_pairs({}) == []
    print("   Empty dict → [] ✓")

    # Single-symbol dict
    assert find_pairs({"A": np.array([1, 2, 3])}) == []
    print("   Single symbol → [] ✓")

    print("\n" + "=" * 60)
    print("All tests passed.")
    print("=" * 60)
