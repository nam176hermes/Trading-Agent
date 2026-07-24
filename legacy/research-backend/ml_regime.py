"""
ml_regime.py
------------
Unsupervised ML regime detection using PCA + K-means clustering on 19-asset returns.

Public API:
    train_regime_model(lookback_days=365)  → train & save PCA + KMeans pipeline
    detect_current_regime()                → classify current market into a regime

Standalone:  python ml_regime.py
Integration: from ml_regime import detect_current_regime
"""

import json
import logging
import math
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler
from local_artifacts import UnsafeLocalArtifactError, atomic_private_write, read_utf8_text
from runtime_paths import data_root

log = logging.getLogger("ml_regime")

# ── Asset universe ───────────────────────────────────────────────────────────
CRYPTO = ["BTC", "ETH", "SOL", "TON", "DOGE", "ADA", "AVAX", "DOT", "LINK", "POL"]
STOCKS = ["AAPL", "NVDA", "MSFT", "GOOGL", "AMZN", "META", "TSLA"]
ETFS   = ["SPY", "QQQ"]
ALL_ASSETS = CRYPTO + STOCKS + ETFS  # 19 total

# ── Paths ────────────────────────────────────────────────────────────────────
MODEL_DIR = data_root() / "memory" / "ml_regime"
REGIME_ARTIFACT_VERSION = 1
MAX_MODEL_ARTIFACT_BYTES = 2 * 1024 * 1024
MAX_MODEL_FEATURES = 256
MAX_MODEL_COMPONENTS = 64
MAX_MODEL_CLUSTERS = 32

# ── Binance mapping (mirrors data_collector.py) ──────────────────────────────
BINANCE_SYMBOL_MAP = {
    "BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT",
    "TON": "TONUSDT", "DOGE": "DOGEUSDT", "ADA": "ADAUSDT",
    "AVAX": "AVAXUSDT", "DOT": "DOTUSDT", "LINK": "LINKUSDT",
    "POL": "POLUSDT",
}

# ── How clusters map to existing regime strings ──────────────────────────────
REGIME_LABEL_MAP = {
    "RISK_OFF":          "trending_down",
    "RISK_ON_MOMENTUM":  "trending_up",
    "LOW_VOL_RANGING":   "choppy",
    "BEARISH_DRIFT":     "trending_down",
}

# ── Cache for in-process detection (avoid re-fetching per symbol) ────────────
_cache: Optional[dict] = None
_cache_ts: float = 0.0
_CACHE_TTL_SEC = 300  # 5 min


# ═══════════════════════════════════════════════════════════════════════════════
# Data fetching
# ═══════════════════════════════════════════════════════════════════════════════

def _fetch_binance_klines(symbol: str, limit: int = 365) -> pd.Series:
    """Fetch daily OHLCV from Binance, return close price Series. Empty on failure."""
    pair = BINANCE_SYMBOL_MAP.get(symbol)
    if not pair:
        log.warning("No Binance pair for %s", symbol)
        return pd.Series(dtype=float)

    url = f"https://api.binance.com/api/v3/klines?symbol={pair}&interval=1d&limit={limit}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        log.warning("Binance fetch failed for %s: %s", symbol, e)
        return pd.Series(dtype=float)

    if not data:
        return pd.Series(dtype=float)

    records = []
    for k in data:
        ts = datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc)
        records.append({"date": ts, "close": float(k[4])})

    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"]).dt.tz_convert(None).dt.normalize()
    df = df.set_index("date").sort_index()
    return df["close"]


def _fetch_yfinance_close(symbol: str, days: int = 365) -> pd.Series:
    """Fetch daily close from yfinance. Empty on failure."""
    try:
        import yfinance as yf
    except ImportError:
        log.warning("yfinance not installed — skipping %s", symbol)
        return pd.Series(dtype=float)

    try:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days + 5)
        ticker = yf.Ticker(symbol)
        df = ticker.history(start=start.strftime("%Y-%m-%d"),
                            end=end.strftime("%Y-%m-%d"))
        if df.empty:
            log.warning("yfinance returned empty history for %s", symbol)
            return pd.Series(dtype=float)
        s = df["Close"].rename(symbol)
        s.index = s.index.tz_convert(None).normalize()
        return s
    except Exception as e:
        log.warning("yfinance fetch failed for %s: %s", symbol, e)
        return pd.Series(dtype=float)


def _fetch_all_returns(lookback_days: int = 365) -> pd.DataFrame:
    """
    Fetch daily returns for all 19 assets.

    Returns: DataFrame (date index, asset columns) of daily returns.
    Missing assets are excluded (columns dropped).
    """
    closes = {}

    # Crypto via Binance
    for sym in CRYPTO:
        s = _fetch_binance_klines(sym, limit=lookback_days + 5)
        if len(s) > 0:
            closes[sym] = s
        time.sleep(0.15)  # polite to Binance rate limits

    # Stocks + ETFs via yfinance
    for sym in STOCKS + ETFS:
        s = _fetch_yfinance_close(sym, days=lookback_days + 5)
        if len(s) > 0:
            closes[sym] = s

    if not closes:
        raise RuntimeError("No asset data fetched — cannot build regime model.")

    prices = pd.DataFrame(closes).sort_index()

    # Keep only rows where most assets have data (handles delisted assets
    # like MATIC whose historical dates would stretch the union index).
    min_assets_per_row = max(2, int(len(prices.columns) * 0.7))
    prices = prices.dropna(thresh=min_assets_per_row)

    returns = prices.pct_change().dropna(how="all")

    # Drop columns with < 200 valid returns (insufficient for training)
    min_days = min(200, len(returns) // 3)
    valid = returns.dropna(axis=1, thresh=min_days)
    log.info("Fetched returns for %d/%d assets: %s", valid.shape[1], len(ALL_ASSETS),
             list(valid.columns))
    return valid


# ═══════════════════════════════════════════════════════════════════════════════
# Feature engineering
# ═══════════════════════════════════════════════════════════════════════════════

def _build_feature_matrix(returns: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    """
    Build rolling feature matrix from daily returns.

    Features per day:
      - Mean return per asset (N features)
      - Volatility per asset (N features)
      - Cross-sectional dispersion: std of returns across assets (1 feature)
      - Top-3 eigenvalues of rolling correlation matrix (3 features)

    Returns: DataFrame (date index, feature columns).
    """
    n = returns.shape[1]

    roll_mean = returns.rolling(window).mean()
    roll_vol  = returns.rolling(window).std()
    cross_disp = returns.std(axis=1)  # cross-sectional dispersion per day

    features = {}
    for col in returns.columns:
        features[f"mean_{col}"] = roll_mean[col]
        features[f"vol_{col}"] = roll_vol[col]
    features["cross_sectional_disp"] = cross_disp

    # Rolling correlation eigenvalues (top 3)
    eig1, eig2, eig3 = [], [], []
    index_dates = []
    for i in range(window - 1, len(returns)):
        window_returns = returns.iloc[i - window + 1 : i + 1]
        corr = window_returns.corr()
        eigvals = np.linalg.eigvalsh(corr.values)
        top3 = sorted(eigvals, reverse=True)[:3]
        eig1.append(top3[0] if len(top3) > 0 else np.nan)
        eig2.append(top3[1] if len(top3) > 1 else np.nan)
        eig3.append(top3[2] if len(top3) > 2 else np.nan)
        index_dates.append(returns.index[i])

    eig_df = pd.DataFrame(
        {"eig1": eig1, "eig2": eig2, "eig3": eig3},
        index=index_dates,
    )

    feature_df = pd.DataFrame(features, index=returns.index)
    feature_df = feature_df.join(eig_df)
    feature_df = feature_df.dropna()

    log.info("Feature matrix: %d rows x %d cols (window=%d, assets=%d)",
             feature_df.shape[0], feature_df.shape[1], window, n)
    return feature_df


# ═══════════════════════════════════════════════════════════════════════════════
# PCA + KMeans pipeline
# ═══════════════════════════════════════════════════════════════════════════════

def _train_pipeline(
    features: pd.DataFrame,
    n_components: int = 3,
    n_clusters: int = 4,
) -> dict:
    """
    Train StandardScaler → PCA → KMeans pipeline.

    Returns dict with trained models, transformed data, and diagnostics.
    """
    X = features.values
    dates = features.index

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # PCA — find components needed for >= 80% variance, capped at n_components max
    pca_full = PCA().fit(X_scaled)
    cum_var = np.cumsum(pca_full.explained_variance_ratio_)
    k80 = int(np.searchsorted(cum_var, 0.80) + 1)
    k = min(k80, n_components, X_scaled.shape[1])
    actual_variance = cum_var[k - 1]
    log.info("PCA %d components explain %.1f%% variance (80%% needs %d)",
             k, actual_variance * 100, k80)

    pca = PCA(n_components=k)
    X_pca = pca.fit_transform(X_scaled)

    # KMeans
    kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
    labels = kmeans.fit_predict(X_pca)

    # Silhouette score (requires >= 2 clusters and >= 2 samples per cluster)
    try:
        if n_clusters >= 2 and all((labels == i).sum() >= 2 for i in range(n_clusters)):
            sil = silhouette_score(X_pca, labels, random_state=42)
            log.info("Silhouette score (k=%d): %.3f", n_clusters, sil)
        else:
            sil = None
    except Exception:
        sil = None

    # Distance-based soft assignment for confidence scoring
    from scipy.spatial.distance import cdist
    distances = cdist(X_pca, kmeans.cluster_centers_)
    # softmax over negative distances
    exp_neg_dist = np.exp(-distances)
    soft_probs = exp_neg_dist / exp_neg_dist.sum(axis=1, keepdims=True)

    return {
        "scaler": scaler,
        "pca": pca,
        "kmeans": kmeans,
        "n_components": k,
        "explained_variance": actual_variance,
        "silhouette_score": sil,
        "X_pca": X_pca,
        "labels": labels,
        "dates": dates,
        "soft_probs": soft_probs,
        "feature_names": list(features.columns),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Regime characterization
# ═══════════════════════════════════════════════════════════════════════════════

def _characterize_regimes(
    returns: pd.DataFrame,
    labels: np.ndarray,
    dates: pd.DatetimeIndex,
    kmeans: KMeans,
) -> list[dict]:
    """
    Compute per-cluster characteristics and assign human-readable labels.

    Labeling logic:
      - High vol + high corr + negative return → RISK_OFF
      - High vol + high corr + positive return → RISK_ON_MOMENTUM
      - Low vol + low corr → LOW_VOL_RANGING
      - Low vol + negative return → BEARISH_DRIFT
    """
    n_clusters = kmeans.n_clusters
    # Align returns with the feature dates (features drop the first window-1 rows)
    aligned_returns = returns.loc[dates]

    clusters = []
    for cid in range(n_clusters):
        mask = labels == cid
        if mask.sum() == 0:
            continue

        cluster_returns = aligned_returns.values[mask]
        avg_return = float(np.mean(cluster_returns))
        avg_vol = float(np.mean(np.std(cluster_returns, axis=1)))

        flat_corr = np.corrcoef(cluster_returns.T)
        n_assets = flat_corr.shape[0]
        upper_tri = flat_corr[np.triu_indices(n_assets, k=1)]
        avg_corr = float(np.mean(upper_tri))

        clusters.append({
            "cluster_id": int(cid),
            "count": int(mask.sum()),
            "avg_return": round(avg_return, 6),
            "avg_volatility": round(avg_vol, 6),
            "avg_correlation": round(avg_corr, 4),
        })

    # Thresholds for classification
    vols = [c["avg_volatility"] for c in clusters]
    corrs = [c["avg_correlation"] for c in clusters]
    median_vol = np.median(vols)
    median_corr = np.median(corrs)

    for c in clusters:
        high_vol = c["avg_volatility"] >= median_vol
        high_corr = c["avg_correlation"] >= median_corr
        neg_return = c["avg_return"] < 0

        if high_vol and high_corr:
            c["label"] = "RISK_OFF" if neg_return else "RISK_ON_MOMENTUM"
        elif high_vol and not high_corr:
            c["label"] = "BEARISH_DRIFT" if neg_return else "LOW_VOL_RANGING"
        elif not high_vol:
            c["label"] = "BEARISH_DRIFT" if neg_return else "LOW_VOL_RANGING"
        else:
            c["label"] = "LOW_VOL_RANGING"

        c["inherited_regime"] = REGIME_LABEL_MAP.get(c["label"], "unclear")

    for c in clusters:
        log.info("Cluster %d — label=%s avg_ret=%.4f%% avg_vol=%.4f avg_corr=%.3f (%d days)",
                 c["cluster_id"], c["label"],
                 c["avg_return"] * 100, c["avg_volatility"], c["avg_correlation"], c["count"])

    return clusters


# ═══════════════════════════════════════════════════════════════════════════════
# Model persistence
# ═══════════════════════════════════════════════════════════════════════════════

def _save_model(pipeline: dict, regimes: list[dict], meta: dict) -> Path:
    """Persist inference-only numeric parameters as a versioned JSON artifact."""
    scaler = pipeline["scaler"]
    pca = pipeline["pca"]
    kmeans = pipeline["kmeans"]
    artifact = {
        "version": REGIME_ARTIFACT_VERSION,
        "scaler": {"mean": np.asarray(scaler.mean_, dtype=float).tolist(), "scale": np.asarray(scaler.scale_, dtype=float).tolist()},
        "pca": {"mean": np.asarray(pca.mean_, dtype=float).tolist(), "components": np.asarray(pca.components_, dtype=float).tolist()},
        "kmeans": {"centers": np.asarray(kmeans.cluster_centers_, dtype=float).tolist()},
        "feature_names": list(pipeline["feature_names"]),
        "metrics": {
            "n_components": int(pipeline["n_components"]),
            "explained_variance": float(pipeline["explained_variance"]),
            "silhouette_score": None if pipeline["silhouette_score"] is None else float(pipeline["silhouette_score"]),
        },
        "regimes": regimes,
        "meta": meta,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    model = _validate_artifact(artifact)
    if model is None:
        raise ValueError("refusing to save an invalid regime artifact")
    model_path = MODEL_DIR / "pipeline.json"

    def _write(stream) -> None:
        stream.write(json.dumps(artifact, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8"))

    atomic_private_write(model_path, _write)
    log.info("Model saved → %s (%d regimes)", model_path, len(regimes))
    return model_path


def _load_model() -> Optional[dict]:
    """Load only a bounded, no-follow JSON inference artifact."""
    model_path = MODEL_DIR / "pipeline.json"
    try:
        raw = read_utf8_text(model_path, max_bytes=MAX_MODEL_ARTIFACT_BYTES)
        artifact = json.loads(raw, object_pairs_hook=_strict_json_object)
    except (OSError, UnsafeLocalArtifactError, UnicodeError, json.JSONDecodeError, RecursionError, TypeError, ValueError):
        return None
    return _validate_artifact(artifact)


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict:
    result: dict[str, object] = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in result:
            raise ValueError("JSON object keys must be unique strings")
        result[key] = value
    return result


def _finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _finite_vector(value: object, expected: int | None = None) -> np.ndarray | None:
    if not isinstance(value, list) or (expected is not None and len(value) != expected):
        return None
    if not value or len(value) > MAX_MODEL_FEATURES or not all(_finite_number(item) for item in value):
        return None
    return np.asarray(value, dtype=float)


def _safe_primitive(value: object, depth: int = 0) -> bool:
    if depth > 8:
        return False
    if value is None or isinstance(value, bool):
        return True
    if _finite_number(value):
        return True
    if isinstance(value, str):
        return len(value) <= 1024
    if isinstance(value, list):
        return len(value) <= MAX_MODEL_FEATURES and all(_safe_primitive(item, depth + 1) for item in value)
    if isinstance(value, dict):
        return len(value) <= MAX_MODEL_FEATURES and all(isinstance(key, str) and len(key) <= 128 and _safe_primitive(item, depth + 1) for key, item in value.items())
    return False


def _validate_artifact(artifact: object) -> Optional[dict]:
    """Validate exact artifact shape before exposing numeric arrays to inference."""
    expected_keys = {"version", "scaler", "pca", "kmeans", "feature_names", "metrics", "regimes", "meta", "saved_at"}
    if not isinstance(artifact, dict) or set(artifact) != expected_keys:
        return None
    if type(artifact["version"]) is not int or artifact["version"] != REGIME_ARTIFACT_VERSION or not isinstance(artifact["saved_at"], str) or len(artifact["saved_at"]) > 128:
        return None
    if not isinstance(artifact["scaler"], dict) or set(artifact["scaler"]) != {"mean", "scale"}:
        return None
    mean = _finite_vector(artifact["scaler"]["mean"])
    if mean is None:
        return None
    scale = _finite_vector(artifact["scaler"]["scale"], len(mean))
    if scale is None or np.any(scale <= 0):
        return None
    feature_names = artifact["feature_names"]
    if not isinstance(feature_names, list) or len(feature_names) != len(mean) or len(feature_names) > MAX_MODEL_FEATURES:
        return None
    if len(set(feature_names)) != len(feature_names) or not all(isinstance(name, str) and 1 <= len(name) <= 128 for name in feature_names):
        return None
    if not isinstance(artifact["pca"], dict) or set(artifact["pca"]) != {"mean", "components"}:
        return None
    pca_mean = _finite_vector(artifact["pca"]["mean"], len(mean))
    components = artifact["pca"]["components"]
    if pca_mean is None or not isinstance(components, list) or not 1 <= len(components) <= MAX_MODEL_COMPONENTS:
        return None
    component_rows = [_finite_vector(row, len(mean)) for row in components]
    if any(row is None for row in component_rows):
        return None
    component_array = np.vstack(component_rows)
    if not isinstance(artifact["kmeans"], dict) or set(artifact["kmeans"]) != {"centers"}:
        return None
    centers = artifact["kmeans"]["centers"]
    if not isinstance(centers, list) or not 1 <= len(centers) <= MAX_MODEL_CLUSTERS:
        return None
    center_rows = [_finite_vector(row, component_array.shape[0]) for row in centers]
    if any(row is None for row in center_rows):
        return None
    if not isinstance(artifact["metrics"], dict) or set(artifact["metrics"]) != {"n_components", "explained_variance", "silhouette_score"}:
        return None
    metrics = artifact["metrics"]
    if type(metrics["n_components"]) is not int or metrics["n_components"] != component_array.shape[0] or not _finite_number(metrics["explained_variance"]):
        return None
    if not 0 <= float(metrics["explained_variance"]) <= 1:
        return None
    if metrics["silhouette_score"] is not None and not _finite_number(metrics["silhouette_score"]):
        return None
    if not isinstance(artifact["regimes"], list) or len(artifact["regimes"]) != len(center_rows) or not _safe_primitive(artifact["regimes"]):
        return None
    if not isinstance(artifact["meta"], dict) or not _safe_primitive(artifact["meta"]):
        return None
    for index, regime in enumerate(artifact["regimes"]):
        if not isinstance(regime, dict) or regime.get("cluster_id") != index:
            return None
        if not all(isinstance(regime.get(key), str) for key in ("label", "inherited_regime")):
            return None
        if not all(_finite_number(regime.get(key)) for key in ("avg_return", "avg_volatility", "avg_correlation")):
            return None
    return {
        "scaler_mean": mean,
        "scaler_scale": scale,
        "pca_mean": pca_mean,
        "pca_components": component_array,
        "kmeans_centers": np.vstack(center_rows),
        "feature_names": feature_names,
        "n_components": metrics["n_components"],
        "explained_variance": float(metrics["explained_variance"]),
        "silhouette_score": metrics["silhouette_score"],
        "regimes": artifact["regimes"],
        "meta": artifact["meta"],
        "saved_at": artifact["saved_at"],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════════

def train_regime_model(lookback_days: int = 365) -> dict:
    """
    Train PCA + KMeans regime detection model on all 19 assets.

    Args:
        lookback_days: days of historical data to use (default 365)

    Returns:
        dict with cluster_centers, labels, regime_stats, and training metadata.
    """
    log.info("── Training ML regime model (lookback=%d days) ──", lookback_days)

    returns = _fetch_all_returns(lookback_days)
    if len(returns.columns) < 10:
        raise RuntimeError(f"Insufficient asset data: {len(returns.columns)} assets. Need ≥10.")

    features = _build_feature_matrix(returns)
    pipeline = _train_pipeline(features)

    regimes = _characterize_regimes(
        returns, pipeline["labels"], pipeline["dates"], pipeline["kmeans"]
    )

    meta = {
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "n_assets": returns.shape[1],
        "assets_used": list(returns.columns),
        "lookback_days": lookback_days,
        "feature_rows": features.shape[0],
        "n_components": pipeline["n_components"],
        "explained_variance": round(float(pipeline["explained_variance"]), 4),
        "silhouette_score": round(float(pipeline["silhouette_score"]), 4)
        if pipeline["silhouette_score"] is not None else None,
    }

    _save_model(pipeline, regimes, meta)

    return {
        "regimes": regimes,
        "meta": meta,
        "cluster_centers": pipeline["kmeans"].cluster_centers_.tolist(),
        "labels": pipeline["labels"].tolist(),
    }


def detect_current_regime() -> Optional[dict]:
    """
    Classify the current market into a trained regime.

    Fetches recent returns, computes the feature vector, projects it through
    the saved PCA+KMeans pipeline, and returns the assigned regime.

    Returns None if model not trained or data unavailable.

    Output format:
        {
            "regime_id": 2,
            "regime_label": "RISK_OFF",
            "inherited_regime": "trending_down",
            "confidence": 0.82,
            "characteristics": {
                "avg_daily_return": -0.0012,
                "avg_volatility": 0.032,
                "avg_correlation": 0.71,
            },
            "trained_at": "2026-05-19T...",
            "n_assets": 19,
            "lookback_days": 365,
        }
    """
    global _cache, _cache_ts

    # Check cache
    now = time.time()
    if _cache is not None and (now - _cache_ts) < _CACHE_TTL_SEC:
        return _cache

    model = _load_model()
    if model is None:
        log.info("ML regime model not trained yet — run train_regime_model() first")
        return None

    # Fetch recent data (need 21 days for 20-day window)
    try:
        returns = _fetch_all_returns(lookback_days=25)
    except RuntimeError as e:
        log.warning("Cannot detect regime — data fetch failed: %s", e)
        return None

    if len(returns.columns) < 10:
        log.warning("Cannot detect regime — insufficient assets: %d", len(returns.columns))
        return None

    # Build feature vector for the most recent day
    window = 20
    if len(returns) < window:
        log.warning("Not enough data for feature window: %d rows", len(returns))
        return None

    recent_returns = returns.iloc[-window:]
    feature_names = model["feature_names"]
    asset_cols_in_model = [c for c in feature_names if c.startswith("mean_")]

    # Compute features for the current day
    features = {}
    for col in returns.columns:
        col_mean_key = f"mean_{col}"
        col_vol_key = f"vol_{col}"
        if col_mean_key in feature_names:
            features[col_mean_key] = recent_returns[col].mean()
        if col_vol_key in feature_names:
            features[col_vol_key] = recent_returns[col].std()

    features["cross_sectional_disp"] = returns.iloc[-1].std()

    # Correlation eigenvalues
    corr = recent_returns.corr()
    eigvals = np.linalg.eigvalsh(corr.values)
    top3 = sorted(eigvals, reverse=True)[:3]
    features["eig1"] = top3[0] if len(top3) > 0 else 0
    features["eig2"] = top3[1] if len(top3) > 1 else 0
    features["eig3"] = top3[2] if len(top3) > 2 else 0

    # Build feature vector in the same order as training
    X_new = np.array([[features.get(f, 0.0) for f in feature_names]])

    # Transform through the validated numeric artifact without object deserialization.
    X_scaled = (X_new - model["scaler_mean"]) / model["scaler_scale"]
    X_pca = (X_scaled - model["pca_mean"]) @ model["pca_components"].T

    # Predict cluster from Euclidean distances to validated centers.
    distances = np.linalg.norm(model["kmeans_centers"] - X_pca[0], axis=1)
    cluster_id = int(np.argmin(distances))

    # Confidence: distance-based softmax probability
    exp_neg_dist = np.exp(-distances)
    probs = exp_neg_dist / exp_neg_dist.sum()
    confidence = float(probs[cluster_id])

    # Match regime info
    regime_info = next(
        (r for r in model["regimes"] if r["cluster_id"] == cluster_id),
        model["regimes"][cluster_id] if cluster_id < len(model["regimes"]) else None,
    )

    if regime_info is None:
        return None

    result = {
        "regime_id": cluster_id,
        "regime_label": regime_info["label"],
        "inherited_regime": regime_info["inherited_regime"],
        "confidence": round(confidence, 4),
        "characteristics": {
            "avg_daily_return": regime_info["avg_return"],
            "avg_volatility": regime_info["avg_volatility"],
            "avg_correlation": regime_info["avg_correlation"],
        },
        "trained_at": model.get("saved_at", ""),
        "n_assets": model.get("meta", {}).get("n_assets", 0),
        "lookback_days": model.get("meta", {}).get("lookback_days", 0),
    }

    _cache = result
    _cache_ts = now
    log.info("ML regime → %s (id=%d conf=%.2f)", result["regime_label"], cluster_id, confidence)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# Standalone
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=" * 60)
    print("Training ML regime model (PCA + KMeans on 19 assets)...")
    print("=" * 60)

    result = train_regime_model(lookback_days=365)

    print("\n── Regime characteristics ──")
    for r in result["regimes"]:
        print(f"  Cluster {r['cluster_id']}: {r['label']:20s} "
              f"return={r['avg_return']:+.4%}  vol={r['avg_volatility']:.4f}  "
              f"corr={r['avg_correlation']:.3f}  count={r['count']}")

    print(f"\n── Metadata ──")
    for k, v in result["meta"].items():
        print(f"  {k}: {v}")

    print("\n── Testing detect_current_regime() ──")
    regime = detect_current_regime()
    if regime:
        print(f"  Current: {regime['regime_label']} (conf={regime['confidence']:.2f})")
        print(f"  Characteristics: {regime['characteristics']}")
    else:
        print("  FAILED — could not detect regime")
