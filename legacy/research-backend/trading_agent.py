#!/usr/bin/env python3
"""
Trading Agent — Autonomous Trading Orchestrator
===============================================
Wires together: research pipeline → risk → execution → PnL tracking.

Modes:
  paper  — Paper trading (default, safe)
  live   — Real exchange execution
  dryrun — Full pipeline, orders created but NOT submitted
  once   — Run one cycle and exit

Usage:
  python trading_agent.py                      # Paper mode, continuous
  python trading_agent.py --mode live          # Live trading
  python trading_agent.py --mode paper --once  # Single cycle
  python trading_agent.py --mode dryrun --symbols BTC,ETH

Env vars:
  TRADING_MASTER_KEY  — Master password for API key decryption
  TELEGRAM_BOT_TOKEN  — (optional) Override for execution alerts
  TELEGRAM_CHAT_ID    — (optional) Chat ID for alerts
"""

import os
import sys
import time
import json
import random
import signal
import urllib.request
import urllib.error
import logging
import argparse
from pathlib import Path
from typing import Optional, Dict, Set, List
from datetime import datetime, timezone

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent))

# Load only an explicitly configured runtime environment file.
from runtime_paths import configured_env_file, data_root, mode_file

env_file = configured_env_file()
if env_file is not None:
    from dotenv import load_dotenv
    load_dotenv(env_file)
from exchange.secrets import load_secrets_into_env
load_secrets_into_env()

from exchange import (
    ExchangeAdapter, ExchangeID, OrderSide, OrderType,
    OrderRequest, OrderExecutor, ExecutionReport,
    PriceFeed, get_exchange_credentials, load_keys,
)
from db import repository as db
from db.repository import get_db
from live_execution_policy import LiveExecutionPolicy, is_explicitly_true

LOG_DIR = data_root() / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "agent.log"),
    ],
)
log = logging.getLogger("trading-agent")

# ── Constants ─────────────────────────────────────────────────

DEFAULT_SYMBOLS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "TON/USDT", "DOGE/USDT",
    "ADA/USDT", "AVAX/USDT", "DOT/USDT", "LINK/USDT", "MATIC/USDT",
]
DEFAULT_CAPITAL = 100_000  # USD
MAX_POSITION_PCT = 0.50    # 50% max total exposure
MAX_PER_TRADE_PCT = 0.05   # 5% max per trade
DAILY_LOSS_LIMIT_PCT = 0.03  # 3% daily loss = halt
SLIPPAGE_ALERT_PCT = 2.0     # Alert if slippage exceeds 2%
STOP_LOSS_PCT = 0.05          # Default 5% stop-loss from entry
TAKE_PROFIT_PCT = 0.10        # Default 10% take-profit from entry
TRAILING_STOP_PCT = 0.05      # Default 5% trailing stop distance
MAX_DRAWDOWN_PCT = 0.20       # 20% drawdown from peak = halt
MIN_SIGNAL_CONFIDENCE = 0.72  # Only act on signals >= 72% confidence
DEDUP_WINDOW_SECS = 60         # Ignore signals for symbol if an order ran in last 60s
REFLECTION_TRIGGER_TRADES = 5  # Run journal analysis after every N completed trades

# Correlation groups — total exposure per group capped at GROUP_MAX_PCT
CORRELATION_GROUPS: Dict[str, Set[str]] = {
    "L1": {"SOL", "AVAX", "DOT", "ADA", "TON", "MATIC"},
    "L2": {"LINK"},
    "store_of_value": {"BTC"},
    "smart_contract": {"ETH"},
    "meme": {"DOGE"},
}
GROUP_MAX_PCT = 0.15  # 15% max total exposure per correlation group


def is_live_execution_enabled() -> bool:
    """Return the primary hard-gate state (approval is evaluated separately)."""
    return is_explicitly_true(os.getenv("LIVE_EXECUTION_ENABLED"))


def is_live_trading_approved() -> bool:
    return is_explicitly_true(os.getenv("LIVE_TRADING_APPROVED"))


def enforce_live_execution_gate(mode: str) -> str:
    decision = LiveExecutionPolicy().evaluate(mode)
    if mode == "live" and not decision.allowed:
        log.critical("LIVE mode request blocked: reason=%s; forcing paper mode", decision.reason_code)
    return decision.effective_mode

# ── Main Agent ─────────────────────────────────────────────────

class TradingAgent:
    """Autonomous trading agent — research → decide → execute."""

    def __init__(self, mode: str = "paper", exchange_name: str = "coinbase",
                 symbols: Optional[List[str]] = None, capital: float = DEFAULT_CAPITAL):
        self.requested_mode = mode
        self.mode = enforce_live_execution_gate(mode)
        self.exchange_name = exchange_name
        self.symbols = symbols or DEFAULT_SYMBOLS
        self.capital = capital
        self.max_position = capital * MAX_POSITION_PCT
        self.max_per_trade = capital * MAX_PER_TRADE_PCT
        self.daily_loss_limit = capital * DAILY_LOSS_LIMIT_PCT

        # State
        self.running = True
        self.halted = False
        self.daily_pnl = 0.0
        self.start_equity = capital
        self.peak_equity = capital   # High-water mark for drawdown calculation
        self.max_drawdown = capital * MAX_DRAWDOWN_PCT
        self._halt_notified = False  # Prevents notification spam
        self.adapter: Optional[ExchangeAdapter] = None
        self.executor: Optional[OrderExecutor] = None
        self._credentials_available = False
        self.price_feed: Optional[PriceFeed] = None
        self._completed_trades = 0  # Counts fills; triggers reflection every N trades

        # Load mode from file if exists
        target = mode_file()
        if target.exists():
            saved_mode = target.read_text().strip()
            if saved_mode in ("paper", "live", "dryrun"):
                self.requested_mode = saved_mode
                self.mode = enforce_live_execution_gate(saved_mode)
                log.info("Mode loaded: requested=%s effective=%s", self.requested_mode, self.mode)

        # Init DB
        self._init_db()

        # Init exchange adapter
        self._init_exchange()

        # Init price feed
        self._init_price_feed()

        # Register signal handlers
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

        log.info("TradingAgent initialized | mode=%s | exchange=%s | capital=$%.0f | %d symbols",
                 self.mode, exchange_name, capital, len(self.symbols))

    # ── Initialization ─────────────────────────────────────────

    def _init_db(self):
        """Initialize database and run schema migration."""
        get_db()  # Triggers schema creation via SCHEMA
        log.info("Database initialized")

    def _init_exchange(self):
        """Initialize exchange adapter for live/dryrun modes."""
        self.mode = enforce_live_execution_gate(self.requested_mode)
        if self.mode == "paper":
            self.adapter = None
            self.executor = None
            self._credentials_available = False
            log.info("Paper mode — no exchange connection needed")
            return

        creds = get_exchange_credentials(self.exchange_name)
        self._credentials_available = bool(creds)
        if not creds:
            log.error("No API keys for %s. Configure with: python -m exchange.secrets add %s",
                     self.exchange_name, self.exchange_name)
            if self.mode == "live":
                raise RuntimeError(f"No API keys for {self.exchange_name}")

        sandbox = self.mode == "dryrun"
        self.adapter = ExchangeAdapter(
            ExchangeID(self.exchange_name),
            api_key=creds["api_key"],
            secret=creds["secret"],
            password=creds.get("password", ""),
            sandbox=sandbox,
        )

        if not self.adapter.test_connection():
            raise RuntimeError(f"Failed to connect to {self.exchange_name}")

        self.executor = OrderExecutor(self.adapter)
        log.info("Exchange connected: %s (sandbox=%s)", self.exchange_name, sandbox)

    def _init_price_feed(self):
        """Start real-time price feed for tracked symbols."""
        base_symbols = {s.replace("/USDT", "") for s in self.symbols}
        self.price_feed = PriceFeed(symbols=base_symbols)
        self.price_feed.start()
        log.info("Price feed started for %d symbols", len(base_symbols))

    # ── Main Loop ──────────────────────────────────────────────

    def _check_mode_switch(self):
        """Re-read .mode file each cycle to allow hot-switching."""
        target = mode_file()
        if target.exists():
            new_mode = target.read_text().strip()
            self.requested_mode = new_mode
            new_mode = enforce_live_execution_gate(self.requested_mode)
            if new_mode in ("paper", "live", "dryrun") and new_mode != self.mode:
                old_mode = self.mode
                self.mode = new_mode
                log.warning("🔄 Mode switched: %s → %s (hot-reload from .mode file)",
                           old_mode.upper(), new_mode.upper())
                if new_mode == "live":
                    log.warning("⚠️  LIVE MODE ACTIVE — trades will execute with real money")
                    self._init_exchange()
                elif new_mode == "paper":
                    log.info("📝 Switched to paper trading")
                elif new_mode == "dryrun":
                    log.info("🔬 Switched to dryrun mode")


    # ── Main Loop ──────────────────────────────────────────────

    def run(self, once: bool = False):
        """Main agent loop."""
        log.info("=" * 60)
        log.info("TRADING AGENT STARTING | mode=%s | %s",
                 self.mode.upper(), datetime.now(timezone.utc).isoformat())

        self._snapshot_equity()

        while self.running:
            try:
                if not self.halted:
                    self._check_mode_switch()  # Re-read .mode file each cycle
                    self._tick()
                else:
                    log.info("Agent HALTED — circuit breaker active. Waiting 60s...")
                    time.sleep(60)
                    # Check if we can resume
                    self._check_halt_conditions()

                if once:
                    break

                time.sleep(5)  # 5-second tick interval for responsiveness

            except KeyboardInterrupt:
                break
            except Exception as e:
                log.exception("Tick error: %s", e)
                db.insert_alert("system", f"Tick error: {e}", "error")
                time.sleep(30)

        self._shutdown()

    def _tick(self):
        """One agent cycle: check data → evaluate → decide → execute."""
        # 1. Update prices from WebSocket feed
        prices = self.price_feed.get_all_prices()
        if not prices:
            log.info("No prices yet, waiting...")
            return

        # Dump live prices for dashboard SSE streaming
        self._dump_live_prices(prices)

        # 2. Check risk limits
        if self._check_halt_conditions():
            return

        # 3. Check stop-loss / take-profit on all positions
        self._check_stops(prices)

        # 4. Get signals from research pipeline
        signals = self._get_signals()
        if signals:
            actionable = [s for s in signals
                         if s.get("direction", "") not in ("HOLD",)
                         and (s.get("confidence") or 0) >= MIN_SIGNAL_CONFIDENCE]
            if actionable:
                log.info("Processing %d signals (%d actionable, %d low-conf)",
                        len(signals), len(actionable), len(signals) - len(actionable))
            else:
                log.debug("All %d signals low-confidence or HOLD — skipping", len(signals))

        # 4. For each signal, evaluate and potentially execute
        # Per-cycle set: prevent processing two signals for the same symbol in one tick
        _cycle_executed: set = set()
        for signal in signals:
            if not self.running or self.halted:
                break
            sym = signal.get("symbol", "")
            if sym in _cycle_executed:
                log.debug("Dedup: skipping duplicate signal for %s in this cycle", sym)
                db.mark_signal_processed(signal["id"])
                continue
            self._process_signal(signal)
            _cycle_executed.add(sym)

        # 5. Update position prices
        self._update_positions(prices)

        # 6. Snapshot equity
        self._snapshot_equity()

        # 7. Trigger learning loop if enough new trades have accumulated
        if self._completed_trades >= REFLECTION_TRIGGER_TRADES:
            self._run_reflection()
            self._completed_trades = 0

        # 8. Send periodic status
        self._send_status()

    # ── Signal Processing ─────────────────────────────────────

    def _run_reflection(self):
        """
        Run journal analysis after every REFLECTION_TRIGGER_TRADES trades.
        Updates SOUL.md learned rules and walk-forward stats for backtest_gate.
        Non-blocking: errors are logged but don't crash the agent.
        """
        try:
            from journal_analyzer import run_analysis
            log.info("Reflection loop triggered after %d trades", REFLECTION_TRIGGER_TRADES)
            stats = run_analysis(update_soul=True)
            wr = stats.get("win_rate", 0)
            pf = stats.get("profit_factor", 0)
            pnl = stats.get("total_pnl", 0)
            log.info("Reflection complete: WinRate=%.0f%% PF=%.2f PnL=$%+.2f",
                     wr * 100, pf, pnl)
            self._notify_telegram(
                f"📊 Reflection: {stats.get('total_trades', 0)} trades | "
                f"WinRate {wr:.0%} | PF {pf:.2f} | P&L ${pnl:+.2f}"
            )
        except Exception as e:
            log.warning("Reflection loop error (non-fatal): %s", e)

    def _get_signals(self) -> List[Dict]:
        """Get latest signals from DB. Syncs pipeline decisions as safety net."""
        # Safety net: sync any pipeline decisions that weren't auto-bridged
        # (e.g., decisions produced while agent was down)
        try:
            from pipeline_to_db import sync_new
            inserted, skipped = sync_new(limit=50)
            if inserted:
                log.info("Safety-net sync: %d new pipeline decisions", inserted)
        except Exception:
            pass

        return db.get_recent_signals(limit=20)

    def _process_signal(self, signal: Dict):
        """Evaluate a signal and execute if approved."""
        symbol = signal["symbol"]
        direction = signal["direction"]
        confidence = signal.get("confidence", 0)

        # Normalize direction from pipeline output
        direction_map = {
            "BUY": "BUY", "STRONG BUY": "BUY",
            "SELL": "SELL", "STRONG SELL": "SELL",
            "WATCH FOR EXIT": "SELL", "WATCH": "HOLD",
            "HOLD": "HOLD",
        }
        direction = direction_map.get(direction, direction)

        # Skip HOLD signals — mark processed so they don't pile up
        if direction == "HOLD":
            db.mark_signal_processed(signal["id"])
            return

        # Skip low confidence — mark processed so they don't pile up
        if confidence and confidence < MIN_SIGNAL_CONFIDENCE:
            log.debug("Skipping %s %s — confidence %.0f%%",
                     symbol, direction, (confidence or 0) * 100)
            db.mark_signal_processed(signal["id"])
            return

        # Check if we already have an open order for this symbol
        open_orders = db.get_open_orders(symbol)
        if open_orders:
            log.debug("Skipping %s — %d open order(s)", symbol, len(open_orders))
            return

        # 60-second dedup: skip if an order was filled for this symbol recently
        try:
            recent_order = db.get_db().execute(
                "SELECT created_at FROM orders WHERE symbol=? AND status='filled' "
                "ORDER BY created_at DESC LIMIT 1", (symbol,)
            ).fetchone()
            if recent_order:
                import datetime as _dt
                last_ts = _dt.datetime.fromisoformat(str(recent_order[0]).replace("Z", "+00:00"))
                if last_ts.tzinfo is None:
                    last_ts = last_ts.replace(tzinfo=_dt.timezone.utc)
                age = (_dt.datetime.now(_dt.timezone.utc) - last_ts).total_seconds()
                if age < DEDUP_WINDOW_SECS:
                    log.debug("Dedup gate: %s last order was %.0fs ago (< %ds), skipping",
                             symbol, age, DEDUP_WINDOW_SECS)
                    db.mark_signal_processed(signal["id"])
                    return
        except Exception:
            pass

        # Paper/live: skip BUY if already holding a position (prevent duplicate accumulation)
        existing = db.get_position(symbol)
        if direction == "BUY" and existing and existing.get("quantity", 0) > 0:
            log.debug("Skipping %s BUY — already holding %.4f", symbol, existing["quantity"])
            db.mark_signal_processed(signal["id"])
            return

        # Load signal metadata once — used by RSI gate and ATR position sizing
        import json as _json
        try:
            _meta_raw = signal.get("metadata_json") or "{}"
            meta_dict = _json.loads(_meta_raw) if isinstance(_meta_raw, str) else (_meta_raw or {})
        except Exception:
            meta_dict = {}

        # RSI gate for SELL in long-only paper mode:
        # Never sell when RSI is neutral/oversold (< 60) — wait for overbought exit.
        # Exception: stop-loss/take-profit triggers bypass this gate (they come from _check_stops).
        if direction == "SELL" and self.mode == "paper":
            try:
                rsi = meta_dict.get("rsi_14") or meta_dict.get("rsi")
                strategy = signal.get("strategy", "")
                if strategy not in ("stop_exit",) and rsi is not None and float(rsi) < 60:
                    log.info("RSI gate: skipping SELL %s — RSI=%.1f < 60 (not overbought, long-only mode)",
                             symbol, float(rsi))
                    db.mark_signal_processed(signal["id"])
                    return
            except Exception:
                pass

        # Get current price
        tick = self.price_feed.get_price(symbol)
        if not tick:
            log.warning("No price for %s, skipping", symbol)
            return

        log.info("Signal: %s %s | confidence=%.0f%% | price=$%.2f",
                 symbol, direction, (confidence or 0) * 100, tick.price)

        # Backtest gate: check walk-forward validation before executing BUY signals.
        # No walk-forward data = first-run mode (allowed with warning).
        # Negative Sharpe or low win rate = blocked.
        _gate_modifier = 1.0
        if direction == "BUY":
            try:
                from backtest_gate import GateChecker
                gate_result = GateChecker().check(symbol)
                if gate_result["status"] == "block":
                    log.warning("Backtest gate BLOCKED %s BUY: %s", symbol, gate_result["reason"])
                    db.mark_signal_processed(signal["id"])
                    return
                _gate_modifier = gate_result.get("position_modifier", 1.0)
                if _gate_modifier < 1.0:
                    log.info("Backtest gate: %s position size reduced to %.0f%%",
                             symbol, _gate_modifier * 100)
            except Exception as e:
                log.debug("Backtest gate check error (non-fatal): %s", e)

        # Position sizing — reuse 'existing' from duplicate guard above
        # Max exposure: only applies to BUYs (SELLs reduce exposure)
        if direction == "BUY":
            current_exposure = sum(
                abs(p.get("quantity", 0) * p.get("current_price", tick.price))
                for p in db.get_positions()
            )

            # ATR-based position sizing: risk 1% of capital per trade.
            # trade_value = risk_amount / stop_distance_pct
            # stop_distance = 2× ATR from entry; capped at MAX_PER_TRADE_PCT.
            atr_14 = meta_dict.get("atr_14")
            if atr_14 and tick.price > 0:
                try:
                    atr_pct = float(atr_14) / tick.price  # ATR as fraction of price
                    stop_distance_pct = atr_pct * 2       # stop 2× ATR from entry
                    risk_amount = self.capital * 0.01     # risk 1% of capital
                    atr_trade_value = risk_amount / stop_distance_pct if stop_distance_pct > 0 else self.max_per_trade
                    trade_value = min(atr_trade_value, self.max_per_trade)
                    log.info("ATR sizing: %s ATR=%.4f (%.2f%%) stop=%.2f%% risk=$%.0f → size=$%.0f",
                             symbol, float(atr_14), atr_pct * 100, stop_distance_pct * 100,
                             risk_amount, trade_value)
                except Exception:
                    trade_value = self.max_per_trade
            else:
                trade_value = self.max_per_trade
            # Apply backtest gate position modifier (1.0 = normal, 0.5 = reduced)
            trade_value = trade_value * _gate_modifier
            if current_exposure + trade_value > self.max_position:
                trade_value = max(0, self.max_position - current_exposure)
                if trade_value <= 0:
                    log.info("Skipping %s — max exposure reached", symbol)
                    db.mark_signal_processed(signal["id"])
                    return

            # Correlation group limit — cap exposure within correlated groups
            for group_name, group_symbols in CORRELATION_GROUPS.items():
                if symbol in group_symbols:
                    group_exposure = sum(
                        abs(p.get("quantity", 0) * p.get("current_price", tick.price))
                        for p in db.get_positions()
                        if p["symbol"] in group_symbols
                    )
                    group_limit = self.capital * GROUP_MAX_PCT
                    if group_exposure + trade_value > group_limit:
                        trade_value = max(0, group_limit - group_exposure)
                        if trade_value <= 0:
                            log.info("Skipping %s — correlation group '%s' at %d%% limit",
                                   symbol, group_name, int(GROUP_MAX_PCT * 100))
                            db.mark_signal_processed(signal["id"])
                            return
                        log.info("Reducing %s trade to %.0f to respect group '%s' limit",
                               symbol, trade_value, group_name)
                    break

            quantity = trade_value / tick.price
        else:
            # SELL: use full position quantity (will be capped by _execute_paper)
            quantity = min(
                self.max_per_trade / tick.price,
                (existing["quantity"] if existing else 0)
            )

        # Paper mode — use PaperTrader
        if self.mode == "paper":
            self._execute_paper(symbol, direction, quantity, tick.price, signal)
            return

        # Live/dryrun — use real executor
        if self.mode == "dryrun":
            log.info("DRYRUN: Would %s %s %.4f @ $%.2f (confidence %.0f%%)",
                    direction, symbol, quantity, tick.price, (confidence or 0) * 100)
            return

        # LIVE execution
        self._execute_live(symbol, direction, quantity, tick.price, signal)

    # ── Execution ──────────────────────────────────────────────

    def _execute_paper(self, symbol: str, direction: str, quantity: float,
                       price: float, signal: Dict):
        """Execute paper trade with realistic slippage simulation."""
        try:
            side = "BUY" if direction in ("BUY", "STRONG BUY") else "SELL"

            # Skip SELL if no position
            existing = db.get_position(symbol)
            if side == "SELL" and (not existing or existing.get("quantity", 0) <= 0):
                log.debug("Skipping SELL %s — no position", symbol)
                db.mark_signal_processed(signal["id"])
                return

            # ── Slippage simulation ──────────────────────────
            # Base: 0.05% for large caps, up to 0.3% for small caps
            # Scale: larger orders (as % of 24h volume proxy) = more slippage
            # Random noise: ±40% around the estimate
            base_slippage = 0.0005  # 0.05%
            # Larger orders relative to typical trade size → more slippage
            trade_value = quantity * price
            size_factor = min(trade_value / 500_000, 1.0)  # Scale up to $500K trades
            estimated_slippage = base_slippage + (size_factor * 0.0025)  # 0.05%–0.30%
            noise = random.uniform(0.6, 1.4)  # ±40% noise
            slippage_pct = estimated_slippage * noise

            # Direction: BUY fills higher, SELL fills lower (adverse)
            fill_price = price * (1 + slippage_pct) if side == "BUY" else price * (1 - slippage_pct)
            slippage_pct_display = slippage_pct * 100  # Convert to percentage
            # ────────────────────────────────────────────────

            # Record in DB with both prices
            order_id = db.insert_order(
                client_order_id=f"paper_{symbol}_{int(time.time())}",
                exchange="paper",
                symbol=symbol,
                side=side.lower(),
                order_type="market",
                quantity=quantity,
                price=price,           # Expected price
                strategy=signal.get("strategy", "research"),
                signal_id=str(signal.get("id", "")),
            )
            db.update_order_status(order_id, "filled",
                                  filled_quantity=quantity,
                                  avg_fill_price=fill_price)  # Actual fill (with slippage)

            # Update position using fill price
            old_qty = existing["quantity"] if existing else 0
            old_entry = existing["avg_entry_price"] if existing else 0
            old_realized = existing["realized_pnl"] if existing else 0

            if side == "BUY":
                new_qty = old_qty + quantity
                new_entry = ((old_qty * (old_entry or fill_price)) + (quantity * fill_price)) / new_qty if new_qty else fill_price
                realized = old_realized
            else:
                sold_qty = min(quantity, old_qty)
                new_qty = old_qty - sold_qty
                new_entry = old_entry if new_qty > 0 else None
                realized = old_realized + (sold_qty * (fill_price - (old_entry or fill_price))) if old_entry else old_realized

            db.upsert_position(
                exchange="paper", symbol=symbol,
                quantity=new_qty, avg_entry_price=new_entry,
                current_price=fill_price, realized_pnl=realized,
                stop_loss=fill_price * (1 - STOP_LOSS_PCT),
                take_profit=fill_price * (1 + TAKE_PROFIT_PCT),
                trailing_stop=1,
                trailing_distance_pct=TRAILING_STOP_PCT,
                highest_price=fill_price,
            )

            db.insert_alert("trade",
                f"📝 PAPER {side} {symbol} {quantity:.4f} @ ${fill_price:.2f}",
                "info")

            log.info("✅ Paper trade: %s %s %.4f @ $%.2f (slippage: %.2f%%)",
                     side, symbol, quantity, fill_price, slippage_pct_display)
            self._completed_trades += 1

            # Build notification
            notify_msg = (
                f"📝 PAPER {side}\n"
                f"{symbol} {quantity:.4f} @ ${fill_price:.2f}\n"
                f"Slippage: {slippage_pct_display:.2f}% | "
                f"Confidence: {(signal.get('confidence', 0) or 0)*100:.0f}%"
            )

            # Slippage alert if excessive
            if slippage_pct_display > SLIPPAGE_ALERT_PCT:
                notify_msg += f"\n⚠️ HIGH SLIPPAGE ({slippage_pct_display:.2f}%)"
                db.insert_alert("system",
                    f"High slippage: {symbol} {side} — {slippage_pct_display:.2f}%",
                    "warning")

            self._notify_telegram(notify_msg)
            db.mark_signal_processed(signal["id"])

        except Exception as e:
            log.error("Paper trade failed: %s", e)

    def _execute_live(self, symbol: str, direction: str, quantity: float,
                      price: float, signal: Dict):
        """Execute real trade on exchange."""
        decision = LiveExecutionPolicy().evaluate(
            "live",
            risk_preflight_pass=not getattr(self, "halted", False),
            adapter_initialized=getattr(self, "adapter", None) is not None and getattr(self, "executor", None) is not None,
            credentials_available=getattr(self, "_credentials_available", False),
        )
        if not decision.allowed:
            self.mode = decision.effective_mode
            log.critical("Blocked live order for %s: reason=%s", symbol, decision.reason_code)
            return
        side = OrderSide.BUY if direction == "BUY" else OrderSide.SELL

        req = OrderRequest(
            exchange=ExchangeID(self.exchange_name),
            symbol=f"{symbol}/USDT",
            side=side,
            order_type=OrderType.MARKET,
            quantity=quantity,
        )

        report = self.executor.execute(req)

        # Place OCO exit orders (stop-loss + take-profit) for live positions
        if report.status == "filled" and report.filled_quantity > 0 and self.adapter:
            try:
                exit_side = OrderSide.SELL if side == OrderSide.BUY else OrderSide.BUY
                sl_price = (report.avg_fill_price or price) * (1 - STOP_LOSS_PCT)
                tp_price = (report.avg_fill_price or price) * (1 + TAKE_PROFIT_PCT)
                oco_result = self.adapter.create_oco_order(
                    symbol=f"{symbol}/USDT",
                    side=exit_side.value,
                    quantity=report.filled_quantity,
                    price=report.avg_fill_price or price,
                    stop_loss_price=sl_price,
                    take_profit_price=tp_price,
                )
                if oco_result:
                    log.info("OCO exit orders placed for %s: SL=%.2f TP=%.2f",
                            symbol, sl_price, tp_price)
            except Exception as e:
                log.warning("Failed to place OCO exit orders for %s: %s", symbol, e)

        # Alert
        emoji = "🟢" if direction == "BUY" else "🔴"
        msg = (
            f"{emoji} ORDER {report.status.upper()}\n"
            f"{symbol} {direction} {report.filled_quantity:.4f} @ ${report.avg_fill_price:.2f}\n"
            f"Fill: {report.filled_quantity/report.quantity*100:.0f}%"
        )
        if report.slippage_pct:
            msg += f" | Slippage: {report.slippage_pct:.2f}%"
        if report.error:
            msg += f"\n⚠️ {report.error}"

        db.insert_alert("trade", msg,
                       "error" if report.error else "info")

        self._notify_telegram(msg)

    # ── Risk Management ───────────────────────────────────────

    def _check_halt_conditions(self) -> bool:
        """Check if trading should be halted. Returns True if halted."""
        if not self.running:
            return True

        # Canonical kill switch shared with the research pipeline and dashboard.
        from kill_switch import is_kill_switch_active
        if is_kill_switch_active():
            self.halted = True
            log.warning("KILL SWITCH ACTIVE — deactivate the canonical sentinel to resume")
            db.insert_alert("system", "Kill switch active", "critical")
            if not self._halt_notified:
                self._notify_telegram("🚨 KILL SWITCH ACTIVE\nTrading halted.")
                self._halt_notified = True
            return True

        # Check daily loss limit
        if self.daily_pnl < -self.daily_loss_limit:
            self.halted = True
            log.error("DAILY LOSS LIMIT HIT: -$%.0f / -$%.0f",
                     abs(self.daily_pnl), self.daily_loss_limit)
            db.insert_alert("system",
                f"Daily loss limit hit: ${self.daily_pnl:.0f} (limit: -${self.daily_loss_limit:.0f})",
                "critical")
            if not self._halt_notified:
                self._notify_telegram(
                    f"🛑 DAILY LOSS LIMIT HIT\n"
                    f"Loss: ${self.daily_pnl:.0f} (limit: -${self.daily_loss_limit:.0f})\n"
                    f"Trading halted."
                )
                self._halt_notified = True
            return True

        # Check max drawdown from peak equity
        current_equity = self.start_equity + self.daily_pnl
        if self.peak_equity > 0 and current_equity < self.peak_equity - self.max_drawdown:
            dd_pct = (self.peak_equity - current_equity) / self.peak_equity * 100
            self.halted = True
            log.error("MAX DRAWDOWN HIT: peak=$%.0f current=$%.0f (%.1f%% drawdown)",
                     self.peak_equity, current_equity, dd_pct)
            db.insert_alert("system",
                f"Max drawdown hit: {dd_pct:.1f}% from peak ${self.peak_equity:.0f}",
                "critical")
            if not self._halt_notified:
                self._notify_telegram(
                    f"🛑 MAX DRAWDOWN HIT\n"
                    f"Drawdown: {dd_pct:.1f}% (limit: {MAX_DRAWDOWN_PCT*100:.0f}%)\n"
                    f"Peak: ${self.peak_equity:,.0f} → Current: ${current_equity:,.0f}\n"
                    f"Trading halted."
                )
                self._halt_notified = True
            return True

        # Reset halt notification flag if no longer halted
        self._halt_notified = False

        return False

    # ── Stop-Loss / Take-Profit ────────────────────────────────

    def _check_stops(self, prices: Dict):
        """Check all positions for stop-loss/take-profit triggers. Execute exits."""
        positions = db.get_positions()
        if not positions:
            return

        for pos in positions:
            symbol = pos["symbol"]
            tick = prices.get(symbol)
            if not tick:
                continue

            current_price = tick.price
            stop_loss = pos.get("stop_loss")
            take_profit = pos.get("take_profit")
            trailing = pos.get("trailing_stop")
            trail_pct = pos.get("trailing_distance_pct")
            qty = pos.get("quantity", 0)
            entry = pos.get("avg_entry_price") or 0

            if qty <= 0:
                continue

            # Update trailing stop: raise stop_loss as price rises
            if trailing and trail_pct:
                highest = max(pos.get("highest_price") or current_price, current_price)
                new_stop = highest * (1 - trail_pct)
                if new_stop > (stop_loss or 0):
                    stop_loss = new_stop
                    db.upsert_position(
                        exchange=pos.get("exchange", self.exchange_name),
                        symbol=symbol,
                        quantity=qty,
                        avg_entry_price=entry,
                        current_price=current_price,
                        unrealized_pnl=pos.get("unrealized_pnl", 0),
                        realized_pnl=pos.get("realized_pnl", 0),
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        trailing_stop=1,
                        trailing_distance_pct=trail_pct,
                        highest_price=highest,
                    )

            # Check stop-loss trigger
            if stop_loss and current_price <= stop_loss:
                loss_pct = (current_price - entry) / entry * 100 if entry else 0
                self._exit_position(symbol, current_price, qty, entry,
                                   f"Stop-loss: ${current_price:.2f} ≤ ${stop_loss:.2f} ({loss_pct:+.1f}%)",
                                   pos)
                continue

            # Check take-profit trigger
            if take_profit and current_price >= take_profit:
                gain_pct = (current_price - entry) / entry * 100 if entry else 0
                self._exit_position(symbol, current_price, qty, entry,
                                   f"Take-profit: ${current_price:.2f} ≥ ${take_profit:.2f} ({gain_pct:+.1f}%)",
                                   pos)
                continue

    def _exit_position(self, symbol: str, price: float, quantity: float,
                       entry_price: float, reason: str, pos: Dict):
        """Exit a position (paper SELL) with full tracking."""
        side = "SELL"
        realized = quantity * (price - entry_price) if entry_price else 0

        # Record the exit order
        order_id = db.insert_order(
            client_order_id=f"exit_{symbol}_{int(time.time())}",
            exchange=pos.get("exchange", "paper"),
            symbol=symbol,
            side="sell",
            order_type="market",
            quantity=quantity,
            price=price,
            strategy="stop_exit",
        )
        db.update_order_status(order_id, "filled",
                              filled_quantity=quantity,
                              avg_fill_price=price)

        # Update position to zero
        old_realized = pos.get("realized_pnl", 0)
        db.upsert_position(
            exchange=pos.get("exchange", "paper"),
            symbol=symbol,
            quantity=0,
            avg_entry_price=None,
            current_price=price,
            unrealized_pnl=0,
            realized_pnl=old_realized + realized,
            stop_loss=None,
            take_profit=None,
            trailing_stop=0,
            trailing_distance_pct=None,
            highest_price=None,
        )

        emoji = "🛑" if "Stop" in reason else "✅"
        log.info("%s %s: %s | PnL: $%.2f", emoji, reason, symbol, realized)

        db.insert_alert("trade",
            f"{emoji} {reason}\n{symbol}: {quantity:.4f} @ ${price:.2f} | PnL: ${realized:+.2f}",
            "warning" if "Stop" in reason else "info")

        self._notify_telegram(
            f"{emoji} EXIT: {symbol}\n"
            f"{reason}\n"
            f"Quantity: {quantity:.4f} @ ${price:.2f}\n"
            f"PnL: ${realized:+.2f}"
        )

    # ── Position Updates ──────────────────────────────────────

    def _measure_rest_latency(self) -> float:
        """Measure Binance REST API latency in milliseconds."""
        try:
            start = time.monotonic()
            req = urllib.request.Request(
                "https://api.binance.com/api/v3/ping",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                resp.read()
            return (time.monotonic() - start) * 1000
        except Exception:
            return float("inf")

    def _dump_live_prices(self, prices: Dict):
        """Write live prices + exchange health to file for dashboard SSE streaming."""
        try:
            tick_data = {}
            for sym, tick in prices.items():
                tick_data[sym] = {
                    "price": tick.price,
                    "change_pct_24h": round(tick.change_pct_24h, 2),
                    "volume_24h": tick.volume_24h,
                    "high_24h": tick.high_24h,
                }
            # Add exchange health metadata
            ws_connected = self.price_feed.connected if self.price_feed else False
            stream_count = len(self.price_feed._symbols) if self.price_feed else 0
            rest_latency_ms = self._measure_rest_latency()
            tick_data["_health"] = {
                "ws_connected": ws_connected,
                "stream_count": stream_count,
                "rest_latency_ms": round(rest_latency_ms, 1),
                "last_health_check": datetime.now(timezone.utc).isoformat(),
            }
            path = data_root() / "live_prices.json"
            path.write_text(json.dumps(tick_data))
        except Exception:
            pass  # Non-critical — dashboard will show stale data

    def _update_positions(self, prices: Dict):
        """Update current prices and unrealized PnL for all positions."""
        positions = db.get_positions()
        for pos in positions:
            tick = prices.get(pos["symbol"])
            if not tick:
                continue

            current_price = tick.price
            entry = pos.get("avg_entry_price") or 0
            qty = pos.get("quantity", 0)
            unrealized = qty * (current_price - entry) if entry and qty else 0

            db.upsert_position(
                exchange=pos.get("exchange", self.exchange_name),
                symbol=pos["symbol"],
                quantity=qty,
                avg_entry_price=entry,
                current_price=current_price,
                unrealized_pnl=unrealized,
                realized_pnl=pos.get("realized_pnl", 0),
                stop_loss=pos.get("stop_loss"),
                take_profit=pos.get("take_profit"),
                trailing_stop=pos.get("trailing_stop", 0),
                trailing_distance_pct=pos.get("trailing_distance_pct"),
                highest_price=pos.get("highest_price"),
            )

    # ── Equity Tracking ───────────────────────────────────────

    def _snapshot_equity(self):
        """Record equity snapshot."""
        positions = db.get_positions()

        positions_value = sum(
            abs((p.get("quantity") or 0) * (p.get("current_price") or 0))
            for p in positions
        )
        # Read realized PnL directly from positions table (pnl_summary can be stale)
        realized = sum((p.get("realized_pnl") or 0) for p in positions)
        unrealized = sum(
            (p.get("quantity") or 0) * ((p.get("current_price") or 0) - (p.get("avg_entry_price") or 0))
            for p in positions if (p.get("quantity") or 0) > 0 and (p.get("avg_entry_price") or 0) > 0
        )
        total_equity = self.capital + realized + unrealized

        self.daily_pnl = total_equity - self.start_equity

        # Track peak equity for drawdown calculation
        if total_equity > self.peak_equity:
            self.peak_equity = total_equity

        # Calculate drawdown percentage
        drawdown_pct = ((self.peak_equity - total_equity) / self.peak_equity * 100) if self.peak_equity > 0 else 0

        db.insert_equity_snapshot(
            total_equity=total_equity,
            cash=self.capital,
            positions_value=positions_value,
            unrealized_pnl=unrealized,
            realized_pnl=realized,
            daily_pnl=self.daily_pnl,
            drawdown_pct=round(drawdown_pct, 2),
        )

    # ── Notifications ─────────────────────────────────────────

    def _send_status(self):
        """Periodic status update."""
        # Only log every ~5 minutes (every 10 ticks at 30s interval)
        tick_count = int(db.get_state("tick_count") or "0") + 1
        db.set_state("tick_count", str(tick_count))

        if tick_count % 10 != 0:
            return

        positions = db.get_positions()
        pnl = db.get_pnl_summary()
        realized = pnl.get("total_realized_pnl", 0) or 0
        unrealized = pnl.get("total_unrealized_pnl", 0) or 0
        total = self.capital + realized + unrealized

        log.info("Status: equity=$%.0f | positions=%d | PnL: +$%.0f (U:$%.0f) | daily: $%.0f | mode=%s",
                total, len(positions), realized, unrealized, self.daily_pnl, self.mode)

        # Telegram status (every ~25 min with 5s ticks)
        if tick_count % 300 == 0:
            pos_list = "\n".join(
                f"  {p['symbol']}: {p['quantity']:.4f} @ ${p.get('avg_entry_price', 0):.2f}"
                for p in positions[:5]
            ) if positions else "  (none)"
            self._notify_telegram(
                f"📊 Status | {self.mode.upper()}\n"
                f"Equity: ${total:,.0f} | PnL: ${realized:+.0f} (U: ${unrealized:+.0f})\n"
                f"Daily: ${self.daily_pnl:+.0f} | Positions: {len(positions)}\n"
                f"{pos_list}"
            )

    def _notify_telegram(self, message: str):
        """Send execution alert via Telegram."""
        # This uses the separate trading bot to avoid spamming main Hermes
        token = os.environ.get("TELEGRAM_BOT_TOKEN",
                               os.environ.get("TRADING_TELEGRAM_TOKEN"))
        chat_id = os.environ.get("TELEGRAM_CHAT_ID",
                                 os.environ.get("TRADING_TELEGRAM_CHAT"))

        if not token or not chat_id:
            return

        try:
            import requests
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
                timeout=5,
            )
        except Exception:
            pass

    # ── Shutdown ──────────────────────────────────────────────

    def _handle_shutdown(self, signum, frame):
        log.info("Shutdown signal received (%s)", signum)
        self.running = False

    def _shutdown(self):
        """Clean shutdown."""
        log.info("Shutting down...")
        if self.price_feed:
            self.price_feed.stop()

        # Final equity snapshot
        self._snapshot_equity()

        # Log final state
        positions = db.get_positions()
        pnl = db.get_pnl_summary()
        log.info("=" * 60)
        log.info("TRADING AGENT STOPPED")
        log.info("Mode: %s | Positions: %d", self.mode, len(positions))
        log.info("Realized PnL: $%.0f | Unrealized: $%.0f",
                pnl.get("total_realized_pnl", 0) or 0,
                pnl.get("total_unrealized_pnl", 0) or 0)
        log.info("=" * 60)


# ── CLI ────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Trading Agent")
    parser.add_argument("--mode", choices=["paper", "live", "dryrun"], default="paper",
                       help="Trading mode (default: paper)")
    parser.add_argument("--exchange", default="coinbase",
                       help="Exchange to use (default: coinbase)")
    parser.add_argument("--symbols", type=str,
                       help="Comma-separated symbols (e.g., BTC,ETH,SOL)")
    parser.add_argument("--capital", type=float, default=DEFAULT_CAPITAL,
                       help=f"Starting capital in USD (default: {DEFAULT_CAPITAL})")
    parser.add_argument("--once", action="store_true",
                       help="Run one cycle and exit")
    args = parser.parse_args()

    symbols = args.symbols.split(",") if args.symbols else None
    if symbols:
        # Normalize to CCXT format
        symbols = [f"{s.strip().upper()}/USDT" for s in symbols]

    agent = TradingAgent(
        mode=args.mode,
        exchange_name=args.exchange,
        symbols=symbols,
        capital=args.capital,
    )
    agent.run(once=args.once)


if __name__ == "__main__":
    main()
