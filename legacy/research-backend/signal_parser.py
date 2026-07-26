"""
signal_parser.py — Structured Trading Signal Extraction
Adapted from TradingAgents-CN (SignalProcessor class).

Phase 1 Rewrite: Adds Pydantic-based structured parsing alongside legacy regex parsing.

Takes verbose LLM trading output and extracts structured decisions:
  {action, target_price, stop_loss, confidence, reasoning, timeframe}

Our agent's 'suggestion' field is currently an opaque string like
"WATCH FOR ENTRY" or "NO SIGNAL" — this module normalizes it into
typed, backtestable decisions.

TACN-CN approach: regex + keyword mapping + action standardization.
We extend it for crypto with position sizing and risk levels.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional, TypeVar, Type
from pydantic import ValidationError

log = logging.getLogger("signal_parser")

# Import new Pydantic schemas
from schemas import (
    TradingSignal as PydanticTradingSignal,
    BullArgument,
    BearArgument,
    DebateRound,
    RiskAssessment,
    TradingDecision,
)

T = TypeVar("T", bound=object)


@dataclass
class TradingSignal:
    """Structured, machine-readable trading decision."""
    ticker: str
    action: str = "hold"          # buy, sell, hold, watch
    confidence: float = 0.0       # 0.0-1.0
    target_price: Optional[float] = None
    stop_loss: Optional[float] = None
    position_size_pct: float = 0.0  # % of portfolio
    risk_level: str = "medium"    # low, medium, high, critical
    risk_reward_ratio: float = 0.0  # R:R ratio (QuantAgent standard field)
    timeframe: str = ""           # "1d", "1w", "1m"
    reasoning: str = ""           # Key sentence from analysis
    raw_text: str = ""            # Original LLM output
    source: str = "parsed"        # "parsed", "manual", "fallback"

    @property
    def is_actionable(self) -> bool:
        return self.action in ("buy", "sell") and self.confidence >= 0.3

    @property
    def is_watch(self) -> bool:
        return self.action == "watch"

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "action": self.action,
            "confidence": round(self.confidence, 2),
            "target_price": self.target_price,
            "stop_loss": self.stop_loss,
            "risk_reward_ratio": self.risk_reward_ratio,
            "position_size_pct": self.position_size_pct,
            "risk_level": self.risk_level,
            "timeframe": self.timeframe,
            "reasoning": self.reasoning[:300],
        }


# ── Action Keyword Mapping (TACN-CN approach, extended for crypto) ───────────

ACTION_MAP = {
    # Direct actions
    "buy": "buy", "买入": "buy", "long": "buy", "做多": "buy",
    "strong buy": "buy", "强力买入": "buy", "强烈建议买入": "buy",
    "sell": "sell", "卖出": "sell", "short": "sell", "做空": "sell",
    "strong sell": "sell", "强力卖出": "sell",
    "hold": "hold", "持有": "hold", "wait": "hold", "观望": "hold",
    "neutral": "hold", "中性": "hold", "no signal": "hold",

    # Watch/alert variants (crypto-specific)
    "watch": "watch", "monitor": "watch", "关注": "watch",
    "watch for entry": "watch", "等待入场": "watch",
    "watch for exit": "watch", "等待出场": "watch",
    "alert": "watch", "警戒": "watch",

    # Position management
    "take profit": "sell", "止盈": "sell",
    "cut loss": "sell", "止损": "sell",
    "add position": "buy", "加仓": "buy",
    "reduce position": "sell", "减仓": "sell",
    "accumulate": "buy", "积累": "buy",
    "distribute": "sell", "分配卖出": "sell",
}

RISK_LEVEL_MAP = {
    "critical": "critical", "high": "high", "medium": "medium", "low": "low",
    "极高": "critical", "高": "high", "中": "medium", "低": "low",
    "🔴": "critical", "🟠": "high", "🟡": "medium", "🟢": "low",
}


# ── Phase 1: Structured Pydantic Parsing ───────────────────────────────────────

def build_schema_prompt(model_cls: Type[T]) -> str:
    """
    Build a prompt instructing the LLM to respond with valid JSON matching a Pydantic schema.

    Args:
        model_cls: Pydantic model class (e.g., TradingSignal, BullArgument)

    Returns:
        Prompt string with schema JSON and instructions
    """
    schema = model_cls.model_json_schema()
    schema_json = json.dumps(schema, indent=2)

    return f"""

Respond with ONLY a valid JSON object matching this schema:

{schema_json}

IMPORTANT:
- Respond with raw JSON only — no markdown fences, no explanations, no commentary.
- All required fields must be present.
- Follow the exact field names and types shown above.
"""


def parse_structured(
    llm_client,
    raw_output: str,
    model_cls: Type[T],
    *,
    context_hint: str = "",
    repair_model: str = "deepseek-chat",
    max_retries: int = 1,
    task_type: str = "json_repair",
) -> T:
    """
    Parse LLM output into a Pydantic model with automatic repair on failure.

    Args:
        llm_client: LLM client with a call() method (sync or async)
        raw_output: Raw text output from LLM
        model_cls: Pydantic model class to validate against
        context_hint: Optional hint about what was being requested (for repair prompt)
        repair_model: Model name for repair attempts
        max_retries: Number of repair attempts (default: 1)

    Returns:
        Validated Pydantic model instance

    Raises:
        ValidationError: If validation fails after all retries
    """
    # Strip markdown fences if present
    cleaned = raw_output.strip()
    if cleaned.startswith("```"):
        # Remove opening fence
        lines = cleaned.split("\n", 1)
        if len(lines) > 1:
            cleaned = lines[1]
        # Remove language identifier if present
        if cleaned.startswith(("json", "JSON")):
            cleaned = cleaned[4:].lstrip()
        # Remove closing fence
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].rstrip()
        cleaned = cleaned.strip()

    # Store original raw text for validation
    original_raw = raw_output

    # Try direct validation
    initial_error: ValidationError | None = None
    try:
        return model_cls.model_validate_json(cleaned)
    except ValidationError as exc:
        initial_error = exc
        log.warning(
            "Initial validation failed for %s: %s",
            model_cls.__name__,
            str(exc)[:100],
        )

    # Repair attempt
    if max_retries > 0:
        # CRITICAL: Pass original raw text to prevent fabrication
        # The repair LLM must NEVER invent values — only reformat existing ones
        repair_prompt = f"""Your previous response was not valid JSON matching the required schema.

Context: {context_hint or "Unknown"}

Error: {str(initial_error)[:500]}

**YOUR ORIGINAL RESPONSE (DO NOT ADD, REMOVE, OR CHANGE ANY VALUES):**
```
{original_raw}
```

Your task: Reformat the text above into valid JSON matching the required schema.
CRITICAL RULES:
- DO NOT add any new values that were not in the original text
- DO NOT remove any values from the original text
- DO NOT change any values from the original text
- ONLY fix the JSON formatting (brackets, quotes, commas)
- Respond with ONLY the raw JSON — no explanations, no markdown fences

If the original text is missing required fields, leave them as null or empty.
"""

        try:
            # Check if client is async
            import asyncio
            if asyncio.iscoroutinefunction(llm_client):
                # For async clients, we need to run in an async context
                # This is a limitation — caller should use async version if needed
                log.warning("Async LLM client passed to sync parse_structured, using fallback")
                repaired = raw_output  # Fallback: return original error
            else:
                # Call with task_type for model selection
                if hasattr(llm_client, '__code__') and 'task_type' in llm_client.__code__.co_varnames:
                    repaired = llm_client(repair_prompt, task_type=task_type)
                else:
                    repaired = llm_client(repair_prompt)

            # Strip fences again from repaired output
            repaired_cleaned = repaired.strip()
            if repaired_cleaned.startswith("```"):
                lines = repaired_cleaned.split("\n", 1)
                if len(lines) > 1:
                    repaired_cleaned = lines[1]
                if repaired_cleaned.startswith(("json", "JSON")):
                    repaired_cleaned = repaired_cleaned[4:].lstrip()
                if repaired_cleaned.endswith("```"):
                    repaired_cleaned = repaired_cleaned[:-3].rstrip()
                repaired_cleaned = repaired_cleaned.strip()

            # Validation: check key fields match original
            # This is a simple check — for production, you might want more sophisticated validation
            try:
                validated = model_cls.model_validate_json(repaired_cleaned)

                # Log successful repair
                log.info("Successfully repaired JSON for %s", model_cls.__name__)

                return validated

            except ValidationError as e2:
                log.error("Repair attempt failed for %s: %s", model_cls.__name__, str(e2)[:100])
                raise
        except ValidationError as e2:
            log.error("Repair attempt failed for %s: %s", model_cls.__name__, str(e2)[:100])
            raise
        except Exception as e3:  # External repair-provider boundary.
            log.error("Unexpected error during repair: %s", str(e3)[:100])
            if initial_error is not None:
                raise initial_error from e3
            raise

    if initial_error is not None:
        raise initial_error
    raise RuntimeError(f"Could not validate {model_cls.__name__} from output")


# ── Price Extraction Patterns (TACN-CN approach) ──────────────────────────────

PRICE_PATTERNS = [
    # Target price patterns
    r'(?:target|目标|看至|目标价)[:\s]*\$?(\d+[.,]?\d*)',
    r'(?:to|up to|towards)\s+\$?(\d+[.,]?\d*)',
    r'(?:目标|看至)\s+\$?(\d+[.,]?\d*)',
    r'(?:resistance|阻力)[^\d]*(\d+[.,]?\d*)',
    # Stop loss patterns
    r'(?:stop[-\s]*(?:loss|limit)|止损)[:\s]*\$?(\d+[.,]?\d*)',
    r'(?:stop below|stops below)\s+\$?(\d+[.,]?\d*)',
    r'(?:support|支撑)[^\d]*(\d+[.,]?\d*)',
    # General price mentions near actions
    r'(?:entry|入场)[^\d]*(\d+[.,]?\d*)',
]


def _normalize_price(val: str) -> float:
    """Normalize price string to float, handling commas."""
    return float(val.replace(",", "").replace("$", "").strip())


def _calculate_rr(
    target: Optional[float],
    stop: Optional[float],
    current: Optional[float],
) -> float:
    """
    Calculate risk-reward ratio (QuantAgent standard).
    R:R = expected gain / potential loss.
    Returns 0.0 if insufficient data.
    """
    if not target or not stop or not current:
        return 0.0
    if stop >= current or current >= target:
        return 0.0
    reward = target - current
    risk = current - stop
    if risk <= 0:
        return 0.0
    return round(reward / risk, 2)


def _extract_action(text: str) -> str:
    """
    Extract trading action from text.
    TACN-CN approach: scan for action keywords, return the strongest signal.
    """
    lower = text.lower().strip()

    # Priority order: explicit > implied
    if any(w in lower for w in ["strong buy", "强力买入", "强烈建议买入"]):
        return "buy"
    if any(w in lower for w in ["strong sell", "强力卖出"]):
        return "sell"

    # Scan ACTION_MAP for matches
    match_scores = {}
    for keyword, action in ACTION_MAP.items():
        if keyword.lower() in lower:
            match_scores[action] = match_scores.get(action, 0) + len(keyword)

    if not match_scores:
        return "hold"

    # Return action with most/longest keyword matches
    return max(match_scores, key=match_scores.get)


def _extract_confidence(text: str) -> float:
    """
    Extract confidence percentage from text.
    Looks for patterns like "75% confident", "confidence: 0.82", "概率: 60%".
    """
    # Percentage pattern
    pct_matches = re.findall(
        r'(?:confidence|信心|概率|确信|把握)[^\d]*(\d{1,3})\s*%',
        text, re.IGNORECASE
    )
    if pct_matches:
        return min(float(pct_matches[-1]), 100) / 100.0

    # Decimal pattern
    dec_matches = re.findall(
        r'(?:confidence|信心)[^\d]*(\d\.\d{1,2})',
        text, re.IGNORECASE
    )
    if dec_matches:
        return float(dec_matches[-1])

    # Language-based fallback
    lower = text.lower()
    if any(w in lower for w in ["high confidence", "strong signal", "clear signal"]):
        return 0.8
    if any(w in lower for w in ["low confidence", "weak signal", "uncertain"]):
        return 0.3
    if any(w in lower for w in ["medium confidence", "moderate"]):
        return 0.5

    # Heuristic: if action is buy/sell (not hold), default to moderate
    action = _extract_action(text)
    return 0.5 if action in ("buy", "sell") else 0.3


def _extract_price(text: str, patterns: list[str] = PRICE_PATTERNS) -> Optional[float]:
    """Extract the first matching price from text using priority patterns."""
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                return _normalize_price(match.group(1))
            except (ValueError, AttributeError):
                continue
    return None


def _extract_position_size(text: str) -> float:
    """Extract position size recommendation as % of portfolio."""
    pct_match = re.search(
        r'(?:position|仓位|position size|allocation)[^\d]*(\d{1,3})\s*%',
        text, re.IGNORECASE
    )
    if pct_match:
        return min(float(pct_match.group(1)), 100) / 100.0
    return 0.0


def _extract_timeframe(text: str) -> str:
    """Extract trading timeframe."""
    tf_patterns = [
        (r'\b(\d+)\s*(?:day|d|日)\b', 'd'),
        (r'\b(\d+)\s*(?:week|w|周)\b', 'w'),
        (r'\b(\d+)\s*(?:month|m|月)\b', 'm'),
        (r'\b(?:short[-\s]*term|短线)\b', '1w'),
        (r'\b(?:medium[-\s]*term|中线)\b', '1m'),
        (r'\b(?:long[-\s]*term|长线)\b', '3m'),
        (r'\b(?:scalp|日内)\b', '1h'),
    ]
    for pattern, unit in tf_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            if match.lastindex and match.group(1).isdigit():
                return f"{match.group(1)}{unit}"
            return unit
    return ""


def _extract_risk_level(text: str) -> str:
    """Extract risk level from text."""
    lower = text.lower()
    for keyword, level in RISK_LEVEL_MAP.items():
        if keyword.lower() in lower:
            return level
    return "medium"


def _extract_reasoning(text: str) -> str:
    """Extract the most informative sentence as reasoning."""
    # Look for sentences with signal-relevant keywords
    key_sentences = re.findall(
        r'[^.!?]*(?:because|due to|signal|indicator|RSI|MACD|volume|trend|support|resistance|because|since|given)[^.!?]*[.!?]',
        text, re.IGNORECASE
    )
    if key_sentences:
        return key_sentences[0].strip()

    # Fallback: first substantive sentence
    sentences = re.split(r'[.!?\n]', text)
    for s in sentences:
        s = s.strip()
        if len(s) > 20:
            return s
    return text[:200]


# ── Main Parser ───────────────────────────────────────────────────────────────

def parse_signal(
    text: str,
    ticker: str,
    current_price: Optional[float] = None,
) -> TradingSignal:
    """
    Parse a free-text LLM trading output into a structured TradingSignal.

    Args:
        text: Raw LLM output (English or Chinese)
        ticker: Asset ticker symbol
        current_price: Current market price (for context, not used in parsing)

    Returns:
        TradingSignal with typed fields
    """
    if not text or text.startswith("[LLM STUB]"):
        return TradingSignal(
            ticker=ticker,
            action="hold",
            confidence=0.0,
            raw_text=text or "",
            source="stub",
            reasoning="LLM not wired — placeholder output",
        )

    signal = TradingSignal(
        ticker=ticker,
        raw_text=text,
        source="parsed",
    )

    # Extract each field
    signal.action = _extract_action(text)
    signal.confidence = _extract_confidence(text)
    signal.target_price = _extract_price(text)
    signal.stop_loss = _extract_price(
        text,
        patterns=[
            r'(?:stop[-\s]*(?:loss|limit)|止损)[:\s]*\$?(\d+[.,]?\d*)',
            r'(?:stop below|stops below)\s+\$?(\d+[.,]?\d*)',
            r'(?:support|支撑)[^\d]*(\d+[.,]?\d*)',
        ]
    )
    signal.position_size_pct = _extract_position_size(text)
    signal.timeframe = _extract_timeframe(text)
    signal.risk_level = _extract_risk_level(text)
    signal.reasoning = _extract_reasoning(text)

    # Normalize: if no target but stop exists, use current_price context
    if not signal.target_price and signal.stop_loss and current_price:
        pass

    # Calculate risk-reward ratio (QuantAgent pattern)
    signal.risk_reward_ratio = _calculate_rr(
        signal.target_price, signal.stop_loss, current_price
    )

    return signal


def parse_asset_json(asset_json: dict) -> TradingSignal:
    """
    Parse an assembled asset JSON into a TradingSignal.
    Uses the suggestion field + debate/risk context if available.
    """
    ticker = asset_json.get("symbol", "?")
    suggestion = str(asset_json.get("suggestion", ""))

    # Build composite text from all available context
    text = suggestion
    if asset_json.get("_debate_context"):
        text += "\n" + asset_json["_debate_context"]
    if asset_json.get("_risk_context"):
        text += "\n" + asset_json["_risk_context"]

    price = asset_json.get("price") or asset_json.get("current_price")
    current_price = float(price) if isinstance(price, (int, float)) else None

    # If the suggestion is a raw keyword, shortcut the parser
    lower_sugg = suggestion.lower().strip()
    if lower_sugg in ACTION_MAP:
        action = ACTION_MAP[lower_sugg]
        return TradingSignal(
            ticker=ticker,
            action=action,
            confidence=float(asset_json.get("confidence", 0.3)) if isinstance(asset_json.get("confidence"), (int, float)) else 0.3,
            target_price=current_price,
            risk_level=str(asset_json.get("risk_assessment", {}).get("risk_level", "medium")).lower(),
            reasoning=suggestion,
            raw_text=text,
            source="assembly",
        )

    return parse_signal(text, ticker, current_price)


def parse_report(report: dict) -> list[TradingSignal]:
    """
    Parse a full assembled report into a list of TradingSignals.
    One signal per asset.
    """
    signals = []
    for asset in report.get("assets", []):
        signal = parse_asset_json(asset)
        signals.append(signal)
    return signals
