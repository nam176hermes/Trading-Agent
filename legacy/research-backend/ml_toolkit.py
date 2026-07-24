"""
ml_toolkit.py — ML interpretation and regime detection enhancements.

Provides:
  - SHAP feature importance for LightGBM models
  - GMM-based probabilistic regime detection
  - Deflated Sharpe Ratio (Lopez de Prado) for multiple-testing correction
  - Purged K-Fold cross-validation for time series

Pure Python with numpy/scipy. All functions degrade gracefully when
optional dependencies (shap, sklearn) are missing.
"""

from __future__ import annotations

import warnings
from typing import Dict, List, Tuple, Optional, Any

import numpy as np
from scipy import stats as scipy_stats

# ---------------------------------------------------------------------------
# Optional dependency helpers
# ---------------------------------------------------------------------------

_SHAP_AVAILABLE = False
_SKLEARN_AVAILABLE = False

try:
    import shap as _shap
    _SHAP_AVAILABLE = True
except ImportError:
    pass

try:
    from sklearn.mixture import GaussianMixture as _GaussianMixture
    _SKLEARN_AVAILABLE = True
except ImportError:
    pass


# ===================================================================
# 1. SHAP interpretation
# ===================================================================

def compute_shap_values(
    model: Any,
    X: np.ndarray,
    feature_names: List[str],
    max_display: int = 20,
) -> Dict[str, Any]:
    """
    Compute SHAP feature importance for a LightGBM model.

    Uses ``shap.TreeExplainer`` for efficient tree-path-dependent SHAP values.
    Falls back gracefully with a message if ``shap`` is not installed.

    Parameters
    ----------
    model : lightgbm.Booster or lightgbm.LGBMClassifier/LGBMRegressor
        Trained LightGBM model.
    X : np.ndarray, shape (n_samples, n_features)
        Feature matrix (preferably a representative sample, e.g. 500–2000 rows).
    feature_names : list of str
        Names corresponding to columns of *X*.
    max_display : int
        Max number of top features to include in ``top_features`` and the
        summary string (default 20).

    Returns
    -------
    dict
        ``feature_importance`` : dict[str, float]
            Mean absolute SHAP value per feature, sorted descending.
        ``top_features`` : list[str]
            Names of the top *max_display* features.
        ``summary`` : str
            Human-readable ranking of the most important features.
        ``error`` : str or None
            Error message if SHAP computation failed.
    """
    if not _SHAP_AVAILABLE:
        return {
            "feature_importance": {},
            "top_features": [],
            "summary": "shap package is not installed. Install with: pip install shap",
            "error": "shap not installed",
        }

    n_features = X.shape[1]
    if n_features != len(feature_names):
        return {
            "feature_importance": {},
            "top_features": [],
            "summary": (
                f"Mismatch: X has {n_features} columns but "
                f"{len(feature_names)} feature names were provided."
            ),
            "error": "feature name mismatch",
        }

    try:
        # LightGBM Booster objects need special handling for TreeExplainer
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)

            # model_byte_string works for both sklearn API and native Booster
            if hasattr(model, "booster_"):
                booster = model.booster_
            else:
                booster = model

            explainer = _shap.TreeExplainer(
                booster,
                feature_perturbation="tree_path_dependent",
            )
            shap_values = explainer.shap_values(X)

        # shap_values shape depends on the task:
        #   - regression / binary classification: (n_samples, n_features)
        #   - multi-class: list of (n_samples, n_features) arrays
        if isinstance(shap_values, list):
            # Multi-class — average absolute SHAP across classes
            abs_shap = np.mean(
                [np.abs(sv).mean(axis=0) for sv in shap_values], axis=0
            )
        else:
            abs_shap = np.abs(shap_values).mean(axis=0)

        # Build descending-order feature importance dict
        sorted_idx = np.argsort(abs_shap)[::-1]
        feature_importance = {
            feature_names[i]: float(abs_shap[i])
            for i in sorted_idx
        }

        top_features = [
            feature_names[i] for i in sorted_idx[:max_display]
        ]

        # Build summary string
        lines = ["SHAP Feature Importance (mean |SHAP|):"]
        for rank, feat in enumerate(top_features, 1):
            val = feature_importance[feat]
            lines.append(f"  {rank:2d}. {feat:30s} {val:.6f}")
        summary = "\n".join(lines)

        return {
            "feature_importance": feature_importance,
            "top_features": top_features,
            "summary": summary,
            "error": None,
        }

    except Exception as exc:
        return {
            "feature_importance": {},
            "top_features": [],
            "summary": f"SHAP computation failed: {exc}",
            "error": str(exc),
        }


# ===================================================================
# 2. GMM regime detection
# ===================================================================

def gmm_regime_detect(
    returns: np.ndarray,
    n_components: int = 4,
    random_state: int = 42,
    **gmm_kwargs: Any,
) -> Dict[str, Any]:
    """
    Detect market regimes via Gaussian Mixture Model (GMM).

    GMM provides *probabilistic* assignments — each point has membership
    probabilities for every regime, unlike hard-clustering methods such as
    K-means.

    Parameters
    ----------
    returns : np.ndarray, shape (n_samples,) or (n_samples, n_features)
        Return series.  If 1-D it is reshaped to (n_samples, 1).
    n_components : int
        Number of GMM components / regimes (default 4).
    random_state : int
        Seed for reproducibility.
    **gmm_kwargs
        Passed through to ``sklearn.mixture.GaussianMixture``
        (e.g. ``covariance_type='full'``, ``tol=1e-3``).

    Returns
    -------
    dict
        ``current_regime`` : int
            Hard regime assignment for the most recent observation.
        ``regime_probs`` : np.ndarray, shape (n_samples, n_components)
            Soft membership probabilities per sample.
        ``bic_score`` : float
            Bayesian Information Criterion (lower is better).
        ``regime_labels`` : list[str]
            Descriptive labels like ``"Regime 0 (μ=+0.12%)"``.
        ``error`` : str or None
            Error message if GMM fitting failed.
    """
    if not _SKLEARN_AVAILABLE:
        return {
            "current_regime": -1,
            "regime_probs": np.array([]),
            "bic_score": float("nan"),
            "regime_labels": [],
            "error": "scikit-learn is not installed. Install with: pip install scikit-learn",
        }

    data = np.asarray(returns, dtype=float)

    if data.ndim == 1:
        data = data.reshape(-1, 1)

    n_samples = data.shape[0]
    if n_samples < n_components:
        return {
            "current_regime": -1,
            "regime_probs": np.array([]),
            "bic_score": float("nan"),
            "regime_labels": [],
            "error": (
                f"Need at least n_components={n_components} samples, "
                f"got {n_samples}"
            ),
        }

    try:
        gmm = _GaussianMixture(
            n_components=n_components,
            random_state=random_state,
            **gmm_kwargs,
        )
        gmm.fit(data)

        regime_probs = gmm.predict_proba(data)
        hard_labels = gmm.predict(data)
        current_regime = int(hard_labels[-1])
        bic_score = float(gmm.bic(data))

        # Build descriptive labels using per-regime mean return
        means = gmm.means_.ravel() if data.shape[1] == 1 else gmm.means_.mean(axis=1)
        regime_labels = [
            f"Regime {i} (μ={means[i]:+.4f})" for i in range(n_components)
        ]

        return {
            "current_regime": current_regime,
            "regime_probs": regime_probs,
            "bic_score": bic_score,
            "regime_labels": regime_labels,
            "error": None,
        }

    except Exception as exc:
        return {
            "current_regime": -1,
            "regime_probs": np.array([]),
            "bic_score": float("nan"),
            "regime_labels": [],
            "error": str(exc),
        }


# ===================================================================
# 3. Deflated Sharpe Ratio (Lopez de Prado)
# ===================================================================

def deflated_sharpe_ratio(
    observed_sr: float,
    sr_list: List[float],
    num_trials: int,
    skew: Optional[float] = None,
    kurt: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Compute the Deflated Sharpe Ratio (DSR) and its p-value.

    Corrects the observed Sharpe ratio for selection bias under multiple
    testing.  Given *num_trials* independent strategy variations that were
    tested, the DSR answers: "What is the probability that the observed
    Sharpe ratio is simply the maximum among *num_trials* random draws from
    the null distribution?"

    The null distribution of Sharpe ratios is estimated from *sr_list*
    (e.g. Sharpe ratios of placebo / random strategies).

    Implementation follows Lopez de Prado & Bailey (2014), "The Deflated
    Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting, and
    Non-Normality."

    Parameters
    ----------
    observed_sr : float
        The Sharpe ratio of the strategy being evaluated.
    sr_list : list of float
        Collection of Sharpe ratios that represent the null / placebo
        distribution (at least 10 values recommended).
    num_trials : int
        Number of independent strategy configurations that were tested
        (multiple-testing correction factor).  Must be ≥ 1.
    skew : float, optional
        Sample skewness of the null distribution.  If not provided it is
        estimated from *sr_list*.
    kurt : float, optional
        Sample excess kurtosis of the null distribution.  If not provided
        it is estimated from *sr_list*.

    Returns
    -------
    dict
        ``deflated_sr`` : float
            The Deflated Sharpe Ratio (in SR units).
        ``p_value`` : float
            Probability that the observed SR is due to selection bias
            (multiple-testing corrected).
        ``significant`` : bool
            True if p_value < 0.05.
        ``expected_max_sr`` : float
            Expected maximum SR among *num_trials* under the null.
        ``error`` : str or None
    """
    sr_array = np.asarray(sr_list, dtype=float)

    if len(sr_array) < 5:
        return {
            "deflated_sr": float("nan"),
            "p_value": float("nan"),
            "significant": False,
            "expected_max_sr": float("nan"),
            "error": "Need at least 5 null Sharpe ratios to estimate distribution",
        }
    if num_trials < 1:
        return {
            "deflated_sr": float("nan"),
            "p_value": float("nan"),
            "significant": False,
            "expected_max_sr": float("nan"),
            "error": "num_trials must be >= 1",
        }

    try:
        mu = float(np.mean(sr_array))
        sigma = float(np.std(sr_array, ddof=1))

        if sigma < 1e-12:
            # Degenerate null — all SRs identical
            sigma = 1e-12

        # Standardised observed SR
        z_obs = (observed_sr - mu) / sigma

        # Use Edgeworth expansion if skew/kurt provided, else plain normal
        if skew is not None and kurt is not None:
            # Cornish-Fisher / Edgeworth expansion for the CDF
            # P(SR ≤ x) ≈ Φ(z) + φ(z) * [skew/6 * (1 - z²) + kurt/24 * (z³ - 3z) + skew²/72 * (z⁵ - 10z³ + 15z)]
            def _edgeworth_cdf(z: float) -> float:
                phi = scipy_stats.norm.pdf(z)
                Phi = scipy_stats.norm.cdf(z)
                z2 = z * z
                z3 = z2 * z
                z5 = z3 * z2
                correction = (
                    skew / 6.0 * (1.0 - z2)
                    + kurt / 24.0 * (z3 - 3.0 * z)
                    + (skew * skew) / 72.0 * (z5 - 10.0 * z3 + 15.0 * z)
                )
                cdf_val = Phi - phi * correction
                return float(np.clip(cdf_val, 0.0, 1.0))

            cdf_obs = _edgeworth_cdf(z_obs)
        else:
            cdf_obs = scipy_stats.norm.cdf(z_obs)

        # --- Deflated p-value ---
        # P(max(SR_1, ..., SR_N) ≥ SR_obs) = 1 - CDF(SR_obs)^N
        p_deflated = 1.0 - cdf_obs ** num_trials
        p_deflated = float(np.clip(p_deflated, 0.0, 1.0))

        # --- Deflated Sharpe Ratio ---
        # Invert: what SR would give this p-value as a single test?
        # DSR ≡ z that satisfies: 1 - Φ(z) = 1 - Φ(z_obs)^N
        #   => Φ(z) = Φ(z_obs)^N
        #   => z = Φ⁻¹(Φ(z_obs)^N)
        target_cdf = cdf_obs ** num_trials
        # When target_cdf is extremely close to 0 or 1, ppf returns ±inf
        target_cdf_clipped = float(np.clip(target_cdf, 1e-15, 1.0 - 1e-15))

        if target_cdf_clipped <= 1e-15:
            z_deflated = -8.0  # effectively -inf
        elif target_cdf_clipped >= 1.0 - 1e-15:
            z_deflated = 8.0
        else:
            z_deflated = float(scipy_stats.norm.ppf(target_cdf_clipped))

        deflated_sr = mu + sigma * z_deflated

        # --- Expected maximum SR under null (for reference) ---
        # E[max] ≈ μ + σ * Φ⁻¹(1 - 1/N) (approximate)
        expected_max_z = float(scipy_stats.norm.ppf(1.0 - 1.0 / max(num_trials, 2)))
        expected_max_sr = mu + sigma * expected_max_z

        return {
            "deflated_sr": round(deflated_sr, 6),
            "p_value": round(p_deflated, 6),
            "significant": p_deflated < 0.05,
            "expected_max_sr": round(expected_max_sr, 6),
            "error": None,
        }

    except Exception as exc:
        return {
            "deflated_sr": float("nan"),
            "p_value": float("nan"),
            "significant": False,
            "expected_max_sr": float("nan"),
            "error": str(exc),
        }


# ===================================================================
# 4. Purged K-Fold CV for time series
# ===================================================================

def purged_kfold_splits(
    n_samples: int,
    n_splits: int = 5,
    embargo_pct: float = 0.01,
) -> List[Tuple[np.ndarray, np.ndarray]]:
    """
    Generate purged train / test index splits for time-series data.

    Standard K-fold leaks future information into the past when applied to
    time series.  This generator:

    1. Partitions the data into *n_splits* contiguous time-ordered groups.
    2. For each fold, the test set is one group; the training set is all
       groups *before* the test group (no future leakage).
    3. **Purge**: removes training samples whose label period overlaps with
       the test period (approximated by removing a fraction of training
       samples closest to the test boundary).
    4. **Embargo**: after each test group, a small fraction of samples is
       embargoed — they are excluded from being training data for any
       subsequent fold (prevents information leaking through serial
       correlation).

    Parameters
    ----------
    n_samples : int
        Total number of observations (time-ordered).
    n_splits : int
        Number of folds (default 5).  Must be ≥ 2.
    embargo_pct : float
        Fraction of *n_samples* to purge before each test boundary and to
        embargo after each test set (default 0.01 = 1%).

    Returns
    -------
    list of tuple
        Each element is ``(train_indices, test_indices)`` as 1-D numpy
        integer arrays.  At most ``n_splits - 1`` splits are returned (the
        first group has no prior training data).
    """
    if n_splits < 2:
        raise ValueError("n_splits must be at least 2")
    if n_samples < n_splits:
        raise ValueError(
            f"n_samples ({n_samples}) must be >= n_splits ({n_splits})"
        )
    if not (0.0 <= embargo_pct <= 0.5):
        raise ValueError("embargo_pct must be in [0, 0.5]")

    embargo_samples = max(1, int(embargo_pct * n_samples))

    # Create contiguous, roughly equal-sized groups
    indices = np.arange(n_samples)
    group_boundaries = np.linspace(0, n_samples, n_splits + 1, dtype=int)

    splits: List[Tuple[np.ndarray, np.ndarray]] = []

    for fold in range(1, n_splits):  # first group needs prior data, so start at 1
        test_start = group_boundaries[fold]
        test_end = group_boundaries[fold + 1]
        test_indices = indices[test_start:test_end]

        # Training data: all groups before this fold
        train_end_unpurged = test_start  # boundary before purging

        # Purge: remove embargo_samples from the end of the training window
        purge_cut = max(0, train_end_unpurged - embargo_samples)
        train_indices = indices[:purge_cut]

        if len(train_indices) == 0:
            continue  # nothing to train on

        splits.append((train_indices, test_indices))

    return splits


# ===================================================================
# Quick smoke-test / demo
# ===================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("ml_toolkit.py — smoke tests")
    print("=" * 60)

    # --- GMM regime detect ---
    print("\n[1] GMM regime detection")
    rng = np.random.default_rng(42)
    fake_returns = np.concatenate([
        rng.normal(0.001, 0.01, 200),   # low-vol bull
        rng.normal(-0.003, 0.03, 150),  # high-vol bear
        rng.normal(0.000, 0.005, 100),  # quiet
    ])
    result = gmm_regime_detect(fake_returns, n_components=3)
    if result["error"]:
        print(f"  ERROR: {result['error']}")
    else:
        print(f"  Current regime : {result['current_regime']}")
        print(f"  BIC            : {result['bic_score']:.2f}")
        for lbl in result["regime_labels"]:
            print(f"  {lbl}")
        print(f"  Prob shape     : {result['regime_probs'].shape}")

    # --- Deflated Sharpe Ratio ---
    print("\n[2] Deflated Sharpe Ratio")
    null_srs = rng.normal(0.0, 0.5, 500).tolist()
    result = deflated_sharpe_ratio(
        observed_sr=1.5, sr_list=null_srs, num_trials=50
    )
    if result["error"]:
        print(f"  ERROR: {result['error']}")
    else:
        print(f"  Observed SR     : 1.5")
        print(f"  Deflated SR     : {result['deflated_sr']}")
        print(f"  p-value         : {result['p_value']}")
        print(f"  Significant     : {result['significant']}")
        print(f"  Expected max SR : {result['expected_max_sr']}")

    result2 = deflated_sharpe_ratio(
        observed_sr=0.3, sr_list=null_srs, num_trials=50
    )
    if not result2["error"]:
        print(f"\n  Observed SR     : 0.3")
        print(f"  Deflated SR     : {result2['deflated_sr']}")
        print(f"  p-value         : {result2['p_value']}")
        print(f"  Significant     : {result2['significant']}")

    # --- Purged K-Fold ---
    print("\n[3] Purged K-Fold CV")
    splits = purged_kfold_splits(n_samples=1000, n_splits=5, embargo_pct=0.01)
    for i, (train, test) in enumerate(splits):
        print(
            f"  Fold {i}: "
            f"train=[{train[0]:4d}..{train[-1]:4d}] ({len(train):4d}), "
            f"test=[{test[0]:4d}..{test[-1]:4d}] ({len(test):4d})"
        )

    # --- SHAP (only if available) ---
    print("\n[4] SHAP values")
    if not _SHAP_AVAILABLE:
        print("  shap not installed — skipping")
    else:
        print("  shap is available (test skipped to avoid LightGBM dependency)")

    print("\nDone.")
