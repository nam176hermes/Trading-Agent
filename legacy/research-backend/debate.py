"""
debate.py — Bull vs Bear Adversarial Debate
Adapted from TradingAgents (TauricResearch).

Before committing to a trade signal, runs two opposing analysts:
  1. Bull Agent — argues FOR the trade
  2. Bear Agent — argues AGAINST the trade
  3. Judge Agent — reviews both arguments and picks the winner

This catches confirmation bias — the #1 failure mode in solo trading agents.
The judge's verdict can override or adjust the original signal.

Phase 2: Adds AdversarialDebate class for multi-round adversarial debate loop.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Optional, Callable, Awaitable

log = logging.getLogger("debate")

STOCK_WATCHLIST = ["AAPL", "NVDA", "MSFT", "GOOGL", "AMZN", "META", "TSLA"]


def format_macro_context(allow_kalshi: bool = True) -> str:
    """
    Get live macro context from live_data module.
    No longer reads stale JSON — fetches fresh from yfinance + World Bank.
    """
    from live_data import get_live_macro_context
    return get_live_macro_context(allow_kalshi=allow_kalshi)


def _load_stock_fundamentals(symbol: str) -> dict | None:
    """Fetch live fundamentals from yfinance."""
    from live_data import fetch_fundamentals
    data = fetch_fundamentals([symbol])
    return data.get(symbol)


def build_stock_fundamentals_context(symbol: str) -> str:
    """Build fundamentals context string for debate prompts."""
    if symbol.upper() not in STOCK_WATCHLIST:
        return ""
    fund = _load_stock_fundamentals(symbol)
    if not fund:
        return ""
    lines = [f"\nSTOCK FUNDAMENTALS ({symbol}):"]
    profile = fund.get("profile", {})
    if profile:
        if profile.get("sector"):
            lines.append(f"  Sector: {profile['sector']}")
        if profile.get("industry"):
            lines.append(f"  Industry: {profile['industry']}")
        if profile.get("marketCap"):
            try:
                mcap = float(profile["marketCap"])
                if mcap >= 1e12:
                    lines.append(f"  Market Cap: ${mcap/1e12:.2f}T")
                else:
                    lines.append(f"  Market Cap: ${mcap/1e9:.0f}B")
            except (ValueError, TypeError):
                pass
    metrics = fund.get("metrics", fund.get("fundamentals", {}))
    if metrics:
        pe = metrics.get("peRatio") or metrics.get("pe_ratio")
        if pe is not None:
            lines.append(f"  P/E: {float(pe):.1f}")
        roe = metrics.get("roe") or metrics.get("returnOnEquity")
        if roe is not None:
            lines.append(f"  ROE: {float(roe)*100:.1f}%" if float(roe) < 10 else f"  ROE: {float(roe)*100:.0f}%")
        rev_growth = metrics.get("revenueGrowth") or metrics.get("revenue_growth")
        if rev_growth is not None:
            lines.append(f"  Revenue Growth: {float(rev_growth)*100:.1f}%")
    earnings = fund.get("earnings", {})
    if earnings:
        next_date = earnings.get("nextDate") or earnings.get("next_date")
        if next_date:
            lines.append(f"  Next Earnings: {next_date}")
    return "\n".join(lines)

# ── Phase 2: System Prompts ─────────────────────────────────────────────────────

BULL_SYSTEM = """You are a senior crypto analyst arguing the bullish case.
You are sharp, data-driven, and combative. You take the other side's arguments
seriously and rebut them with specifics — on-chain metrics, flow data, market
structure, narrative shifts. You never concede a point without addressing it directly."""

BEAR_SYSTEM = """You are a senior crypto analyst arguing the bearish case.
You hunt for risk the bull is ignoring: liquidity, leverage, regime change,
regulatory overhang, narrative exhaustion. You attack the bull's weakest
assumptions with data, not opinions."""

SYNTH_SYSTEM = """You synthesize debate arguments into sharpened theses.
Keep only the points that survived rebuttal. Drop anything the other side
successfully attacked. Plain prose, 3-5 sentences, no JSON, no preamble."""

# ── Agent Prompts ──────────────────────────────────────────────────────────────

BULL_PROMPT = """You are a bullish crypto analyst. Your job is to argue FOR taking this trade.

Asset: {ticker}
Current price: ${price}
Signal: {suggestion} (confidence: {confidence}%)

Technical context:
{ta_summary}

Market data:
{market_context}

Make your STRONGEST case for entering this trade:
1. What's the most compelling bullish signal?
2. What catalyst could drive price higher?
3. Why is now the right entry point?
4. What's the upside target and timeline?

Be convincing but honest. Don't fabricate data. If the bullish case is weak, 
say so — but make the best case you can with what's available."""


BEAR_PROMPT = """You are a bearish crypto analyst. Your job is to argue AGAINST taking this trade.

Asset: {ticker}
Current price: ${price}
Signal: {suggestion} (confidence: {confidence}%)

Technical context:
{ta_summary}

Market data:
{market_context}

Make your STRONGEST case against entering this trade:
1. What's the most concerning bearish signal?
2. What could go wrong — what's the risk catalyst?
3. Why might this be a bad entry point?
4. What price action would prove the bear case right?

Be convincing but honest. Don't fabricate data. If the bearish case is weak, 
say so — but make the best case you can with what's available."""


JUDGE_PROMPT = """You are a neutral trading judge. Review the bull and bear arguments 
and decide the outcome.

Asset: {ticker}
Original signal: {suggestion} (confidence: {confidence}%)

BULL ARGUMENT:
{bull_argument}

BEAR ARGUMENT:
{bear_argument}

Decide:
1. Which side has the stronger argument? (BULL / BEAR / DRAW)
2. Should the original signal be ADJUSTED, CONFIRMED, or REVERSED?
3. What's your adjusted confidence level (0-100)?
4. What's the decisive factor that made you choose?

ONE PARAGRAPH verdict. Be decisive."""


# ── Agent runners (callable — replace with real LLM in integration) ────────────

@dataclass
class DebateResult:
    """Complete debate outcome."""
    ticker: str
    original_suggestion: str
    original_confidence: float

    bull_argument: str = ""
    bear_argument: str = ""
    judge_verdict: str = ""

    winner: str = "DRAW"       # BULL, BEAR, DRAW
    action: str = "CONFIRMED"  # CONFIRMED, ADJUSTED, REVERSED
    adjusted_confidence: float = 0.0
    decisive_factor: str = ""


def _normalize_confidence(val) -> float:
    """Convert various confidence formats to 0.0-1.0 float."""
    if val is None:
        return 0.5
    if isinstance(val, (int, float)):
        return float(val)
    mapping = {
        "high": 0.8, "medium": 0.5, "low": 0.3,
        "strong": 0.9, "weak": 0.2, "neutral": 0.5,
    }
    return mapping.get(str(val).lower(), 0.5)


def _normalize_price(val) -> float:
    """Convert price to float safely."""
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    try:
        return float(str(val).replace(",", "").replace("$", ""))
    except (ValueError, TypeError):
        return 0.0


def build_debate_prompts(
    ticker: str,
    price: float,
    suggestion: str,
    confidence: float,
    ta_summary: str = "",
    market_context: str = "",
) -> dict[str, str]:
    """Build all three prompts for the debate."""
    conf_pct = _normalize_confidence(confidence) * 100
    price_str = f"{_normalize_price(price):,.2f}"

    ctx = {
        "ticker": ticker,
        "price": price_str,
        "suggestion": str(suggestion),
        "confidence": f"{conf_pct:.0f}",
        "ta_summary": ta_summary or "No technical data available.",
        "market_context": market_context or "No market context available.",
    }

    return {
        "bull": BULL_PROMPT.format(**ctx),
        "bear": BEAR_PROMPT.format(**ctx),
        "judge": "",  # Filled after bull/bear respond
    }


def parse_judge_verdict(raw_verdict: str, ticker: str) -> DebateResult:
    """Parse judge's natural language verdict into structured result."""
    result = DebateResult(
        ticker=ticker,
        original_suggestion="",
        original_confidence=0.0,
        judge_verdict=raw_verdict,
    )

    lower = raw_verdict.lower()

    # Winner detection
    if "bull" in lower and "bear" not in lower[:lower.find("bull") + 10]:
        result.winner = "BULL"
    elif "bear" in lower:
        result.winner = "BEAR"
    else:
        result.winner = "DRAW"

    # Action detection
    if any(w in lower for w in ["reversed", "reverse", "opposite"]):
        result.action = "REVERSED"
    elif any(w in lower for w in ["adjusted", "adjust", "lower", "reduce", "increase"]):
        result.action = "ADJUSTED"
    else:
        result.action = "CONFIRMED"

    # Confidence extraction (look for number followed by %)
    import re
    conf_matches = re.findall(r'(\d{1,3})\s*%', raw_verdict)
    if conf_matches:
        result.adjusted_confidence = min(float(conf_matches[-1]), 100) / 100.0
    elif result.action == "REVERSED":
        result.adjusted_confidence = 0.3
    else:
        # Can't extract confidence → treat as unparseable, not 50%.
        # A debate that can't express confidence has no signal value.
        result.adjusted_confidence = 0.0

    # Extract decisive factor (sentence containing "because" or "factor")
    decisive_match = re.search(
        r'(?:because|factor|reason|decisive)[^.]*\.',
        raw_verdict, re.IGNORECASE
    )
    if decisive_match:
        result.decisive_factor = decisive_match.group(0).strip()

    return result


def format_debate_context(result: DebateResult) -> str:
    """Format debate outcome as context for the final decision prompt."""
    lines = [
        f"\n## Adversarial Debate Results for {result.ticker}",
        f"Bull Argument: {result.bull_argument[:300]}...",
        f"Bear Argument: {result.bear_argument[:300]}...",
        f"\nJudge Verdict: {result.judge_verdict}",
        f"\nOutcome: {result.winner} wins — signal {result.action}",
        f"Adjusted confidence: {result.adjusted_confidence*100:.0f}%",
    ]
    if result.decisive_factor:
        lines.append(f"Decisive factor: {result.decisive_factor}")
    return "\n".join(lines)


def format_bull_bear_for_prompt(
    ticker: str,
    bull_text: str,
    bear_text: str,
) -> str:
    """Format bull/bear arguments for injection into the full pipeline prompt."""
    return f"""
## Adversarial Review — {ticker}

**🟢 Bull Case:**
{bull_text[:500]}

**🔴 Bear Case:**
{bear_text[:500]}

Consider both perspectives in your analysis. If the bear case is strong,
adjust your confidence and signal accordingly.
"""


# ── Phase 2: AdversarialDebate Class ──────────────────────────────────────────────

@dataclass
class DebateConfig:
    """Configuration for adversarial debate loop."""
    rounds: int = 2
    debate_model: str = "deep"      # for bull/bear turns
    synthesis_model: str = "deep"   # for final synthesis


class AdversarialDebate:
    """
    Multi-round adversarial debate between bull and bear analysts.

    Round structure:
      Round 1: Bull speaks → Bear responds to THAT bull
      Round 2: Bull rebuts (counters bear's round 1) → Bear rebuts (counters bull's round 2)
      → Synthesis: sharpen each side's final thesis, dropping points that got rebutted
    """

    def __init__(
        self,
        llm_client: Callable[[str, str], Awaitable[str]],
        config: DebateConfig = None,
        allow_kalshi: bool = True,
        macro_context_override: Optional[str] = None,
    ):
        """
        Initialize AdversarialDebate.

        Args:
            llm_client: Async function that takes (prompt, system) and returns str
            config: DebateConfig with rounds and model settings
        """
        self.llm_client = llm_client
        self.config = config or DebateConfig()
        self.allow_kalshi = allow_kalshi
        self.macro_context_override = macro_context_override
        self._import_schemas()

    def _import_schemas(self):
        """Lazy import schemas to avoid circular imports."""
        from schemas import BullArgument, BearArgument, DebateRound
        from signal_parser import parse_structured, build_schema_prompt
        self.BullArgument = BullArgument
        self.BearArgument = BearArgument
        self.DebateRound = DebateRound
        self.parse_structured = parse_structured
        self.build_schema_prompt = build_schema_prompt

    async def run(
        self,
        asset: str,
        market_data: dict,
        signal: "TradingSignal",
    ) -> tuple[list, str, str]:
        """
        Run multi-round adversarial debate.

        Args:
            asset: Asset symbol (e.g., "BTC")
            market_data: Dict with price, volume_24h, change_24h, rsi, macd, etc.
            signal: TradingSignal Pydantic model from schemas.py

        Returns:
            (list[DebateRound], bull_synthesis: str, bear_synthesis: str)
        """
        from schemas import TradingSignal

        history = []
        last_bull = None
        last_bear = None

        # Run debate rounds
        for round_num in range(1, self.config.rounds + 1):
            log.info("[%s] Debate round %d starting", asset, round_num)

            # Debate asymmetry rule: alternate opening side
            # Odd rounds: bull opens, even rounds: bear opens
            if round_num % 2 == 1:  # Odd round: bull opens
                # Bull turn (opens)
                bull_arg = await self._bull_turn(
                    asset, market_data, signal, history, last_bear
                )
                log.info("[%s] Bull round %d complete (conviction=%.2f) [BULL OPENED]",
                         asset, round_num, bull_arg.conviction)

                # Bear turn (responds to bull)
                bear_arg = await self._bear_turn(
                    asset, market_data, signal, history, bull_arg
                )
                log.info("[%s] Bear round %d complete (conviction=%.2f)",
                         asset, round_num, bear_arg.conviction)
            else:  # Even round: bear opens
                # Bear turn (opens)
                bear_arg = await self._bear_turn(
                    asset, market_data, signal, history, last_bull
                )
                log.info("[%s] Bear round %d complete (conviction=%.2f) [BEAR OPENED]",
                         asset, round_num, bear_arg.conviction)

                # Bull turn (responds to bear)
                bull_arg = await self._bull_turn(
                    asset, market_data, signal, history, bear_arg
                )
                log.info("[%s] Bull round %d complete (conviction=%.2f)",
                         asset, round_num, bull_arg.conviction)

            # Create round and add to history
            round_obj = self.DebateRound(
                round_num=round_num,
                bull=bull_arg,
                bear=bear_arg,
            )
            history.append(round_obj)

            last_bull = bull_arg
            last_bear = bear_arg

        # Synthesize final theses
        # Note to synthesis: be aware of turn-order bias
        bull_synth = await self._synthesize("bull", asset, history)
        bear_synth = await self._synthesize("bear", asset, history)

        log.info("[%s] Debate complete: %d rounds, bull_synth=%d chars, bear_synth=%d chars",
                 asset, len(history), len(bull_synth), len(bear_synth))

        return history, bull_synth, bear_synth

    async def _bull_turn(
        self,
        asset: str,
        market_data: dict,
        signal: "TradingSignal",
        history: list,
        last_bear: Optional["BearArgument"],
    ) -> "BullArgument":
        """Generate bull argument for current round."""
        # Build context from market data
        market_context = self._format_market_context(market_data)

        # Build history context
        history_context = self._format_history(history, "bull")

        # Build bear rebuttal context if available
        bear_context = ""
        if last_bear:
            bear_context = f"""
PREVIOUS BEAR ARGUMENT (Round {history[-1].round_num if history else 1}):
Thesis: {last_bear.thesis}
Risk Factors: {', '.join(last_bear.risk_factors[:3])}
Conviction: {last_bear.conviction:.2f}

You must rebut these specific points. Don't ignore them.
"""

        # Build prompt
        base_prompt = f"""You are arguing the BULLISH case for {asset}.

CURRENT MARKET DATA:
{market_context}

INITIAL TRADING SIGNAL:
- Asset: {signal.asset}
- Action: {signal.action}
- Confidence: {signal.confidence:.2f}
- Reasoning: {signal.reasoning}

{bear_context}

{history_context}

Respond with a JSON object matching the BullArgument schema.
Make your strongest bullish case. If responding to bear arguments, rebut them directly
with specific data points (on-chain metrics, flow data, market structure, narrative).
"""

        full_prompt = base_prompt + self.build_schema_prompt(self.BullArgument)

        # Call LLM
        raw_output = await self.llm_client(full_prompt, BULL_SYSTEM)

        # Parse structured output
        try:
            return self.parse_structured(
                self.llm_client,
                raw_output,
                self.BullArgument,
                context_hint=f"Bull argument for {asset}",
            )
        except Exception as e:
            log.warning("Failed to parse bull argument for %s: %s", asset, e)
            # Fallback
            return self.BullArgument(
                thesis=f"Bullish thesis for {asset} based on technical and market indicators.",
                supporting_signals=["Positive momentum", "Strong support levels"],
                counter_to_bear=last_bear.thesis[:100] if last_bear else None,
                conviction=0.6,
            )

    async def _bear_turn(
        self,
        asset: str,
        market_data: dict,
        signal: "TradingSignal",
        history: list,
        current_bull: "BullArgument",
    ) -> "BearArgument":
        """Generate bear argument responding to current bull."""
        # Build context from market data
        market_context = self._format_market_context(market_data)

        # Build history context
        history_context = self._format_history(history, "bear")

        # Build bull rebuttal context
        bull_context = f"""
CURRENT BULL ARGUMENT (Round {len(history) + 1}):
Thesis: {current_bull.thesis}
Supporting Signals: {', '.join(current_bull.supporting_signals[:3])}
Conviction: {current_bull.conviction:.2f}

You must attack the bull's weakest assumptions with data, not opinions.
"""

        # Build prompt
        base_prompt = f"""You are arguing the BEARISH case for {asset}.

CURRENT MARKET DATA:
{market_context}

INITIAL TRADING SIGNAL:
- Asset: {signal.asset}
- Action: {signal.action}
- Confidence: {signal.confidence:.2f}
- Reasoning: {signal.reasoning}

{bull_context}

{history_context}

Respond with a JSON object matching the BearArgument schema.
Hunt for risk the bull is ignoring: liquidity, leverage, regime change,
regulatory overhang, narrative exhaustion. Attack specific claims with data.
"""

        full_prompt = base_prompt + self.build_schema_prompt(self.BearArgument)

        # Call LLM
        raw_output = await self.llm_client(full_prompt, BEAR_SYSTEM)

        # Parse structured output
        try:
            return self.parse_structured(
                self.llm_client,
                raw_output,
                self.BearArgument,
                context_hint=f"Bear argument for {asset}",
            )
        except Exception as e:
            log.warning("Failed to parse bear argument for %s: %s", asset, e)
            # Fallback
            return self.BearArgument(
                thesis=f"Bearish thesis for {asset} highlighting key risks.",
                risk_factors=["Market volatility", "Liquidity concerns"],
                counter_to_bull=current_bull.thesis[:100] if current_bull else None,
                conviction=0.5,
            )

    async def _synthesize(self, side: str, asset: str, rounds: list) -> str:
        """Synthesize final thesis for one side, keeping only surviving points."""
        # Collect all arguments from this side
        if side == "bull":
            arguments = [r.bull for r in rounds]
            other_arguments = [r.bear for r in rounds]
        else:
            arguments = [r.bear for r in rounds]
            other_arguments = [r.bull for r in rounds]

        # Build synthesis prompt
        args_text = "\n\n".join([
            f"Round {r.round_num} {side.capitalize()}:\n{arg.thesis}"
            for r, arg in zip(rounds, arguments)
        ])

        other_text = "\n\n".join([
            f"Round {r.round_num} Other:\n{arg.thesis if side == 'bull' else arg.thesis}"
            for r, arg in zip(rounds, other_arguments)
        ])

        # Note: Turn order bias awareness
        # Odd rounds: bull opened, even rounds: bear opened
        turn_order_note = "Note: In this debate, bull opened in odd rounds (1, 3, ...) and bear opened in even rounds (2, 4, ...). The opening side may have an advantage in framing the narrative."

        prompt = f"""Synthesize the {side} case for {asset}.

ALL {side.upper()} ARGUMENTS:
{args_text}

OPPOSING ARGUMENTS THEY FACED:
{other_text}

{turn_order_note}

Synthesize into 3-5 sentences of plain prose.
Keep ONLY the points that survived rebuttal.
Drop anything the other side successfully attacked.
Be aware of turn-order bias — the opening side may have had framing advantage.
No JSON, no preamble — just the synthesized thesis.
"""

        return await self.llm_client(prompt, SYNTH_SYSTEM)

    def _format_market_context(self, market_data: dict) -> str:
        """Format market data into compact view, including stock fundamentals and macro context."""
        asset = market_data.get("asset", "")
        lines = [
            f"Price: ${market_data.get('price', 'N/A')}",
            f"24h Volume: ${market_data.get('volume_24h', 'N/A')}",
            f"24h Change: {market_data.get('change_24h', 'N/A')}%",
        ]
        if "rsi" in market_data:
            lines.append(f"RSI: {market_data['rsi']:.2f}")
        if "macd" in market_data:
            lines.append(f"MACD: {market_data['macd']}")
        if asset.upper() in STOCK_WATCHLIST:
            fund_ctx = build_stock_fundamentals_context(asset)
            if fund_ctx:
                lines.append(fund_ctx)
        # Add macro context
        macro_ctx = (
            self.macro_context_override
            if self.macro_context_override is not None
            else format_macro_context(allow_kalshi=self.allow_kalshi)
        )
        if macro_ctx:
            lines.append(macro_ctx)
        return "\n".join(lines)

    def _format_history(self, history: list, for_side: str) -> str:
        """Format debate history for context."""
        if not history:
            return "This is the first round."

        lines = [f"DEBATE HISTORY ({len(history)} round(s) already completed):"]
        for r in history:
            lines.append(f"\n--- Round {r.round_num} ---")
            lines.append(f"Bull: {r.bull.thesis[:150]}...")
            lines.append(f"Bear: {r.bear.thesis[:150]}...")

        if for_side == "bull":
            lines.append("\nYou are the bull. Don't repeat your previous points.")
        else:
            lines.append("\nYou are the bear. Don't repeat your previous points.")

        return "\n".join(lines)
