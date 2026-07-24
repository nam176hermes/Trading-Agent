"""
risk_personas.py — Multi-Persona Risk Assessment
Adapted from TradingAgents (TauricResearch).

Upgrades the existing risk_scan prompt with three risk personas:
  1. Aggressive — "How much can I size up safely?"
  2. Conservative — "What's the worst that could happen?"
  3. Neutral — "What's the balanced risk-adjusted view?"

Each persona reviews the same data independently, then outputs a position
sizing recommendation. The final risk score is a weighted blend.

Phase 2: Adds RiskDebate class for 3-way iterative debate between personas.
"""

import json
import logging
from dataclasses import dataclass
from typing import Callable, Awaitable

log = logging.getLogger("risk_personas")

STOCK_WATCHLIST = ["AAPL", "NVDA", "MSFT"]


def _load_stock_fundamentals(symbol: str) -> dict | None:
    """Fetch live fundamentals from yfinance."""
    from live_data import fetch_fundamentals
    data = fetch_fundamentals([symbol])
    return data.get(symbol)


def apply_stock_risk_rules(symbol: str, position_size_pct: float) -> tuple[float, list[str]]:
    """
    Apply stock-specific risk rules. Returns (adjusted_position_size_pct, warnings).
    """
    if symbol.upper() not in STOCK_WATCHLIST:
        return position_size_pct, []

    warnings = []
    adjusted = position_size_pct
    fund = _load_stock_fundamentals(symbol)

    # Market cap filter: no positions < $10B
    if fund:
        profile = fund.get("profile", {})
        mcap = profile.get("marketCap")
        if mcap is not None:
            try:
                mcap_b = float(mcap) / 1e9
                if mcap_b < 10:
                    warnings.append(f"Market cap ${mcap_b:.1f}B < $10B minimum — position size halved")
                    adjusted *= 0.5
            except (ValueError, TypeError):
                pass

    # Pre-earnings: reduce position size 50% or exit
    if fund:
        earnings = fund.get("earnings", {})
        next_date = earnings.get("nextDate") or earnings.get("next_date")
        if next_date:
            try:
                from datetime import datetime, timezone
                # Parse date — handle various formats
                for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%fZ"):
                    try:
                        earnings_dt = datetime.strptime(str(next_date)[:19], fmt).replace(tzinfo=timezone.utc)
                        break
                    except ValueError:
                        continue
                else:
                    earnings_dt = None

                if earnings_dt:
                    days_until = (earnings_dt - datetime.now(timezone.utc)).days
                    if 0 <= days_until <= 7:
                        warnings.append(f"Earnings in {days_until}d — position size reduced 50%")
                        adjusted *= 0.5
                    elif days_until < 0:
                        warnings.append("Earnings date passed — exercise caution")
            except Exception:
                pass

    # Sector concentration: max 40% in any single sector
    if fund:
        profile = fund.get("profile", {})
        sector = profile.get("sector", "")
        if sector == "Technology":
            warnings.append("Technology sector — monitor concentration (max 40%)")

    return adjusted, warnings

# ── Phase 2: System Prompts ─────────────────────────────────────────────────────

AGGRESSIVE_SYSTEM = """You are an aggressive risk manager. Find the maximum
safe position size. You embrace calculated risk for asymmetric returns."""

CONSERVATIVE_SYSTEM = """You are a conservative risk manager. Prioritize
capital preservation. Find every way this trade could go wrong."""

NEUTRAL_SYSTEM = """You are a neutral risk arbiter. Weigh both aggressive
and conservative views. Find the balanced risk-adjusted recommendation."""


# ── Persona Prompts ────────────────────────────────────────────────────────────

AGGRESSIVE_PROMPT = """You are an aggressive risk manager for crypto trading.
Your job: find the maximum safe position size given the current signals.

Report data:
{report_json}

Answer:
1. What's the maximum position size (% of portfolio) you'd recommend?
2. What's the best-case scenario and its probability?
3. What would make you reduce this position immediately?
4. What's the ONE signal that gives you the most confidence to size up?

Be specific with numbers. Output as bullet points."""


CONSERVATIVE_PROMPT = """You are a conservative risk manager for crypto trading.
Your job: identify EVERYTHING that could go wrong and protect capital.

Report data:
{report_json}

Answer:
1. What's the maximum position size (% of portfolio) you'd recommend?
2. What's the worst-case scenario and its probability?
3. List every risk factor you can identify in the current data
4. If you HAD to enter, what stop-loss would make you comfortable?

Be paranoid. Capital preservation is your only goal. Output as bullet points."""


NEUTRAL_PROMPT = """You are a neutral risk analyst for crypto trading.
Your job: give the balanced, risk-adjusted view between aggressive and conservative.

Report data:
{report_json}

Answer:
1. What's the optimal position size (% of portfolio)?
2. What's the expected return vs risk ratio?
3. Which signals are most reliable, which are noise?
4. What's the one condition that would flip you from neutral to bearish?

Balance the bull and bear cases. Output as bullet points."""


SYNTHESIS_PROMPT = """You are the head of risk management. Three analysts have given
their assessments. Synthesize them into a single risk decision.

Asset: {ticker}

AGGRESSIVE ASSESSMENT:
{aggressive}

CONSERVATIVE ASSESSMENT:
{conservative}

NEUTRAL ASSESSMENT:
{neutral}

Output:
1. FINAL POSITION SIZE (% of portfolio)
2. STOP-LOSS LEVEL (% below entry)
3. RISK LEVEL (LOW / MEDIUM / HIGH / CRITICAL)
4. ONE-SENTENCE rationale for the decision

Be decisive. No hedging."""


# ── Persona Builders ───────────────────────────────────────────────────────────

def build_persona_prompts(report_json: str) -> dict[str, str]:
    """Build all three persona prompts from report data."""
    return {
        "aggressive": AGGRESSIVE_PROMPT.format(report_json=report_json),
        "conservative": CONSERVATIVE_PROMPT.format(report_json=report_json),
        "neutral": NEUTRAL_PROMPT.format(report_json=report_json),
    }


def build_synthesis_prompt(
    ticker: str,
    aggressive: str,
    conservative: str,
    neutral: str,
) -> str:
    """Build the synthesis/judge prompt for the three personas."""
    return SYNTHESIS_PROMPT.format(
        ticker=ticker,
        aggressive=aggressive,
        conservative=conservative,
        neutral=neutral,
    )


# ── Result Parsing ─────────────────────────────────────────────────────────────

def parse_risk_synthesis(raw: str) -> dict:
    """Parse the synthesis output into structured risk decision."""
    import re

    result = {
        "position_size_pct": 0,
        "stop_loss_pct": 0,
        "risk_level": "MEDIUM",
        "rationale": raw[:200],
    }

    # Position size: look for "%" preceded by a number
    pos_match = re.search(
        r'(?:position\s*(?:size|allocation)?[:\s]*)?(\d{1,3})\s*%',
        raw, re.IGNORECASE
    )
    if pos_match:
        result["position_size_pct"] = int(pos_match.group(1))

    # Stop loss: look for "stop" near "%"
    stop_match = re.search(
        r'(?:stop[-\s]*(?:loss|limit)?[:\s]*)?(\d{1,3})\s*%',
        raw, re.IGNORECASE
    )
    if stop_match:
        result["stop_loss_pct"] = int(stop_match.group(1))

    # Risk level
    lower = raw.lower()
    if "critical" in lower:
        result["risk_level"] = "CRITICAL"
    elif "high" in lower:
        result["risk_level"] = "HIGH"
    elif "low" in lower:
        result["risk_level"] = "LOW"

    return result


def format_risk_context(ticker: str, risk_result: dict) -> str:
    """Format risk assessment for injection into the main analysis prompt."""
    return f"""
## Risk Assessment — {ticker}
- Position size: {risk_result.get('position_size_pct', '?')}% of portfolio
- Stop-loss: {risk_result.get('stop_loss_pct', '?')}% below entry
- Risk level: **{risk_result.get('risk_level', 'MEDIUM')}**
- Rationale: {risk_result.get('rationale', 'N/A')[:200]}
"""


def format_three_personas_for_prompt(
    ticker: str,
    aggressive: str,
    conservative: str,
    neutral: str,
    synthesis: str,
) -> str:
    """Format all three persona assessments + synthesis for the main prompt."""
    return f"""
## Multi-Persona Risk Review — {ticker}

**🔥 Aggressive View:**
{aggressive[:400]}

**🛡️ Conservative View:**
{conservative[:400]}

**⚖️ Neutral View:**
{neutral[:400]}

**📊 Synthesis Decision:**
{synthesis[:300]}
"""


# ── Phase 2: RiskDebate Class ─────────────────────────────────────────────────────

@dataclass
class RiskDebateConfig:
    """Configuration for risk debate loop."""
    rounds: int = 1  # Default 1 round, can increase for more debate


class RiskDebate:
    """
    3-way iterative debate between risk personas.

    Three personas debate ITERATIVELY:
    - Aggressive starts round 1
    - Conservative responds to aggressive's position
    - Neutral critiques both
    - If >1 round: each responds to the others' prior positions
    """

    def __init__(self, llm_client: Callable[[str, str], Awaitable[str]], config: RiskDebateConfig = None):
        """
        Initialize RiskDebate.

        Args:
            llm_client: Async function that takes (prompt, system) and returns str
            config: RiskDebateConfig with rounds setting
        """
        self.llm_client = llm_client
        self.config = config or RiskDebateConfig()
        self._import_schemas()

    def _import_schemas(self):
        """Lazy import schemas to avoid circular imports."""
        from schemas import RiskAssessment
        from signal_parser import parse_structured, build_schema_prompt
        self.RiskAssessment = RiskAssessment
        self.parse_structured = parse_structured
        self.build_schema_prompt = build_schema_prompt

    async def run(
        self,
        signal: "TradingSignal",
        bull_synth: str,
        bear_synth: str,
        market_data: dict,
    ) -> list:
        """
        Run 3-way risk debate.

        Args:
            signal: TradingSignal Pydantic model from schemas.py
            bull_synth: Synthesized bull thesis from debate
            bear_synth: Synthesized bear thesis from debate
            market_data: Dict with price, volume_24h, change_24h, rsi, macd, etc.

        Returns:
            list[RiskAssessment] - one per persona (aggressive, conservative, neutral)
        """
        from schemas import TradingSignal

        assessments = []
        prior_assessments = {  # For multi-round debate
            "aggressive": None,
            "conservative": None,
            "neutral": None,
        }

        # Run debate rounds
        for round_num in range(1, self.config.rounds + 1):
            log.info("Risk debate round %d starting", round_num)

            # Aggressive turn
            aggressive = await self._persona_turn(
                "aggressive", signal, bull_synth, bear_synth, market_data,
                prior_assessments, AGGRESSIVE_SYSTEM
            )
            prior_assessments["aggressive"] = aggressive

            # Conservative turn (responds to aggressive if multi-round)
            conservative = await self._persona_turn(
                "conservative", signal, bull_synth, bear_synth, market_data,
                prior_assessments, CONSERVATIVE_SYSTEM
            )
            prior_assessments["conservative"] = conservative

            # Neutral turn (critiques both)
            neutral = await self._persona_turn(
                "neutral", signal, bull_synth, bear_synth, market_data,
                prior_assessments, NEUTRAL_SYSTEM
            )
            prior_assessments["neutral"] = neutral

            if round_num == self.config.rounds:
                # Final round - collect assessments
                assessments = [aggressive, conservative, neutral]

        log.info("Risk debate complete: %d assessments", len(assessments))
        return assessments

    async def _persona_turn(
        self,
        persona: str,
        signal: "TradingSignal",
        bull_synth: str,
        bear_synth: str,
        market_data: dict,
        prior_assessments: dict,
        system_prompt: str,
    ) -> "RiskAssessment":
        """Generate risk assessment for one persona."""
        # Build context
        market_context = self._format_market_context(market_data)

        # Build debate context
        debate_context = f"""
BULL SYNTHESIS:
{bull_synth}

BEAR SYNTHESIS:
{bear_synth}
"""

        # Build prior assessments context for multi-round debate
        prior_context = ""
        if self.config.rounds > 1:
            for other_persona, other_assessment in prior_assessments.items():
                if other_assessment and other_persona != persona:
                    prior_context += f"""
PREVIOUS {other_persona.upper()} ASSESSMENT:
- Accept Signal: {other_assessment.accept_signal}
- Position Size: {other_assessment.position_size_pct:.1f}%
- Rationale: {other_assessment.rationale[:200]}...

"""

        # Build prompt
        base_prompt = f"""You are a {persona} risk manager evaluating a trading signal.

TRADING SIGNAL:
- Asset: {signal.asset}
- Action: {signal.action}
- Confidence: {signal.confidence:.2f}
- Entry Price: ${signal.entry_price or 'N/A'}
- Stop Loss: ${signal.stop_loss or 'N/A'}
- Take Profit: ${signal.take_profit or 'N/A'}
- Reasoning: {signal.reasoning}

{market_context}

{debate_context}

{prior_context}

Respond with a JSON object matching the RiskAssessment schema.
"""

        full_prompt = base_prompt + self.build_schema_prompt(self.RiskAssessment)

        # Call LLM
        raw_output = await self.llm_client(full_prompt, system_prompt)

        # Parse structured output
        try:
            assessment = self.parse_structured(
                self.llm_client,
                raw_output,
                self.RiskAssessment,
                context_hint=f"{persona.capitalize()} risk assessment for {signal.asset}",
            )
            # Ensure persona field is set correctly
            assessment.persona = persona
            return assessment
        except Exception as e:
            log.warning("Failed to parse %s risk assessment: %s", persona, e)
            # Fallback
            default_position = 5.0 if persona == "aggressive" else 1.0
            return self.RiskAssessment(
                persona=persona,
                accept_signal=signal.action in ("BUY", "BUY"),
                position_size_pct=default_position,
                rationale=f"{persona.capitalize()} risk assessment for {signal.asset}.",
            )

    def _format_market_context(self, market_data: dict) -> str:
        """Format market data into compact view, including stock risk rules."""
        asset = market_data.get("asset", "")
        lines = [
            "CURRENT MARKET DATA:",
            f"Price: ${market_data.get('price', 'N/A')}",
            f"24h Volume: ${market_data.get('volume_24h', 'N/A')}",
            f"24h Change: {market_data.get('change_24h', 'N/A')}%",
        ]
        if "rsi" in market_data:
            lines.append(f"RSI: {market_data['rsi']:.2f}")
        if "macd" in market_data:
            lines.append(f"MACD: {market_data['macd']}")

        if asset.upper() in STOCK_WATCHLIST:
            from debate import build_stock_fundamentals_context
            fund_ctx = build_stock_fundamentals_context(asset)
            if fund_ctx:
                lines.append(fund_ctx)
            _, warnings = apply_stock_risk_rules(asset, 10.0)
            if warnings:
                lines.append("\nSTOCK RISK RULES:")
                for w in warnings:
                    lines.append(f"  - {w}")

        return "\n".join(lines)
