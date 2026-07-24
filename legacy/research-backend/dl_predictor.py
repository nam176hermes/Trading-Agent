#!/usr/bin/env python3
"""
dl_predictor.py — Deep Learning price predictor (LSTM/GRU) for crypto 1h candles.
Complements/replaces LightGBM in assembly.py.

Usage:
  python dl_predictor.py --train --symbol BTC/USDT
  python dl_predictor.py --train --all
  python dl_predictor.py --predict --symbol BTC/USDT
  python dl_predictor.py --backtest --symbol BTC/USDT
"""

import argparse
import json
import logging
import math
import warnings
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader
from local_artifacts import atomic_private_write, open_regular_read
from runtime_paths import data_root

log = logging.getLogger("dl_predictor")
warnings.filterwarnings("ignore", category=UserWarning)

HISTORICAL_DIR = data_root() / "memory" / "backtest" / "historical"
MODELS_DIR = data_root() / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)

DEVICE = torch.device("cpu")
torch.set_num_threads(min(torch.get_num_threads(), 8))
CHECKPOINT_VERSION = 1
MAX_CHECKPOINT_BYTES = 128 * 1024 * 1024
MAX_CHECKPOINT_TENSORS = 256
MAX_CHECKPOINT_TENSOR_ELEMENTS = 10_000_000


def _base_symbol(symbol: str) -> str:
    """Extract base currency: 'BTC/USDT' → 'BTC', 'ETH' → 'ETH'."""
    return symbol.split("/")[0].upper()


# ══════════════════════════════════════════════════════════════════════════════
# Config
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class LSTMConfig:
    seq_len: int = 30
    horizon: int = 4
    threshold: float = 0.005
    hidden_dim: int = 32
    num_layers: int = 1
    dropout: float = 0.5
    lr: float = 1e-3
    weight_decay: float = 1e-2
    batch_size: int = 32
    max_epochs: int = 150
    early_stop_patience: int = 25
    model_type: str = "lstm"


_CONFIG_FIELDS = tuple(field.name for field in fields(LSTMConfig))


def _config_to_dict(config: LSTMConfig) -> dict:
    """Serialize configuration as validated primitives, never as a Python object."""
    if not isinstance(config, LSTMConfig):
        raise ValueError("invalid LSTM configuration")
    result = {name: getattr(config, name) for name in _CONFIG_FIELDS}
    validated = _config_from_dict(result)
    return {name: getattr(validated, name) for name in _CONFIG_FIELDS}


def _config_from_dict(raw: object) -> LSTMConfig:
    if not isinstance(raw, dict) or set(raw) != set(_CONFIG_FIELDS):
        raise ValueError("checkpoint config schema is invalid")
    int_bounds = {
        "seq_len": (1, 512), "horizon": (1, 168), "hidden_dim": (1, 2048),
        "num_layers": (1, 8), "batch_size": (1, 4096), "max_epochs": (1, 10_000),
        "early_stop_patience": (1, 10_000),
    }
    for key, (minimum, maximum) in int_bounds.items():
        value = raw[key]
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise ValueError("checkpoint config bounds are invalid")
    for key, minimum, maximum in (("threshold", 0.0, 1.0), ("dropout", 0.0, 0.95), ("lr", 0.0, 1.0), ("weight_decay", 0.0, 1.0)):
        value = raw[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or not minimum <= float(value) <= maximum:
            raise ValueError("checkpoint config bounds are invalid")
    if float(raw["lr"]) <= 0 or raw["model_type"] not in {"lstm", "gru"}:
        raise ValueError("checkpoint config bounds are invalid")
    return LSTMConfig(**{name: raw[name] for name in _CONFIG_FIELDS})


def _finite_primitive(value: object, depth: int = 0) -> bool:
    if depth > 8:
        return False
    if value is None or isinstance(value, bool):
        return True
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return math.isfinite(float(value))
    if isinstance(value, str):
        return len(value) <= 1024
    if isinstance(value, list):
        return len(value) <= 1024 and all(_finite_primitive(item, depth + 1) for item in value)
    if isinstance(value, dict):
        return len(value) <= 1024 and all(isinstance(key, str) and len(key) <= 128 and _finite_primitive(item, depth + 1) for key, item in value.items())
    return False


# ══════════════════════════════════════════════════════════════════════════════
# Feature engineering
# ══════════════════════════════════════════════════════════════════════════════

def _compute_features(df: pd.DataFrame) -> pd.DataFrame:
    """Derive features from raw OHLCV bars — matches ml_predictor.py feature set."""
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    volume = df["volume"].astype(float)

    feats = pd.DataFrame(index=df.index)
    feats["open"] = df["open"].astype(float)
    feats["high"] = high
    feats["low"] = low
    feats["close"] = close
    feats["volume"] = volume

    # Derived price features
    feats["returns"] = close.pct_change()
    feats["hl_ratio"] = (high - low) / close.replace(0, np.nan)
    feats["close_position"] = (close - low) / (high - low).replace(0, np.nan)

    # Volume features
    vol_sma_24 = volume.rolling(24).mean().replace(0, np.nan)
    feats["volume_sma_ratio"] = volume / vol_sma_24
    feats["volume_trend"] = vol_sma_24.pct_change(12)

    # Log returns at multiple horizons (from ml_predictor)
    for h in [1, 4, 12, 24, 48]:
        feats[f"return_{h}h"] = np.log(close / close.shift(h))

    # Rolling volatility
    feats["vol_24h"] = feats["return_1h"].rolling(24).std()
    feats["vol_168h"] = feats["return_1h"].rolling(168).std()

    # Price position within range (from ml_predictor)
    feats["dist_from_high_50"] = (close - high.rolling(50).max()) / close
    feats["dist_from_low_50"] = (close - low.rolling(50).min()) / close
    sma_200 = close.rolling(200).mean()
    feats["close_over_sma200"] = close / sma_200

    # Momentum (from ml_predictor)
    for h in [12, 24]:
        feats[f"roc_{h}"] = (close - close.shift(h)) / close.shift(h)
    feats["mom_48"] = close - close.shift(48)

    # Mean reversion z-scores (from ml_predictor)
    for w in [20, 50]:
        sma = close.rolling(w).mean()
        std = close.rolling(w).std()
        feats[f"zscore_sma{w}"] = (close - sma) / std

    # Bollinger %B (from ml_predictor)
    sma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    bb_upper = sma20 + 2 * std20
    bb_lower = sma20 - 2 * std20
    feats["bollinger_pct_b"] = (close - bb_lower) / (bb_upper - bb_lower).replace(0, np.nan)

    return feats


FEATURE_COLS = [
    "open", "high", "low", "close", "volume",
    "returns", "hl_ratio", "close_position", "volume_sma_ratio",
    "volume_trend",
    "return_1h", "return_4h", "return_12h", "return_24h", "return_48h",
    "vol_24h", "vol_168h",
    "dist_from_high_50", "dist_from_low_50", "close_over_sma200",
    "roc_12", "roc_24", "mom_48",
    "zscore_sma20", "zscore_sma50",
    "bollinger_pct_b",
]


# ══════════════════════════════════════════════════════════════════════════════
# Dataset
# ══════════════════════════════════════════════════════════════════════════════

class SequenceDataset(Dataset):
    """Chronological sequences of OHLCV bars with binary directional targets."""

    def __init__(self, df: pd.DataFrame, seq_len: int = 60, horizon: int = 12,
                 threshold: float = 0.005):
        feats = _compute_features(df).dropna()
        if len(feats) < seq_len + horizon + 1:
            raise ValueError(f"Need at least {seq_len + horizon + 1} bars, got {len(feats)}")

        self.seq_len = seq_len
        self.horizon = horizon
        self.threshold = threshold

        n = len(feats)
        # Build sequences and targets
        # sliding_window_view appends window dim at end: [n-seq_len+1, n_features, seq_len]
        X = np.lib.stride_tricks.sliding_window_view(
            feats[FEATURE_COLS].values, seq_len, axis=0
        )
        X = np.moveaxis(X, -1, 1)  # → [num_sequences, seq_len, n_features]
        X = X[:n - seq_len - horizon + 1]  # ensure target exists

        close_vals = feats["close"].values
        future_close = np.array([close_vals[i + seq_len + horizon - 1]
                                  for i in range(len(X))])
        current_close = np.array([close_vals[i + seq_len - 1]
                                   for i in range(len(X))])

        pct_change = (future_close - current_close) / current_close
        self.y = (pct_change > threshold).astype(np.float32)
        self.returns = pct_change.astype(np.float32)  # actual horizon returns
        self.X = X.astype(np.float32)

    def __len__(self):
        return len(self.X)

    def __getitem__(self, idx):
        x = torch.from_numpy(self.X[idx])
        y = torch.tensor(self.y[idx])
        return x, y


def _chronological_split(dataset: SequenceDataset, train_pct: float = 0.6,
                         val_pct: float = 0.2):
    """Split dataset chronologically with global z-score normalization.
    Normalization stats computed from training split only.
    Returns train_ds, val_ds, test_ds, test_returns, normalizers dict.
    """
    n = len(dataset)
    train_end = int(n * train_pct)
    val_end = int(n * (train_pct + val_pct))

    # Compute global mean/std from training data only
    X_train_raw = dataset.X[:train_end]
    global_mean = X_train_raw.mean(axis=(0, 1))
    global_std = X_train_raw.std(axis=(0, 1)) + 1e-8

    normalizers = {"mean": global_mean.astype(np.float32),
                   "std": global_std.astype(np.float32)}

    def _slice(start, end):
        X_norm = (dataset.X[start:end] - global_mean) / global_std
        X_t = torch.from_numpy(X_norm.astype(np.float32))
        y_t = torch.from_numpy(dataset.y[start:end].copy())
        return torch.utils.data.TensorDataset(X_t, y_t)

    test_returns = dataset.returns[val_end:n]
    return (_slice(0, train_end), _slice(train_end, val_end),
            _slice(val_end, n), test_returns, normalizers)


# ══════════════════════════════════════════════════════════════════════════════
# Model
# ══════════════════════════════════════════════════════════════════════════════

class PriceLSTM(nn.Module):
    """LSTM/GRU with dropout and a linear head for binary direction prediction."""

    def __init__(self, input_dim: int, config: LSTMConfig):
        super().__init__()
        self.config = config
        self.input_dim = input_dim

        rnn_cls = nn.GRU if config.model_type == "gru" else nn.LSTM
        self.rnn = rnn_cls(
            input_size=input_dim,
            hidden_size=config.hidden_dim,
            num_layers=config.num_layers,
            dropout=config.dropout if config.num_layers > 1 else 0.0,
            batch_first=True,
        )
        self.head = nn.Sequential(
            nn.Linear(config.hidden_dim, 64),
            nn.ReLU(),
            nn.Dropout(config.dropout),
            nn.Linear(64, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """x: [batch, seq_len, input_dim] → logit: [batch, 1]"""
        out, _ = self.rnn(x)           # out: [batch, seq_len, hidden_dim]
        last = out[:, -1, :]           # last timestep hidden state
        return self.head(last)


def _validate_checkpoint(checkpoint: object) -> dict:
    expected_keys = {
        "version", "state_dict", "config", "input_dim", "feature_cols",
        "normalizers", "trained_at", "metrics",
    }
    if not isinstance(checkpoint, dict) or set(checkpoint) != expected_keys:
        raise ValueError("checkpoint schema is invalid")
    if checkpoint["version"] != CHECKPOINT_VERSION:
        raise ValueError("checkpoint version is unsupported")
    input_dim = checkpoint["input_dim"]
    if isinstance(input_dim, bool) or not isinstance(input_dim, int) or input_dim != len(FEATURE_COLS):
        raise ValueError("checkpoint input dimension is invalid")
    if checkpoint["feature_cols"] != FEATURE_COLS:
        raise ValueError("checkpoint feature columns are invalid")
    config = _config_from_dict(checkpoint["config"])
    normalizers = checkpoint["normalizers"]
    if not isinstance(normalizers, dict) or set(normalizers) != {"mean", "std"}:
        raise ValueError("checkpoint normalizers are invalid")
    normalized: dict[str, list[float]] = {}
    for name in ("mean", "std"):
        values = normalizers[name]
        if not isinstance(values, list) or len(values) != input_dim:
            raise ValueError("checkpoint normalizers are invalid")
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) for value in values):
            raise ValueError("checkpoint normalizers are invalid")
        normalized[name] = [float(value) for value in values]
    if any(value == 0 for value in normalized["std"]):
        raise ValueError("checkpoint normalizers are invalid")
    if not isinstance(checkpoint["trained_at"], str) or not 1 <= len(checkpoint["trained_at"]) <= 128:
        raise ValueError("checkpoint metadata is invalid")
    if not isinstance(checkpoint["metrics"], dict) or not _finite_primitive(checkpoint["metrics"]):
        raise ValueError("checkpoint metrics are invalid")
    state_dict = checkpoint["state_dict"]
    if not isinstance(state_dict, dict) or not 1 <= len(state_dict) <= MAX_CHECKPOINT_TENSORS:
        raise ValueError("checkpoint state dictionary is invalid")
    expected_state = PriceLSTM(input_dim, config).state_dict()
    if set(state_dict) != set(expected_state) or not all(isinstance(key, str) for key in state_dict):
        raise ValueError("checkpoint state dictionary is invalid")
    for key, value in state_dict.items():
        expected = expected_state[key]
        if not torch.is_tensor(value) or tuple(value.shape) != tuple(expected.shape) or value.numel() > MAX_CHECKPOINT_TENSOR_ELEMENTS:
            raise ValueError("checkpoint state dictionary is invalid")
        if (torch.is_floating_point(value) or torch.is_complex(value)) and not bool(torch.isfinite(value).all().item()):
            raise ValueError("checkpoint state dictionary is invalid")
    return {
        "state_dict": state_dict,
        "config": config,
        "input_dim": input_dim,
        "feature_cols": list(FEATURE_COLS),
        "normalizers": normalized,
        "trained_at": checkpoint["trained_at"],
        "metrics": checkpoint["metrics"],
    }


def _load_checkpoint(path: Path) -> dict:
    """Load a bounded no-follow tensor-only checkpoint or require retraining."""
    try:
        with open_regular_read(path, max_bytes=MAX_CHECKPOINT_BYTES) as stream:
            checkpoint = torch.load(stream, map_location=DEVICE, weights_only=True)
        return _validate_checkpoint(checkpoint)
    except Exception as exc:
        raise ValueError("unsafe or legacy checkpoint rejected; retrain-required") from exc


def _save_checkpoint(path: Path, checkpoint: dict) -> None:
    """Persist a validated tensor-only checkpoint atomically with private mode."""
    _validate_checkpoint(checkpoint)
    atomic_private_write(path, lambda stream: torch.save(checkpoint, stream))


# ══════════════════════════════════════════════════════════════════════════════
# Trainer
# ══════════════════════════════════════════════════════════════════════════════

def _compute_class_weight(y: torch.Tensor) -> torch.Tensor:
    """pos_weight = n_neg / n_pos for BCEWithLogitsLoss."""
    n_pos = max(int(y.sum()), 1)
    n_neg = max(len(y) - n_pos, 1)
    return torch.tensor([n_neg / n_pos])


def train_model(symbol: str, config: LSTMConfig, max_bars: Optional[int] = None) -> dict:
    """
    Full training pipeline for a symbol.
    Returns metrics dict with keys: accuracy, auc, precision, recall, sharpe_sim, history_path.
    """
    symbol_slug = _base_symbol(symbol)
    path = HISTORICAL_DIR / f"{symbol_slug}_1h.csv"
    if not path.exists():
        raise FileNotFoundError(f"No data for {symbol}: {path}")

    df = pd.read_csv(path, parse_dates=["timestamp"], index_col="timestamp")
    df.sort_index(inplace=True)
    if max_bars:
        df = df.tail(max_bars)

    log.info("[%s] Loaded %d candles (%s → %s)", symbol_slug, len(df),
             str(df.index[0])[:16], str(df.index[-1])[:16])

    # Create full dataset then split chronologically
    full_ds = SequenceDataset(df, config.seq_len, config.horizon, config.threshold)
    train_ds, val_ds, test_ds, test_returns, normalizers = _chronological_split(full_ds, 0.6, 0.2)
    log.info("[%s] Sequences: train=%d val=%d test=%d", symbol_slug,
             len(train_ds), len(val_ds), len(test_ds))

    pos_weight = _compute_class_weight(val_ds.tensors[1])
    log.info("[%s] Class balance — pos_weight: %.2f", symbol_slug, pos_weight.item())

    train_loader = DataLoader(train_ds, batch_size=config.batch_size, shuffle=True,
                               drop_last=False)
    val_loader = DataLoader(val_ds, batch_size=config.batch_size * 2, shuffle=False)

    model = PriceLSTM(len(FEATURE_COLS), config).to(DEVICE)
    criterion = nn.BCEWithLogitsLoss(pos_weight=pos_weight)
    optimizer = torch.optim.AdamW(model.parameters(), lr=config.lr,
                                   weight_decay=config.weight_decay)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
        optimizer, T_max=config.max_epochs)

    best_val_loss = float("inf")
    best_state = None
    patience_left = config.early_stop_patience
    history = {"train_loss": [], "val_loss": [], "lr": []}

    for epoch in range(config.max_epochs):
        model.train()
        train_losses = []
        for X_batch, y_batch in train_loader:
            X_batch, y_batch = X_batch.to(DEVICE), y_batch.to(DEVICE)
            optimizer.zero_grad()
            logits = model(X_batch).squeeze(-1)
            loss = criterion(logits, y_batch)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            train_losses.append(loss.item())

        model.eval()
        val_losses = []
        with torch.no_grad():
            for X_batch, y_batch in val_loader:
                X_batch, y_batch = X_batch.to(DEVICE), y_batch.to(DEVICE)
                logits = model(X_batch).squeeze(-1)
                loss = criterion(logits, y_batch)
                val_losses.append(loss.item())

        avg_train = np.mean(train_losses)
        avg_val = np.mean(val_losses)
        history["train_loss"].append(avg_train)
        history["val_loss"].append(avg_val)
        history["lr"].append(optimizer.param_groups[0]["lr"])

        scheduler.step()

        if avg_val < best_val_loss - 1e-6:
            best_val_loss = avg_val
            best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}
            patience_left = config.early_stop_patience
        else:
            patience_left -= 1

        if (epoch + 1) % 10 == 0 or epoch == 0 or patience_left <= 0:
            log.info("[%s] Epoch %3d | train_loss: %.4f | val_loss: %.4f | lr: %.6f",
                     symbol_slug, epoch + 1, avg_train, avg_val,
                     optimizer.param_groups[0]["lr"])

        if patience_left <= 0:
            log.info("[%s] Early stopping at epoch %d", symbol_slug, epoch + 1)
            break

    if best_state is None:
        best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}

    model.load_state_dict(best_state)

    # ── Evaluate on test set ──
    metrics = _evaluate(model, test_ds, config, test_returns)
    metrics["train_epochs"] = len(history["train_loss"])
    metrics["best_val_loss"] = round(best_val_loss, 6)

    # ── Save model ──
    model_path = MODELS_DIR / f"{symbol_slug.lower()}_lstm.pt"
    _save_checkpoint(model_path, {
        "version": CHECKPOINT_VERSION,
        "state_dict": best_state,
        "config": _config_to_dict(config),
        "input_dim": len(FEATURE_COLS),
        "feature_cols": list(FEATURE_COLS),
        "normalizers": {"mean": normalizers["mean"].tolist(),
                         "std": normalizers["std"].tolist()},
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics,
    })
    log.info("[%s] Model saved → %s", symbol_slug, model_path)

    # ── Save history ──
    history_path = MODELS_DIR / f"{symbol_slug.lower()}_lstm_history.json"
    with open(history_path, "w") as f:
        json.dump(history, f, indent=2)
    metrics["history_path"] = str(history_path)
    metrics["model_path"] = str(model_path)

    return metrics


def _evaluate(model: nn.Module, test_ds, config: LSTMConfig,
              test_returns: Optional[np.ndarray] = None) -> dict:
    """Compute test metrics with full pass over dataset."""
    model.eval()
    loader = DataLoader(test_ds, batch_size=config.batch_size * 2, shuffle=False)
    all_probs = []
    all_y = []

    with torch.no_grad():
        for X_batch, y_batch in loader:
            X_batch = X_batch.to(DEVICE)
            logits = model(X_batch).squeeze(-1).cpu()
            probs = torch.sigmoid(logits)
            all_probs.extend(probs.tolist())
            all_y.extend(y_batch.tolist())

    probs = np.array(all_probs)
    y_true = np.array(all_y)
    y_pred = (probs >= 0.5).astype(int)

    accuracy = float((y_true == y_pred).mean())
    tp = int(((y_pred == 1) & (y_true == 1)).sum())
    fp = int(((y_pred == 1) & (y_true == 0)).sum())
    fn = int(((y_pred == 0) & (y_true == 1)).sum())
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0

    # AUC
    try:
        from sklearn.metrics import roc_auc_score
        auc = float(roc_auc_score(y_true, probs))
    except Exception:
        auc = 0.0

    # Simulated returns using actual horizon returns
    simulated = []
    if test_returns is not None and len(test_returns) == len(probs):
        for i in range(len(probs)):
            ret = float(test_returns[i])
            if probs[i] >= 0.55:
                simulated.append(ret)
            elif probs[i] <= 0.45:
                simulated.append(-ret)

    sharpe_sim = _compute_sharpe(simulated) if simulated else 0.0

    return {
        "accuracy": round(accuracy, 4),
        "auc": round(auc, 4),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "sharpe_simulated": round(sharpe_sim, 4),
        "test_samples": len(probs),
        "class_balance": round(float(y_true.mean()), 4),
    }


def _compute_sharpe(returns: list) -> float:
    if len(returns) < 3:
        return 0.0
    arr = np.array(returns)
    mean = arr.mean()
    std = arr.std(ddof=1)
    return float(mean / std * np.sqrt(8760)) if std > 0 else 0.0


# ══════════════════════════════════════════════════════════════════════════════
# DLPredictor — public interface matching ml_predictor.py pattern
# ══════════════════════════════════════════════════════════════════════════════

class DLPredictor:
    """Loads a trained LSTM model and generates predictions from OHLCV data."""

    def __init__(self, symbol: str):
        self.symbol = _base_symbol(symbol)
        self.model = None
        self.config = None
        self._load()

    def _load(self):
        path = MODELS_DIR / f"{self.symbol.lower()}_lstm.pt"
        if not path.exists():
            raise FileNotFoundError(f"No LSTM model for {self.symbol}: {path}")
        ckpt = _load_checkpoint(path)
        self.config = ckpt["config"]
        self.model = PriceLSTM(ckpt["input_dim"], self.config).to(DEVICE)
        self.model.load_state_dict(ckpt["state_dict"])
        self.model.eval()
        self.normalizers = ckpt.get("normalizers", None)

    def predict(self, symbol: str, df: pd.DataFrame) -> dict:
        """
        Returns {direction: "UP"|"DOWN", probability: float, confidence: str}.
        """
        prob = self.predict_proba(symbol, df)
        if prob >= 0.55:
            direction = "UP"
            confidence = "high" if prob >= 0.75 else "medium"
        elif prob <= 0.45:
            direction = "DOWN"
            confidence = "high" if prob <= 0.25 else "medium"
        else:
            direction = "NEUTRAL"
            confidence = "low"
            prob = 0.5
        return {"direction": direction, "probability": prob, "confidence": confidence}

    def predict_proba(self, symbol: str, df: pd.DataFrame) -> float:
        """Returns P(UP) in [0, 1]."""
        need_bars = self.config.seq_len
        if len(df) < need_bars:
            return 0.5

        feats = _compute_features(df).dropna()
        if len(feats) < need_bars:
            return 0.5

        seq = feats[FEATURE_COLS].iloc[-need_bars:].values.astype(np.float32)

        # Apply global normalization from training
        if self.normalizers is not None:
            seq = (seq - np.array(self.normalizers["mean"])) / np.array(self.normalizers["std"])
        else:
            # Fallback: per-sequence z-score
            mean = seq.mean(axis=0)
            std = seq.std(axis=0) + 1e-8
            seq = (seq - mean) / std

        x = torch.from_numpy(seq).unsqueeze(0).to(DEVICE)  # [1, seq_len, n_features]
        with torch.no_grad():
            logit = self.model(x).squeeze().cpu()
            prob = float(torch.sigmoid(logit).item())
        return prob


# ══════════════════════════════════════════════════════════════════════════════
# Backtest
# ══════════════════════════════════════════════════════════════════════════════

def run_backtest(symbol: str, max_bars: Optional[int] = None) -> dict:
    """Walk-forward backtest: predict each step, accumulate PnL."""
    symbol_slug = _base_symbol(symbol)
    model_path = MODELS_DIR / f"{symbol_slug.lower()}_lstm.pt"
    if not model_path.exists():
        raise FileNotFoundError(f"No model: {model_path}. Train first.")

    path = HISTORICAL_DIR / f"{symbol_slug}_1h.csv"
    df = pd.read_csv(path, parse_dates=["timestamp"], index_col="timestamp")
    df.sort_index(inplace=True)
    if max_bars:
        df = df.tail(max_bars)

    config = _load_checkpoint(model_path)["config"]
    seq_len = config.seq_len
    horizon = config.horizon

    feats = _compute_features(df).dropna()
    need = seq_len + horizon + 1
    if len(feats) < need:
        raise ValueError(f"Need {need} bars, have {len(feats)}")

    predictor = DLPredictor(symbol)
    correct = 0
    total = 0
    returns = []

    for i in range(len(feats) - seq_len - horizon):
        window = df.iloc[i:i + seq_len + horizon]
        prob = predictor.predict_proba(symbol, window.iloc[:seq_len])
        actual_ret = (float(df["close"].iloc[i + seq_len + horizon - 1])
                       / float(df["close"].iloc[i + seq_len - 1]) - 1)

        direction = 1 if prob >= 0.55 else (-1 if prob <= 0.45 else 0)
        if direction == 1 and actual_ret > config.threshold:
            correct += 1
        elif direction == -1 and actual_ret < -config.threshold:
            correct += 1
        elif direction == 0:
            pass  # neutral — skip count
        else:
            pass  # wrong

        if direction != 0:
            total += 1
            returns.append(direction * actual_ret)

        if i > 0 and i % 500 == 0:
            acc = correct / max(total, 1)
            log.info("[%s] Backtest step %d/%d — acc %.3f", symbol_slug, i,
                     len(feats) - seq_len - horizon, acc)

    accuracy = correct / max(total, 1)
    sharpe = _compute_sharpe(returns)
    total_return_pct = float(np.sum(returns)) * 100 if returns else 0.0

    result = {
        "symbol": symbol_slug,
        "total_signals": total,
        "total_steps": len(feats) - seq_len - horizon,
        "accuracy": round(accuracy, 4),
        "sharpe": round(sharpe, 4),
        "total_return_pct": round(total_return_pct, 4),
        "avg_return_per_trade_pct": round(total_return_pct / max(total, 1), 4),
    }
    return result


# ══════════════════════════════════════════════════════════════════════════════
# Assembly integration helper
# ══════════════════════════════════════════════════════════════════════════════

def predict_from_ohlcv(symbol: str, ohlcv_df: pd.DataFrame) -> Optional[dict]:
    """
    Drop-in integration for assembly.py.
    Returns None if no LSTM model exists (caller falls back to LightGBM).
    """
    symbol_slug = _base_symbol(symbol)
    model_path = MODELS_DIR / f"{symbol_slug.lower()}_lstm.pt"
    if not model_path.exists():
        return None
    predictor = DLPredictor(symbol)
    return predictor.predict(symbol, ohlcv_df)


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

SUPPORTED_SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Deep Learning price predictor (LSTM/GRU) for crypto 1h candles",
    )
    parser.add_argument("--train", action="store_true",
                        help="Train a model")
    parser.add_argument("--symbol", type=str, default=None,
                        help="Symbol to train/predict/backtest (e.g. BTC/USDT)")
    parser.add_argument("--all", action="store_true",
                        help="Train all supported symbols")
    parser.add_argument("--predict", action="store_true",
                        help="Generate a live prediction")
    parser.add_argument("--backtest", action="store_true",
                        help="Walk-forward backtest")
    parser.add_argument("--max-bars", type=int, default=None,
                        help="Limit data to last N bars (for quick testing)")
    parser.add_argument("--model-type", type=str, default="lstm",
                        choices=["lstm", "gru"],
                        help="RNN architecture (default: lstm)")
    parser.add_argument("--seq-len", type=int, default=60,
                        help="Sequence length in bars (default: 60)")
    parser.add_argument("--horizon", type=int, default=12,
                        help="Prediction horizon in bars (default: 12)")
    parser.add_argument("--epochs", type=int, default=100,
                        help="Max training epochs (default: 100)")
    args = parser.parse_args()

    config = LSTMConfig(
        seq_len=args.seq_len,
        horizon=args.horizon,
        model_type=args.model_type,
        max_epochs=args.epochs,
    )

    if args.predict:
        symbol = args.symbol or "BTC/USDT"
        try:
            predictor = DLPredictor(symbol)
            symbol_slug = _base_symbol(symbol)
            path = HISTORICAL_DIR / f"{symbol_slug}_1h.csv"
            df = pd.read_csv(path, parse_dates=["timestamp"], index_col="timestamp")
            df.sort_index(inplace=True)
            if args.max_bars:
                df = df.tail(args.max_bars)
            result = predictor.predict(symbol, df)
            print(json.dumps({
                "symbol": symbol_slug,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                **result,
            }, indent=2))
        except FileNotFoundError as e:
            print(f"Error: {e}")
            return
        return

    if args.backtest:
        symbol = args.symbol or "BTC/USDT"
        try:
            result = run_backtest(symbol, args.max_bars)
            print(json.dumps(result, indent=2))
        except FileNotFoundError as e:
            print(f"Error: {e}")
        return

    if args.train:
        symbols = SUPPORTED_SYMBOLS if args.all else [args.symbol or "BTC/USDT"]
        for symbol in symbols:
            symbol_slug = _base_symbol(symbol)
            print(f"\n{'='*60}")
            print(f"Training {symbol_slug} {config.model_type.upper()} predictor...")
            print(f"{'='*60}")
            try:
                metrics = train_model(symbol, config, args.max_bars)
                print(f"\n── {symbol_slug} Results ──")
                print(f"  Epochs:        {metrics['train_epochs']}")
                print(f"  Accuracy:      {metrics['accuracy']:.4f}")
                print(f"  AUC:           {metrics['auc']:.4f}")
                print(f"  Precision:     {metrics['precision']:.4f}")
                print(f"  Recall:        {metrics['recall']:.4f}")
                print(f"  Sharpe (sim):  {metrics['sharpe_simulated']:.4f}")
                print(f"  Test samples:  {metrics['test_samples']}")
                print(f"  Class balance: {metrics['class_balance']:.2%} UP")
                print(f"  Model:         {metrics['model_path']}")
            except Exception as e:
                log.error("[%s] Training failed: %s", symbol_slug, e)
                continue
        print("\nDone.")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
