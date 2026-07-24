"""
analysts.py — Four Specialist Analysts for Trading Agent

Runs four specialized analysts BEFORE the adversarial debate:
1. Technical Analyst: RSI, MACD, support/resistance, volume profile
2. Sentiment Analyst: Fear & Greed, social volume, funding rates
3. On-chain Analyst: Whale movements, exchange flows (stub for now)
4. Macro/News Analyst: ETF flows, regulatory events (stub for now)

Each emits a Pydantic model from schemas.py. This provides rich context
for the bull/bear debate without overwhelming a single prompt.
"""

import logging
from typing import Optional
from pydantic import ValidationError

from schemas import (
    TechnicalReport,
    SentimentReport,
    OnchainReport,
    MacroReport,
    TradingSignal,
)
from signal_parser import parse_structured, build_schema_prompt

log = logging.getLogger("analysts")

# ── System Prompts ──────────────────────────────────────────────────────────────

TECHNICAL_SYSTEM = """You are a senior technical analyst specializing in crypto markets.
You analyze price action, indicators, volume, and market structure.
You are precise, data-driven, and focus on actionable levels."""

SENTIMENT_SYSTEM = """You are a sentiment analyst specializing in crypto markets.
You analyze social media chatter, funding rates, and market emotion.
You focus on crowd psychology and positioning extremes."""

ONCHAIN_SYSTEM = """You are an on-chain analyst specializing in crypto markets.
You analyze whale movements, exchange flows, and HODLer behavior.
You focus on smart money flows and long-term holder trends."""

MACRO_SYSTEM = """You are a macro analyst specializing in crypto markets.
You analyze ETF flows, regulatory events, and correlation with traditional markets.
You focus on institutional adoption and macroeconomic trends."""


# ── Technical Analyst ────────────────────────────────────────────────────────────

class TechnicalAnalyst:
    """Analyzes technical indicators and market structure."""

    async def analyze(
        self,
        llm_client,
        asset: str,
        market_data: dict,
        ta_data: Optional[dict] = None,
    ) -> TechnicalReport:
        """
        Generate technical analysis report.

        Args:
            llm_client: LLM client with call() method
            asset: Asset symbol (e.g., "BTC")
            market_data: Dict with price, volume_24h, change_24h, etc.
            ta_data: Optional dict with RSI, MACD, etc. from ta_engine

        Returns:
            TechnicalReport with structured technical analysis
        """
        # Build context from available data
        context = self._build_context(asset, market_data, ta_data)

        prompt = f"""Analyze the technical setup for {asset}.

{context}

Respond with a JSON object matching the TechnicalReport schema.
Provide specific levels for support/resistance.
Assess trend based on price action and indicators.
Evaluate volume profile relative to recent norms."""

        full_prompt = prompt + build_schema_prompt(TechnicalReport)

        try:
            return parse_structured(
                llm_client,
                await llm_client(full_prompt, TECHNICAL_SYSTEM, task_type="analyst_reports"),
                TechnicalReport,
                context_hint=f"Technical analysis for {asset}",
            )
        except ValidationError as e:
            log.warning("Technical analysis for %s failed validation: %s", asset, e)
            # Fallback
            return self._fallback_report(asset, ta_data)

    def _build_context(self, asset: str, market_data: dict, ta_data: Optional[dict]) -> str:
        """Build market context string."""
        lines = [
            f"Asset: {asset}",
            f"Price: ${market_data.get('price', 'N/A')}",
            f"24h Volume: ${market_data.get('volume_24h', 'N/A')}",
            f"24h Change: {market_data.get('change_24h', 'N/A')}%",
        ]

        if ta_data:
            if ta_data.get("rsi") is not None:
                lines.append(f"RSI: {ta_data['rsi']:.2f}")
            if ta_data.get("macd"):
                lines.append(f"MACD: {ta_data['macd']}")

        return "\n".join(lines)

    def _fallback_report(self, asset: str, ta_data: Optional[dict]) -> TechnicalReport:
        """Generate fallback report when LLM fails."""
        rsi = ta_data.get("rsi") if ta_data else None

        # Determine trend from RSI
        if rsi is not None:
            if rsi > 70:
                trend = "overbought"
            elif rsi < 30:
                trend = "oversold"
            elif rsi > 50:
                trend = "bullish"
            else:
                trend = "bearish"
        else:
            trend = "neutral"

        return TechnicalReport(
            asset=asset,
            rsi=rsi,
            macd=ta_data.get("macd") if ta_data else None,
            trend=trend,
            support_levels=[],
            resistance_levels=[],
            volume_profile="normal",
            key_signals=["Limited data — LLM analysis failed"],
            summary=f"Technical analysis for {asset} based on available indicators.",
        )


# ── Sentiment Analyst ───────────────────────────────────────────────────────────

class SentimentAnalyst:
    """Analyzes market sentiment and positioning."""

    async def analyze(
        self,
        llm_client,
        asset: str,
        market_data: dict,
        sentiment_data: Optional[dict] = None,
    ) -> SentimentReport:
        """
        Generate sentiment analysis report.

        Args:
            llm_client: LLM client with call() method
            asset: Asset symbol (e.g., "BTC")
            market_data: Dict with price, volume_24h, change_24h, etc.
            sentiment_data: Optional dict with Fear & Greed, social volume, etc.

        Returns:
            SentimentReport with structured sentiment analysis
        """
        context = self._build_context(asset, market_data, sentiment_data)

        prompt = f"""Analyze market sentiment for {asset}.

{context}

Based on price action (strong up/down moves suggest sentiment extremes),
volume patterns (anomalous volume = heightened emotion), and
available sentiment metrics, assess the current mood.

Respond with a JSON object matching the SentimentReport schema."""

        full_prompt = prompt + build_schema_prompt(SentimentReport)

        try:
            return parse_structured(
                llm_client,
                await llm_client(full_prompt, SENTIMENT_SYSTEM, task_type="analyst_reports"),
                SentimentReport,
                context_hint=f"Sentiment analysis for {asset}",
            )
        except ValidationError as e:
            log.warning("Sentiment analysis for %s failed validation: %s", asset, e)
            return self._fallback_report(asset, market_data)

    def _build_context(self, asset: str, market_data: dict, sentiment_data: Optional[dict]) -> str:
        """Build sentiment context string."""
        lines = [
            f"Asset: {asset}",
            f"Price: ${market_data.get('price', 'N/A')}",
            f"24h Change: {market_data.get('change_24h', 'N/A')}%",
        ]

        if sentiment_data:
            if sentiment_data.get("sentiment"):
                lines.append(f"Sentiment: {sentiment_data['sentiment']}")

        return "\n".join(lines)

    def _fallback_report(self, asset: str, market_data: dict) -> SentimentReport:
        """Generate fallback report when LLM fails."""
        change_24h = market_data.get("change_24h", 0)

        # Infer sentiment from price action
        if isinstance(change_24h, (int, float)):
            if change_24h > 5:
                social_volume = "spiking"
            elif change_24h > 2:
                social_volume = "high"
            elif change_24h < -5:
                social_volume = "spiking"
            elif change_24h < -2:
                social_volume = "high"
            else:
                social_volume = "normal"
        else:
            social_volume = "normal"

        return SentimentReport(
            asset=asset,
            fear_greed_index=None,
            social_volume=social_volume,
            funding_rate=None,
            funding_bias="neutral",
            key_signals=["Limited data — LLM analysis failed"],
            summary=f"Sentiment analysis for {asset} based on price action.",
        )


# ── On-chain Analyst ─────────────────────────────────────────────────────────────

class OnchainAnalyst:
    """
    Analyzes on-chain metrics using Binance large trades as data proxy.
    No API key required — uses public Binance REST API.
    """

    async def analyze(
        self,
        llm_client,
        asset: str,
        market_data: dict,
        allow_exchange: bool = True,
    ) -> OnchainReport:
        """
        Generate on-chain analysis report from live Binance data.

        Args:
            llm_client: LLM client with call() method
            asset: Asset symbol (e.g., "BTC")
            market_data: Dict with price, volume_24h, change_24h, etc.

        Returns:
            OnchainReport with real trade data
        """
        if not allow_exchange:
            return OnchainReport(
                asset=asset,
                whale_activity="neutral",
                exchange_outflows="neutral",
                exchange_inflows="neutral",
                hdl_state="neutral",
                key_signals=["Exchange data disabled for research-only mode"],
                summary="On-chain exchange proxy disabled.",
            )

        from live_data import fetch_binance_large_trades
        onchain_data = fetch_binance_large_trades(asset)
        
        if not onchain_data:
            return OnchainReport(
                asset=asset,
                whale_activity="neutral",
                exchange_outflows="neutral",
                exchange_inflows="neutral",
                hdl_state="neutral",
                key_signals=["No on-chain data available for this asset"],
                summary="On-chain analysis unavailable.",
            )

        context = (
            f"Asset: {asset}\n"
            f"Recent trades analyzed: {onchain_data['recent_trades']}\n"
            f"Buy volume ratio: {onchain_data['buy_volume_ratio']:.1%}\n"
            f"Large buys (>$10k): {onchain_data['large_buy_count']}\n"
            f"Large sells (>$10k): {onchain_data['large_sell_count']}\n"
            f"Whale pattern: {onchain_data['whale_activity']}\n"
            f"Exchange flow signal: {onchain_data['exchange_flow_signal']}\n"
        )

        prompt = (
            f"Analyze on-chain activity for {asset} based on recent trade data.\n\n"
            f"{context}\n\n"
            f"Based on the buy/sell volume ratio, large trade patterns, and whale activity, "
            f"assess whether on-chain participants are accumulating, distributing, or neutral.\n\n"
            f"Respond with JSON matching the OnchainReport schema."
        )

        try:
            return parse_structured(
                llm_client,
                await llm_client(prompt, ONCHAIN_SYSTEM, task_type="analyst_reports"),
                OnchainReport,
                context_hint=f"On-chain analysis for {asset}",
            )
        except Exception:
            # Fallback based on data
            return OnchainReport(
                asset=asset,
                whale_activity=onchain_data.get("whale_activity", "neutral"),
                exchange_outflows="strong" if onchain_data.get("buy_volume_ratio", 0.5) > 0.55 else "neutral",
                exchange_inflows="strong" if onchain_data.get("buy_volume_ratio", 0.5) < 0.45 else "neutral",
                hdl_state=onchain_data.get("whale_activity", "neutral"),
                key_signals=[
                    f"Buy ratio: {onchain_data['buy_volume_ratio']:.1%}",
                    f"Whales: {onchain_data['whale_activity']}",
                ],
                summary=f"On-chain: {onchain_data['large_buy_count']} large buys vs {onchain_data['large_sell_count']} large sells. {onchain_data['whale_activity'].title()} pattern.",
            )


# ── Macro/News Analyst ───────────────────────────────────────────────────────────

class MacroAnalyst:
    """Analyzes macroeconomic and regulatory factors from real report files."""

    async def analyze(
        self,
        llm_client,
        asset: str,
        market_data: dict,
        allow_persistent_cache: bool = True,
        macro_snapshot: Optional[dict] = None,
    ) -> MacroReport:
        from macro_data import format_macro_context
        if macro_snapshot is not None:
            macro_summary = format_macro_context(macro_snapshot)
        elif allow_persistent_cache:
            from macro_data import get_macro_snapshot
            try:
                snapshot = get_macro_snapshot()
                macro_summary = format_macro_context(snapshot)
            except Exception:
                macro_summary = "Macro data unavailable."
        else:
            raise ValueError("approved read-only macro snapshot is required")

        # News stub (news_collector integration is future work)
        news_summary = "No real-time news feed configured. Use macro indicators above."

        prompt = (
            "Analyze macro and news context for {}.\n\n"
            "MACRO INDICATORS:\n{}\n\n"
            "RECENT NEWS:\n{}\n\n"
            "ASSET: {}\nPrice: {}\n\n"
            "Respond with JSON matching MacroReport schema. "
            "Focus on how macro conditions affect {} specifically."
        ).format(asset, macro_summary, news_summary, asset, market_data.get("price", "N/A"), asset)

        try:
            result = parse_structured(
                llm_client,
                await llm_client(prompt, MACRO_SYSTEM, task_type="analyst_reports"),
                MacroReport,
                context_hint="Macro analysis for {}".format(asset),
            )
            return result
        except Exception:
            return MacroReport(
                asset=asset,
                etf_flows="neutral",
                regulatory_status="neutral",
                macro_correlation="moderate",
                key_events=[macro_summary[:200]],
                summary="Macro: {}".format(macro_summary[:300]),
            )


class AnalystCoordinator:
    """
    Coordinates all four analysts and returns their reports.

    This is the main entry point for the analyst layer.
    """

    def __init__(self, llm_client, allow_exchange: bool = True):
        """
        Initialize analyst coordinator.

        Args:
            llm_client: LLM client with call() method
        """
        self.llm_client = llm_client
        self.allow_exchange = allow_exchange
        self.technical = TechnicalAnalyst()
        self.sentiment = SentimentAnalyst()
        self.onchain = OnchainAnalyst()
        self.macro = MacroAnalyst()

    async def analyze_all(
        self,
        asset: str,
        market_data: dict,
        ta_data: Optional[dict] = None,
        sentiment_data: Optional[dict] = None,
        macro_snapshot: Optional[dict] = None,
    ) -> tuple[TechnicalReport, SentimentReport, OnchainReport, MacroReport]:
        """
        Run all four analysts in parallel.

        Args:
            asset: Asset symbol (e.g., "BTC")
            market_data: Dict with price, volume_24h, change_24h, etc.
            ta_data: Optional dict with RSI, MACD, etc. from ta_engine
            sentiment_data: Optional dict with Fear & Greed, social volume, etc.

        Returns:
            (technical, sentiment, onchain, macro) reports
        """
        import asyncio

        log.info("[%s] Running all four analysts", asset)

        technical, sentiment, onchain, macro = await asyncio.gather(
            self.technical.analyze(self.llm_client, asset, market_data, ta_data),
            self.sentiment.analyze(self.llm_client, asset, market_data, sentiment_data),
            self.onchain.analyze(
                self.llm_client,
                asset,
                market_data,
                allow_exchange=self.allow_exchange,
            ),
            self.macro.analyze(
                self.llm_client,
                asset,
                market_data,
                allow_persistent_cache=self.allow_exchange,
                macro_snapshot=macro_snapshot,
            ),
        )

        log.info("[%s] Analyst reports complete: technical=%s, sentiment=%s, onchain=%s (stub), macro=%s (stub)",
                 asset, technical.trend, sentiment.social_volume, onchain.whale_activity, macro.etf_flows)

        return technical, sentiment, onchain, macro

    def format_for_debate(
        self,
        technical: TechnicalReport,
        sentiment: SentimentReport,
        onchain: OnchainReport,
        macro: MacroReport,
    ) -> str:
        """
        Format all analyst reports into a context string for debate.

        This string is injected into bull/bear prompts to ground their arguments.
        """
        lines = [
            "## Analyst Reports",
            "",
            f"**Technical Analysis:**",
            f"  Trend: {technical.trend}",
            f"  RSI: {technical.rsi:.2f}" if technical.rsi else "  RSI: N/A",
            f"  MACD: {technical.macd}" if technical.macd else "  MACD: N/A",
            f"  Volume Profile: {technical.volume_profile}",
            f"  Summary: {technical.summary[:200]}",
            "",
            f"**Sentiment Analysis:**",
            f"  Social Volume: {sentiment.social_volume}",
            f"  Funding Bias: {sentiment.funding_bias}",
            f"  Fear & Greed: {sentiment.fear_greed_index:.0f}" if sentiment.fear_greed_index else "  Fear & Greed: N/A",
            f"  Summary: {sentiment.summary[:200]}",
            "",
            f"**On-chain Analysis:**",
            f"  Whale Activity: {onchain.whale_activity}",
            f"  Exchange Outflows: {onchain.exchange_outflows}",
            f"  HODLer State: {onchain.hdl_state}",
            f"  Note: {onchain.note}",
            "",
            f"**Macro Analysis:**",
            f"  ETF Flows: {macro.etf_flows}",
            f"  Regulatory Status: {macro.regulatory_status}",
            f"  Macro Correlation: {macro.macro_correlation}",
            f"  Note: {macro.note}",
        ]

        return "\n".join(lines)
