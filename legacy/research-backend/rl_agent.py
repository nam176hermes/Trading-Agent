"""
rl_agent.py
Tabular Q-learning trading agent using numpy (no torch/tensorflow required).

State: discretized (RSI bucket, MACD_dir, SMA_dir, vol_ratio bucket, sentiment bucket)
Action space: {0: HOLD, 1: BUY, 2: SELL}
Reward: realized equity delta between steps (from equity curve or position PnL estimate)

Training runs offline against cached signal history in memory/decisions.jsonl.
Inference: get_action(signal_dict) returns {"action": "BUY"|"SELL"|"HOLD", "q_values": [...]}

Q-table is persisted to memory/rl_qtable.npy + memory/rl_state.json between runs.
"""

import json
import logging
import math
import random
from pathlib import Path
from typing import Optional

import numpy as np
from runtime_paths import data_root

log = logging.getLogger("rl_agent")

# ── Paths ─────────────────────────────────────────────────────────────────────
MEMORY_DIR  = data_root() / "memory"
QTABLE_PATH = MEMORY_DIR / "rl_qtable.npy"
STATE_PATH  = MEMORY_DIR / "rl_state.json"
DECISIONS_PATH = MEMORY_DIR / "decisions.jsonl"
EQUITY_PATH = MEMORY_DIR / "paper" / "equity_curve.jsonl"

# ── Hyperparameters ───────────────────────────────────────────────────────────
ALPHA      = 0.10    # learning rate
GAMMA      = 0.95    # discount factor
EPSILON    = 0.15    # exploration rate (decays during training)
EPSILON_MIN = 0.02
EPSILON_DECAY = 0.995

N_ACTIONS = 3        # HOLD=0, BUY=1, SELL=2

# State discretization bins
RSI_BINS      = [30, 45, 55, 70]          # 5 buckets: <30, 30-45, 45-55, 55-70, >70
VOL_BINS      = [0.7, 0.9, 1.1, 1.5]     # 5 buckets around 1.0
SENTIMENT_BINS = [-0.3, -0.1, 0.1, 0.3]  # 5 buckets
# MACD_dir: {-1, 0, 1} → 3 values
# SMA_dir:  {-1, 0, 1} → 3 values (price vs SMA200)

# State space size: 5 × 5 × 5 × 3 × 3 = 675
N_RSI        = len(RSI_BINS) + 1
N_VOL        = len(VOL_BINS) + 1
N_SENTIMENT  = len(SENTIMENT_BINS) + 1
N_MACD       = 3
N_SMA        = 3
STATE_SPACE  = N_RSI * N_VOL * N_SENTIMENT * N_MACD * N_SMA


def _digitize(value: float, bins: list[float]) -> int:
    """Return bin index (0 = below first bin, len(bins) = above last)."""
    for i, b in enumerate(bins):
        if value < b:
            return i
    return len(bins)


def _direction(value: Optional[float]) -> int:
    """Convert float to {0: negative, 1: neutral, 2: positive}."""
    if value is None:
        return 1
    if value > 0.01:
        return 2
    if value < -0.01:
        return 0
    return 1


def _signal_to_state(signal: dict) -> int:
    """
    Convert a signal dict (from assembly.py output or decisions.jsonl) to a
    flat integer state index.

    Expected keys (all optional — missing → neutral/mid):
      rsi_14, macd_histogram (or macd_signal str), price_vs_sma200,
      volume_trend_ratio, sentiment_score
    """
    rsi       = float(signal.get("rsi_14") or 50.0)
    macd_hist = signal.get("macd_histogram")
    if macd_hist is None:
        # Fall back to macd_signal string
        ms = (signal.get("macd_signal") or "").lower()
        macd_hist = 1.0 if "bull" in ms else (-1.0 if "bear" in ms else 0.0)
    sma_val   = signal.get("price_vs_sma200")
    vol_ratio = float(signal.get("volume_trend_ratio") or 1.0)
    sentiment = float(signal.get("sentiment_score") or 0.0)

    i_rsi  = _digitize(rsi, RSI_BINS)
    i_vol  = _digitize(vol_ratio, VOL_BINS)
    i_sent = _digitize(sentiment, SENTIMENT_BINS)
    i_macd = _direction(float(macd_hist) if macd_hist is not None else None)
    i_sma  = _direction(float(sma_val) if sma_val is not None else None)

    state = (
        i_rsi  * (N_VOL * N_SENTIMENT * N_MACD * N_SMA) +
        i_vol  * (N_SENTIMENT * N_MACD * N_SMA) +
        i_sent * (N_MACD * N_SMA) +
        i_macd * N_SMA +
        i_sma
    )
    return state


def _load_qtable() -> np.ndarray:
    if QTABLE_PATH.exists():
        try:
            return np.load(str(QTABLE_PATH))
        except Exception:
            pass
    return np.zeros((STATE_SPACE, N_ACTIONS), dtype=np.float32)


def _save_qtable(q: np.ndarray, meta: dict) -> None:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    np.save(str(QTABLE_PATH), q)
    STATE_PATH.write_text(json.dumps(meta, indent=2))


def _load_decisions() -> list[dict]:
    if not DECISIONS_PATH.exists():
        return []
    out = []
    for line in DECISIONS_PATH.read_text().splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _load_equity() -> list[float]:
    if not EQUITY_PATH.exists():
        return []
    curve = []
    for line in EQUITY_PATH.read_text().splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line).get("equity")
            if e is not None:
                curve.append(float(e))
        except Exception:
            continue
    return curve


def _action_to_str(a: int) -> str:
    return {0: "HOLD", 1: "BUY", 2: "SELL"}.get(a, "HOLD")


def _str_to_action(s: str) -> int:
    return {"HOLD": 0, "BUY": 1, "SELL": 2}.get((s or "HOLD").upper(), 0)


def train(episodes: int = 1, verbose: bool = False) -> dict:
    """
    Run Q-learning over decisions.jsonl history.

    Each decision record is one step. Reward = equity delta between consecutive
    steps (positive if equity rose, negative if fell). When no equity curve is
    available, reward is estimated from the BUY/SELL confidence.

    Returns training summary dict.
    """
    decisions = _load_decisions()
    equity    = _load_equity()

    if len(decisions) < 2:
        log.info("Not enough decisions to train (%d found)", len(decisions))
        return {"trained": False, "reason": "insufficient_data", "steps": len(decisions)}

    q      = _load_qtable()
    meta   = json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else {}
    epsilon = float(meta.get("epsilon", EPSILON))

    total_reward = 0.0
    steps        = 0

    for ep in range(episodes):
        for i in range(len(decisions) - 1):
            d     = decisions[i]
            d_nxt = decisions[i + 1]

            ta = d.get("ta") or {}
            s  = _signal_to_state({**ta, **d})
            s_ = _signal_to_state({**(d_nxt.get("ta") or {}), **d_nxt})

            # True action taken
            action_taken = _str_to_action(d.get("action", "HOLD"))

            # Reward: equity delta if available, else confidence-based estimate
            if len(equity) > i + 1:
                e0 = equity[i]
                e1 = equity[i + 1]
                reward = (e1 - e0) / e0 if e0 > 0 else 0.0
            else:
                conf = d.get("confidence", "low")
                conf_val = {"high": 0.8, "medium": 0.5, "low": 0.2}.get(conf, 0.3)
                action = d.get("action", "HOLD")
                if action == "BUY":
                    reward = conf_val * 0.01
                elif action == "SELL":
                    reward = conf_val * 0.005
                else:
                    reward = 0.0

            # Q update
            best_next = np.max(q[s_])
            q[s, action_taken] += ALPHA * (
                reward + GAMMA * best_next - q[s, action_taken]
            )
            total_reward += reward
            steps += 1

        epsilon = max(EPSILON_MIN, epsilon * EPSILON_DECAY)

    meta = {
        "epsilon":      round(epsilon, 6),
        "steps_trained": meta.get("steps_trained", 0) + steps,
        "episodes":     meta.get("episodes", 0) + episodes,
    }
    _save_qtable(q, meta)

    summary = {
        "trained":       True,
        "steps":         steps,
        "episodes":      episodes,
        "avg_reward":    round(total_reward / steps, 6) if steps else 0.0,
        "epsilon":       round(epsilon, 4),
        "state_space":   STATE_SPACE,
        "nonzero_states": int(np.count_nonzero(q.sum(axis=1))),
    }
    if verbose:
        log.info("RL training done: %s", summary)
    return summary


def get_action(signal: dict) -> dict:
    """
    Inference: given a signal dict, return the greedy action from the Q-table.

    signal keys (from assembly.py output):
      rsi_14, macd_histogram, price_vs_sma200, volume_trend_ratio, sentiment_score

    Returns:
      {action: "BUY"|"SELL"|"HOLD", q_values: [hold, buy, sell], state_idx: int}
    """
    q     = _load_qtable()
    state = _signal_to_state(signal)
    qv    = q[state].tolist()
    best  = int(np.argmax(q[state]))

    return {
        "action":    _action_to_str(best),
        "q_values":  [round(v, 4) for v in qv],
        "state_idx": state,
    }


def rl_signal_for_assembly(signal: dict) -> Optional[dict]:
    """
    Convenience wrapper for assembly.py:
    Returns {direction, confidence, source} or None if Q-table is untrained.
    """
    q = _load_qtable()
    if not np.any(q):
        return None   # untrained — don't influence decisions

    result = get_action(signal)
    action = result["action"]
    qv     = result["q_values"]

    # Confidence = softmax-based certainty of the chosen action
    qv_arr = np.array(qv, dtype=np.float32)
    exp_q  = np.exp(qv_arr - qv_arr.max())
    probs  = exp_q / exp_q.sum()
    best_idx = {"HOLD": 0, "BUY": 1, "SELL": 2}.get(action, 0)
    confidence = float(probs[best_idx])

    if action == "HOLD" or confidence < 0.40:
        return None

    return {
        "direction":  action,
        "confidence": round(confidence, 3),
        "source":     "rl_qtable",
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    summary = train(episodes=3, verbose=True)
    print(json.dumps(summary, indent=2))

    # Quick inference demo
    demo_signal = {
        "rsi_14": 35.0,
        "macd_histogram": -0.5,
        "price_vs_sma200": -0.03,
        "volume_trend_ratio": 1.4,
        "sentiment_score": -0.2,
    }
    result = get_action(demo_signal)
    print(f"Demo action: {result['action']} | Q={result['q_values']}")
