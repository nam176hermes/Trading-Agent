#!/usr/bin/env python3
"""
Train ensemble ML classifier (LightGBM + RF stacking) and compare to single LightGBM.

Uses the existing ml_predictor.generate_features() for feature engineering
and ensemble_ml.train_ensemble() for training. Saves trained model via pickle.

Usage:
    python train_ensemble.py --symbol BTC/USDT
    python train_ensemble.py --symbol ETH/USDT --compare
    python train_ensemble.py --all
"""

import argparse
import json
import logging
import pickle
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from runtime_paths import data_root

log = logging.getLogger("train_ensemble")

MODELS_DIR = data_root() / "models"
DATA_DIR = data_root() / "data"

SYMBOL_MAP = {
    "BTC": "BTC/USDT", "BTC/USDT": "BTC/USDT",
    "ETH": "ETH/USDT", "ETH/USDT": "ETH/USDT",
    "SOL": "SOL/USDT", "SOL/USDT": "SOL/USDT",
}


def load_ohlcv(symbol: str) -> pd.DataFrame:
    """Load OHLCV data from data/ directory or yfinance fallback."""
    clean = symbol.replace("/", "")
    csv_path = DATA_DIR / f"{clean}_1h.csv"
    if csv_path.exists():
        df = pd.read_csv(csv_path, parse_dates=["timestamp"], index_col="timestamp")
        if all(c in df.columns for c in ["open", "high", "low", "close", "volume"]):
            return df

    # Fallback: try backtest cache
    cache_dir = data_root() / "memory" / "backtest" / "cache"
    cache_path = cache_dir / f"{clean}_1h.parquet"
    if cache_path.exists():
        df = pd.read_parquet(cache_path)
        return df

    raise FileNotFoundError(f"No OHLCV data found for {symbol}")


def train_ensemble_for_symbol(symbol: str) -> dict:
    """Train ensemble model for one symbol. Returns metrics dict."""
    from ml_predictor import generate_features
    from ensemble_ml import train_ensemble, predict_ensemble, compare_single_vs_ensemble

    symbol = SYMBOL_MAP.get(symbol, symbol)
    clean = symbol.replace("/", "")
    log.info("Loading data for %s...", symbol)

    df = load_ohlcv(symbol)
    log.info("Loaded %d bars for %s", len(df), symbol)

    # Generate features
    feats = generate_features(df)
    feats = feats.dropna()

    if len(feats) < 500:
        raise ValueError(f"Only {len(feats)} rows after feature engineering — need >= 500")

    # Extract features and targets
    target_col = "target_12h"
    if target_col not in feats.columns:
        # Try alternative target column
        target_cols = [c for c in feats.columns if c.startswith("target")]
        if target_cols:
            target_col = target_cols[0]
        else:
            raise ValueError("No target column found in features DataFrame")

    y = feats[target_col].values.astype(int)
    feature_cols = [c for c in feats.columns if not c.startswith("target")]
    X = feats[feature_cols].values

    # Train/test split (80/20, chronological — first 80% train, last 20% test)
    split_idx = int(len(X) * 0.8)
    X_train, X_test = X[:split_idx], X[split_idx:]
    y_train, y_test = y[:split_idx], y[split_idx:]

    log.info("Training ensemble on %d samples, testing on %d", len(X_train), len(X_test))

    # Train ensemble
    result = train_ensemble(X_train, y_train)
    model = result["model"]

    # Evaluate
    train_acc = result["train_accuracy"]
    cv_scores = result["cv_scores"]
    cv_mean = float(np.mean(cv_scores)) if cv_scores else 0.0

    # Test set predictions
    test_preds = predict_ensemble(result, X_test)
    test_acc = float(np.mean(test_preds == y_test))

    # Compare to single LightGBM
    comparison = compare_single_vs_ensemble(X_train, y_train)

    log.info("Ensemble: train_acc=%.4f, cv_mean=%.4f, test_acc=%.4f",
             train_acc, cv_mean, test_acc)
    log.info("Single LGBM: cv_mean=%.4f", comparison.get("single_cv_mean", 0))

    # Save model
    save_path = MODELS_DIR / f"{clean}_ensemble_latest.pkl"
    with open(save_path, "wb") as f:
        pickle.dump({
            "model": model,
            "feature_names": feature_cols,
            "target_col": target_col,
            "trained_at": datetime.now(timezone.utc).isoformat(),
        }, f)
    log.info("Saved ensemble model to %s", save_path)

    # Save metrics
    meta_path = MODELS_DIR / f"{clean}_ensemble_latest.json"
    meta_path.write_text(json.dumps({
        "symbol": symbol,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "n_train": len(X_train),
        "n_test": len(X_test),
        "n_features": len(feature_cols),
        "train_accuracy": round(train_acc, 4),
        "cv_mean": round(cv_mean, 4),
        "test_accuracy": round(test_acc, 4),
        "single_cv_mean": round(comparison.get("single_cv_mean", 0), 4),
        "ensemble_vs_single_delta": round(test_acc - comparison.get("single_cv_mean", 0), 4),
    }, indent=2))

    return {
        "symbol": symbol,
        "train_accuracy": train_acc,
        "cv_mean": cv_mean,
        "test_accuracy": test_acc,
        "single_cv_mean": comparison.get("single_cv_mean", 0),
        "delta": test_acc - comparison.get("single_cv_mean", 0),
    }


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="Train ensemble ML for crypto signals")
    parser.add_argument("--symbol", type=str, default="BTC/USDT", help="Symbol to train")
    parser.add_argument("--all", action="store_true", help="Train all symbols")
    parser.add_argument("--compare", action="store_true", help="Show detailed comparison")
    args = parser.parse_args()

    symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT"] if args.all else [args.symbol]

    results = []
    for sym in symbols:
        try:
            r = train_ensemble_for_symbol(sym)
            results.append(r)
        except Exception as e:
            log.error("Failed to train %s: %s", sym, e)

    if not results:
        log.error("No models trained successfully")
        sys.exit(1)

    # Summary
    print("\n" + "=" * 70)
    print("  ENSEMBLE TRAINING SUMMARY")
    print("=" * 70)
    for r in results:
        print(f"  {r['symbol']:12s}  Ensemble Acc: {r['test_accuracy']:.4f}  "
              f"Single LGBM: {r['single_cv_mean']:.4f}  "
              f"Δ: {r['delta']:+.4f}")
    print("=" * 70)


if __name__ == "__main__":
    main()
