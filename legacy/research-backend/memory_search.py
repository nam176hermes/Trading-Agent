"""
memory_search.py — Semantic search over past trading decisions.

Simple keyword-based search over past executive_summary fields.
No heavy embedding libraries needed for v1 — uses Jaccard similarity.
"""

import json
import logging
from pathlib import Path
from typing import Optional

from schemas import TradingDecision, TradingSignal
from runtime_paths import data_root

log = logging.getLogger("memory_search")

MEMORY_DIR = data_root() / "memory"
TYPED_DECISIONS_FILE = MEMORY_DIR / "typed_decisions.jsonl"


def _tokenize(text: str) -> set[str]:
    """
    Tokenize text into lowercase word set.
    Simple tokenizer: removes punctuation, splits on whitespace.
    """
    text = text.lower()
    # Remove common punctuation
    for char in ".,!?;:()[]{}\"'":
        text = text.replace(char, " ")
    # Split and filter
    words = {w.strip() for w in text.split() if w.strip() and len(w.strip()) > 2}
    return words


def _jaccard_similarity(set1: set[str], set2: set[str]) -> float:
    """
    Calculate Jaccard similarity between two token sets.
    Returns 0.0 to 1.0.
    """
    if not set1 or not set2:
        return 0.0

    intersection = set1 & set2
    union = set1 | set2

    return len(intersection) / len(union) if union else 0.0


def search_similar_decisions(
    ticker: str,
    query: str,
    limit: int = 3,
    min_similarity: float = 0.1,
) -> list[tuple[TradingDecision, float]]:
    """
    Find past decisions with similar executive_summary content.

    Uses Jaccard similarity on tokenized summaries.

    Returns:
        List of (decision, similarity_score) tuples, sorted by similarity.
    """
    if not TYPED_DECISIONS_FILE.exists():
        return []

    query_tokens = _tokenize(query)
    if not query_tokens:
        log.warning("[memory_search] Query tokenization failed or empty")
        return []

    matches = []

    with open(TYPED_DECISIONS_FILE) as f:
        for line in f:
            try:
                entry = json.loads(line)
                asset = entry.get("asset", "").upper()
                if asset != ticker.upper():
                    continue

                decision = TradingDecision(**entry)
                summary_tokens = _tokenize(decision.executive_summary)

                similarity = _jaccard_similarity(query_tokens, summary_tokens)
                if similarity >= min_similarity:
                    matches.append((decision, similarity))

            except (json.JSONDecodeError, KeyError, TypeError) as e:
                log.debug("[memory_search] Skipping malformed entry: %s", e)
                continue

    # Sort by similarity descending
    matches.sort(key=lambda x: x[1], reverse=True)
    return matches[:limit]


def search_cross_asset_lessons(
    query: str,
    exclude_ticker: str = None,
    limit: int = 3,
    min_similarity: float = 0.1,
) -> list[tuple[TradingDecision, float]]:
    """
    Find similar decisions across OTHER assets (cross-pollination).

    Excludes the current ticker to avoid recency bias.
    """
    if not TYPED_DECISIONS_FILE.exists():
        return []

    query_tokens = _tokenize(query)
    if not query_tokens:
        return []

    matches = []

    with open(TYPED_DECISIONS_FILE) as f:
        for line in f:
            try:
                entry = json.loads(line)
                asset = entry.get("asset", "").upper()
                if exclude_ticker and asset == exclude_ticker.upper():
                    continue

                decision = TradingDecision(**entry)
                summary_tokens = _tokenize(decision.executive_summary)

                similarity = _jaccard_similarity(query_tokens, summary_tokens)
                if similarity >= min_similarity:
                    matches.append((decision, similarity))

            except (json.JSONDecodeError, KeyError, TypeError):
                continue

    matches.sort(key=lambda x: x[1], reverse=True)
    return matches[:limit]


def build_enriched_context(
    ticker: str,
    current_signal: Optional[TradingSignal] = None,
    limit_per_section: int = 3,
) -> str:
    """
    Build rich memory context for injection into prompts.

    Includes:
    1. Recent same-ticker decisions
    2. Similar past decisions (by keyword overlap)
    3. Cross-ticker lessons learned

    Returns:
        Markdown formatted string for prompt injection.
    """
    parts = []

    # 1. Recent same-ticker decisions
    recent_decisions = _get_recent_typed_decisions(ticker, limit=limit_per_section)
    if recent_decisions:
        parts.append(f"## Past Decisions on {ticker}\n")
        for decision in recent_decisions:
            parts.append(
                f"- [{decision.timestamp.strftime('%Y-%m-%d')}] "
                f"{decision.initial_signal.action} → {decision.final_action} "
                f"(confidence: {decision.initial_signal.confidence:.0%})\n"
                f"  Summary: {decision.executive_summary[:200]}...\n"
            )

    # 2. Similar decisions (if current signal provided)
    similar_decisions = []
    if current_signal:
        query = current_signal.reasoning
        similar_decisions = search_similar_decisions(
            ticker, query, limit=limit_per_section
        )

    if similar_decisions:
        parts.append(f"\n## Similar Past Decisions ({ticker})\n")
        for decision, similarity in similar_decisions:
            parts.append(
                f"- [{decision.timestamp.strftime('%Y-%m-%d')}] "
                f"{decision.initial_signal.action} (similarity: {similarity:.0%})\n"
                f"  {decision.executive_summary[:150]}...\n"
            )

    # 3. Cross-ticker lessons
    cross_lessons = []
    query = current_signal.reasoning if current_signal else ticker
    cross_lessons = search_cross_asset_lessons(
        query, exclude_ticker=ticker, limit=limit_per_section
    )

    if cross_lessons:
        parts.append(f"\n## Cross-Asset Lessons\n")
        for decision, similarity in cross_lessons:
            parts.append(
                f"- [{decision.timestamp.strftime('%Y-%m-%d')}] {decision.asset}: "
                f"{decision.initial_signal.action} → {decision.final_action} "
                f"(similarity: {similarity:.0%})\n"
                f"  {decision.executive_summary[:150]}...\n"
            )

    return "".join(parts) if parts else ""


def _get_recent_typed_decisions(ticker: str, limit: int = 5) -> list[TradingDecision]:
    """
    Helper: retrieve recent typed decisions for a ticker.
    """
    if not TYPED_DECISIONS_FILE.exists():
        return []

    decisions = []
    with open(TYPED_DECISIONS_FILE) as f:
        for line in f:
            try:
                entry = json.loads(line)
                if entry.get("asset", "").upper() == ticker.upper():
                    decisions.append(TradingDecision(**entry))
            except (json.JSONDecodeError, KeyError, TypeError):
                continue

    decisions.sort(key=lambda d: d.timestamp, reverse=True)
    return decisions[:limit]
