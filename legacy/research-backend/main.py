"""
main.py
-------
Entry point for the crypto trading research agent.
Wires: data_collector → ta_engine → debate → assembly → risk_personas → prompts → memory

v3.0 — Now with Dexter-inspired features:
  - Scratchpad audit trail: JSONL log of every decision
  - Task decomposition: dynamic research planning
  - SOUL.md: agent identity + investment philosophy
  - Memory/reflection loop: learns from past trade outcomes
  - Bull/Bear debate: adversarial check before committing
  - 3-way risk personas: aggressive, conservative, neutral risk review

Usage:
  python main.py                        # single snapshot run, prints full report
  python main.py --mode poll            # continuous polling loop
  python main.py --mode brief           # morning brief prompt only
  python main.py --symbol BTC           # single asset snapshot
  python main.py --mode entry --symbol ETH  # entry check for one asset
  python main.py --mode debate --symbol SOL  # full debate + risk personas
  python main.py --mode reflect         # process pending reflections
  python main.py --mode plan --question "Is SOL overbought?"  # custom research plan
  python main.py --mode replay --session <id>  # replay a scratchpad session
"""

import asyncio
import argparse
import importlib
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

# BEGIN ISOLATED SEALED BACKEND IMPORT BOOTSTRAP
def _bootstrap_isolated_backend_imports() -> None:
    """Restore only the attested script root removed by Python ``-I``."""

    if not sys.flags.isolated:
        return
    entrypoint = Path(__file__)
    try:
        if entrypoint.is_symlink():
            raise RuntimeError("isolated backend entrypoint cannot be a symlink")
        resolved_entrypoint = entrypoint.resolve(strict=True)
        resolved_cwd = Path.cwd().resolve(strict=True)
    except OSError as exc:
        raise RuntimeError("isolated backend entrypoint cannot be resolved") from exc
    backend_root = resolved_entrypoint.parent
    if resolved_entrypoint.name != "main.py" or resolved_cwd != backend_root:
        raise RuntimeError("isolated backend must run from its sealed script root")
    backend_root_text = str(backend_root)
    sys.path[:] = [item for item in sys.path if item != backend_root_text]
    sys.path.insert(0, backend_root_text)


_bootstrap_isolated_backend_imports()
del _bootstrap_isolated_backend_imports
# END ISOLATED SEALED BACKEND IMPORT BOOTSTRAP

# Worker-owned environment must be validated before importing legacy modules.
from job_attribution import (
    ResearchInvocation,
    ResearchInvocationError,
    bootstrap_strict_worker_invocation,
    build_replay_sidecar,
    load_worker_replay,
    resolve_research_invocation,
    with_lineage,
    write_json_exclusive,
)

STRICT_WORKER_INVOCATION = bootstrap_strict_worker_invocation()

from data_collector import collect_all, polling_loop, POLL_INTERVALS
from ta_engine import calculate_indicators
from assembly import assemble_full_report, assemble_asset_json
from research_semantics import SnapshotSemanticInputs, load_snapshot_semantic_inputs
from runtime_paths import configured_env_file, data_root, reports_dir
from derivatives_collector import fetch_derivatives
from regime_detector import detect_regime
from model_config import get_model_config
from prompts import (
    morning_brief,
    entry_check,
    risk_scan,
    weekly_recap,
    full_pipeline,
)

# ── New Dexter-inspired modules ────────────────────────────────────────────────
from scratchpad import Scratchpad, render_session, replay_session, list_recent_sessions
from planner import (
    build_snapshot_plan,
    build_custom_plan,
    format_plan,
    validate_data_completeness,
    validate_consistency,
)
from signal_parser import parse_report, parse_asset_json, TradingSignal, parse_structured, build_schema_prompt
from schemas import TradingDecision as TypedDecision, TradingSignal as TypedSignal, DebateRound, RiskAssessment, BullArgument, BearArgument

# ── Phase 3: Analyst Layer ─────────────────────────────────────────────────────
from analysts import AnalystCoordinator
from portfolio_manager import PortfolioManager

# ── Existing modules (from v2.0) ──────────────────────────────────────────────
from signal_quality import score_decision

from memory import (
    store_decision,
    build_memory_context,
    get_pending_reflections,
    mark_reflected,
    store_reflection,
    build_reflection_prompt,
    store_typed_decision,
    get_typed_decisions,
    get_memory_for_bull,
    get_memory_for_bear,
)
from reflection_engine import ReflectionStatus, get_reflection_engine
from memory_search import build_enriched_context
from debate import (
    build_debate_prompts,
    parse_judge_verdict,
    format_debate_context,
    format_bull_bear_for_prompt,
    AdversarialDebate,
    DebateConfig,
)
from risk_personas import (
    build_persona_prompts,
    build_synthesis_prompt,
    parse_risk_synthesis,
    format_risk_context,
    format_three_personas_for_prompt,
    RiskDebate,
    RiskDebateConfig,
)

# ── Logging ───────────────────────────────────────────────────────────────────

LOG_DIR = data_root() / "logs"
if STRICT_WORKER_INVOCATION is None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

_LOG_HANDLERS: list[logging.Handler] = [logging.StreamHandler()]
if STRICT_WORKER_INVOCATION is None:
    _LOG_HANDLERS.insert(0, logging.FileHandler(LOG_DIR / "main.log"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=_LOG_HANDLERS,
)
log = logging.getLogger("main")

REPORT_DIR = reports_dir()
if STRICT_WORKER_INVOCATION is None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

# ── Config ────────────────────────────────────────────────────────────────────

WATCHLIST = ["BTC", "ETH", "SOL", "TON", "DOGE", "ADA", "AVAX", "DOT", "LINK", "MATIC"]
STOCK_WATCHLIST = ["AAPL", "NVDA", "MSFT", "GOOGL", "AMZN", "META", "TSLA"]
ETF_WATCHLIST = ["SPY", "QQQ"]
FOREX_WATCHLIST = ["EURUSD=X"]


def is_us_market_open() -> bool:
    """True if NYSE is currently open (Mon-Fri 9:30-16:00 ET)."""
    try:
        import zoneinfo
        from datetime import time as dtime
        et = datetime.now(zoneinfo.ZoneInfo("America/New_York"))
    except ImportError:
        from datetime import time as dtime
        import pytz
        et = datetime.now(pytz.timezone("America/New_York"))
    if et.weekday() >= 5:
        return False
    return dtime(9, 30) <= et.time() <= dtime(16, 0)


async def fetch_sentiment(symbol: str, allow_shared_env: bool = True) -> dict:
    """Fetch sentiment via sentiment_collector (VADER) with Exa + DeepSeek fallback."""
    try:
        from sentiment_collector import get_sentiment_for_asset
        result = get_sentiment_for_asset(symbol)
        if result.get("sentiment") is not None:
            return result
    except Exception as e:
        log.debug("sentiment_collector failed for %s: %s, trying Exa", symbol, e)

    if allow_shared_env:
        try:
            from sentiment_filter import fetch_sentiment as _fetch_sentiment_real
            return await _fetch_sentiment_real(symbol)
        except Exception as e:
            log.warning("Sentiment fetch failed for %s: %s, using null fallback", symbol, e)

    return {
        "sentiment": None,
        "sentiment_source": "unavailable — no configured research provider",
    }


async def fetch_onchain_risk(symbol: str) -> dict:
    """Fetch on-chain risk data from onchain_collector, with graceful fallback."""
    try:
        from onchain_collector import get_onchain_for_asset
        result = get_onchain_for_asset(symbol)
        if result.get("onchain_risk") is not None:
            return result
    except Exception as e:
        log.debug("onchain_collector lookup failed for %s: %s", symbol, e)

    return {
        "onchain_risk": None,
        "onchain_source": "unavailable — no onchain data",
    }


def _compact_for_llm(text: str, max_chars: int = 40000) -> str:
    """Compact large prompt text for LLM consumption.

    Strips JSON indentation whitespace and caps verbose text fields in asset
    reports. Preserves all assets and signal data. Never corrupts content.
    """
    if len(text) <= max_chars:
        return text

    try:
        import json as _json

        # Find JSON blocks by tracking brace depth, then compact each one.
        # Build result by stitching together non-JSON runs and compacted blocks.
        json_starts = []  # stack of (start_pos, depth_at_start)
        depth = 0

        for i, ch in enumerate(text):
            if ch == "{":
                if depth == 0:
                    json_starts.append(i)
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and json_starts:
                    json_starts.pop()  # balance restored (but not used here)

        # Rescan to collect block ranges
        blocks = []  # (start, end_exclusive)
        depth = 0
        block_start = -1
        for i, ch in enumerate(text):
            if ch == "{":
                if depth == 0:
                    block_start = i
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and block_start >= 0:
                    blocks.append((block_start, i + 1))
                    block_start = -1

        # Build result: interleave non-JSON text with compacted JSON blocks
        result = []
        prev_end = 0
        for start, end in blocks:
            # Append text between previous block and this one
            result.append(text[prev_end:start])
            block = text[start:end]
            try:
                parsed = _json.loads(block)
                if isinstance(parsed, dict) and "assets" in parsed:
                    for asset in parsed.get("assets", []):
                        if isinstance(asset, dict):
                            _strip_verbose_fields(asset)
                elif isinstance(parsed, dict) and "symbol" in parsed:
                    _strip_verbose_fields(parsed)
                compact = _json.dumps(parsed, separators=(",", ":"), default=str)
                result.append(compact)
            except (_json.JSONDecodeError, ValueError, TypeError):
                result.append(block)  # keep original if parsing fails
            prev_end = end

        # Append trailing text after the last block
        result.append(text[prev_end:])
        text = "".join(result)
    except Exception:
        pass

    return text


def _strip_verbose_fields(asset: dict) -> None:
    """Strip verbose context fields from an asset dict in-place.

    Keeps signal data (symbol, price, suggestion, confidence, rsi, macd, etc.)
    Caps long text fields at 300 chars to save tokens.
    """
    VERBOSE_FIELDS = (
        "_memory_context", "_debate_context", "_risk_context",
        "_portfolio_context", "reasoning", "rationale",
        "sentiment_summary",
    )
    MAX_FIELD_LEN = 300

    for field in VERBOSE_FIELDS:
        val = asset.get(field)
        if isinstance(val, str) and len(val) > MAX_FIELD_LEN:
            asset[field] = val[:MAX_FIELD_LEN].rsplit(" ", 1)[0] + "..."

    # Drop entirely redundant verbose nested structures
    for key in ("_debate_rounds", "_risk_assessments"):
        if key in asset:
            del asset[key]


# ── LLM Stub ──────────────────────────────────────────────────────────────────
# Replace with real LLM call (DeepSeek/OpenAI/Anthropic) in production.

async def call_llm(
    prompt: str,
    system: str = "",
    task_type: str = "default",
    allow_shared_env: bool = True,
    **kwargs,
) -> str:
    """
    Call LLM API with model routing based on task type.

    Args:
        prompt: User prompt
        system: System message
        task_type: Task type for model selection (data_summarization, bull_bear_debate, etc.)
        **kwargs: Additional LLM parameters (response_format, etc.)
    """
    from model_config import TaskType

    # Convert string to TaskType enum if needed
    if isinstance(task_type, str):
        try:
            task_type = TaskType(task_type.lower())
        except ValueError:
            task_type = TaskType.DEFAULT

    config = get_model_config()
    # Access by attribute name matching the enum value
    model_attr = task_type.value.lower()
    model = getattr(config, model_attr, config.default)

    try:
        from openai import AsyncOpenAI
        if allow_shared_env:
            env_file = configured_env_file()
            if env_file is not None:
                from dotenv import load_dotenv
                load_dotenv(env_file)

        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            log.warning("DEEPSEEK_API_KEY not set, returning stub")
            return f"[NO API KEY] Would call {model} for {task_type}"

        client = AsyncOpenAI(
            base_url="https://api.deepseek.com/v1",
            api_key=api_key,
        )

        messages = []
        if system:
            messages.append({"role": "system", "content": system})

        # RTK prompt compression for large reports (>25KB)
        prompt = _compact_for_llm(prompt)
        messages.append({"role": "user", "content": prompt})

        extra = {}
        if kwargs.get("response_format"):
            extra["response_format"] = kwargs["response_format"]

        logger_module_name = os.getenv("TRADING_LLM_LOGGER_MODULE", "").strip()
        log_llm_call = None
        if STRICT_WORKER_INVOCATION is None and logger_module_name:
            try:
                logger_module = importlib.import_module(logger_module_name)
                candidate = getattr(logger_module, "log_llm_call")
                if callable(candidate):
                    log_llm_call = candidate
            except Exception:
                log.warning("Configured LLM logger unavailable; using standard logging")

        if log_llm_call is None:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                **extra,
            )
            return response.choices[0].message.content

        with log_llm_call(provider="deepseek", model=model, agent="trading-pipeline", mode=str(task_type.value)) as llm_log:
            response = await client.chat.completions.create(
                model=model, messages=messages, **extra,
            )
            usage = response.usage
            if usage:
                llm_log.tokens_in = usage.prompt_tokens or 0
                llm_log.tokens_out = usage.completion_tokens or 0
                # DeepSeek pricing: $0.27/1M input, $1.10/1M output
                llm_log.cost_usd = (usage.prompt_tokens * 0.27 + usage.completion_tokens * 1.10) / 1_000_000
            llm_log.status = 200

        return response.choices[0].message.content
    except Exception as e:
        log.error("LLM call failed (model=%s, task=%s): %s", model, task_type, e)
        return f"[LLM ERROR: {e}]"


# ── Core pipeline ─────────────────────────────────────────────────────────────

async def run_pipeline(
    symbols: list[str],
    enable_debate: bool = False,
    enable_risk_personas: bool = False,
    pad: Scratchpad | None = None,
    allow_execution: bool = False,
    semantic_inputs: SnapshotSemanticInputs | None = None,
) -> dict:
    """Full single-pass pipeline with optional debate, risk personas, and audit trail."""
    paper_execute = None
    broker_execute = None
    send_telegram_text = None
    process_alert_signals = None
    live_execute = None
    get_execution_mode = None

    from kill_switch import is_kill_switch_active
    if is_kill_switch_active():
        log.warning("── Kill switch active — pipeline aborted ──")
        return {"status": "kill_switch_active", "assets": []}

    if allow_execution:
        from paper_trader import execute_signal as paper_execute, check_stops
        from broker import execute as broker_execute
        from alert_manager import send_telegram_text, process_alerts as process_alert_signals
        from execute_live import execute_signal as live_execute
        from exchange.ccxt_bridge import get_mode as get_execution_mode

    async def pipeline_call_llm(*args, **kwargs):
        return await call_llm(*args, allow_shared_env=allow_execution, **kwargs)

    log.info("── Pipeline start: %s (debate=%s, risk_personas=%s) ──",
             symbols, enable_debate, enable_risk_personas)
    optional_source_statuses: list[dict] = []
    operational_failures: list[dict] = []

    def record_optional_source(
        source: str,
        *,
        status: str,
        reason_code: str | None = None,
        error_type: str | None = None,
    ) -> dict:
        entry = {
            "source": source,
            "status": status,
            "reason_code": reason_code,
            "trace_id": uuid4().hex[:16],
            "error_type": error_type,
        }
        optional_source_statuses.append(entry)
        return entry

    def record_operational_failure(
        source: str,
        *,
        reason_code: str,
        error_type: str,
        trace_id: str | None = None,
    ) -> dict:
        entry = {
            "source": source,
            "status": "UNAVAILABLE",
            "reason_code": reason_code,
            "trace_id": trace_id or uuid4().hex[:16],
            "error_type": error_type,
        }
        operational_failures.append(entry)
        return entry

    if pad is None:
        pad = Scratchpad(query=f"Research: {', '.join(symbols)}")
    pad.init_session(query=f"Research: {', '.join(symbols)}", symbols=symbols)

    # Step 0.5: sweep open positions for stop-loss / trailing-stop exits
    # Done before collecting new data so stale positions are closed first.
    # Uses last-known prices from the most recent report (paper_trader._load_report_prices).
    if allow_execution:
        from alert_manager import send_telegram_text as send_stop_alert
        from paper_trader import (
            PortfolioStateError,
            _load_report_prices,
            check_stops as paper_check_stops,
        )

        last_prices = _load_report_prices()
        if last_prices.status != "AVAILABLE":
            log.error(
                "event=stop_sweep_unavailable trace_id=%s reason_code=%s",
                last_prices.trace_id,
                last_prices.reason_code,
            )
            return {
                "status": "unavailable",
                "reason_code": last_prices.reason_code,
                "trace_id": last_prices.trace_id,
                "assets": [],
            }
        try:
            stopped_out = paper_check_stops(dict(last_prices))
        except PortfolioStateError as exc:
            log.error(
                "event=stop_sweep_unavailable trace_id=%s reason_code=%s",
                exc.trace_id,
                exc.reason_code,
            )
            return {
                "status": "unavailable",
                "reason_code": exc.reason_code,
                "trace_id": exc.trace_id,
                "assets": [],
            }
        if stopped_out.status != "COMPLETED":
            log.error(
                "event=stop_sweep_partial trace_id=%s reason_code=%s symbols=%s",
                stopped_out.trace_id,
                stopped_out.reason_code,
                ",".join(stopped_out.unavailable_symbols[:16]),
            )
            return {
                "status": "unavailable",
                "reason_code": stopped_out.reason_code,
                "trace_id": stopped_out.trace_id,
                "assets": [],
            }
        if stopped_out:
            syms_out = [order["symbol"] for order in stopped_out]
            log.info(
                "Stop sweep closed %d position(s): %s",
                len(stopped_out),
                syms_out,
            )
            for order in stopped_out:
                try:
                    send_stop_alert(
                        f"🛑 [{order['exit_reason'].upper()}] {order['symbol']} closed "
                        f"{order['shares']:.4f} @ ${order['fill_price']:,.2f} | "
                        f"PnL ${order['pnl']:+,.2f}"
                    )
                except Exception as exc:
                    log.warning(
                        "event=stop_alert_delivery_failed symbol=%s error_type=%s",
                        order["symbol"],
                        type(exc).__name__,
                    )

    # Step 1 pre-run: refresh external signal sources. These three sources can
    # touch exchange/trading credentials, so research-only runs omit them.
    if allow_execution:
        try:
            from adanos_collector import collect as _adanos_collect
            _adanos_collect()
            source_entry = record_optional_source("adanos", status="AVAILABLE")
            log.info(
                "event=optional_source_available source=adanos trace_id=%s",
                source_entry["trace_id"],
            )
        except Exception as exc:  # Optional collector boundary.
            source_entry = record_optional_source(
                "adanos",
                status="UNAVAILABLE",
                reason_code="ADANOS_REFRESH_FAILED",
                error_type=type(exc).__name__,
            )
            log.warning(
                "event=optional_source_unavailable source=adanos "
                "trace_id=%s reason_code=ADANOS_REFRESH_FAILED error_type=%s",
                source_entry["trace_id"],
                type(exc).__name__,
            )
        try:
            from kalshi_collector import collect as _kalshi_collect
            _kalshi_collect()
            source_entry = record_optional_source("kalshi", status="AVAILABLE")
            log.info(
                "event=optional_source_available source=kalshi trace_id=%s",
                source_entry["trace_id"],
            )
        except Exception as exc:  # Optional collector boundary.
            source_entry = record_optional_source(
                "kalshi",
                status="UNAVAILABLE",
                reason_code="KALSHI_REFRESH_FAILED",
                error_type=type(exc).__name__,
            )
            log.warning(
                "event=optional_source_unavailable source=kalshi "
                "trace_id=%s reason_code=KALSHI_REFRESH_FAILED error_type=%s",
                source_entry["trace_id"],
                type(exc).__name__,
            )
        try:
            from orderflow_collector import collect as _of_collect
            _of_collect()
            source_entry = record_optional_source("orderflow", status="AVAILABLE")
            log.info(
                "event=optional_source_available source=orderflow trace_id=%s",
                source_entry["trace_id"],
            )
        except Exception as exc:  # Optional collector boundary.
            source_entry = record_optional_source(
                "orderflow",
                status="UNAVAILABLE",
                reason_code="ORDERFLOW_REFRESH_FAILED",
                error_type=type(exc).__name__,
            )
            log.warning(
                "event=optional_source_unavailable source=orderflow "
                "trace_id=%s reason_code=ORDERFLOW_REFRESH_FAILED error_type=%s",
                source_entry["trace_id"],
                type(exc).__name__,
            )
    if allow_execution:
        try:
            from polymarket_collector import collect as _pm_collect
            _pm_collect()
            source_entry = record_optional_source("polymarket", status="AVAILABLE")
            log.info(
                "event=optional_source_available source=polymarket trace_id=%s",
                source_entry["trace_id"],
            )
        except Exception as exc:  # Optional collector boundary.
            source_entry = record_optional_source(
                "polymarket",
                status="UNAVAILABLE",
                reason_code="POLYMARKET_REFRESH_FAILED",
                error_type=type(exc).__name__,
            )
            log.warning(
                "event=optional_source_unavailable source=polymarket "
                "trace_id=%s reason_code=POLYMARKET_REFRESH_FAILED error_type=%s",
                source_entry["trace_id"],
                type(exc).__name__,
            )

    # Step 1: collect raw data
    pad.log_thinking("Starting data collection phase", "planning")
    raw_assets = await collect_all(symbols, allow_exchange=allow_execution)
    pad.log_tool_call("data_collector.collect_all", {"symbols": symbols},
                      llm_summary=f"Collected data for {len(raw_assets)} assets")

    # Step 1.5: inject enriched memory context for each symbol
    memory_contexts = {}
    for sym in symbols:
        mc = build_memory_context(sym)  # Legacy reflections
        enriched_mc = build_enriched_context(sym)  # New typed search
        combined_mc = mc + enriched_mc
        if combined_mc:
            memory_contexts[sym] = combined_mc
            log.info("[%s] Memory context loaded (%d chars)", sym, len(combined_mc))

    # Steps 2–5: TA → debate → personas → assemble per asset
    assembled_assets = []
    for sym in symbols:
        raw = raw_assets.get(sym, {})
        ohlcv = raw.get("ohlcv")

        # Step 2: technical analysis
        ta = None
        if ohlcv:
            ta = calculate_indicators(ohlcv, sym)
            if ta is None:
                log.warning("[%s] TA returned None — likely <200 candles", sym)
        else:
            log.warning("[%s] No OHLCV — TA skipped", sym)

        # Step 2.5: memory context prepends to analysis
        memory_ctx = memory_contexts.get(sym, "")

        # Step 2.7: Regime detection (before signal resolution)
        regime_result = None
        if ohlcv:
            regime_result = detect_regime(ohlcv, sym)

        # Step 3: Enrichment data fetch
        if semantic_inputs is None:
            sentiment_data = await fetch_sentiment(sym, allow_shared_env=allow_execution)
            onchain_data = await fetch_onchain_risk(sym)
        else:
            sentiment_data = semantic_inputs.sentiment_for(sym)
            onchain_data = semantic_inputs.onchain_for(sym)
        derivatives_data = await fetch_derivatives(
            sym,
            price_change_24h_pct=raw.get("price_change_24h_pct"),
            allow_exchange=allow_execution,
        )

        # Assemble initial asset JSON
        asset_kwargs = dict(
            raw_data=raw,
            ta_data=ta,
            sentiment_data=sentiment_data,
            onchain_data=onchain_data,
            derivatives_data=derivatives_data,
            regime_result=regime_result,
        )
        if semantic_inputs is not None:
            asset_kwargs["macro_regime_override"] = semantic_inputs.macro_regime
        asset_json = assemble_asset_json(**asset_kwargs)

        # GAP 1: ATR-based default stop-loss / take-profit fallback
        atr_14 = asset_json.get("atr_14", 0)
        current_price = asset_json.get("current_price", 0) or asset_json.get("price", 0)
        if current_price and atr_14:
            if asset_json.get("stop_loss_suggestion") is None:
                asset_json["stop_loss_suggestion"] = round(current_price - (atr_14 * 2), 2)
            if asset_json.get("target_suggestion") is None:
                asset_json["target_suggestion"] = round(current_price + (atr_14 * 3), 2)

        # Step 3.5: Run analyst reports (BEFORE debate)
        analyst_context = ""
        analyst_reports = None
        if enable_debate:
            try:
                # Build market data dict
                market_data_dict = {
                    "asset": sym,
                    "price": raw.get("price") or raw.get("current_price"),
                    "volume_24h": raw.get("volume_24h", "N/A"),
                    "change_24h": raw.get("change_24h", "N/A"),
                }
                if ta:
                    if "rsi" in ta:
                        market_data_dict["rsi"] = ta["rsi"]
                    if "macd" in ta:
                        market_data_dict["macd"] = ta["macd"]

                # Run all four analysts
                analysts_coordinator = AnalystCoordinator(
                    pipeline_call_llm, allow_exchange=allow_execution
                )
                technical, sentiment, onchain, macro = await analysts_coordinator.analyze_all(
                    sym,
                    market_data_dict,
                    ta,
                    sentiment_data,
                    macro_snapshot=(
                        dict(semantic_inputs.macro_snapshot)
                        if semantic_inputs is not None
                        else None
                    ),
                )
                analyst_reports = {
                    "technical": technical,
                    "sentiment": sentiment,
                    "onchain": onchain,
                    "macro": macro,
                }

                # Format for debate context
                analyst_context = analysts_coordinator.format_for_debate(
                    technical, sentiment, onchain, macro
                )

                log.info("[%s] Analyst reports complete", sym)

            except Exception as e:
                log.warning("[%s] Analyst reports failed, continuing without: %s", sym, e)

        # Step 4: Bull/Bear debate (if enabled)
        debate_context = ""
        bull_synth = ""
        bear_synth = ""
        debate_rounds = []
        typed_signal = None

        if enable_debate and asset_json.get("suggestion") not in (None, "wait"):
            # Build TradingSignal Pydantic model
            try:
                # Normalize action
                raw_action = str(asset_json.get("suggestion", "HOLD")).upper()
                if "WATCH" in raw_action or "ENTRY" in raw_action:
                    action = "WATCH"
                elif "BUY" in raw_action:
                    action = "BUY"
                elif "SELL" in raw_action or "SHORT" in raw_action:
                    action = "SELL"
                else:
                    action = "HOLD"

                typed_signal = TypedSignal(
                    asset=sym,
                    action=action,
                    confidence=float(asset_json.get("confidence", 0.5)),
                    entry_price=asset_json.get("price"),
                    stop_loss=asset_json.get("stop_loss_suggestion"),
                    reasoning=asset_json.get("rationale", "No rationale provided")[:500],
                )

                # Build market data dict
                market_data_dict = {
                    "asset": sym,
                    "price": raw.get("price") or raw.get("current_price"),
                    "volume_24h": raw.get("volume_24h", "N/A"),
                    "change_24h": raw.get("change_24h", "N/A"),
                }
                if ta:
                    if "rsi" in ta:
                        market_data_dict["rsi"] = ta["rsi"]
                    if "macd" in ta:
                        market_data_dict["macd"] = ta["macd"]

                # Get per-agent memory for bull/bear
                bull_memory = get_memory_for_bull(sym, limit=3)
                bear_memory = get_memory_for_bear(sym, limit=3)

                # Inject analyst reports and memory into debate
                # (This is handled inside debate.py via the market_data and signal)

                # Run adversarial debate
                debate_config = DebateConfig(rounds=2)
                debate = AdversarialDebate(
                    pipeline_call_llm,
                    debate_config,
                    allow_kalshi=allow_execution,
                    macro_context_override=(
                        semantic_inputs.debate_macro_context
                        if semantic_inputs is not None
                        else None
                    ),
                )

                # Note: Memory injection happens inside debate.run()
                # For now, we prepend it to the signal reasoning
                if bull_memory:
                    typed_signal.reasoning = f"BULL PAST MISTAKES:\n{bull_memory}\n\nOriginal: {typed_signal.reasoning}"
                if bear_memory:
                    typed_signal.reasoning = f"{typed_signal.reasoning}\n\nBEAR PAST MISTAKES:\n{bear_memory}"

                debate_rounds, bull_synth, bear_synth = await debate.run(
                    sym, market_data_dict, typed_signal
                )

                # Format for prompt (backward compatible)
                debate_context = f"""
## Adversarial Debate Results — {sym}

**Analyst Reports:**
{analyst_context[:500] if analyst_context else 'N/A'}

**Bull Synthesis:**
{bull_synth[:500]}

**Bear Synthesis:**
{bear_synth[:500]}

**Debate Rounds:** {len(debate_rounds)}
"""

                log.info("[%s] AdversarialDebate complete — %d rounds, bull=%d chars, bear=%d chars",
                         sym, len(debate_rounds), len(bull_synth), len(bear_synth))

            except Exception as e:
                log.warning("[%s] AdversarialDebate failed, falling back to legacy: %s", sym, e)
                # Fallback to legacy debate
                price = asset_json.get("price", 0)
                suggestion = asset_json.get("suggestion", "wait")
                confidence = asset_json.get("confidence", 0.5)

                ta_summary = json.dumps(ta, indent=2)[:500] if ta else "No TA data"
                market_ctx = json.dumps({
                    "volume_24h": raw.get("volume_24h", "N/A"),
                    "market_cap": raw.get("market_cap", "N/A"),
                    "change_24h": raw.get("change_24h", "N/A"),
                }, indent=2)

                prompts = build_debate_prompts(
                    ticker=sym,
                    price=price,
                    suggestion=suggestion,
                    confidence=confidence,
                    ta_summary=ta_summary,
                    market_context=market_ctx,
                )

                bull_text = await pipeline_call_llm(prompts["bull"], "You are a bullish crypto analyst.", task_type="bull_bear_debate")
                bear_text = await pipeline_call_llm(prompts["bear"], "You are a bearish crypto analyst.", task_type="bull_bear_debate")

                debate_context = format_bull_bear_for_prompt(sym, bull_text, bear_text)
                log.info("[%s] Legacy debate complete — bull=%d chars, bear=%d chars",
                         sym, len(bull_text), len(bear_text))

        # Step 4.6: 3-way risk personas (if enabled)
        risk_context = ""
        risk_assessments_list = []

        if enable_risk_personas:
            try:
                if typed_signal is None:
                    raise RuntimeError("typed signal unavailable for risk assessment")
                # Build market data dict
                market_data_dict = {
                    "asset": sym,
                    "price": raw.get("price") or raw.get("current_price"),
                    "volume_24h": raw.get("volume_24h", "N/A"),
                    "change_24h": raw.get("change_24h", "N/A"),
                }
                if ta:
                    if "rsi" in ta:
                        market_data_dict["rsi"] = ta["rsi"]
                    if "macd" in ta:
                        market_data_dict["macd"] = ta["macd"]

                # Run risk debate
                risk = RiskDebate(pipeline_call_llm, RiskDebateConfig(rounds=1))
                risk_assessments_list = await risk.run(
                    typed_signal, bull_synth, bear_synth, market_data_dict
                )

                # Format for prompt (backward compatible)
                aggressive_rationale = next((r.rationale for r in risk_assessments_list if r.persona == "aggressive"), "")
                conservative_rationale = next((r.rationale for r in risk_assessments_list if r.persona == "conservative"), "")
                neutral_rationale = next((r.rationale for r in risk_assessments_list if r.persona == "neutral"), "")

                risk_context = f"""
## Risk Debate Results — {sym}

**Aggressive:** {aggressive_rationale[:300]}
**Conservative:** {conservative_rationale[:300]}
**Neutral:** {neutral_rationale[:300]}
"""

                log.info("[%s] RiskDebate complete — %d assessments", sym, len(risk_assessments_list))

            except Exception as exc:
                reason_code = "RISK_ASSESSMENT_UNAVAILABLE"
                failure = record_operational_failure(
                    "risk_debate",
                    reason_code=reason_code,
                    error_type=type(exc).__name__,
                )
                asset_json["risk_stage"] = {
                    "status": "UNAVAILABLE",
                    "reason_code": reason_code,
                    "trace_id": failure["trace_id"],
                }
                asset_json["risk_assessment"] = {
                    "status": "UNAVAILABLE",
                    "reason_code": reason_code,
                    "trace_id": failure["trace_id"],
                    "decision": "REJECT" if allow_execution else "UNKNOWN",
                    "accept_signal": False,
                    "position_size_pct": 0,
                }
                if allow_execution:
                    asset_json["suggestion"] = "HOLD"
                risk_context = (
                    f"\n## Risk Debate — {sym}\n"
                    f"UNAVAILABLE ({reason_code}, trace={failure['trace_id']}).\n"
                )
                log.error(
                    "event=risk_debate_unavailable trace_id=%s symbol=%s "
                    "reason_code=%s error_type=%s execution_blocked=%s",
                    failure["trace_id"],
                    sym,
                    reason_code,
                    failure["error_type"],
                    allow_execution,
                )

        # Inject debate + risk context into asset JSON for downstream prompts
        asset_json["_memory_context"] = memory_ctx
        asset_json["_debate_context"] = debate_context
        asset_json["_risk_context"] = risk_context

        # Store new Phase 2 data
        if debate_rounds:
            asset_json["_debate_rounds"] = [r.model_dump() for r in debate_rounds]
            asset_json["bull_synthesis"] = bull_synth
            asset_json["bear_synthesis"] = bear_synth
        if risk_assessments_list:
            asset_json["_risk_assessments"] = [r.model_dump() for r in risk_assessments_list]

        # Step 5: Portfolio Manager (FINAL DECISION)
        # Only run if we have debate results and risk assessments
        portfolio_context = ""
        if (
            enable_debate
            and enable_risk_personas
            and typed_signal is not None
            and debate_rounds
            and risk_assessments_list
        ):
            try:
                pm = PortfolioManager()
                portfolio_decision = await pm.decide(
                    pipeline_call_llm,
                    typed_signal,
                    debate_rounds,
                    bull_synth,
                    bear_synth,
                    risk_assessments_list,
                    analyst_context,  # Include analyst reports
                    execution_mode="paper" if not allow_execution else None,
                )

                # Apply portfolio decision to signal
                final_signal = pm.apply_decision(typed_signal, portfolio_decision)

                # Update asset_json with portfolio decision
                asset_json["portfolio_decision"] = portfolio_decision.model_dump(mode="json", exclude_none=True)
                asset_json["suggestion"] = final_signal.action
                asset_json["confidence"] = float(final_signal.confidence)
                asset_json["price"] = final_signal.entry_price
                asset_json["stop_loss_suggestion"] = final_signal.stop_loss
                asset_json["target_suggestion"] = final_signal.take_profit

                portfolio_context = f"""
## Portfolio Manager Decision — {sym}

**Original:** {typed_signal.action} (confidence: {typed_signal.confidence:.2f})
**Final:** {final_signal.action} (confidence: {final_signal.confidence:.2f})
**Decision:** {portfolio_decision.action.upper()}
**Rationale:** {portfolio_decision.rationale[:300]}
"""

                log.info("[%s] Portfolio Manager: %s → %s (%s)",
                         sym, typed_signal.action, final_signal.action, portfolio_decision.action)

            except Exception as exc:
                failure = record_operational_failure(
                    "portfolio_manager",
                    reason_code="PORTFOLIO_DECISION_UNAVAILABLE",
                    error_type=type(exc).__name__,
                )
                asset_json["portfolio_decision"] = {
                    "status": "UNAVAILABLE",
                    "reason_code": failure["reason_code"],
                    "trace_id": failure["trace_id"],
                }
                if allow_execution:
                    asset_json["suggestion"] = "HOLD"
                portfolio_context = (
                    f"\n## Portfolio Manager — {sym}\n"
                    f"UNAVAILABLE ({failure['reason_code']}, "
                    f"trace={failure['trace_id']}).\n"
                )
                log.error(
                    "event=portfolio_decision_unavailable trace_id=%s symbol=%s "
                    "reason_code=%s error_type=%s execution_blocked=%s",
                    failure["trace_id"],
                    sym,
                    failure["reason_code"],
                    failure["error_type"],
                    allow_execution,
                )

        else:
            # No portfolio manager run
            portfolio_context = f"\n## Portfolio Manager — {sym}\nSkipped (debate or risk personas not enabled).\n"

        asset_json["_portfolio_context"] = portfolio_context

        # ── Phase 5: Execution ───────────────────────────────────────────────────
        action = str(asset_json.get("suggestion", "")).upper()
        regime = (asset_json.get("market_regime") or "").lower()
        
        # ── Backtest-optimized regime filter (before paper execution) ──
        # Prevents trading in losing regimes. Same rules as typed decision filter.
        if regime == "unclear":
            action = "HOLD"  # 36% win rate — reject all
        elif action == "SELL" and regime != "trending_down":
            action = "HOLD"  # SHORT loses -2.22% avg
        elif action == "BUY" and regime not in ("trending_up",):
            action = "HOLD"  # LONG only in trending_up
        
        # ── Backtest gate check (only for new BUY/SELL entries) ──
        gate_modifier = 1.0
        if allow_execution and action == "BUY":
            try:
                from backtest_gate import check as gate_check
                gate_result = gate_check(sym)
                if gate_result["status"] == "block":
                    log.info("[%s] Backtest gate BLOCK: %s", sym, gate_result["reason"])
                    action = "HOLD"
                elif gate_result["position_modifier"] < 1.0:
                    gate_modifier = gate_result["position_modifier"]
                    log.info("[%s] Backtest gate WARNING — position reduced to %.0f%%",
                             sym, gate_modifier * 100)
            except Exception as exc:  # Safety-gate integration boundary.
                failure = record_operational_failure(
                    "backtest_gate",
                    reason_code="BACKTEST_GATE_UNAVAILABLE",
                    error_type=type(exc).__name__,
                )
                action = "HOLD"
                asset_json["execution"] = {
                    "status": "UNAVAILABLE",
                    "reason_code": failure["reason_code"],
                    "trace_id": failure["trace_id"],
                }
                log.error(
                    "event=execution_gate_unavailable trace_id=%s symbol=%s "
                    "reason_code=%s error_type=%s",
                    failure["trace_id"],
                    sym,
                    failure["reason_code"],
                    failure["error_type"],
                )

        if allow_execution and action in ("BUY", "SELL"):
            exec_mode = None
            try:
                if not callable(get_execution_mode):
                    raise RuntimeError("execution mode dependency unavailable")
                if not callable(paper_execute):
                    raise RuntimeError("paper execution dependency unavailable")
                if not callable(live_execute):
                    raise RuntimeError("live execution dependency unavailable")
                execution_mode_fn = get_execution_mode
                paper_execute_fn = paper_execute
                live_execute_fn = live_execute
                price = asset_json.get("price") or raw.get("current_price")
                if price is None:
                    raise ValueError("execution price unavailable")
                current_prices = {sym: float(price)}
                exec_signal = {
                    "asset": sym,
                    "action": action,
                    "confidence": float(asset_json.get("confidence", 0.5)),
                    "reasoning": asset_json.get("rationale", "")[:300],
                    "entry_price": price,
                    "stop_loss": asset_json.get("stop_loss_suggestion"),
                    "take_profit": asset_json.get("target_suggestion"),
                    "position_modifier": gate_modifier,
                }
                exec_mode = execution_mode_fn()
                if exec_mode == "paper":
                    confirmation = paper_execute_fn(exec_signal, current_prices)
                else:
                    confirmation = live_execute_fn(exec_signal, current_prices)
                if not isinstance(confirmation, dict):
                    raise TypeError("execution result must be a mapping")
            except Exception as exc:  # Paper/live adapter boundary.
                if exec_mode == "paper":
                    source = "paper_execution"
                    reason_code = "PAPER_EXECUTION_FAILED"
                elif exec_mode:
                    source = "live_execution"
                    reason_code = "LIVE_EXECUTION_FAILED"
                else:
                    source = "execution_precheck"
                    reason_code = "EXECUTION_PRECHECK_FAILED"
                failure = record_operational_failure(
                    source,
                    reason_code=reason_code,
                    error_type=type(exc).__name__,
                )
                asset_json["execution"] = {
                    "status": "UNAVAILABLE",
                    "reason_code": reason_code,
                    "trace_id": failure["trace_id"],
                }
                log.error(
                    "event=execution_unavailable trace_id=%s symbol=%s "
                    "reason_code=%s error_type=%s",
                    failure["trace_id"],
                    sym,
                    reason_code,
                    failure["error_type"],
                )
            else:
                execution_status = confirmation.get("status")
                paper_audit_partial = bool(
                    exec_mode == "paper"
                    and execution_status == "filled"
                    and confirmation.get("audit_status") == "PARTIAL"
                )
                if execution_status not in {"filled", "rejected"}:
                    failure = record_operational_failure(
                        "execution_result",
                        reason_code="EXECUTION_RESULT_INVALID",
                        error_type="InvalidExecutionStatus",
                    )
                    asset_json["execution"] = {
                        "status": "UNAVAILABLE",
                        "reason_code": failure["reason_code"],
                        "trace_id": failure["trace_id"],
                    }
                    log.error(
                        "event=execution_unavailable trace_id=%s symbol=%s "
                        "reason_code=%s error_type=%s",
                        failure["trace_id"],
                        sym,
                        failure["reason_code"],
                        failure["error_type"],
                    )
                else:
                    asset_json["execution"] = confirmation

                if paper_audit_partial:
                    audit_reason = confirmation.get(
                        "audit_reason_code",
                        "PAPER_AUDIT_WRITE_FAILED",
                    )
                    audit_trace_id = confirmation.get("trace_id")
                    failure = record_operational_failure(
                        "paper_execution_audit",
                        reason_code=audit_reason,
                        error_type="AuditPersistenceUnavailable",
                        trace_id=audit_trace_id,
                    )
                    asset_json["broker"] = {
                        "status": "SKIPPED",
                        "reason_code": "PAPER_AUDIT_INCOMPLETE",
                        "trace_id": failure["trace_id"],
                    }
                    log.error(
                        "event=paper_execution_audit_partial trace_id=%s "
                        "symbol=%s reason_code=%s",
                        failure["trace_id"],
                        sym,
                        audit_reason,
                    )

                if execution_status == "filled" and not paper_audit_partial:
                    try:
                        if not callable(broker_execute):
                            raise RuntimeError("broker dependency unavailable")
                        broker_result = broker_execute(
                            confirmation["symbol"],
                            confirmation.get("shares", 0),
                            confirmation["side"].lower()
                            if isinstance(confirmation.get("side"), str)
                            else "",
                        )
                        if not isinstance(broker_result, dict):
                            raise TypeError("broker result must be a mapping")
                    except Exception as exc:  # Broker integration boundary.
                        failure = record_operational_failure(
                            "broker_execution",
                            reason_code="BROKER_EXECUTION_FAILED",
                            error_type=type(exc).__name__,
                        )
                        asset_json["broker"] = {
                            "status": "UNAVAILABLE",
                            "reason_code": failure["reason_code"],
                            "trace_id": failure["trace_id"],
                        }
                        log.error(
                            "event=broker_execution_unavailable trace_id=%s "
                            "symbol=%s reason_code=%s error_type=%s",
                            failure["trace_id"],
                            sym,
                            failure["reason_code"],
                            failure["error_type"],
                        )
                    else:
                        if broker_result == {"paper": "skipped", "alpaca": None}:
                            asset_json["broker"] = {
                                "status": "SKIPPED",
                                "reason_code": "SECONDARY_BROKER_NOT_APPLICABLE",
                                **broker_result,
                            }
                        elif broker_result.get("status") not in {
                            "accepted",
                            "new",
                            "partially_filled",
                            "filled",
                            "done_for_day",
                            "canceled",
                            "expired",
                            "replaced",
                            "pending_cancel",
                            "pending_replace",
                            "stopped",
                            "rejected",
                            "suspended",
                            "calculated",
                            "blocked",
                            "skipped",
                        }:
                            failure = record_operational_failure(
                                "broker_result",
                                reason_code="BROKER_RESULT_INVALID",
                                error_type="InvalidBrokerStatus",
                            )
                            asset_json["broker"] = {
                                "status": "UNAVAILABLE",
                                "reason_code": failure["reason_code"],
                                "trace_id": failure["trace_id"],
                            }
                            log.error(
                                "event=broker_execution_unavailable trace_id=%s "
                                "symbol=%s reason_code=%s error_type=%s",
                                failure["trace_id"],
                                sym,
                                failure["reason_code"],
                                failure["error_type"],
                            )
                        else:
                            asset_json["broker"] = broker_result
                        log.info(
                            "[%s] Execution complete — %s=%s broker=%s",
                            sym,
                            exec_mode,
                            execution_status,
                            asset_json["broker"].get("status", "N/A"),
                        )

                    # Telegram is observability-only after the authoritative fill.
                    if exec_mode == "paper":
                        try:
                            if not callable(send_telegram_text):
                                raise RuntimeError("alert dependency unavailable")
                            send_telegram_text(
                                f"[PAPER] {action} {sym}: {confirmation.get('shares', 0):.4f} shares "
                                f"@ ${confirmation.get('fill_price', 0):,.2f} "
                                f"(conf={float(asset_json.get('confidence', 0)):.2f})"
                            )
                        except Exception as exc:
                            alert_trace_id = uuid4().hex[:16]
                            log.warning(
                                "event=execution_alert_delivery_failed trace_id=%s "
                                "symbol=%s error_type=%s",
                                alert_trace_id,
                                sym,
                                type(exc).__name__,
                            )
                elif execution_status == "rejected":
                    confidence = float(asset_json.get("confidence", 0))
                    if confidence > 0.6:
                        try:
                            if not callable(send_telegram_text):
                                raise RuntimeError("alert dependency unavailable")
                            send_telegram_text(
                                f"[REJECTED] {sym} {action} (conf={confidence:.2f}): "
                                f"{confirmation.get('reason', 'N/A')}"
                            )
                        except Exception as exc:
                            alert_trace_id = uuid4().hex[:16]
                            log.warning(
                                "event=execution_alert_delivery_failed trace_id=%s "
                                "symbol=%s error_type=%s",
                                alert_trace_id,
                                sym,
                                type(exc).__name__,
                            )

        assembled_assets.append(asset_json)
        log.info("[%s] Assembled — suggestion: %s | confidence: %s",
                 sym, asset_json.get("suggestion"), asset_json.get("confidence"))

        # Log to scratchpad
        pad.log_tool_call(
            f"assembly.assemble_asset_json",
            {"symbol": sym},
            llm_summary=f"{sym}: {asset_json.get('suggestion')} (confidence={asset_json.get('confidence')})",
        )

        # Validate asset completeness
        valid, detail = validate_data_completeness(asset_json)
        pad.log_validation(f"data_completeness_{sym}", valid, detail)

        # Step 5: Store decision for future reflection (skip NO SIGNAL)
        suggestion_val = asset_json.get("suggestion", "")
        if (
            allow_execution
            and suggestion_val
            and str(suggestion_val).upper() not in ("NO SIGNAL", "WAIT", "", "NONE")
        ):
            # Score signal quality before storing
            asset_json["quality_score"] = score_decision(asset_json, [])
            store_decision(
                ticker=sym,
                trade_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                suggestion=suggestion_val,
                confidence=asset_json.get("confidence", 0.0),
                price=asset_json.get("current_price") or asset_json.get("price", 0.0),
                signals=ta,
                report_snippet=json.dumps(asset_json, indent=2)[:500],
            )

    # Final validation
    report = assemble_full_report(assembled_assets)
    report["optional_source_refresh"] = optional_source_statuses
    report["operational_failures"] = operational_failures
    optional_source_failures = [
        source
        for source in optional_source_statuses
        if source["status"] != "AVAILABLE"
    ]
    partial_reason_codes = [
        reason_code
        for reason_code in [report.get("reason_code")]
        if reason_code
    ]
    if optional_source_failures:
        partial_reason_codes.append("OPTIONAL_SOURCE_REFRESH_PARTIAL")
    if operational_failures:
        partial_reason_codes.append("EXECUTION_PATH_PARTIAL")
    if partial_reason_codes and report.get("status") != "UNAVAILABLE":
        report["status"] = "PARTIAL"
        report["reason_codes"] = list(dict.fromkeys(partial_reason_codes))
        report["reason_code"] = (
            report["reason_codes"][0]
            if len(report["reason_codes"]) == 1
            else "MULTIPLE_PARTIAL_FAILURES"
        )
    else:
        report.setdefault("status", "COMPLETED")
        report.setdefault("reason_code", None)
        report["reason_codes"] = []
    consistent, warnings = validate_consistency(report)
    pad.log_validation("report_consistency", consistent, "; ".join(warnings) if warnings else "All checks passed")

    # Log final decisions
    for asset in assembled_assets:
        sym = asset.get("symbol", "?")
        suggestion = asset.get("suggestion", "N/A")
        confidence = asset.get("confidence", 0)
        risk = asset.get("risk_assessment", {})
        pad.log_final_decision(
            ticker=sym,
            decision=str(suggestion),
            confidence=float(confidence) if isinstance(confidence, (int, float)) else 0.0,
            risk_level=str(risk.get("risk_level", "MEDIUM")),
            reasoning=asset.get("_debate_context", "")[:500] or asset.get("_risk_context", "")[:500],
        )

    # Save scratchpad
    pad.log_thinking("Research complete — saving audit trail", "reflection")
    if allow_execution:
        pad.save()

    # Parse structured signals from the report
    parsed_signals = parse_report(report)
    report["parsed_signals"] = [s.to_dict() for s in parsed_signals]

    actionable = [s for s in parsed_signals if s.is_actionable]
    watch = [s for s in parsed_signals if s.is_watch]
    log.info("Signal parsing complete: %d actionable, %d watch, %d total",
             len(actionable), len(watch), len(parsed_signals))

    # Process alerts from parsed signals
    if allow_execution:
        try:
            if not callable(process_alert_signals):
                raise RuntimeError("alert processing dependency unavailable")
            process_alert_signals_fn = process_alert_signals
            alert_signals = []
            report_ts = report.get("timestamp", datetime.now(timezone.utc).isoformat())
            for s in parsed_signals:
                if s.is_actionable:
                    alert_signals.append({
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "asset": s.ticker,
                        "action": s.action.upper(),
                        "confidence": s.confidence,
                        "price": s.entry_price,
                        "reasoning": s.reasoning[:300] if s.reasoning else "",
                        "delivered": False,
                        "report_ts": report_ts,
                    })
            if alert_signals:
                delivered = process_alert_signals_fn(alert_signals)
                log.info("[alert] Processed %d alerts, %d delivered", len(alert_signals), delivered)
        except Exception as e:
            log.warning("[alert] Alert processing failed: %s", e)

    return report


def save_report(
    report: dict,
    allow_notifications: bool = True,
    invocation: ResearchInvocation | None = None,
):
    """Save the JSON report with a timestamped filename."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    saved_report = with_lineage(report, invocation) if invocation is not None else report
    if invocation is not None and invocation.reports_dir is not None:
        suffix = invocation.attempt_id or ts
        path = write_json_exclusive(
            invocation.reports_dir, f"report_{suffix}.json", saved_report,
        )
        log.info("Attributed research report saved")
    else:
        path = REPORT_DIR / f"report_{ts}.json"
        with open(path, "w") as f:
            json.dump(saved_report, f, indent=2, default=str)
        log.info("Report saved → %s", path)
    
    # Also save structured typed decision for 2nd Brain dashboard
    if invocation is None:
        try:
            save_typed_decision(saved_report, ts, allow_notifications=allow_notifications)
        except Exception as e:
            log.warning("Typed decision save skipped: %s", e)
    
    return path


def _load_last_debate_for(symbol: str) -> dict | None:
    """Load the last debate with real bull/bear content for a given asset from typed_decisions.jsonl."""
    try:
        path = data_root() / "memory" / "typed_decisions.jsonl"
        if not path.exists():
            return None
        with open(path) as f:
            lines = f.readlines()
        # Search in reverse for the last decision with real content
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("asset") == symbol:
                bull = d.get("bull_synthesis", "")
                if bull and len(bull) > 20 and "[LLM STUB]" not in bull:
                    return d
        return None
    except Exception:
        return None


def save_typed_decision(report: dict, ts: str, allow_notifications: bool = True):
    """Convert report dict → TypedDecision and save as structured JSON for dashboard."""
    DECISIONS_DIR = data_root() / "decisions"
    DECISIONS_DIR.mkdir(parents=True, exist_ok=True)
    
    assets = report.get("assets", [])
    if not assets:
        return
    
    decisions = []
    for asset in assets:
        try:
            # Build initial signal from parsed report data
            # ── Backtest-optimized regime filter (v4.0) ──
            # Based on 4,686-day backtest (2023-2025):
            #   - "unclear" regime = 33.3% win rate (243 signals) → REJECT ALL
            #   - SHORT signals = -2.28% avg 14d return → REJECT unless trending_down
            #   - "trending_up" = 55.2% win rate → ONLY allow BUY/LONG here
            regime = (asset.get("market_regime") or "").lower()
            if regime == "unclear":
                log.debug("[filter] %s suppressed: unclear regime (33%% win rate)", asset.get("symbol"))
                continue
            # Normalize action to our schema
            raw_action = asset.get("suggestion", "HOLD").upper()
            if "WATCH" in raw_action or "ENTRY" in raw_action:
                action = "WATCH"
            elif "BUY" in raw_action:
                if regime not in ("trending_up",):
                    log.debug("[filter] %s BUY suppressed: regime=%s", asset.get("symbol"), regime)
                    continue
                action = "BUY"
            elif "SELL" in raw_action or "SHORT" in raw_action:
                if regime != "trending_down":
                    log.debug("[filter] %s SELL suppressed: regime=%s (SHORT loses -2.28%% avg)", asset.get("symbol"), regime)
                    continue
                action = "SELL"
            elif "NO SIGNAL" in raw_action or "NONE" in raw_action:
                action = "HOLD"
            else:
                action = "HOLD"
            
            signal = TypedSignal(
                asset=asset.get("symbol", "UNKNOWN"),
                action=action,
                confidence=float(asset.get("confidence", 0.5)),
                entry_price=asset.get("price") or asset.get("current_price"),
                stop_loss=asset.get("stop_loss_suggestion"),
                take_profit=asset.get("target_suggestion"),
                reasoning=(asset.get("rationale") or "No detailed rationale available for this asset")[:500],
            )
            
            # Build risk assessments — prefer new _risk_assessments list, fall back to legacy risk_personas dict
            risk_assessments = []
            raw_risk = asset.get("_risk_assessments", [])
            if raw_risk:
                risk_assessments = [RiskAssessment(**r) for r in raw_risk if r]
            else:
                personas = asset.get("risk_personas", {})
                for persona_name in ["aggressive", "conservative", "neutral"]:
                    pdata = personas.get(persona_name, {})
                    if pdata:
                        risk_assessments.append(RiskAssessment(
                            persona=persona_name,
                            accept_signal=pdata.get("accept", True),
                            position_size_pct=float(pdata.get("position_size_pct", 0)),
                            rationale=pdata.get("rationale", "")[:500],
                        ))
            
            # Preserve debate synthesis from previous runs if current is empty
            bull_synth = asset.get("bull_synthesis", "") or asset.get("bull_case", "")
            bear_synth = asset.get("bear_synthesis", "") or asset.get("bear_case", "")
            debate_rds = [DebateRound(**r) for r in asset.get("_debate_rounds", []) if r]
            
            # If no debate data in this run, carry forward from last known good debate
            if not bull_synth and not bear_synth and not debate_rds:
                prev = _load_last_debate_for(asset.get("symbol", ""))
                if prev:
                    bull_synth = prev.get("bull_synthesis", "")
                    bear_synth = prev.get("bear_synthesis", "")
                    debate_rds = prev.get("debate_rounds", [])
                    # Also carry risk assessments
                    if prev.get("risk_assessments") and not risk_assessments:
                        risk_assessments = prev["risk_assessments"]
            
            decision = TypedDecision(
                timestamp=datetime.now(timezone.utc),
                asset=asset.get("symbol", "UNKNOWN"),
                initial_signal=signal,
                debate_rounds=debate_rds,
                bull_synthesis=bull_synth,
                bear_synthesis=bear_synth,
                risk_assessments=risk_assessments,
                final_action=action,
                final_position_size_pct=float(asset.get("allocation_pct", 0)),
                executive_summary=asset.get("rationale", f"No detailed rationale available for {asset.get('symbol', 'this asset')}")[:500],
                quality_score=asset.get("quality_score"),
            )
            decisions.append(decision)
        except Exception as e:
            log.warning("Failed to build typed decision for %s: %s", asset.get("symbol", "?"), e)
    
    if decisions:
        path = DECISIONS_DIR / f"decisions_{ts}.json"
        with open(path, "w") as f:
            json.dump([d.model_dump(mode="json") for d in decisions], f, indent=2, default=str)
        log.info("Typed decisions saved → %s (%d assets)", path, len(decisions))

        # Also store to memory system for Phase 5 reflection
        for decision in decisions:
            store_typed_decision(decision)

        # ── Telegram alerts for actionable decisions ──
        actionable = [d for d in decisions if d.final_action in ("BUY", "SELL")] if allow_notifications else []
        if actionable:
            try:
                from alert_manager import send_telegram_text
                lines = ["🔔 *Trading Signals* (filtered)\n"]
                for d in actionable:
                    emoji = "🟢" if d.final_action == "BUY" else "🔴"
                    entry = d.initial_signal.entry_price
                    stop = d.initial_signal.stop_loss
                    target = d.initial_signal.take_profit
                    price_str = f" @ ${entry:,.0f}" if entry else ""
                    sl_str = f" | SL: ${stop:,.2f}" if stop else ""
                    tp_str = f" | TP: ${target:,.2f}" if target else ""
                    lines.append(
                        f"{emoji} *{d.asset}* → {d.final_action}{price_str}"
                        f"{sl_str}{tp_str}"
                        f"\n  Conviction: {d.initial_signal.confidence:.0%}"
                        f"\n  {d.executive_summary[:150]}"
                    )
                msg = "\n".join(lines)
                send_telegram_text(msg)
                log.info("[alert] Sent %d actionable signals to Telegram", len(actionable))
            except Exception as e:
                log.warning("[alert] Telegram delivery failed: %s", e)


# ── Reflection Mode ───────────────────────────────────────────────────────────

async def mode_reflect():
    """Process pending reflections using Phase 5 reflection engine."""
    import argparse as _ap
    log.info("── Reflection mode: processing pending typed decisions ──")

    engine = get_reflection_engine()
    # During sprint/dev, use horizon=0 to reflect immediately.
    # In production, defaults to 7 days.
    horizon_days = 0  # Force immediate reflection for development
    result = await engine.reflect_on_pending(call_llm, horizon_days=horizon_days)
    log.info(
        "── Reflection complete: status=%s processed=%d unavailable=%d ──",
        result.status.value,
        result.processed,
        result.unavailable,
    )
    if result.status is ReflectionStatus.UNAVAILABLE:
        reasons = ",".join(result.reason_codes[:16]) or "REFLECTION_UNAVAILABLE"
        raise RuntimeError(f"reflection unavailable: {reasons}")
    return result


# ── Polling callback ──────────────────────────────────────────────────────────

_poll_cache: dict[str, dict] = {}

async def on_asset_update(symbol: str, raw_asset: dict):
    """Called by polling_loop whenever fresh data arrives for a symbol."""
    ohlcv = raw_asset.get("ohlcv")
    ta = calculate_indicators(ohlcv, symbol) if ohlcv else None

    regime_result = detect_regime(ohlcv, symbol) if ohlcv else None

    sentiment_data = await fetch_sentiment(symbol)
    onchain_data = await fetch_onchain_risk(symbol)
    derivatives_data = await fetch_derivatives(
        symbol, price_change_24h_pct=raw_asset.get("price_change_24h_pct")
    )

    asset_json = assemble_asset_json(
        raw_data=raw_asset,
        ta_data=ta,
        sentiment_data=sentiment_data,
        onchain_data=onchain_data,
        derivatives_data=derivatives_data,
        regime_result=regime_result,
    )

    _poll_cache[symbol] = asset_json
    log.info("[POLL] %s updated — %s | %s",
             symbol, asset_json.get("suggestion"), asset_json.get("confidence"))

    alerts = asset_json.get("alerts", [])
    if alerts:
        log.warning("[ALERT] %s → %s", symbol, alerts)
        print(f"\n🔔 ALERT [{symbol}]: {', '.join(alerts)}\n")

    if set(_poll_cache.keys()) == set(WATCHLIST):
        report = assemble_full_report(list(_poll_cache.values()))
        save_report(report)


# ── CLI modes ─────────────────────────────────────────────────────────────────

def _research_context(
    allow_execution: bool,
    invocation: ResearchInvocation | None,
) -> ResearchInvocation | None:
    if allow_execution:
        return None
    return invocation or resolve_research_invocation(True)


async def mode_snapshot(
    symbols: list[str],
    allow_execution: bool = False,
    invocation: ResearchInvocation | None = None,
):
    invocation = _research_context(allow_execution, invocation)
    # Refresh correlation matrix (daily)
    if allow_execution:
        try:
            from portfolio_manager import compute_correlation_matrix
            pf = {}
            pf_path = data_root() / "memory" / "paper" / "portfolio.json"
            if pf_path.exists():
                import json as _json
                pf = _json.loads(pf_path.read_text()).get("positions", {})
            compute_correlation_matrix(pf)
            log.info("Correlation matrix refreshed")
        except Exception as e:
            log.warning("Correlation matrix refresh failed: %s", e)

    # Legacy collector entry points publish and rotate their own artifacts.
    # Strict research workers publish only their attributed canonical result.
    if allow_execution:
        try:
            from yfinance_collector import main as yf_main
            yf_main()
        except Exception as e:
            log.warning("yfinance fundamentals collection failed: %s", e)

        try:
            from macro import main as macro_main
            macro_main(allow_kalshi=True)
        except Exception as e:
            log.warning("Macro collection failed: %s", e)

        try:
            from news_collector import main as news_main
            news_main()
        except Exception as e:
            log.warning("News collection failed: %s", e)
        try:
            from sentiment_collector import main as sentiment_main
            sentiment_main()
        except Exception as e:
            log.warning("Sentiment collection failed: %s", e)

        try:
            from onchain_collector import main as onchain_main
            onchain_main()
        except Exception as e:
            log.warning("On-chain collection failed: %s", e)

    semantic_inputs = (
        None if allow_execution else load_snapshot_semantic_inputs(data_root())
    )
    report = await run_pipeline(
        symbols,
        allow_execution=allow_execution,
        semantic_inputs=semantic_inputs,
    )
    if semantic_inputs is not None:
        report = {**report, "semantic_input_fingerprint": semantic_inputs.source_fingerprint}
    if invocation is not None:
        report = with_lineage(report, invocation)
    save_report(report, allow_notifications=allow_execution, invocation=invocation)
    print(json.dumps(report, indent=2, default=str))


async def mode_poll():
    log.info("Starting continuous polling loop. Ctrl+C to stop.")
    await polling_loop(WATCHLIST, on_update=on_asset_update)


async def mode_brief(symbols: list[str]):
    report = await run_pipeline(symbols)
    save_report(report)
    print(morning_brief(report=json.dumps(report, indent=2, default=str)))


async def mode_entry(symbol: str):
    report = await run_pipeline([symbol])
    asset = report["assets"][0] if report.get("assets") else {}
    print(entry_check(
        report=json.dumps(asset, indent=2, default=str),
        symbol=symbol,
        stop_loss=asset.get("stop_loss_suggestion", "N/A"),
        target=asset.get("target_suggestion", "N/A"),
    ))


async def mode_risk(symbols: list[str]):
    report = await run_pipeline(symbols)
    save_report(report)
    print(risk_scan(report=json.dumps(report, indent=2, default=str)))


async def mode_debate(
    symbols: list[str],
    allow_execution: bool = False,
    invocation: ResearchInvocation | None = None,
):
    """Full pipeline with debate + risk personas enabled."""
    invocation = _research_context(allow_execution, invocation)
    semantic_inputs = (
        None if allow_execution else load_snapshot_semantic_inputs(data_root())
    )
    report = await run_pipeline(
        symbols,
        enable_debate=True,
        enable_risk_personas=True,
        allow_execution=allow_execution,
        semantic_inputs=semantic_inputs,
    )
    if semantic_inputs is not None:
        report = {**report, "semantic_input_fingerprint": semantic_inputs.source_fingerprint}
    if invocation is not None:
        report = with_lineage(report, invocation)
    save_report(report, allow_notifications=allow_execution, invocation=invocation)
    _print_signal_summary(report)


async def mode_backtest(
    symbols: list[str],
    allow_execution: bool = False,
    invocation: ResearchInvocation | None = None,
):
    """
    QuantAgent-inspired binary backtest mode.
    Forces LONG or SHORT on every asset — HOLD is prohibited.
    Used for evaluating signal quality, not production trading.
    """
    invocation = _research_context(allow_execution, invocation)
    semantic_inputs = (
        None if allow_execution else load_snapshot_semantic_inputs(data_root())
    )
    report = await run_pipeline(
        symbols,
        enable_debate=False,
        enable_risk_personas=False,
        allow_execution=allow_execution,
        semantic_inputs=semantic_inputs,
    )
    if semantic_inputs is not None:
        report = {**report, "semantic_input_fingerprint": semantic_inputs.source_fingerprint}
    if invocation is not None:
        report = with_lineage(report, invocation)
    save_report(report, allow_notifications=allow_execution, invocation=invocation)

    parsed = parse_report(report)
    print(f"\n{'='*60}")
    print(f"  BACKTEST MODE — Binary LONG/SHORT")
    print(f"{'='*60}")

    for signal in parsed:
        # Force binary: any bullish leaning → LONG, bearish → SHORT
        action = signal.action
        if action in ("buy", "strong buy", "watch"):
            action = "LONG"
        elif action in ("sell", "strong sell", "watch for exit"):
            action = "SHORT"
        elif signal.confidence >= 0.5:
            # Weighted score: if net bullish → LONG, net bearish → SHORT
            action = "LONG"  # default optimistic
        else:
            action = "SHORT"  # default pessimistic

        rr = f"R:R={signal.risk_reward_ratio:.1f}" if signal.risk_reward_ratio > 0 else "R:R=N/A"
        print(f"  {signal.ticker:<6} {action:<6} conf={signal.confidence:.0%}  {rr}  {signal.reasoning[:80]}")

    long_count = sum(1 for s in parsed if s.action in ("buy", "strong buy", "watch"))
    short_count = len(parsed) - long_count
    print(f"\n  LONG: {long_count}  |  SHORT: {short_count}  |  Total: {len(parsed)}")


def _print_signal_summary(report: dict):
    """Print debate + risk contexts for each asset."""
    for asset in report.get("assets", []):
        sym = asset.get("symbol", "?")
        print(f"\n{'='*60}")
        print(f"  {sym}")
        print(f"{'='*60}")

        if asset.get("_debate_context"):
            print(asset["_debate_context"][:500])

        if asset.get("_risk_context"):
            print(asset["_risk_context"][:500])

        if asset.get("_memory_context"):
            print(asset["_memory_context"][:500])


# ── Main ──────────────────────────────────────────────────────────────────────

def load_soul() -> str:
    """Load the agent's SOUL.md for identity injection."""
    soul_path = Path(__file__).parent / "SOUL.md"
    if soul_path.exists():
        return soul_path.read_text()
    return ""


def parse_args():
    parser = argparse.ArgumentParser(description="Crypto Research Agent v3.0")
    parser.add_argument(
        "--mode",
        choices=["snapshot", "poll", "brief", "entry", "risk", "debate", "reflect", "plan", "replay", "backtest", "risk-check", "health-check", "pairs"],
        default="debate",
        help="Run mode (default: debate)",
    )
    parser.add_argument(
        "--symbol",
        type=str,
        default=None,
        help="Single symbol override (e.g. BTC)",
    )
    parser.add_argument(
        "--question",
        type=str,
        default="",
        help="Custom research question (for --mode plan)",
    )
    parser.add_argument(
        "--session",
        type=str,
        default="",
        help="Session ID or filepath (for --mode replay)",
    )
    parser.add_argument(
        "--research-only",
        action="store_true",
        help="Required for snapshot, debate, backtest, and replay; disables all execution paths",
    )
    args = parser.parse_args()
    if args.research_only and args.mode not in {"snapshot", "debate", "backtest", "replay"}:
        parser.error(
            "--research-only is only supported with snapshot, debate, backtest, or replay mode"
        )
    return args


async def mode_plan(symbols: list[str], question: str):
    """Generate and display a custom research plan."""
    plan = build_custom_plan(question, symbols)
    print(format_plan(plan))
    print(f"\nRun with --mode snapshot to execute this plan.")


async def mode_replay(
    session_id: str,
    research_only: bool = False,
    invocation: ResearchInvocation | None = None,
):
    """Replay a scratchpad session."""
    if research_only:
        invocation = invocation or resolve_research_invocation(True)
    if not session_id:
        sessions = list_recent_sessions(5)
        if not sessions:
            print("No scratchpad sessions found.")
            return
        print("Recent sessions:")
        for i, s in enumerate(sessions):
            print(f"  [{i}] {s.stem}")
        return

    sanitized_events = None
    if research_only and invocation is not None and invocation.job_id is not None:
        events, sanitized_events = load_worker_replay(invocation, session_id)
        rendered = render_session(session_id, events)
        filepath = None
    elif research_only:
        import re
        from scratchpad import SCRATCHPAD_DIR

        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", session_id):
            print(f"Invalid session ID '{session_id}'.")
            return
        sessions = list_recent_sessions(50)
        matches = [session for session in sessions if session.stem == session_id]
        if len(matches) != 1:
            qualifier = "Ambiguous" if len(matches) > 1 else "No exact"
            print(f"{qualifier} session matching '{session_id}' found.")
            return
        candidate = matches[0]
        try:
            scratchpad_root = SCRATCHPAD_DIR.resolve(strict=True)
            if (
                candidate.is_symlink()
                or candidate.suffix != ".jsonl"
                or not candidate.is_file()
            ):
                print(f"Rejected session '{session_id}': invalid scratchpad file.")
                return
            resolved_candidate = candidate.resolve(strict=True)
        except OSError:
            print(f"Rejected session '{session_id}': inaccessible scratchpad file.")
            return
        if resolved_candidate.parent != scratchpad_root:
            print(f"Rejected session '{session_id}': outside scratchpad directory.")
            return
        filepath = str(resolved_candidate)
    # Legacy replay preserves explicit filepath support when research-only is off.
    elif os.path.exists(session_id):
        filepath = session_id
    else:
        sessions = list_recent_sessions(50)
        matches = [s for s in sessions if session_id in str(s)]
        if not matches:
            print(f"No session matching '{session_id}' found.")
            return
        filepath = str(matches[0])

    if filepath is not None:
        rendered = replay_session(filepath)
    print(rendered)
    if research_only and invocation is not None and invocation.signal_output_dir is not None:
        if sanitized_events is None:
            raise ResearchInvocationError("worker replay did not produce sanitized events")
        document = build_replay_sidecar(invocation, session_id, sanitized_events)
        suffix = invocation.attempt_id or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        write_json_exclusive(
            invocation.signal_output_dir, f"replay_{suffix}.json", document,
        )


async def mode_risk_check(symbols: list[str]):
    """Run Monte Carlo risk validation pipeline (monte_carlo → risk_engine)."""
    from risk_validation import run_risk_validation, print_summary

    log.info("── Risk validation: %s (n=%d simulations) ──", symbols, 10_000)
    report = run_risk_validation(
        n_simulations=10_000,
        horizon_days=30,
        method="bootstrap",
    )
    print_summary(report)
    log.info("Risk validation report: %s", report.get("_filepath", "unknown"))


async def mode_health_check(symbols: list[str]):
    """Check exchange health for all Canada-legal exchanges.

    Also provides a cron-friendly minimal-status wrapper: prints OK/FAIL per exchange,
    then saves the full health report to reports/exchange_health_*.json.
    """
    import json as _json
    from exchange_health import check_all_exchanges, get_exchange_health_summary

    log.info("── Exchange health check ──")

    # Full detailed check
    results = check_all_exchanges()
    summary = get_exchange_health_summary()

    # Cron-friendly minimal output
    print("\\n=== Exchange Health (cron) ===")
    for ex_id, health in sorted(results.items()):
        status = "OK" if health["online"] else "FAIL"
        wd = "WD" if health.get("withdrawals_enabled") else "NO_WD"
        lat = f"{health.get('latency_ms', 0):.0f}ms"
        print(f"{ex_id:12s} {status:4s} {wd:5s} lat={lat}")

    print(f"\\nSummary: {'ALL ONLINE' if summary['all_online'] else 'SOME OFFLINE'}")
    if summary["failing"]:
        print(f"  FAILING: {', '.join(summary['failing'])}")
    if summary["degraded"]:
        print(f"  DEGRADED: {', '.join(summary['degraded'])}")

    # Save full report
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    health_path = REPORT_DIR / f"exchange_health_{ts}.json"
    health_path.write_text(_json.dumps({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "exchanges": results,
    }, indent=2, default=str))
    log.info("Exchange health report saved → %s", health_path)


async def mode_pairs(symbols: list[str]):
    """Scan for cointegrated pairs across the watchlist and print candidates."""
    from data_collector import collect_all
    from pairs_strategy import scan_pairs, print_candidates
    import numpy as np

    log.info("── Pairs scan: %s ──", symbols)

    # Collect OHLCV data for all symbols
    raw_assets = await collect_all(symbols)
    price_dict: dict[str, np.ndarray] = {}

    for sym in symbols:
        raw = raw_assets.get(sym, {})
        ohlcv = raw.get("ohlcv")
        if ohlcv and len(ohlcv) >= 30:
            prices = np.array([c["close"] for c in ohlcv], dtype=float)
            price_dict[sym] = prices
        else:
            log.warning("[%s] Insufficient OHLCV data for pairs scan", sym)

    if len(price_dict) < 2:
        print("Need at least 2 symbols with sufficient data for cointegration scan.")
        return

    candidates = scan_pairs(price_dict, max_pairs=50)
    print(print_candidates(candidates))


async def main():
    args = parse_args()
    research_modes = {"snapshot", "debate", "backtest", "replay"}
    if args.mode in research_modes and not args.research_only:
        raise ResearchInvocationError(
            f"{args.mode} mode requires --research-only in the research backend"
        )
    if STRICT_WORKER_INVOCATION is not None:
        if not args.research_only:
            raise ResearchInvocationError(
                "strict worker attribution requires the research-only CLI flag"
            )
        invocation = STRICT_WORKER_INVOCATION
    else:
        invocation = resolve_research_invocation(args.research_only)
    symbols = [args.symbol.upper()] if args.symbol else WATCHLIST
    if not args.symbol and is_us_market_open():
        symbols = symbols + STOCK_WATCHLIST
        log.info("US market open — adding stocks: %s", STOCK_WATCHLIST)

    mode_map = {
        "snapshot":     lambda: mode_snapshot(
            symbols, allow_execution=False, invocation=invocation
        ),
        "poll":         lambda: mode_poll(),
        "brief":        lambda: mode_brief(symbols),
        "entry":        lambda: mode_entry(symbols[0]),
        "risk":         lambda: mode_risk(symbols),
        "debate":       lambda: mode_debate(
            symbols, allow_execution=False, invocation=invocation
        ),
        "reflect":      lambda: mode_reflect(),
        "plan":         lambda: mode_plan(symbols, args.question or f"Analyze {', '.join(symbols)}"),
        "replay":       lambda: mode_replay(
            args.session, research_only=True, invocation=invocation
        ),
        "backtest":     lambda: mode_backtest(
            symbols, allow_execution=False, invocation=invocation
        ),
        "risk-check":   lambda: mode_risk_check(symbols),
        "health-check": lambda: mode_health_check(symbols),
        "pairs":        lambda: mode_pairs(symbols),
    }

    await mode_map[args.mode]()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Agent stopped by user.")
