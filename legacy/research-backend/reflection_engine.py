"""
reflection_engine.py — Auto-reflection for trading decisions.

Resolves pending decisions by fetching actual returns and generating LLM reflections.
Transforms the agent from amnesiac → learning system.

Phase 3 Update: Adds outcome-tagged reflection with entry/exit prices,
outcome labels (win/loss/breakeven), and resolve_outcome() function.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Literal

from schemas import TradingDecision
from memory import get_pending_reflections_typed, mark_decision_reflected, store_reflection

log = logging.getLogger("reflection_engine")


class ReflectionEngine:
    """
    Resolves pending decisions by fetching actual returns and generating LLM reflections.
    """

    REFLECTION_HORIZON_DAYS = 7  # Wait 7 days before reflecting

    async def reflect_on_pending(self, llm_client, horizon_days: int = None) -> int:
        """
        Find all pending decisions past horizon, fetch returns, generate reflections.

        Returns:
            Number of reflections processed.
        """
        horizon = horizon_days if horizon_days is not None else self.REFLECTION_HORIZON_DAYS
        pending = get_pending_reflections_typed(horizon_days=horizon)

        if not pending:
            log.info("[reflection] No pending decisions to reflect on")
            return 0

        log.info("[reflection] Found %d pending decisions (horizon=%d days)",
                 len(pending), horizon)

        processed = 0
        for entry_date, decision in pending:
            try:
                # Fetch current price (stub — integrate with price API)
                current_price = await self._fetch_current_price(decision.asset)

                # Resolve outcome (set exit_price, outcome_label, resolved_at)
                resolved = await self.resolve_outcome(decision, current_price)

                # Generate LLM reflection (only if exit_price is set)
                if resolved.exit_price is not None:
                    actual_return = resolved.pnl_pct or 0.0

                    # Generate LLM reflection
                    await self.reflect_single(
                        llm_client, resolved, current_price, actual_return
                    )

                    # Mark as reflected
                    mark_decision_reflected(decision.timestamp, decision.asset)
                    processed += 1
                else:
                    log.warning("[reflection] Cannot reflect on %s %s: exit_price not set",
                               decision.asset, decision.timestamp)

            except Exception as e:
                log.error("[reflection] Failed to process %s %s: %s",
                          decision.asset, decision.timestamp, e)

        log.info("[reflection] Processed %d/%d reflections",
                 processed, len(pending))
        return processed

    async def resolve_outcome(
        self,
        decision: TradingDecision,
        current_price: float,
    ) -> TradingDecision:
        """
        Resolve a trading decision by setting exit_price, pnl_pct, outcome_label, and resolved_at.

        This function should be called when a position is closed or when
        we want to mark the current unrealized outcome.

        Args:
            decision: Trading decision with entry_price set
            current_price: Current market price

        Returns:
            Updated TradingDecision with outcome fields populated
        """
        entry_price = decision.initial_signal.entry_price

        if entry_price is None or entry_price <= 0:
            log.warning("[reflection] Cannot resolve outcome for %s: invalid entry_price",
                       decision.asset)
            return decision

        # Calculate PnL based on action
        action = decision.initial_signal.action
        if action == "BUY":
            pnl_pct = (current_price - entry_price) / entry_price
        elif action == "SELL":
            pnl_pct = (entry_price - current_price) / entry_price
        else:
            # HOLD or other: no position taken
            pnl_pct = 0.0

        # Determine outcome label
        if pnl_pct > 0.01:  # > 1% profit
            outcome_label = "win"
        elif pnl_pct < -0.01:  # < -1% loss
            outcome_label = "loss"
        else:
            outcome_label = "breakeven"

        # Update decision with outcome fields
        decision.exit_price = current_price
        decision.pnl_pct = pnl_pct
        decision.outcome_label = outcome_label
        decision.resolved_at = datetime.now(timezone.utc)

        log.info("[reflection] Resolved outcome for %s: entry=$%.2f, exit=$%.2f, pnl=%.2f%%, outcome=%s",
                 decision.asset, entry_price, current_price, pnl_pct * 100, outcome_label)

        return decision

    async def reflect_single(
        self,
        llm_client,
        decision: TradingDecision,
        current_price: float,
        actual_return: float,
    ):
        """
        Calculate actual return, generate LLM reflection, store it.

        NOTE: This should only be called AFTER resolve_outcome() has been called
        and exit_price is set.
        """
        if decision.exit_price is None:
            log.warning("[reflection] reflect_single called but exit_price not set for %s",
                       decision.asset)
            return

        entry_price = decision.initial_signal.entry_price or 0.0
        days_passed = (datetime.now(timezone.utc) - decision.timestamp).days

        # Calculate alpha vs benchmark (stub — BTC as benchmark for crypto)
        alpha_return = actual_return  # TODO: fetch benchmark return

        # Build reflection prompt
        prompt = self._build_reflection_prompt(
            decision=decision,
            current_price=current_price,
            actual_return=actual_return,
            alpha_return=alpha_return,
            days_passed=days_passed,
        )

        # Call LLM
        system_msg = "You are a trading performance analyst reviewing past decisions."
        reflection_text = await llm_client(prompt, system=system_msg, task_type="reflection")

        # Store reflection
        store_reflection(
            ticker=decision.asset,
            trade_date=decision.timestamp.strftime("%Y-%m-%d"),
            suggestion=decision.initial_signal.action,
            confidence=decision.initial_signal.confidence,
            raw_return_pct=actual_return * 100,
            alpha_return_pct=alpha_return * 100,
            holding_days=days_passed,
            reflection=reflection_text,
        )

        log.info("[reflection] Generated reflection for %s: return=%.2f%%, alpha=%.2f%%, outcome=%s",
                 decision.asset, actual_return * 100, alpha_return * 100,
                 decision.outcome_label or "unknown")

    async def _fetch_current_price(self, asset: str) -> float:
        """
        Fetch current price from yfinance.
        Falls back to CoinGecko if yfinance fails.
        """
        # Try yfinance first
        try:
            import yfinance as yf
            ticker_map = {
                "BTC": "BTC-USD", "ETH": "ETH-USD", "SOL": "SOL-USD",
                "TON": "TON11419-USD", "DOGE": "DOGE-USD", "ADA": "ADA-USD",
                "AVAX": "AVAX-USD", "DOT": "DOT-USD", "LINK": "LINK-USD",
                "MATIC": "MATIC-USD",
            }
            ticker = ticker_map.get(asset.upper(), asset)
            t = yf.Ticker(ticker)
            info = t.fast_info
            price = getattr(info, 'last_price', None) or getattr(info, 'regular_market_previous_close', None)
            if price:
                return float(price)
        except Exception:
            pass

        # Fallback: CoinGecko
        try:
            import aiohttp
            cg_id_map = {
                "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
                "TON": "the-open-network", "DOGE": "dogecoin", "ADA": "cardano",
                "AVAX": "avalanche-2", "DOT": "polkadot", "LINK": "chainlink",
                "MATIC": "matic-network",
            }
            cg_id = cg_id_map.get(asset.upper(), asset.lower())
            url = f"https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=usd"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    data = await resp.json()
                    price = data.get(cg_id, {}).get("usd")
                    if price:
                        return float(price)
        except Exception:
            pass

        log.warning("[reflection] Price fetch failed for %s", asset)
        return 0.0

    def _build_reflection_prompt(
        self,
        decision: TradingDecision,
        current_price: float,
        actual_return: float,
        alpha_return: float,
        days_passed: int,
    ) -> str:
        """Build reflection prompt for LLM."""
        entry_price = decision.initial_signal.entry_price or 0.0
        exit_price = decision.exit_price or current_price
        outcome = decision.outcome_label or "unknown"

        prompt = f"""You are reviewing a past trading decision for {decision.asset}.

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
- Alpha vs Benchmark: {alpha_return * 100:+.2f}%

Write a ONE-PARAGRAPH honest reflection. Cover:
1. Was this call right or wrong? Why?
2. What signal was most predictive (or misleading)?
3. What one lesson should be carried forward to the next {decision.asset} analysis?

Be direct. No fluff. If the call was wrong, say so clearly."""

        return prompt


# Singleton instance
_reflection_engine: Optional[ReflectionEngine] = None


def get_reflection_engine() -> ReflectionEngine:
    """Get or create the reflection engine singleton."""
    global _reflection_engine
    if _reflection_engine is None:
        _reflection_engine = ReflectionEngine()
    return _reflection_engine
