"""
Ensemble ML Stacking for crypto trading signals.

Base models: LightGBM + XGBoost + RandomForest
Meta model: LogisticRegression (via sklearn StackingClassifier)

Provides:
  - train_ensemble(X, y, models=None) -> dict
  - predict_ensemble(result_dict, X) -> np.array
  - compare_single_vs_ensemble(X, y) -> dict
"""

import logging
import warnings
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier, StackingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, roc_auc_score
from sklearn.model_selection import StratifiedKFold, cross_val_score

logger = logging.getLogger(__name__)

# ── optional xgboost ──────────────────────────────────────────────────────────
try:
    import xgboost as xgb

    HAS_XGBOOST = True
except ImportError:  # pragma: no cover
    xgb = None  # type: ignore[assignment]
    HAS_XGBOOST = False
    logger.info("xgboost not installed – ensemble will use LightGBM + RF only")


def _build_base_models(
    models: Optional[List[object]] = None,
) -> List[Tuple[str, object]]:
    """Return labelled base estimators for the stacking ensemble."""
    if models is not None:
        # user-supplied: expect (name, estimator) tuples
        return models

    estimators: List[Tuple[str, object]] = []

    # LightGBM (required)
    try:
        import lightgbm as lgb

        estimators.append(
            (
                "lgbm",
                lgb.LGBMClassifier(
                    n_estimators=200,
                    max_depth=6,
                    learning_rate=0.05,
                    subsample=0.8,
                    colsample_bytree=0.8,
                    random_state=42,
                    verbose=-1,
                ),
            )
        )
    except ImportError:
        raise ImportError(
            "lightgbm is required for ensemble_ml.py. Install with: pip install lightgbm"
        )

    # XGBoost (optional)
    if HAS_XGBOOST:
        estimators.append(
            (
                "xgb",
                xgb.XGBClassifier(
                    n_estimators=200,
                    max_depth=6,
                    learning_rate=0.05,
                    subsample=0.8,
                    colsample_bytree=0.8,
                    random_state=42,
                    eval_metric="logloss",
                    verbosity=0,
                ),
            )
        )

    # RandomForest
    estimators.append(
        (
            "rf",
            RandomForestClassifier(
                n_estimators=200,
                max_depth=10,
                min_samples_leaf=5,
                random_state=42,
                n_jobs=-1,
            ),
        )
    )

    return estimators


def _extract_feature_importance(ensemble: StackingClassifier, feature_names: List[str]) -> dict:
    """Extract feature importance dict from base estimators in the ensemble."""
    importances: Dict[str, List[float]] = {}

    for name, est in ensemble.named_estimators_.items():
        if hasattr(est, "feature_importances_"):
            importances[name] = est.feature_importances_.tolist()

    # compute mean importance across all base models
    if importances and feature_names:
        stacked = np.column_stack([np.array(v) for v in importances.values()])
        mean_imp = stacked.mean(axis=1).tolist()
        importances["_mean"] = mean_imp
        # also return a sorted ranking dict
        importances["_ranked"] = {
            feature_names[i]: round(mean_imp[i], 6)
            for i in np.argsort(mean_imp)[::-1]
        }

    return importances


# ── Public API ────────────────────────────────────────────────────────────────


def train_ensemble(
    X,  # array-like or DataFrame
    y,  # array-like
    models: Optional[List[object]] = None,
) -> dict:
    """
    Train a stacking ensemble.

    Base estimators: LightGBM + XGBoost (optional) + RandomForest.
    Meta estimator: LogisticRegression with balanced class weights.
    Cross-validation: 5-fold stratified.

    Parameters
    ----------
    X : array-like of shape (n_samples, n_features)
        Feature matrix.
    y : array-like of shape (n_samples,)
        Binary target labels.
    models : list of (str, estimator) tuples, optional
        Custom base models. If None, default models are used.

    Returns
    -------
    dict with keys:
        ensemble      – trained StackingClassifier
        base_models   – list of (name, estimator) tuples
        cv_score      – mean ROC-AUC from 5-fold CV (float)
        feature_importance – dict: per-model importances + '_mean' + '_ranked'
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=UserWarning)

        X_arr = np.asarray(X, dtype=np.float64)
        y_arr = np.asarray(y, dtype=np.int64)

        feature_names: List[str] = []
        if isinstance(X, pd.DataFrame):
            feature_names = list(X.columns)
        elif hasattr(X, "columns"):
            feature_names = [str(c) for c in getattr(X, "columns")]

        estimators = _build_base_models(models)

        meta = LogisticRegression(
            class_weight="balanced",
            max_iter=2000,
            random_state=42,
        )

        cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)

        ensemble = StackingClassifier(
            estimators=estimators,
            final_estimator=meta,
            cv=cv,
            stack_method="predict_proba",
            n_jobs=-1,
        )

        # CV score (ROC-AUC)
        cv_score = float(
            cross_val_score(ensemble, X_arr, y_arr, cv=cv, scoring="roc_auc").mean()
        )

        # Fit on full data
        ensemble.fit(X_arr, y_arr)

        # Feature importance
        fi = _extract_feature_importance(ensemble, feature_names)

        return {
            "ensemble": ensemble,
            "base_models": estimators,
            "cv_score": round(cv_score, 6),
            "feature_importance": fi,
        }


def predict_ensemble(
    result_dict: dict,
    X,  # array-like
) -> np.ndarray:
    """
    Predict class labels using a trained ensemble.

    Parameters
    ----------
    result_dict : dict
        Output from train_ensemble().
    X : array-like of shape (n_samples, n_features)

    Returns
    -------
    np.ndarray of predicted class labels (0/1).
    """
    ensemble = result_dict["ensemble"]
    X_arr = np.asarray(X, dtype=np.float64)
    return ensemble.predict(X_arr)


def compare_single_vs_ensemble(X, y) -> dict:
    """
    Train a standalone LightGBM and the stacking ensemble side-by-side,
    comparing accuracy, ROC-AUC, and a simple Sharpe approximation.

    Parameters
    ----------
    X : array-like
    y : array-like binary targets

    Returns
    -------
    dict with keys:
        comparison     – dict of metric -> {lgbm: float, ensemble: float}
        winner         – model name with best AUC
        ensemble_cv    – ensemble CV ROC-AUC
        lgbm_cv        – single LGBM CV ROC-AUC
    """
    import lightgbm as lgb

    X_arr = np.asarray(X, dtype=np.float64)
    y_arr = np.asarray(y, dtype=np.int64)
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)

    # ── Single LightGBM ──────────────────────────────────────────────────
    lgbm = lgb.LGBMClassifier(
        n_estimators=200,
        max_depth=6,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=42,
        verbose=-1,
    )

    lgbm_cv = float(
        cross_val_score(lgbm, X_arr, y_arr, cv=cv, scoring="roc_auc").mean()
    )

    # ── Ensemble ─────────────────────────────────────────────────────────
    result = train_ensemble(X_arr, y_arr)
    ensemble_cv = result["cv_score"]

    # ── Train/test split for comparison metrics ─────────────────────────
    from sklearn.model_selection import train_test_split

    X_tr, X_te, y_tr, y_te = train_test_split(
        X_arr, y_arr, test_size=0.2, random_state=42, stratify=y_arr
    )

    lgbm.fit(X_tr, y_tr)
    ensemble = result["ensemble"]
    ensemble.fit(X_tr, y_tr)  # re-fit on the same train split for fairness

    lgbm_preds = lgbm.predict(X_te)
    ens_preds = ensemble.predict(X_te)

    lgbm_proba = lgbm.predict_proba(X_te)[:, 1]
    ens_proba = ensemble.predict_proba(X_te)[:, 1]

    lgbm_acc = float(accuracy_score(y_te, lgbm_preds))
    ens_acc = float(accuracy_score(y_te, ens_preds))
    lgbm_auc = float(roc_auc_score(y_te, lgbm_proba))
    ens_auc = float(roc_auc_score(y_te, ens_proba))

    # ── Sharpe approximation: daily-return Sharpe ────────────────────────
    # Treat predictions as positions (+1 / -1) and true class as sign of return.
    # Assume each correct bet yields +1 unit, incorrect yields -1.
    def _simple_sharpe(preds, truth):
        returns = np.where(preds == truth, 1.0, -1.0)
        mu = returns.mean()
        sigma = returns.std(ddof=1)
        if sigma == 0:
            return 0.0
        return float(mu / sigma)

    lgbm_sharpe = _simple_sharpe(lgbm_preds, y_te)
    ens_sharpe = _simple_sharpe(ens_preds, y_te)

    comparison = {
        "accuracy": {"lgbm": round(lgbm_acc, 6), "ensemble": round(ens_acc, 6)},
        "roc_auc": {"lgbm": round(lgbm_auc, 6), "ensemble": round(ens_auc, 6)},
        "sharpe_approx": {
            "lgbm": round(lgbm_sharpe, 6),
            "ensemble": round(ens_sharpe, 6),
        },
    }

    winner = "ensemble" if ens_auc >= lgbm_auc else "lgbm"

    return {
        "comparison": comparison,
        "winner": winner,
        "ensemble_cv": ensemble_cv,
        "lgbm_cv": round(lgbm_cv, 6),
    }
