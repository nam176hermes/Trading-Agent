"""
signal_quality.py — 5-dimension heuristic scorer for every decision in decisions.jsonl.

Scores 0-5: verifiability(0.3) + evidence(0.25) + specificity(0.2) + novelty(0.15) + review(0.1)
"""

import json
import logging
from pathlib import Path
from runtime_paths import data_root
from collections import Counter

log = logging.getLogger(__name__)

DECISIONS_PATH = data_root() / "decisions.jsonl"
MEMORY_DECISIONS = data_root() / "memory" / "decisions.jsonl"
SCORED_PATH = data_root() / "decisions_scored.jsonl"

KEYWORDS = ("because", "risk", "evidence", "data", "chart",
            "catalyst", "support", "resistance")


def score_decision(decision: dict, recent_decisions: list[dict] = None) -> dict:
    """Score a single decision across 5 dimensions. Returns dict with scores 0-5."""

    symbol = str(decision.get("symbol", decision.get("ticker", "")))
    suggestion = str(decision.get("suggestion", decision.get("final_action", "")))
    reasoning = str(decision.get("reasoning", decision.get("rationale", decision.get("executive_summary", ""))))
    confidence_raw = decision.get("confidence", 0)
    _CONF_MAP = {"low": 0.35, "medium": 0.65, "high": 0.85, "conflicted": 0.20}
    if isinstance(confidence_raw, str):
        confidence = float(_CONF_MAP.get(confidence_raw.lower(), 0.5))
    else:
        confidence = float(confidence_raw) if confidence_raw else 0.5
    sentiment_score = decision.get("sentiment_score", 0) or 0
    sentiment_source = str(decision.get("sentiment_source", ""))
    bull_synth = str(decision.get("bull_synthesis", ""))
    bear_synth = str(decision.get("bear_synthesis", ""))
    stop_loss = decision.get("stop_loss_suggestion", decision.get("stop_loss"))
    target = decision.get("target_suggestion", decision.get("target"))

    # ── Verifiability (0-5) ──
    verifiability = 1.0
    if suggestion and str(suggestion).upper() in ("BUY", "SELL", "STRONG BUY", "STRONG SELL", "LONG", "SHORT"):
        verifiability += 1.2
    if symbol:
        verifiability += 0.8
    if stop_loss is not None and target is not None:
        verifiability += 1.5
    if confidence > 0:
        verifiability += 0.5
    verifiability = max(0.0, min(5.0, verifiability))

    # ── Evidence (0-5) ──
    evidence = min(3.0, len(reasoning) / 160.0) if reasoning else 0
    reasoning_lower = reasoning.lower()
    for kw in KEYWORDS:
        if kw in reasoning_lower:
            evidence += 0.7
    if sentiment_score != 0:
        evidence += 0.5
    evidence = max(0.0, min(5.0, evidence))

    # ── Specificity (0-5) ──
    specificity = 1.0
    if symbol:
        specificity += 1.0
    if decision.get("current_price") or decision.get("price"):
        specificity += 0.5
    if stop_loss is not None and target is not None:
        specificity += 1.0
    specificity += min(1.5, len(reasoning) / 320.0) if reasoning else 0
    specificity = max(0.0, min(5.0, specificity))

    # ── Novelty (0-5) ──
    if recent_decisions:
        dup_count = sum(
            1 for d in recent_decisions
            if (d.get("symbol") == symbol or d.get("ticker") == symbol)
            and (d.get("suggestion") == suggestion or d.get("final_action") == suggestion)
        )
        if dup_count == 0:
            novelty = 5.0
        elif dup_count == 1:
            novelty = 3.0
        elif dup_count == 2:
            novelty = 1.5
        else:
            novelty = 0.5
    else:
        novelty = 5.0

    # ── Review (0-5) ──
    review = 1.0
    if reasoning and len(reasoning) > 100:
        review += 1.0
    if sentiment_source and sentiment_source not in ("", "unavailable", "unavailable — MistTrack not wired"):
        review += 1.5
    if bull_synth or bear_synth:
        review += 1.5
    review = max(0.0, min(5.0, review))

    # ── Overall ──
    overall = (
        verifiability * 0.3
        + evidence * 0.25
        + specificity * 0.2
        + novelty * 0.15
        + review * 0.1
    )

    return {
        "symbol": symbol,
        "suggestion": suggestion,
        "verifiability": round(verifiability, 4),
        "evidence": round(evidence, 4),
        "specificity": round(specificity, 4),
        "novelty": round(novelty, 4),
        "review": round(review, 4),
        "overall": round(overall, 4),
    }


def _read_decisions(path: Path) -> list[dict]:
    if not path.exists():
        return []
    decisions = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                decisions.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return decisions


def score_all_pending(limit: int = 200) -> int:
    """Read decisions.jsonl, score any without quality_score, write to decisions_scored.jsonl."""
    source_path = DECISIONS_PATH if DECISIONS_PATH.exists() else MEMORY_DECISIONS
    decisions = _read_decisions(source_path)
    if not decisions:
        log.info("No decisions found in %s", source_path)
        return 0

    # Load existing scored decisions to avoid re-scoring
    existing_scored = []
    if SCORED_PATH.exists():
        existing_scored = _read_decisions(SCORED_PATH)

    scored_symbols = set()
    for d in existing_scored:
        sym = d.get("symbol", "") or d.get("ticker", "")
        ts = d.get("timestamp", "") or d.get("stored_at", "")
        scored_symbols.add(f"{sym}|{ts}")

    scored_count = 0
    with open(SCORED_PATH, "a") as f:
        for decision in decisions[-limit:]:
            sym = decision.get("symbol", "") or decision.get("ticker", "")
            ts = decision.get("timestamp", "") or decision.get("stored_at", "")

            # Skip if already scored
            if decision.get("quality_score") is not None:
                continue
            if f"{sym}|{ts}" in scored_symbols:
                continue

            # Compute novelty using last 50 decisions in the source file
            recent = decisions[-50:]
            scores = score_decision(decision, recent)

            # Merge scores into the decision record
            decision["quality_score"] = scores
            f.write(json.dumps(decision) + "\n")
            scored_count += 1

    log.info("Scored %d decisions → %s", scored_count, SCORED_PATH)
    return scored_count


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    n = score_all_pending()
    print(f"Scored {n} decisions.")
