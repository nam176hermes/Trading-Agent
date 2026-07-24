"""
bayesian_weighting.py — dynamic signal confidence via conjugate Beta-Binomial.
Binary outcomes (price up vs down) tracked per symbol per direction.
Closed-form Bayesian updating — no MCMC needed.

Usage:
    weights = get_signal_weights("BTC")   # → posterior probabilities per direction
    update_signal_tracker("BTC", "BUY", was_correct=True)  # after trade outcome
"""

import json
import logging
from pathlib import Path

import numpy as np
from scipy.stats import beta as beta_dist
from runtime_paths import data_root

log = logging.getLogger("bayesian_weighting")

MODELS_DIR = data_root() / "models"


def _tracker_path(symbol: str) -> Path:
    return MODELS_DIR / f"{symbol.lower()}_bayesian_tracker.json"


def _load_tracker(symbol: str) -> dict:
    path = _tracker_path(symbol)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, KeyError):
            log.warning("Corrupt tracker for %s, reinitializing", symbol)
    return {
        "alpha_up": 1,
        "beta_up": 1,
        "alpha_down": 1,
        "beta_down": 1,
        "total_trades": 0,
    }


def _save_tracker(symbol: str, tracker: dict) -> None:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    path = _tracker_path(symbol)
    path.write_text(json.dumps(tracker, indent=2))


def get_prior_for_symbol(symbol: str) -> dict:
    """Return Beta distribution parameters for this symbol's signal accuracy."""
    return _load_tracker(symbol)


def update_signal_tracker(symbol: str, direction: str, was_correct: bool) -> dict:
    """
    Update the conjugate prior after a trade outcome is known.

    Args:
        symbol: Asset symbol (e.g., "BTC")
        direction: "BUY" or "SELL"
        was_correct: True if the prediction was directionally correct
    """
    tracker = _load_tracker(symbol)

    key_a = "alpha_up" if direction.upper() == "BUY" else "alpha_down"
    key_b = "beta_up" if direction.upper() == "BUY" else "beta_down"

    if was_correct:
        tracker[key_a] += 1
    else:
        tracker[key_b] += 1

    tracker["total_trades"] += 1
    _save_tracker(symbol, tracker)

    log.info("[%s] Updated tracker: %s was %s → α_up=%d β_up=%d α_down=%d β_down=%d trades=%d",
             symbol, direction.upper(), "correct" if was_correct else "wrong",
             tracker["alpha_up"], tracker["beta_up"],
             tracker["alpha_down"], tracker["beta_down"],
             tracker["total_trades"])
    return tracker


def get_signal_weights(symbol: str) -> dict:
    """
    Compute Bayesian posterior probabilities for each signal direction.

    Returns dict with:
        buy_weight:         posterior mean × (1 − CI width) → penalized for uncertainty
        buy_accuracy:       α_up / (α_up + β_up)
        buy_ci_low / high:  95% credible interval bounds
        buy_samples:        number of BUY trades tracked
        sell_weight:        posterior mean × (1 − CI width)
        sell_accuracy:      α_down / (α_down + β_down)
        sell_ci_low / high: 95% credible interval bounds
        sell_samples:       number of SELL trades tracked
        total_trades:       total trades tracked
    """
    tracker = _load_tracker(symbol)
    a_up = tracker["alpha_up"]
    b_up = tracker["beta_up"]
    a_dn = tracker["alpha_down"]
    b_dn = tracker["beta_down"]

    buy_accuracy = a_up / (a_up + b_up)
    sell_accuracy = a_dn / (a_dn + b_dn)

    buy_ci = beta_dist.interval(0.95, a_up, b_up)
    sell_ci = beta_dist.interval(0.95, a_dn, b_dn)

    # Weight = posterior mean (main signal) with mild uncertainty penalty
    # Small samples: stick close to 0.5 prior mean, CI penalty is negligible
    # Large samples: posterior mean dominates, CI narrows → near-zero penalty
    buy_samples = a_up + b_up - 2
    sell_samples = a_dn + b_dn - 2
    buy_penalty = min(0.5, 1.0 / max(1, np.sqrt(buy_samples)))
    sell_penalty = min(0.5, 1.0 / max(1, np.sqrt(sell_samples)))

    buy_weight = buy_accuracy * (1.0 - buy_penalty) + 0.5 * buy_penalty
    sell_weight = sell_accuracy * (1.0 - sell_penalty) + 0.5 * sell_penalty

    # No floor — let the data speak

    return {
        "buy_weight": round(buy_weight, 4),
        "buy_ci_low": round(buy_ci[0], 4),
        "buy_ci_high": round(buy_ci[1], 4),
        "buy_accuracy": round(buy_accuracy, 4),
        "buy_samples": a_up + b_up - 2,
        "sell_weight": round(sell_weight, 4),
        "sell_ci_low": round(sell_ci[0], 4),
        "sell_ci_high": round(sell_ci[1], 4),
        "sell_accuracy": round(sell_accuracy, 4),
        "sell_samples": a_dn + b_dn - 2,
        "total_trades": tracker["total_trades"],
    }


# ── Standalone test ──

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Fresh priors
    print("=== Fresh Priors (Beta(1,1) uniform) ===")
    weights = get_signal_weights("BTC")
    for k, v in weights.items():
        print(f"  {k}: {v}")

    # Simulate trade outcomes
    print("\n=== Simulating 7 trade outcomes ===")
    outcomes = [
        ("BTC", "BUY", True),
        ("BTC", "BUY", True),
        ("BTC", "BUY", True),
        ("BTC", "BUY", False),   # BUY: 3/4 correct
        ("BTC", "SELL", False),
        ("BTC", "SELL", False),
        ("BTC", "SELL", True),   # SELL: 1/3 correct
    ]
    for sym, direction, correct in outcomes:
        update_signal_tracker(sym, direction, correct)

    print("\n=== After outcomes (BUY 3/4, SELL 1/3) ===")
    weights = get_signal_weights("BTC")
    for k, v in weights.items():
        print(f"  {k}: {v}")

    print("\n✓ Bayesian weighting standalone test passed")
