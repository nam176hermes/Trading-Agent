"""
reflection_engine.py - Typed reflection and learning loop.

Resolves outcomes for typed TradingDecision records, generates LLM reflections,
and stores explicit benchmark availability. Source failures are observable and
never become prices, benchmark results, or successful reflection state.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4

from memory import (
    get_pending_reflections_typed,
    mark_decision_reflected,
    store_reflection,
)
from schemas import TradingDecision

log = logging.getLogger("reflection_engine")


class ReflectionStatus(str, Enum):
    """Typed state for reflection work and optional benchmark enrichment."""

    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class PriceFetchResult:
    status: ReflectionStatus
    price: Optional[float]
    reason_code: Optional[str]
    trace_id: str


@dataclass(frozen=True)
class OutcomeResolution:
    status: ReflectionStatus
    decision: Optional[TradingDecision]
    reason_code: Optional[str]
    trace_id: str


@dataclass(frozen=True)
class ReflectionWriteResult:
    status: ReflectionStatus
    reason_code: Optional[str]
    trace_id: str


@dataclass(frozen=True)
class ReflectionBatchResult:
    status: ReflectionStatus
    processed: int
    unavailable: int
    total: int
    trace_id: str
    reason_codes: tuple[str, ...] = ()



def _new_trace_id() -> str:
    return uuid4().hex[:16]


class ReflectionEngine:
    """Resolve outcomes and generate typed reflections."""

    REFLECTION_HORIZON_DAYS = 7

    async def reflect_on_pending(
        self,
        llm_client,
        horizon_days: Optional[int] = None,
    ) -> ReflectionBatchResult:
        """Reflect on eligible decisions without converting failures to success."""
        trace_id = _new_trace_id()
        horizon = (
            horizon_days
            if horizon_days is not None
            else self.REFLECTION_HORIZON_DAYS
        )
        try:
            pending = get_pending_reflections_typed(horizon_days=horizon)
        except (OSError, ValueError, TypeError) as exc:
            log.error(
                "event=reflection_pending_unavailable trace_id=%s "
                "reason_code=PENDING_DECISIONS_UNAVAILABLE error_type=%s",
                trace_id,
                type(exc).__name__,
            )
            return ReflectionBatchResult(
                status=ReflectionStatus.UNAVAILABLE,
                processed=0,
                unavailable=0,
                total=0,
                trace_id=trace_id,
                reason_codes=("PENDING_DECISIONS_UNAVAILABLE",),
            )

        if not pending:
            log.info("event=reflection_batch_empty trace_id=%s", trace_id)
            return ReflectionBatchResult(
                status=ReflectionStatus.COMPLETED,
                processed=0,
                unavailable=0,
                total=0,
                trace_id=trace_id,
            )

        processed = 0
        partial = 0
        unavailable = 0
        reason_codes: list[str] = []

        for _entry_date, decision in pending:
            item_trace_id = _new_trace_id()
            price_result = await self._fetch_current_price(decision.asset)
            if (
                price_result.status is ReflectionStatus.UNAVAILABLE
                or price_result.price is None
            ):
                unavailable += 1
                reason = price_result.reason_code or "PRICE_UNAVAILABLE"
                reason_codes.append(reason)
                log.warning(
                    "event=reflection_item_unavailable trace_id=%s "
                    "batch_trace_id=%s asset=%s reason_code=%s",
                    price_result.trace_id,
                    trace_id,
                    decision.asset,
                    reason,
                )
                continue

            outcome = await self.resolve_outcome(
                decision,
                price_result.price,
                trace_id=item_trace_id,
            )
            if (
                outcome.status is ReflectionStatus.UNAVAILABLE
                or outcome.decision is None
            ):
                unavailable += 1
                reason_codes.append(
                    outcome.reason_code or "OUTCOME_UNAVAILABLE"
                )
                continue

            actual_return = outcome.decision.pnl_pct
            if actual_return is None:
                unavailable += 1
                reason_codes.append("OUTCOME_RETURN_UNAVAILABLE")
                log.error(
                    "event=reflection_item_unavailable trace_id=%s asset=%s "
                    "reason_code=OUTCOME_RETURN_UNAVAILABLE",
                    item_trace_id,
                    decision.asset,
                )
                continue

            write_result = await self.reflect_single(
                llm_client,
                outcome.decision,
                price_result.price,
                actual_return,
                trace_id=item_trace_id,
            )
            if write_result.status is ReflectionStatus.UNAVAILABLE:
                unavailable += 1
                reason_codes.append(
                    write_result.reason_code or "REFLECTION_WRITE_UNAVAILABLE"
                )
                continue
            if write_result.status is ReflectionStatus.PARTIAL:
                partial += 1
                reason_codes.append(
                    write_result.reason_code or "REFLECTION_WRITE_PARTIAL"
                )

            try:
                marked = mark_decision_reflected(
                    decision.timestamp,
                    decision.asset,
                )
            except (OSError, ValueError, TypeError, KeyError) as exc:
                marked = False
                log.error(
                    "event=reflection_mark_unavailable trace_id=%s asset=%s "
                    "reason_code=MARK_REFLECTED_FAILED error_type=%s",
                    item_trace_id,
                    decision.asset,
                    type(exc).__name__,
                )
            if not marked:
                unavailable += 1
                reason_codes.append("MARK_REFLECTED_FAILED")
                continue
            processed += 1

        if unavailable == 0 and partial == 0:
            status = ReflectionStatus.COMPLETED
        elif processed == 0 and partial == 0:
            status = ReflectionStatus.UNAVAILABLE
        else:
            status = ReflectionStatus.PARTIAL

        bounded_reasons = tuple(dict.fromkeys(reason_codes))
        log.info(
            "event=reflection_batch_complete trace_id=%s status=%s "
            "processed=%d unavailable=%d total=%d reason_codes=%s",
            trace_id,
            status.value,
            processed,
            unavailable,
            len(pending),
            ",".join(bounded_reasons[:8]),
        )
        return ReflectionBatchResult(
            status=status,
            processed=processed,
            unavailable=unavailable,
            total=len(pending),
            trace_id=trace_id,
            reason_codes=bounded_reasons,
        )

    async def resolve_outcome(
        self,
        decision: TradingDecision,
        current_price: float,
        *,
        trace_id: Optional[str] = None,
    ) -> OutcomeResolution:
        """Resolve a decision only when entry, exit, and direction are valid."""
        trace_id = trace_id or _new_trace_id()
        entry_price = decision.initial_signal.entry_price

        if entry_price is None or entry_price <= 0:
            log.warning(
                "event=reflection_outcome_unavailable trace_id=%s asset=%s "
                "reason_code=ENTRY_PRICE_INVALID",
                trace_id,
                decision.asset,
            )
            return OutcomeResolution(
                ReflectionStatus.UNAVAILABLE,
                None,
                "ENTRY_PRICE_INVALID",
                trace_id,
            )
        if current_price <= 0:
            log.warning(
                "event=reflection_outcome_unavailable trace_id=%s asset=%s "
                "reason_code=EXIT_PRICE_INVALID",
                trace_id,
                decision.asset,
            )
            return OutcomeResolution(
                ReflectionStatus.UNAVAILABLE,
                None,
                "EXIT_PRICE_INVALID",
                trace_id,
            )

        action = decision.initial_signal.action
        if action == "BUY":
            pnl_pct = (current_price - entry_price) / entry_price
        elif action == "SELL":
            pnl_pct = (entry_price - current_price) / entry_price
        else:
            log.warning(
                "event=reflection_outcome_unavailable trace_id=%s asset=%s "
                "reason_code=NON_DIRECTIONAL_ACTION action=%s",
                trace_id,
                decision.asset,
                action,
            )
            return OutcomeResolution(
                ReflectionStatus.UNAVAILABLE,
                None,
                "NON_DIRECTIONAL_ACTION",
                trace_id,
            )

        if pnl_pct > 0.02:
            outcome_label = "win"
        elif pnl_pct < -0.02:
            outcome_label = "loss"
        else:
            outcome_label = "breakeven"

        decision.exit_price = current_price
        decision.pnl_pct = pnl_pct
        decision.outcome_label = outcome_label
        decision.resolved_at = datetime.now(timezone.utc)

        log.info(
            "event=reflection_outcome_resolved trace_id=%s asset=%s "
            "entry_price=%.8f exit_price=%.8f pnl_pct=%.6f outcome=%s",
            trace_id,
            decision.asset,
            entry_price,
            current_price,
            pnl_pct,
            outcome_label,
        )
        return OutcomeResolution(
            ReflectionStatus.COMPLETED,
            decision,
            None,
            trace_id,
        )

    async def reflect_single(
        self,
        llm_client,
        decision: TradingDecision,
        current_price: float,
        actual_return: float,
        *,
        trace_id: Optional[str] = None,
    ) -> ReflectionWriteResult:
        """Generate and store a reflection with benchmark marked unavailable."""
        trace_id = trace_id or _new_trace_id()
        if decision.exit_price is None:
            log.warning(
                "event=reflection_write_unavailable trace_id=%s asset=%s "
                "reason_code=EXIT_PRICE_UNAVAILABLE",
                trace_id,
                decision.asset,
            )
            return ReflectionWriteResult(
                ReflectionStatus.UNAVAILABLE,
                "EXIT_PRICE_UNAVAILABLE",
                trace_id,
            )

        days_passed = max(
            0,
            (datetime.now(timezone.utc) - decision.timestamp).days,
        )
        benchmark_reason = "ALPHA_BENCHMARK_DISABLED"
        prompt = self._build_reflection_prompt(
            decision,
            current_price,
            actual_return,
            alpha_return=None,
            days_passed=days_passed,
        )
        system_msg = (
            "You are a trading performance analyst. Be honest, concise, and "
            "specific. Focus on actionable lessons, not generic advice."
        )

        try:
            reflection_text = await llm_client(
                prompt,
                system=system_msg,
                task_type="reflection",
            )
        except Exception as exc:  # External provider boundary.
            log.error(
                "event=reflection_provider_unavailable trace_id=%s asset=%s "
                "reason_code=REFLECTION_PROVIDER_FAILED error_type=%s",
                trace_id,
                decision.asset,
                type(exc).__name__,
            )
            return ReflectionWriteResult(
                ReflectionStatus.UNAVAILABLE,
                "REFLECTION_PROVIDER_FAILED",
                trace_id,
            )
        if not isinstance(reflection_text, str) or not reflection_text.strip():
            log.error(
                "event=reflection_provider_unavailable trace_id=%s asset=%s "
                "reason_code=REFLECTION_PROVIDER_EMPTY",
                trace_id,
                decision.asset,
            )
            return ReflectionWriteResult(
                ReflectionStatus.UNAVAILABLE,
                "REFLECTION_PROVIDER_EMPTY",
                trace_id,
            )

        try:
            store_reflection(
                ticker=decision.asset,
                trade_date=decision.timestamp.strftime("%Y-%m-%d"),
                suggestion=decision.initial_signal.action,
                confidence=decision.initial_signal.confidence,
                raw_return_pct=actual_return * 100,
                alpha_return_pct=None,
                holding_days=days_passed,
                reflection=reflection_text,
                benchmark_status="UNAVAILABLE",
                benchmark_reason_code=benchmark_reason,
            )
        except (OSError, TypeError, ValueError) as exc:
            log.error(
                "event=reflection_store_unavailable trace_id=%s asset=%s "
                "reason_code=REFLECTION_STORE_FAILED error_type=%s",
                trace_id,
                decision.asset,
                type(exc).__name__,
            )
            return ReflectionWriteResult(
                ReflectionStatus.UNAVAILABLE,
                "REFLECTION_STORE_FAILED",
                trace_id,
            )

        log.info(
            "event=reflection_stored trace_id=%s asset=%s status=PARTIAL "
            "raw_return_pct=%.2f benchmark_status=UNAVAILABLE outcome=%s",
            trace_id,
            decision.asset,
            actual_return * 100,
            decision.outcome_label or "unknown",
        )
        return ReflectionWriteResult(
            ReflectionStatus.PARTIAL,
            benchmark_reason,
            trace_id,
        )

    async def _fetch_current_price(self, asset: str) -> PriceFetchResult:
        """Fetch price from providers and return explicit unavailability."""
        trace_id = _new_trace_id()
        provider_failures: list[str] = []

        try:
            import yfinance as yf

            ticker_map = {
                "BTC": "BTC-USD",
                "ETH": "ETH-USD",
                "SOL": "SOL-USD",
                "BNB": "BNB-USD",
                "XRP": "XRP-USD",
                "ADA": "ADA-USD",
                "DOGE": "DOGE-USD",
                "AVAX": "AVAX-USD",
                "DOT": "DOT-USD",
                "MATIC": "MATIC-USD",
            }
            symbol = ticker_map.get(asset.upper(), f"{asset.upper()}-USD")
            data = yf.download(symbol, period="1d", progress=False)
            if not data.empty:
                price = float(data["Close"].iloc[-1])
                if price > 0:
                    return PriceFetchResult(
                        ReflectionStatus.COMPLETED,
                        price,
                        None,
                        trace_id,
                    )
        except Exception as exc:  # External provider boundary.
            provider_failures.append(f"yfinance:{type(exc).__name__}")
            log.warning(
                "event=reflection_price_provider_failed trace_id=%s asset=%s "
                "provider=yfinance error_type=%s",
                trace_id,
                asset,
                type(exc).__name__,
            )

        try:
            import aiohttp

            coin_ids = {
                "BTC": "bitcoin",
                "ETH": "ethereum",
                "SOL": "solana",
                "BNB": "binancecoin",
                "XRP": "ripple",
                "ADA": "cardano",
                "DOGE": "dogecoin",
                "AVAX": "avalanche-2",
                "DOT": "polkadot",
                "MATIC": "matic-network",
            }
            coin_id = coin_ids.get(asset.upper())
            if coin_id:
                url = (
                    "https://api.coingecko.com/api/v3/simple/price"
                    f"?ids={coin_id}&vs_currencies=usd"
                )
                timeout = aiohttp.ClientTimeout(total=10)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(url) as response:
                        if response.status == 200:
                            payload = await response.json()
                            price = payload.get(coin_id, {}).get("usd")
                            if price is not None and float(price) > 0:
                                return PriceFetchResult(
                                    ReflectionStatus.COMPLETED,
                                    float(price),
                                    None,
                                    trace_id,
                                )
        except Exception as exc:  # External provider boundary.
            provider_failures.append(f"coingecko:{type(exc).__name__}")
            log.warning(
                "event=reflection_price_provider_failed trace_id=%s asset=%s "
                "provider=coingecko error_type=%s",
                trace_id,
                asset,
                type(exc).__name__,
            )

        log.warning(
            "event=reflection_price_unavailable trace_id=%s asset=%s "
            "reason_code=PRICE_PROVIDERS_UNAVAILABLE providers=%s",
            trace_id,
            asset,
            ",".join(provider_failures[:4]) or "no_data",
        )
        return PriceFetchResult(
            ReflectionStatus.UNAVAILABLE,
            None,
            "PRICE_PROVIDERS_UNAVAILABLE",
            trace_id,
        )

    def _build_reflection_prompt(
        self,
        decision: TradingDecision,
        current_price: float,
        actual_return: float,
        alpha_return: Optional[float],
        days_passed: int,
    ) -> str:
        """Build a reflection prompt without asserting unavailable benchmark data."""
        entry_price = decision.initial_signal.entry_price or 0.0
        exit_price = decision.exit_price or current_price
        outcome = decision.outcome_label or "unknown"
        alpha_line = (
            f"{alpha_return * 100:+.2f}%"
            if alpha_return is not None
            else "UNAVAILABLE (ALPHA_BENCHMARK_DISABLED)"
        )

        return f"""You are reviewing a past trading decision for {decision.asset}.

**Original Decision (made {decision.timestamp.strftime('%Y-%m-%d')})**
- Action: {decision.initial_signal.action}
- Confidence: {decision.initial_signal.confidence:.0%}
- Entry Price: ${entry_price:.2f}
- Position Size: {decision.final_position_size_pct:.1f}%

**Executive Summary:**
{decision.executive_summary}

**Bull Case:**
{decision.bull_synthesis[:500] if decision.bull_synthesis else 'N/A'}

**Bear Case:**
{decision.bear_synthesis[:500] if decision.bear_synthesis else 'N/A'}

**Actual Outcome (after {days_passed} days)**
- Entry Price: ${entry_price:.2f}
- Exit Price: ${exit_price:.2f}
- Outcome: {outcome.upper()}
- Raw Return: {actual_return * 100:+.2f}%
- Alpha vs Benchmark: {alpha_line}

Write a ONE-PARAGRAPH honest reflection. Cover:
1. Was this call right or wrong? Why?
2. What signal was most predictive (or misleading)?
3. What one lesson should be carried forward to the next {decision.asset} analysis?

Be direct. No fluff. If the call was wrong, say so clearly."""


_reflection_engine: Optional[ReflectionEngine] = None


def get_reflection_engine() -> ReflectionEngine:
    """Get or create the reflection engine singleton."""
    global _reflection_engine
    if _reflection_engine is None:
        _reflection_engine = ReflectionEngine()
    return _reflection_engine
