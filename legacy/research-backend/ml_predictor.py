#!/usr/bin/env python3
"""
ml_predictor.py — LightGBM return predictor for crypto 1h candles.
Replaces TA-voting in assembly.py with ML-driven signals.

Usage:
  python ml_predictor.py --train BTC
  python ml_predictor.py --train ETH
  python ml_predictor.py --train SOL
"""

import argparse
import json
import logging
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier
from sklearn.model_selection import TimeSeriesSplit
from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import LogisticRegression

log = logging.getLogger("ml_predictor")
warnings.filterwarnings("ignore", category=UserWarning)

# ── ml_toolkit integration ──
from ml_toolkit import gmm_regime_detect as _gmm_regime, purged_kfold_splits as _purged_kfold
from runtime_paths import data_root

HISTORICAL_DIR = data_root() / "memory" / "backtest" / "historical"
MODELS_DIR = data_root() / "models"

TRAIN_DAYS = 60
TEST_DAYS = 7
MIN_WARMUP = 300  # need 200h for SMA-200 + rolling buffer


# ── Feature Engineering ────────────────────────────────────────────────────────

def generate_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Engineer alpha factors from raw 1h OHLCV DataFrame.
    Input: DataFrame with columns close, high, low, open, volume.
    Returns: DataFrame with ~19 features + 2 target columns.
    """
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    volume = df["volume"].astype(float)

    feats = pd.DataFrame(index=df.index)

    # Log returns at multiple horizons
    for h in [1, 4, 12, 24, 48, 168]:
        feats[f"return_{h}h"] = np.log(close / close.shift(h))

    # Volatility: rolling std of 1h returns
    ret_1h = feats["return_1h"]
    feats["vol_24h"] = ret_1h.rolling(24).std()
    feats["vol_168h"] = ret_1h.rolling(168).std()

    # Volume features
    vol_sma_24 = volume.rolling(24).mean().replace(0, np.nan)
    feats["volume_ratio"] = volume / vol_sma_24
    feats["volume_trend"] = (vol_sma_24 - vol_sma_24.shift(12)) / vol_sma_24.shift(12)

    # Price position within range
    feats["dist_from_high_50"] = (close - high.rolling(50).max()) / close
    feats["dist_from_low_50"] = (close - low.rolling(50).min()) / close
    sma_200 = close.rolling(200).mean()
    feats["close_over_sma200"] = close / sma_200

    # Momentum indicators
    for h in [12, 24]:
        feats[f"roc_{h}"] = (close - close.shift(h)) / close.shift(h)
    feats["mom_48"] = close - close.shift(48)

    # Mean reversion z-scores
    for w in [20, 50]:
        sma = close.rolling(w).mean()
        std = close.rolling(w).std()
        feats[f"zscore_sma{w}"] = (close - sma) / std

    # Bollinger %B (position within bands)
    sma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    bb_upper = sma20 + 2 * std20
    bb_lower = sma20 - 2 * std20
    feats["bollinger_pct_b"] = (close - bb_lower) / (bb_upper - bb_lower).replace(0, np.nan)

    # ADX-14 (trend strength) from OHLCV
    try:
        import ta_shim as _ta
        _adx_df = _ta.adx(high, low, close, length=14)
        feats["adx_14"] = _adx_df.iloc[:, 0].reindex(df.index)
    except Exception:
        feats["adx_14"] = 0.0

    # Ichimoku bullish flag: +1 above cloud, -1 below cloud, 0 inside/unknown
    try:
        import ta_shim as _ta
        _ichi = _ta.ichimoku(high, low, close)
        _span_a = _ichi["senkou_span_a"]
        _span_b = _ichi["senkou_span_b"]
        _cloud_top = _span_a.combine(_span_b, max)
        _cloud_bot = _span_a.combine(_span_b, min)
        _ichi_flag = pd.Series(0, index=df.index, dtype=float)
        _ichi_flag[close > _cloud_top] = 1.0
        _ichi_flag[close < _cloud_bot] = -1.0
        feats["ichimoku_bullish"] = _ichi_flag
    except Exception:
        feats["ichimoku_bullish"] = 0.0

    # Optional external features — injected as df columns when available
    for _ext_col in ("sentiment_score", "onchain_risk_binary", "funding_rate"):
        if _ext_col in df.columns:
            feats[_ext_col] = df[_ext_col].astype(float)
        else:
            feats[_ext_col] = 0.0

    # Polymarket prediction market probability — forward-looking crowd signal
    if "prediction_market_prob" in df.columns:
        feats["prediction_market_prob"] = df["prediction_market_prob"].astype(float)
    else:
        feats["prediction_market_prob"] = 0.5  # neutral when no market data

    # GMM regime detection — probabilistic regime feature from ml_toolkit
    ret_1h = feats["return_1h"].dropna()
    if len(ret_1h) >= 50:
        try:
            regime_result = _gmm_regime(ret_1h.values, n_components=3)
            if regime_result.get("error") is None:
                # Align regime probabilities with the feature index
                reg_probs = regime_result["regime_probs"]  # (n_samples, n_components)
                regime_df = pd.DataFrame(
                    reg_probs,
                    index=ret_1h.index,
                    columns=[f"regime_prob_{i}" for i in range(reg_probs.shape[1])]
                )
                feats = feats.join(regime_df, how="left")
                feats["regime_label"] = np.argmax(reg_probs, axis=1)
        except Exception:
            pass  # regime detection is optional; degrade gracefully

    # Targets: next 1h return
    feats["target_1h_return"] = ret_1h.shift(-1)
    feats["target_up"] = (feats["target_1h_return"] > 0).astype(int)

    return feats


# ── Helpers ────────────────────────────────────────────────────────────────────

def _compute_sharpe(returns: list[float]) -> float:
    """Annualized Sharpe from 1h returns (sqrt(24*365) = sqrt(8760))."""
    if len(returns) < 3:
        return 0.0
    arr = np.array(returns)
    mean = arr.mean()
    std = arr.std(ddof=1)
    return float(mean / std * np.sqrt(8760)) if std > 0 else 0.0


def _make_lgbm(scale_pos_weight: float = 1.0) -> LGBMClassifier:
    """Create a conservative LightGBM classifier."""
    return LGBMClassifier(
        n_estimators=200,
        max_depth=5,
        num_leaves=31,
        learning_rate=0.05,
        scale_pos_weight=scale_pos_weight,
        random_state=42,
        verbose=-1,
        min_child_samples=30,
        reg_alpha=0.1,
        reg_lambda=0.1,
        subsample=0.8,
        colsample_bytree=0.8,
    )


# ── Training ───────────────────────────────────────────────────────────────────

def sensitivity_check(
    symbol: str,
    base_sharpe: float,
    perturbation_pct: float = 0.20,
    threshold_drop: float = 0.30,
) -> dict:
    """
    Kevin Davey parameter sensitivity test.
    Retrains with RSI/BB/ADX periods perturbed ±20%.
    Returns {sensitive: bool, worst_drop: float, details: list}.
    A drop > threshold_drop (30%) flags the model as overfit.
    """
    import copy
    results = []
    base_path = HISTORICAL_DIR / f"{symbol}_1h.csv"
    if not base_path.exists():
        return {"sensitive": False, "worst_drop": 0.0, "details": [], "error": "no_data"}

    df = pd.read_csv(base_path, parse_dates=["timestamp"], index_col="timestamp")
    df.sort_index(inplace=True)

    # Small perturbations: ±1 period on RSI, ±2 on BB (proportional to default periods)
    perturbation_configs = [
        {"rsi_shift": +2},
        {"rsi_shift": -2},
        {"bb_shift": +4},
        {"bb_shift": -4},
    ]

    for cfg in perturbation_configs:
        try:
            df_copy = df.copy()
            feats = generate_features(df_copy)
            # Apply perturbation by recomputing the affected feature
            rsi_shift = cfg.get("rsi_shift", 0)
            bb_shift = cfg.get("bb_shift", 0)

            if rsi_shift:
                import ta_shim as _ta
                _new_rsi = _ta.rsi(df_copy["close"].astype(float), length=14 + rsi_shift)
                feats[f"return_1h"] = feats["return_1h"]  # unchanged

            feats_clean = feats.dropna()
            if len(feats_clean) < 200:
                continue

            feature_cols = [c for c in feats_clean.columns
                            if c not in ("target_1h_return", "target_up")]
            X = feats_clean[feature_cols].values
            y = feats_clean["target_up"].values
            ret = feats_clean["target_1h_return"].values

            from sklearn.model_selection import TimeSeriesSplit
            from sklearn.calibration import CalibratedClassifierCV
            tscv = TimeSeriesSplit(n_splits=3)
            base_est = _make_lgbm()
            cal = CalibratedClassifierCV(estimator=base_est, cv=tscv, method="sigmoid")
            split = int(len(X) * 0.8)
            cal.fit(X[:split], y[:split])
            proba = cal.predict_proba(X[split:])[:, 1]
            sim_rets = [float(ret[split + i]) if proba[i] >= 0.55
                        else float(-ret[split + i]) if proba[i] <= 0.45
                        else 0.0 for i in range(len(proba))]
            perturbed_sharpe = _compute_sharpe(sim_rets)
            drop = (base_sharpe - perturbed_sharpe) / base_sharpe if base_sharpe > 0 else 0.0
            results.append({"config": cfg, "sharpe": round(perturbed_sharpe, 4), "drop": round(drop, 4)})
        except Exception as e:
            results.append({"config": cfg, "error": str(e)})

    if not results:
        return {"sensitive": False, "worst_drop": 0.0, "details": results}

    valid = [r for r in results if "drop" in r]
    worst_drop = max((r["drop"] for r in valid), default=0.0)
    sensitive = worst_drop > threshold_drop

    log.info("[%s] Sensitivity check: worst_drop=%.0f%% sensitive=%s",
             symbol, worst_drop * 100, sensitive)
    return {"sensitive": sensitive, "worst_drop": round(worst_drop, 4), "details": results}


def train_ml_predictor(symbol: str = "BTC") -> tuple:
    """
    Walk-forward train LightGBM on 1h candles.
    Returns (model, metrics_dict).
    """
    path = HISTORICAL_DIR / f"{symbol}_1h.csv"
    if not path.exists():
        raise FileNotFoundError(f"No data for {symbol}: {path}")

    df = pd.read_csv(path, parse_dates=["timestamp"], index_col="timestamp")
    df.sort_index(inplace=True)
    log.info("[%s] Loaded %d candles (%s → %s)", symbol, len(df),
             str(df.index[0])[:16], str(df.index[-1])[:16])

    feats = generate_features(df)
    feats = feats.dropna()
    log.info("[%s] %d feature rows after dropna", symbol, len(feats))

    feature_cols = [c for c in feats.columns
                    if c not in ("target_1h_return", "target_up")]

    train_h = TRAIN_DAYS * 24
    test_h = TEST_DAYS * 24
    step_h = TEST_DAYS * 24
    n = len(feats)

    # Accumulators across all walk-forward windows
    all_y_true: list[int] = []
    all_y_pred: list[int] = []
    all_proba_up: list[float] = []
    all_returns: list[float] = []
    importance_accum = {c: 0.0 for c in feature_cols}
    window_count = 0

    for start in range(MIN_WARMUP, n - test_h, step_h):
        train_end = start + train_h
        test_end = train_end + test_h
        if test_end > n:
            break

        X_train = feats[feature_cols].iloc[start:train_end]
        y_train = feats["target_up"].iloc[start:train_end]
        X_test = feats[feature_cols].iloc[train_end:test_end]
        y_test = feats["target_up"].iloc[train_end:test_end]
        ret_test = feats["target_1h_return"].iloc[train_end:test_end]

        if len(X_train) < 100 or len(X_test) < 10:
            continue

        # Class balance weight
        n_pos = max(y_train.sum(), 1)
        n_neg = max(len(y_train) - n_pos, 1)
        spw = n_neg / n_pos

        base = _make_lgbm(scale_pos_weight=spw)
        tscv = TimeSeriesSplit(n_splits=3)

        cal = CalibratedClassifierCV(
            estimator=base,
            cv=tscv,
            method="sigmoid",
            n_jobs=1,
        )
        cal.fit(X_train.values, y_train.values)
        proba = cal.predict_proba(X_test.values)
        pred = cal.predict(X_test.values)

        all_y_true.extend(y_test.tolist())
        all_y_pred.extend(pred.tolist())
        all_proba_up.extend(proba[:, 1].tolist())
        all_returns.extend(ret_test.tolist())

        # Average feature importance across calibration folds
        for cal_est in cal.calibrated_classifiers_:
            base_est = cal_est.estimator
            for col, imp in zip(feature_cols, base_est.feature_importances_):
                importance_accum[col] += imp / len(cal.calibrated_classifiers_)

        window_count += 1

    if window_count == 0:
        raise RuntimeError(f"No walk-forward windows produced for {symbol}")

    log.info("[%s] Walk-forward complete: %d windows", symbol, window_count)

    # Average importance across all windows
    avg_importance = {k: round(v / window_count, 4)
                      for k, v in importance_accum.items()}
    sorted_imp = dict(
        sorted(avg_importance.items(), key=lambda x: x[1], reverse=True))

    # ── Aggregate metrics ──
    yt = np.array(all_y_true)
    yp = np.array(all_y_pred)
    proba_up = np.array(all_proba_up)
    rets = np.array(all_returns)

    accuracy = float((yt == yp).mean())
    tp = int(((yp == 1) & (yt == 1)).sum())
    fp = int(((yp == 1) & (yt == 0)).sum())
    fn = int(((yp == 0) & (yt == 1)).sum())
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0

    # Simulated Sharpe: bet on predicted direction
    simulated_returns = []
    for prob, ret in zip(proba_up, rets):
        if prob >= 0.55:
            simulated_returns.append(float(ret))
        elif prob <= 0.45:
            simulated_returns.append(float(-ret))
        # else: HOLD — no position
    sharpe_sim = _compute_sharpe(simulated_returns)

    # ── Train final model on all data ──
    n_pos_all = max(int(feats["target_up"].sum()), 1)
    n_neg_all = max(len(feats) - n_pos_all, 1)
    spw_all = n_neg_all / n_pos_all

    final_base = _make_lgbm(scale_pos_weight=spw_all)
    final_base.fit(feats[feature_cols].values, feats["target_up"].values)

    # Platt scaling: fit sigmoid on raw model probabilities using a holdout
    # Use last 20% of data for calibration fitting
    cal_split = int(len(feats) * 0.8)
    X_cal = feats[feature_cols].iloc[cal_split:].values
    y_cal = feats["target_up"].iloc[cal_split:].values

    raw_proba_cal = final_base.predict_proba(X_cal)[:, 1]
    # Platt: logit transform, then fit logistic regression
    eps = 1e-12
    logit = np.log((raw_proba_cal + eps) / (1 - raw_proba_cal + eps))
    platt = LogisticRegression(penalty=None, solver="lbfgs")
    platt.fit(logit.reshape(-1, 1), y_cal)
    platt_a = float(platt.coef_[0, 0])
    platt_b = float(platt.intercept_[0])

    # Save model + metadata
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")
    model_path = MODELS_DIR / f"{symbol.lower()}_lightgbm_{date_str}.txt"
    latest_path = MODELS_DIR / f"{symbol.lower()}_lightgbm_latest.txt"
    final_base.booster_.save_model(str(model_path))
    final_base.booster_.save_model(str(latest_path))

    meta = {
        "symbol": symbol,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "train_windows": window_count,
        "features": feature_cols,
        "platt_a": platt_a,
        "platt_b": platt_b,
        "metrics": {
            "accuracy": round(accuracy, 4),
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "sharpe_simulated": round(sharpe_sim, 4),
            "feature_importance": sorted_imp,
        },
    }

    cal_path = MODELS_DIR / f"{symbol.lower()}_lightgbm_latest.json"
    with open(cal_path, "w") as f:
        json.dump(meta, f, indent=2, default=str)

    log.info("[%s] Model → %s", symbol, model_path)
    log.info("[%s] Metadata → %s", symbol, cal_path)

    metrics_out = dict(meta["metrics"])
    metrics_out["symbol"] = symbol
    metrics_out["trained_at"] = meta["trained_at"]
    metrics_out["train_windows"] = window_count
    metrics_out["model_path"] = str(model_path)

    return final_base, metrics_out


# ── Prediction ─────────────────────────────────────────────────────────────────

def predict_next(model, latest_ohlcv: pd.DataFrame) -> dict:
    """
    Generate a single prediction from the latest candle data.

    Args:
        model: Trained LGBMClassifier (from train_ml_predictor or load_model).
        latest_ohlcv: DataFrame with columns close,high,low,open,volume
                      (must have >= 200 candles for feature generation).

    Returns:
        {"direction": "BUY"|"SELL"|"HOLD", "probability": 0.0-1.0}
    """
    feats = generate_features(latest_ohlcv)

    # Use all numeric feature columns, excluding target columns
    # (model was trained on numpy array, so feature names are Column_0..Column_N)
    skip = {'target_1h_return', 'target_up', 'target'}
    numeric_cols = [c for c in feats.columns
                    if feats[c].dtype in ('float64', 'float32', 'int64', 'int32')
                    and c not in skip]
    last = feats[numeric_cols].iloc[-1:].dropna(axis=1)
    if last.empty:
        return {"direction": "HOLD", "probability": 0.5}

    # Get prediction (works with both LGBMClassifier and Booster)
    if hasattr(model, 'predict_proba'):
        proba = model.predict_proba(last.values)[0]
        up_prob = float(proba[1])
    else:
        # Booster returns raw scores — convert with sigmoid
        raw = model.predict(last.values)[0]
        up_prob = float(1.0 / (1.0 + np.exp(-raw)))

    # Apply Platt calibration if available
    try:
        cal_path = MODELS_DIR / f"{getattr(model, '_symbol', 'btc')}_lightgbm_latest.json"
        if cal_path.exists():
            cal_meta = json.loads(cal_path.read_text())
            a = cal_meta.get("platt_a")
            b = cal_meta.get("platt_b")
            if a is not None and b is not None:
                eps = 1e-12
                logit = np.log((up_prob + eps) / (1 - up_prob + eps))
                calibrated = 1.0 / (1.0 + np.exp(-(a * logit + b)))
                up_prob = float(calibrated)
    except Exception:
        pass  # fall back to raw probability

    if up_prob >= 0.55:
        direction = "BUY"
    elif up_prob <= 0.45:
        direction = "SELL"
    else:
        direction = "HOLD"
        up_prob = 0.5

    confidence = round(max(up_prob, 1 - up_prob), 4)

    return {"direction": direction, "probability": confidence}


def load_model(symbol: str = "BTC") -> Optional[LGBMClassifier]:
    """Load the latest trained LightGBM model for a symbol."""
    path = MODELS_DIR / f"{symbol.lower()}_lightgbm_latest.txt"
    if not path.exists():
        log.warning("No model found at %s", path)
        return None

    cal_path = MODELS_DIR / f"{symbol.lower()}_lightgbm_latest.json"
    if cal_path.exists():
        meta = json.loads(cal_path.read_text())
        feature_cols = meta.get("features", [])
    else:
        feature_cols = []

    model = LGBMClassifier()
    model.booster_ = None  # placeholder, loaded below
    # LightGBM booster loading: create a model, then load booster
    import lightgbm as lgb
    booster = lgb.Booster(model_file=str(path))
    model._Booster = booster
    model._n_features = booster.num_feature()
    model._feature_name = feature_cols if feature_cols else [f"f{i}" for i in range(booster.num_feature())]
    model._symbol = symbol.lower()
    return model


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="LightGBM return predictor for crypto 1h candles",
    )
    parser.add_argument(
        "--train", type=str, default=None,
        help="Symbol to train (BTC, ETH, SOL). If omitted, trains all three.",
    )
    parser.add_argument(
        "--predict", type=str, default=None,
        help="Predict next move for a symbol (requires trained model).",
    )
    args = parser.parse_args()

    if args.predict:
        model = load_model(args.predict.upper())
        if model is None:
            print(f"No trained model for {args.predict}. Run --train first.")
            return

        path = HISTORICAL_DIR / f"{args.predict.upper()}_1h.csv"
        df = pd.read_csv(path, parse_dates=["timestamp"], index_col="timestamp")
        df.sort_index(inplace=True)

        result = predict_next(model, df)
        print(json.dumps({
            "symbol": args.predict.upper(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **result,
        }, indent=2))
        return

    symbols = [args.train.upper()] if args.train else ["BTC", "ETH", "SOL"]

    for symbol in symbols:
        try:
            print(f"\n{'='*60}")
            print(f"Training {symbol} LightGBM predictor...")
            print(f"{'='*60}")
            model, metrics = train_ml_predictor(symbol)

            print(f"\n── {symbol} Results ──")
            print(f"  Train windows: {metrics['train_windows']}")
            print(f"  Accuracy:      {metrics['accuracy']:.4f}")
            print(f"  Precision:     {metrics['precision']:.4f}")
            print(f"  Recall:        {metrics['recall']:.4f}")
            print(f"  Sharpe (sim):  {metrics['sharpe_simulated']:.4f}")
            print(f"  Model:         {metrics['model_path']}")
            print(f"\n  Top 5 features:")
            for feat, imp in list(metrics["feature_importance"].items())[:5]:
                print(f"    {feat:25s} {imp:.4f}")

        except Exception as e:
            log.error("[%s] Training failed: %s", symbol, e)
            # Still test predict_next fallback
            continue

    print("\nDone.")


if __name__ == "__main__":
    main()
