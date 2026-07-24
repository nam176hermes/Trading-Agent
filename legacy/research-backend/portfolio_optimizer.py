"""
Portfolio Optimizer — Efficient Frontier, Risk Parity, and Portfolio Construction.

Pure Python implementation using numpy + scipy.optimize.
All functions gracefully degrade with ImportError if scipy is unavailable.

Usage:
    from portfolio_optimizer import efficient_frontier, risk_parity, optimize_portfolio
"""

from __future__ import annotations

import warnings
from typing import Any

import numpy as np

_scipy_minimize = None  # type: ignore[assignment]
_SCIPY_AVAILABLE = False
try:
    from scipy.optimize import minimize as _scipy_minimize  # type: ignore[assignment]
    _SCIPY_AVAILABLE = True
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_cov(returns_array: np.ndarray, symbols: list[str]) -> np.ndarray:
    """Compute covariance matrix with fallback to identity if singular."""
    try:
        cov = np.cov(returns_array, rowvar=False)
        # Ensure positive semi-definite
        cov = (cov + cov.T) / 2.0
        eigvals = np.linalg.eigvalsh(cov)
        if np.any(eigvals < -1e-10):
            raise ValueError("Covariance matrix is not positive semi-definite")
        return cov
    except Exception:
        warnings.warn("Covariance matrix is singular; falling back to diagonal approximation.")
        var = np.var(returns_array, axis=0, ddof=1)
        return np.diag(var)


def _validate_returns_dict(returns_dict: dict[str, np.ndarray]) -> tuple[list[str], np.ndarray]:
    """Validate returns dict and return (symbols, returns_array)."""
    if not isinstance(returns_dict, dict):
        raise TypeError(f"returns_dict must be a dict, got {type(returns_dict).__name__}")
    if len(returns_dict) < 2:
        raise ValueError(f"Need at least 2 assets, got {len(returns_dict)}")

    symbols = list(returns_dict.keys())
    returns_list = []
    n_obs = None
    for sym in symbols:
        r = np.asarray(returns_dict[sym], dtype=float).ravel()
        if n_obs is None:
            n_obs = len(r)
        elif len(r) != n_obs:
            raise ValueError(f"All return arrays must have the same length; {sym} has {len(r)} vs {n_obs}")
        returns_list.append(r)

    returns_array = np.column_stack(returns_list)
    return symbols, returns_array


def _build_constraints_and_bounds(
    n_assets: int,
    constraints: dict[str, Any] | None = None,
) -> tuple[list[dict], list[tuple[float, float]]]:
    """
    Build scipy constraints list and bounds list from user-friendly spec.

    constraints can contain:
        - 'max_weight': float (0-1)      -> upper bound on every asset
        - 'min_weight': float (0-1)      -> lower bound on every asset
        - 'per_asset': dict[str, dict]   -> per-asset overrides, e.g.
            {'BTC': {'max': 0.4, 'min': 0.0}}
        - 'sum_to_one': bool (default True) -> weights sum to 1

    Returns (constraints_list, bounds_list).
    """
    constraints = constraints or {}
    sum_to_one = constraints.get("sum_to_one", True)
    max_weight = constraints.get("max_weight", 1.0)
    min_weight = constraints.get("min_weight", 0.0)
    per_asset = constraints.get("per_asset", {})

    cons = []
    if sum_to_one:
        cons.append({"type": "eq", "fun": lambda w: np.sum(w) - 1.0})

    bounds = []
    for i in range(n_assets):
        lo, hi = min_weight, max_weight
        # Per-asset overrides (applied by index; caller must pass in symbol order)
        key = str(i)
        if key in per_asset:
            lo = per_asset[key].get("min", lo)
            hi = per_asset[key].get("max", hi)
        bounds.append((lo, hi))

    return cons, bounds


# ---------------------------------------------------------------------------
# Core optimizations
# ---------------------------------------------------------------------------

def _max_sharpe_weights(
    mu: np.ndarray,
    cov: np.ndarray,
    constraints: list[dict],
    bounds: list[tuple[float, float]],
    risk_free_rate: float = 0.0,
) -> np.ndarray:
    """Find weights that maximize (w·μ - rf) / sqrt(w^T Σ w)."""
    n = len(mu)
    x0 = np.ones(n) / n

    def neg_sharpe(w: np.ndarray) -> float:
        port_ret = np.dot(w, mu) - risk_free_rate
        port_vol = np.sqrt(np.dot(w, np.dot(cov, w)))
        if port_vol < 1e-12:
            return 1e9
        return -port_ret / port_vol

    result = _scipy_minimize(
        neg_sharpe, x0, method="SLSQP", bounds=bounds, constraints=constraints,
        options={"maxiter": 2000, "ftol": 1e-10},
    )
    if not result.success:
        warnings.warn(f"Max Sharpe optimization did not converge: {result.message}")
    return result.x / np.sum(result.x)  # re-normalize


def _min_variance_weights(
    cov: np.ndarray,
    constraints: list[dict],
    bounds: list[tuple[float, float]],
) -> np.ndarray:
    """Find weights that minimize w^T Σ w."""
    n = cov.shape[0]
    x0 = np.ones(n) / n

    def variance(w: np.ndarray) -> float:
        return float(np.dot(w, np.dot(cov, w)))

    result = _scipy_minimize(
        variance, x0, method="SLSQP", bounds=bounds, constraints=constraints,
        options={"maxiter": 2000, "ftol": 1e-10},
    )
    if not result.success:
        warnings.warn(f"Min variance optimization did not converge: {result.message}")
    return result.x / np.sum(result.x)


def _target_return_weights(
    mu: np.ndarray,
    cov: np.ndarray,
    target_ret: float,
    constraints: list[dict],
    bounds: list[tuple[float, float]],
) -> np.ndarray | None:
    """Find min-variance weights that achieve at least `target_ret`. Returns None if infeasible."""
    n = len(mu)

    def variance(w: np.ndarray) -> float:
        return float(np.dot(w, np.dot(cov, w)))

    cons = list(constraints)
    cons.append({"type": "eq", "fun": lambda w: np.dot(w, mu) - target_ret})

    x0 = np.ones(n) / n
    result = _scipy_minimize(
        variance, x0, method="SLSQP", bounds=bounds, constraints=cons,
        options={"maxiter": 2000, "ftol": 1e-10},
    )
    if not result.success:
        return None
    return result.x / np.sum(result.x)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def efficient_frontier(
    returns_dict: dict[str, np.ndarray],
    num_points: int = 50,
    risk_free_rate: float = 0.0,
    constraints: dict | None = None,
) -> dict:
    """
    Compute the efficient frontier for a set of asset returns.

    Args:
        returns_dict: {'SYM': np.array of returns, ...} — all arrays same length.
        num_points: Number of points along the frontier.
        risk_free_rate: Annualised risk-free rate (default 0).
        constraints: Dict with optional 'max_weight', 'min_weight', 'per_asset'.

    Returns:
        {
            'frontier': [{'ret': float, 'vol': float, 'weights': dict}, ...],
            'max_sharpe': {'weights': dict, 'ret': float, 'vol': float, 'sharpe': float},
            'min_variance': {'weights': dict, 'ret': float, 'vol': float},
        }
    """
    if not _SCIPY_AVAILABLE:
        raise ImportError("scipy is required for efficient_frontier; install with `pip install scipy`")

    symbols, returns_array = _validate_returns_dict(returns_dict)
    n_assets = len(symbols)
    mu = np.mean(returns_array, axis=0)
    cov = _safe_cov(returns_array, symbols)
    cons, bounds = _build_constraints_and_bounds(n_assets, constraints)

    # Min variance portfolio
    w_mv = _min_variance_weights(cov, cons, bounds)
    mv_ret = float(np.dot(w_mv, mu))
    mv_vol = float(np.sqrt(np.dot(w_mv, np.dot(cov, w_mv))))

    # Max Sharpe portfolio
    w_ms = _max_sharpe_weights(mu, cov, cons, bounds, risk_free_rate)
    ms_ret = float(np.dot(w_ms, mu))
    ms_vol = float(np.sqrt(np.dot(w_ms, np.dot(cov, w_ms))))
    ms_sharpe = (ms_ret - risk_free_rate) / ms_vol if ms_vol > 0 else 0.0

    # Frontier: range from min-variance return to max return (or max Sharpe)
    min_ret = float(np.min(mu))
    max_ret = float(np.max(mu))
    # Extend a bit beyond the extremes
    lo = max(min_ret, mv_ret - 0.5 * (mv_ret - min_ret)) if mv_ret > min_ret else min_ret
    hi = max(max_ret, ms_ret * 1.1) if ms_ret > max_ret else max_ret * 1.05
    targets = np.linspace(lo, hi, num_points)

    frontier = []
    for target in targets:
        w = _target_return_weights(mu, cov, float(target), cons, bounds)
        if w is not None:
            port_ret = float(np.dot(w, mu))
            port_vol = float(np.sqrt(np.dot(w, np.dot(cov, w))))
            if port_vol > 1e-12:
                point = {
                    "ret": port_ret,
                    "vol": port_vol,
                    "weights": {sym: float(w[i]) for i, sym in enumerate(symbols)},
                }
                frontier.append(point)

    # Prune dominated points (higher vol with lower ret)
    frontier.sort(key=lambda p: p["vol"])
    pruned = []
    max_ret_so_far = -np.inf
    for pt in frontier:
        if pt["ret"] > max_ret_so_far:
            pruned.append(pt)
            max_ret_so_far = pt["ret"]

    return {
        "frontier": pruned,
        "max_sharpe": {
            "weights": {sym: float(w_ms[i]) for i, sym in enumerate(symbols)},
            "ret": ms_ret,
            "vol": ms_vol,
            "sharpe": ms_sharpe,
        },
        "min_variance": {
            "weights": {sym: float(w_mv[i]) for i, sym in enumerate(symbols)},
            "ret": mv_ret,
            "vol": mv_vol,
        },
    }


def risk_parity(
    cov_matrix: np.ndarray,
    symbols: list[str] | None = None,
    constraints: dict | None = None,
) -> dict:
    """
    Compute equal risk contribution (risk parity) weights.

    Minimises Σᵢ (RCᵢ — 1/N)² where RCᵢ = wᵢ·(Σw)ᵢ / wᵀΣw.

    Args:
        cov_matrix: N×N covariance matrix.
        symbols: List of N symbols (auto-generated if None).
        constraints: Dict with optional 'max_weight', 'min_weight', 'per_asset'.

    Returns:
        {
            'weights': dict,
            'risk_contributions': dict,
            'total_risk': float,
            'converged': bool,
        }
    """
    if not _SCIPY_AVAILABLE:
        raise ImportError("scipy is required for risk_parity; install with `pip install scipy`")

    cov = np.asarray(cov_matrix, dtype=float)
    if cov.ndim != 2 or cov.shape[0] != cov.shape[1]:
        raise ValueError(f"cov_matrix must be square, got shape {cov.shape}")

    n = cov.shape[0]
    if symbols is None:
        symbols = [f"Asset_{i}" for i in range(n)]
    elif len(symbols) != n:
        raise ValueError(f"symbols length ({len(symbols)}) must match cov_matrix size ({n})")

    cons, bounds = _build_constraints_and_bounds(n, constraints)

    def risk_parity_objective(w: np.ndarray) -> float:
        port_var = np.dot(w, np.dot(cov, w))
        if port_var < 1e-12:
            return 1e9
        marginal = np.dot(cov, w)
        risk_contrib = w * marginal / port_var
        target = 1.0 / n
        return float(np.sum((risk_contrib - target) ** 2))

    x0 = np.ones(n) / n
    result = _scipy_minimize(
        risk_parity_objective, x0, method="SLSQP", bounds=bounds,
        constraints=cons, options={"maxiter": 3000, "ftol": 1e-12},
    )
    w = result.x / np.sum(result.x)

    port_var = np.dot(w, np.dot(cov, w))
    marginal = np.dot(cov, w)
    risk_contrib = w * marginal / max(port_var, 1e-12)

    return {
        "weights": {sym: float(w[i]) for i, sym in enumerate(symbols)},
        "risk_contributions": {sym: float(risk_contrib[i]) for i, sym in enumerate(symbols)},
        "total_risk": float(np.sqrt(port_var)) if port_var > 0 else 0.0,
        "converged": bool(result.success),
    }


def optimize_portfolio(
    returns_dict: dict[str, np.ndarray],
    method: str = "max_sharpe",
    constraints: dict | None = None,
    risk_free_rate: float = 0.0,
) -> dict:
    """
    Unified portfolio optimizer.

    Args:
        returns_dict: {'SYM': np.array of returns, ...}.
        method: One of 'max_sharpe', 'min_variance', 'risk_parity', 'efficient_frontier'.
        constraints: Dict with optional 'max_weight', 'min_weight', 'per_asset'.
        risk_free_rate: Annualised risk-free rate (used for Sharpe calculations).

    Returns:
        Optimisation result dict. The shape depends on `method`:
        - 'max_sharpe' / 'min_variance': {'weights': dict, 'ret': float, 'vol': float, ...}
        - 'risk_parity': {'weights': dict, 'risk_contributions': dict, ...}
        - 'efficient_frontier': {'frontier': list, 'max_sharpe': ..., 'min_variance': ...}
    """
    valid_methods = ("max_sharpe", "min_variance", "risk_parity", "efficient_frontier")
    if method not in valid_methods:
        raise ValueError(f"method must be one of {valid_methods}, got '{method}'")

    if method == "efficient_frontier":
        return efficient_frontier(returns_dict, risk_free_rate=risk_free_rate, constraints=constraints)

    if not _SCIPY_AVAILABLE:
        raise ImportError("scipy is required; install with `pip install scipy`")

    symbols, returns_array = _validate_returns_dict(returns_dict)
    n_assets = len(symbols)
    mu = np.mean(returns_array, axis=0)
    cov = _safe_cov(returns_array, symbols)
    cons, bounds = _build_constraints_and_bounds(n_assets, constraints)

    if method == "risk_parity":
        result = risk_parity(cov, symbols, constraints)
        # Add return and vol for consistency
        w = np.array([result["weights"][s] for s in symbols])
        result["ret"] = float(np.dot(w, mu))
        result["vol"] = float(np.sqrt(np.dot(w, np.dot(cov, w))))
        return result

    if method == "max_sharpe":
        w = _max_sharpe_weights(mu, cov, cons, bounds, risk_free_rate)
    elif method == "min_variance":
        w = _min_variance_weights(cov, cons, bounds)
    else:
        raise ValueError(f"Unknown method: {method}")  # pragma: no cover

    port_ret = float(np.dot(w, mu))
    port_vol = float(np.sqrt(np.dot(w, np.dot(cov, w))))
    sharpe = (port_ret - risk_free_rate) / port_vol if port_vol > 1e-12 else 0.0
    weights_dict = {sym: float(w[i]) for i, sym in enumerate(symbols)}

    result: dict[str, Any] = {
        "weights": weights_dict,
        "ret": port_ret,
        "vol": port_vol,
    }
    if method == "max_sharpe":
        result["sharpe"] = sharpe

    return result


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    print("=== Portfolio Optimizer Self-Test ===\n")

    # Generate random 5-asset data
    np.random.seed(42)
    n_assets = 5
    n_periods = 252
    symbols_list = ["BTC", "ETH", "SOL", "ADA", "DOT"]

    true_mu = np.array([0.12, 0.10, 0.15, 0.08, 0.09])
    true_cov = np.array([
        [0.10, 0.03, 0.02, 0.01, 0.01],
        [0.03, 0.08, 0.02, 0.01, 0.01],
        [0.02, 0.02, 0.20, 0.02, 0.01],
        [0.01, 0.01, 0.02, 0.09, 0.01],
        [0.01, 0.01, 0.01, 0.01, 0.07],
    ])
    raw = np.random.multivariate_normal(true_mu / 252, true_cov / 252, n_periods)
    returns_dict = {sym: raw[:, i] for i, sym in enumerate(symbols_list)}

    print(f"Generated {n_periods} daily returns for {n_assets} assets: {symbols_list}\n")

    # -----------------------------------------------------------------------
    # Test 1: efficient_frontier
    # -----------------------------------------------------------------------
    print("--- Test: efficient_frontier ---")
    try:
        ef = efficient_frontier(returns_dict, num_points=30)
        print(f"  Frontier points: {len(ef['frontier'])}")
        print(f"  Max Sharpe: ret={ef['max_sharpe']['ret']:.4f}, "
              f"vol={ef['max_sharpe']['vol']:.4f}, "
              f"sharpe={ef['max_sharpe']['sharpe']:.4f}")
        print(f"  Max Sharpe weights: {ef['max_sharpe']['weights']}")
        print(f"  Min Variance: ret={ef['min_variance']['ret']:.4f}, "
              f"vol={ef['min_variance']['vol']:.4f}")
        print(f"  Weight sum check (max_sharpe): {sum(ef['max_sharpe']['weights'].values()):.6f}")
        assert abs(sum(ef["max_sharpe"]["weights"].values()) - 1.0) < 1e-8, "Weights don't sum to 1"
        print("  ✓ PASSED\n")
    except Exception as e:
        print(f"  ✗ FAILED: {e}\n")
        sys.exit(1)

    # -----------------------------------------------------------------------
    # Test 2: risk_parity
    # -----------------------------------------------------------------------
    print("--- Test: risk_parity ---")
    try:
        cov_mat = np.cov(raw, rowvar=False)
        rp = risk_parity(cov_mat, symbols_list)
        print(f"  Weights: {rp['weights']}")
        print(f"  Risk contributions: {rp['risk_contributions']}")
        print(f"  Total risk: {rp['total_risk']:.6f}")
        print(f"  Converged: {rp['converged']}")
        # Risk contributions should be roughly equal (1/N ≈ 0.2 each)
        rc_values = list(rp["risk_contributions"].values())
        assert all(abs(rc - 0.2) < 0.15 for rc in rc_values), f"Risk contributions not equal: {rc_values}"
        assert abs(sum(rp["weights"].values()) - 1.0) < 1e-8, "Weights don't sum to 1"
        print("  ✓ PASSED\n")
    except Exception as e:
        print(f"  ✗ FAILED: {e}\n")
        sys.exit(1)

    # -----------------------------------------------------------------------
    # Test 3: optimize_portfolio (all methods)
    # -----------------------------------------------------------------------
    print("--- Test: optimize_portfolio ---")
    methods = ["max_sharpe", "min_variance", "risk_parity", "efficient_frontier"]
    for m in methods:
        try:
            result = optimize_portfolio(returns_dict, method=m)
            if m == "efficient_frontier":
                ok = len(result["frontier"]) > 0
                print(f"  {m}: {len(result['frontier'])} frontier points" + (" ✓" if ok else " ✗"))
            else:
                print(f"  {m}: ret={result['ret']:.4f}, vol={result['vol']:.4f}, "
                      f"Σw={sum(result['weights'].values()):.4f} ✓")
        except Exception as e:
            print(f"  {m}: ✗ FAILED — {e}")
            sys.exit(1)
    print()

    # -----------------------------------------------------------------------
    # Test 4: constraints
    # -----------------------------------------------------------------------
    print("--- Test: constraints ---")
    try:
        constrained = optimize_portfolio(
            returns_dict,
            method="max_sharpe",
            constraints={"max_weight": 0.3, "min_weight": 0.05},
        )
        all_ok = all(0.05 - 1e-8 <= w <= 0.3 + 1e-8 for w in constrained["weights"].values())
        print(f"  Max 30% / Min 5%: {constrained['weights']}")
        print(f"  All in bounds: {all_ok}")
        assert all_ok, "Constraint violation"
        print("  ✓ PASSED\n")
    except Exception as e:
        print(f"  ✗ FAILED: {e}\n")
        sys.exit(1)

    print("=== All tests passed ===")
