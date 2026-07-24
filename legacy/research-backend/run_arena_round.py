"""
run_arena_round.py — Execute one round of Alpha Arena across all symbols.
Called by cron or manually. Generates leaderboard output.
"""
import asyncio
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
from runtime_paths import configured_env_file

# Load only the explicitly configured runtime environment file.
env_file = configured_env_file()
if env_file is not None:
    from dotenv import load_dotenv
    load_dotenv(env_file)

# Load encrypted secrets if available
try:
    from exchange.secrets import load_secrets_into_env
    load_secrets_into_env()
except Exception:
    pass

from openai import AsyncOpenAI

from alpha_arena import (
    start_round, record_signal, update_portfolio_pnl,
    update_arena_stats, format_leaderboard, ANALYST_NAMES,
)
from analysts import (
    TECHNICAL_SYSTEM, SENTIMENT_SYSTEM, ONCHAIN_SYSTEM, MACRO_SYSTEM,
)
from schemas import TradingSignal
from signal_parser import parse_structured, build_schema_prompt

COMPETITION_ID = 1
SYMBOLS = ["BTC", "ETH", "SOL"]

# Analyst system prompts — same as analysts.py
SYSTEM_PROMPTS = {
    "technical": TECHNICAL_SYSTEM,
    "sentiment": SENTIMENT_SYSTEM,
    "onchain": ONCHAIN_SYSTEM,
    "macro": MACRO_SYSTEM,
}


def fetch_prices() -> dict:
    """Fetch current prices from CoinGecko free API."""
    ids = "bitcoin,ethereum,solana"
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd&include_24hr_change=true&include_24hr_vol=true"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        print(f"⚠️  CoinGecko failed: {e}", file=sys.stderr)
        return {}

    mapping = {"bitcoin": "BTC", "ethereum": "ETH", "solana": "SOL"}
    prices = {}
    for cg_id, symbol in mapping.items():
        if cg_id in data:
            prices[symbol] = {
                "price": data[cg_id].get("usd") or 0,
                "change_24h": data[cg_id].get("usd_24h_change") or 0,
                "volume_24h": data[cg_id].get("usd_24h_vol") or 0,
                "high_24h": data[cg_id].get("usd_24h_high") or 0,
                "low_24h": data[cg_id].get("usd_24h_low") or 0,
            }
    return prices


async def get_signal(llm, analyst_type: str, symbol: str, market: dict) -> TradingSignal:
    """Ask an analyst-type LLM for a TradingSignal."""
    system = SYSTEM_PROMPTS[analyst_type]

    # Build market data lines, skipping zero/None values
    md_lines = []
    price = market.get('price', 0)
    if price:
        md_lines.append(f"- Current Price: ${price:,.2f}")
    chg = market.get('change_24h', 0)
    md_lines.append(f"- 24h Change: {chg:+.2f}%")
    vol = market.get('volume_24h', 0)
    if vol and vol > 0:
        md_lines.append(f"- 24h Volume: ${vol:,.0f}")
    hi = market.get('high_24h', 0)
    if hi and hi > 0:
        md_lines.append(f"- 24h High: ${hi:,.2f}")
    lo = market.get('low_24h', 0)
    if lo and lo > 0:
        md_lines.append(f"- 24h Low: ${lo:,.2f}")

    prompt = f"""Analyze {symbol} from your {analyst_type} perspective and produce a trading signal.

MARKET DATA:
{chr(10).join(md_lines)}

Based on your specialty ({analyst_type}), determine a trading signal.
Even in uncertain conditions, express your best directional view.
- action: BUY, SELL, HOLD, or WATCH (prefer BUY/SELL over WATCH when you have any edge)
- confidence: 0.0 to 1.0 (how sure are you?)
- entry_price: price you'd enter at (can be current price)
- stop_loss: price to cut losses
- take_profit: price target
- time_horizon: intraday, swing, or position
- reasoning: at least 20 chars explaining your decision

Respond with ONLY a valid JSON object matching the TradingSignal schema."""

    full_prompt = prompt + build_schema_prompt(TradingSignal)
    raw = await llm(full_prompt, system, task_type="analyst_reports")
    return parse_structured(llm, raw, TradingSignal, context_hint=f"{analyst_type} signal for {symbol}")


class LLMClient:
    """Async callable wrapper for openai.AsyncOpenAI."""
    def __init__(self, client, model="deepseek-chat"):
        self.client = client
        self.model = model

    async def __call__(self, prompt, system=None, task_type=None):
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        resp = await self.client.chat.completions.create(
            model=self.model, messages=messages, temperature=0.3, max_tokens=800,
        )
        return resp.choices[0].message.content


async def main():
    # 1. Get prices
    print("📡 Fetching prices from CoinGecko...", file=sys.stderr)
    prices = fetch_prices()
    if not prices:
        print("❌ No price data available. Aborting.", file=sys.stderr)
        return

    for sym in SYMBOLS:
        if sym not in prices or not prices[sym].get("price"):
            print(f"⚠️  No price for {sym}, skipping.", file=sys.stderr)
            continue
        print(f"  {sym}: ${prices[sym]['price']:,.2f} ({prices[sym]['change_24h']:+.2f}%)", file=sys.stderr)

    # 2. Set up LLM
    # Set up LLM — prioritize DeepSeek, then OpenAI, then Anthropic
    base_url = None
    model = "deepseek-chat"
    if os.environ.get("DEEPSEEK_API_KEY"):
        api_key = os.environ["DEEPSEEK_API_KEY"]
        base_url = "https://api.deepseek.com/v1"
    elif os.environ.get("OPENAI_API_KEY"):
        api_key = os.environ["OPENAI_API_KEY"]
        model = "gpt-4o-mini"
    elif os.environ.get("ANTHROPIC_API_KEY"):
        api_key = os.environ["ANTHROPIC_API_KEY"]
        model = "gpt-4o-mini"
    else:
        print("❌ No LLM API key found.", file=sys.stderr)
        return

    ac = AsyncOpenAI(api_key=api_key, base_url=base_url) if base_url else AsyncOpenAI(api_key=api_key)

    llm_raw = LLMClient(ac, model=model)
    print(f"🤖 Using LLM: {model}", file=sys.stderr)

    # 3. Process each symbol
    for symbol in SYMBOLS:
        if symbol not in prices or not prices[symbol].get("price"):
            continue

        market = prices[symbol]
        price = market["price"]

        print(f"\n{'='*50}", file=sys.stderr)
        print(f"🔄 {symbol} @ ${price:,.2f}", file=sys.stderr)

        # Start round
        try:
            round_id = start_round(COMPETITION_ID, symbol, price)
            print(f"  Round #{round_id} created", file=sys.stderr)
        except Exception as e:
            print(f"  ⚠️  Failed to create round: {e}", file=sys.stderr)
            continue

        # Process each analyst
        for analyst_name in ANALYST_NAMES:
            print(f"  🧠 {analyst_name} analyzing...", file=sys.stderr)
            try:
                signal = await get_signal(llm_raw, analyst_name, symbol, market)
                action = signal.action.upper() if hasattr(signal, 'action') else "HOLD"
                confidence = signal.confidence if hasattr(signal, 'confidence') else 0.0
                entry = signal.entry_price
                stop = signal.stop_loss
                tp = signal.take_profit
                reasoning = signal.reasoning if hasattr(signal, 'reasoning') else ""

                print(f"     → {action} (confidence: {confidence:.0%})", file=sys.stderr)
                if entry:
                    print(f"        Entry: ${entry:,.2f} | SL: ${stop:,.2f} | TP: ${tp:,.2f}" if stop and tp else f"        Entry: ${entry:,.2f}", file=sys.stderr)

                # Record signal
                record_signal(
                    round_id=round_id,
                    analyst=analyst_name,
                    action=action,
                    confidence=confidence,
                    entry_price=entry,
                    stop_loss=stop,
                    take_profit=tp,
                    reasoning=reasoning[:500] if reasoning else "",
                )

                # Update portfolio PnL
                if action in ("BUY", "SELL") and entry:
                    update_portfolio_pnl(
                        competition_id=COMPETITION_ID,
                        analyst=analyst_name,
                        signal_action=action,
                        entry_price=entry,
                        current_price=price,
                    )
            except Exception as e:
                print(f"     ❌ Failed: {e}", file=sys.stderr)
                # Record a HOLD as fallback
                try:
                    record_signal(
                        round_id=round_id,
                        analyst=analyst_name,
                        action="HOLD",
                        confidence=0.0,
                        reasoning=f"Analysis failed: {str(e)[:200]}",
                    )
                except Exception:
                    pass

    # 4. Update stats and print leaderboard
    print(f"\n{'='*50}", file=sys.stderr)
    print("📊 Computing stats...", file=sys.stderr)
    try:
        update_arena_stats(COMPETITION_ID)
    except Exception as e:
        print(f"  ⚠️  Stats update failed (non-fatal): {e}", file=sys.stderr)

    leaderboard = format_leaderboard(COMPETITION_ID)
    print(leaderboard, file=sys.stderr)
    return leaderboard


if __name__ == "__main__":
    result = asyncio.run(main())
    if result:
        print(result)
