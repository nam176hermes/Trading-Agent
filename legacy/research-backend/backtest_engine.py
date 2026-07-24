#!/usr/bin/env python3
"""
Event-driven backtesting engine for crypto + stock strategies.

Architecture: DataHandler → Strategy → Broker → Portfolio → Recorder
Inspired by zipline but dependency-free beyond pandas/numpy/lightgbm.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import json
import logging
import numpy as np
import pandas as pd

from risk_engine import funding_aware_pnl
from runtime_paths import data_root

log = logging.getLogger("backtest_engine")

PROJECT_ROOT = Path(__file__).parent
BACKTEST_DIR = data_root() / "memory" / "backtest"
MODELS_DIR = data_root() / "models"
CACHE_DIR = BACKTEST_DIR / "cache"


# ── Config ──────────────────────────────────────────────────────────────────────

@dataclass
class BacktestConfig:
    initial_capital: float = 10_000.0
    commission_pct: float = 0.001       # 0.1%
    slippage_pct: float = 0.0005        # 0.05%
    position_size_pct: float = 0.2      # 20% max per position
    max_positions: int = 5
    stop_loss_pct: float = 0.05
    take_profit_pct: float = 0.10
    funding_rate_pct: float = 0.0001   # 0.01% per 8h funding interval

    def to_dict(self) -> dict:
        return {
            "initial_capital": self.initial_capital,
            "commission_pct": self.commission_pct,
            "slippage_pct": self.slippage_pct,
            "position_size_pct": self.position_size_pct,
            "max_positions": self.max_positions,
            "stop_loss_pct": self.stop_loss_pct,
            "take_profit_pct": self.take_profit_pct,
            "funding_rate_pct": self.funding_rate_pct,
        }


# ── Data Structures ─────────────────────────────────────────────────────────────

@dataclass
class Bar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Signal:
    action: str            # BUY, SELL, HOLD
    confidence: float
    stop_loss_pct: float
    take_profit_pct: float


@dataclass
class Order:
    symbol: str
    side: str              # BUY, SELL
    quantity: float
    order_type: str        # MARKET
    stop_loss_pct: float
    take_profit_pct: float
    created_at: int


@dataclass
class Fill:
    timestamp: datetime
    symbol: str
    side: str
    quantity: float
    price: float
    commission: float
    bar_index: int


@dataclass
class Position:
    symbol: str
    side: str
    entry_price: float
    quantity: float
    stop_loss_price: float
    take_profit_price: float
    entry_time: datetime
    entry_bar: int
    entry_notional: float = 0.0


# ── DataHandler ─────────────────────────────────────────────────────────────────

class DataHandler:
    """Loads OHLCV from CCXT (crypto) or yfinance (stocks), caches to disk."""

    def __init__(self, symbol: str, start: str, end: str, timeframe: str = "1h"):
        self.symbol = symbol
        self.start = start
        self.end = end
        self.timeframe = timeframe
        self._df: Optional[pd.DataFrame] = None

    def load(self) -> Optional[pd.DataFrame]:
        cache_file = CACHE_DIR / f"{self.symbol.replace('/', '_')}_{self.start}_{self.end}_{self.timeframe}.csv"
        if cache_file.exists():
            df = pd.read_csv(cache_file, parse_dates=["timestamp"], index_col="timestamp")
            df.sort_index(inplace=True)
            self._df = df
            return df

        df = self._fetch()
        if df is not None and not df.empty:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            df.to_csv(cache_file)
        self._df = df
        return df

    def _fetch(self) -> Optional[pd.DataFrame]:
        # Check existing historical CSVs first (from ml_predictor data pipeline)
        base = _base_symbol(self.symbol)
        historical_path = BACKTEST_DIR / "historical" / f"{base.upper()}_1h.csv"
        if historical_path.exists() and self.timeframe == "1h":
            df = pd.read_csv(historical_path, parse_dates=["timestamp"], index_col="timestamp")
            df.sort_index(inplace=True)
            # Filter to requested date range
            start_ts = pd.Timestamp(self.start, tz="UTC")
            end_ts = pd.Timestamp(self.end, tz="UTC")
            df = df[(df.index >= start_ts) & (df.index <= end_ts)]
            if not df.empty:
                log.info("Loaded %d candles from historical CSV: %s", len(df), historical_path)
                return df

        if "/" in self.symbol:
            return self._fetch_ccxt()
        return self._fetch_yfinance()

    def _fetch_ccxt(self) -> Optional[pd.DataFrame]:
        try:
            import ccxt
        except ImportError:
            log.error("ccxt not installed")
            return None

        exchange = ccxt.binance()
        since = exchange.parse8601(f"{self.start}T00:00:00Z")
        end_ts = exchange.parse8601(f"{self.end}T23:59:59Z")
        tf_map = {"1h": "1h", "4h": "4h", "1d": "1d"}
        tf = tf_map.get(self.timeframe, "1h")

        all_ohlcv = []
        while since < end_ts:
            try:
                ohlcv = exchange.fetch_ohlcv(self.symbol, tf, since=since, limit=1000)
                if not ohlcv:
                    break
                all_ohlcv.extend(ohlcv)
                since = ohlcv[-1][0] + 1
            except Exception as e:
                log.warning("CCXT fetch for %s: %s", self.symbol, e)
                break

        if not all_ohlcv:
            log.warning("No data fetched for %s", self.symbol)
            return None

        df = pd.DataFrame(all_ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df.set_index("timestamp", inplace=True)
        df.sort_index(inplace=True)
        df = df[~df.index.duplicated()]
        log.info("CCXT: %d candles for %s", len(df), self.symbol)
        return df

    def _fetch_yfinance(self) -> Optional[pd.DataFrame]:
        try:
            import yfinance as yf
        except ImportError:
            log.error("yfinance not installed")
            return None

        ticker = yf.Ticker(self.symbol)
        tf_map = {"1h": "1h", "4h": "4h", "1d": "1d"}
        interval = tf_map.get(self.timeframe, "1h")
        df = ticker.history(start=self.start, end=self.end, interval=interval)
        if df.empty:
            log.warning("yfinance: no data for %s", self.symbol)
            return None
        df = df.rename(columns={"Open": "open", "High": "high", "Low": "low",
                                "Close": "close", "Volume": "volume"})
        df.index.name = "timestamp"
        keep_cols = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
        df = df[keep_cols]
        log.info("yfinance: %d candles for %s", len(df), self.symbol)
        return df

    @property
    def df(self) -> pd.DataFrame:
        if self._df is None:
            self.load()
        return self._df if self._df is not None else pd.DataFrame()

    def to_bars(self, df: pd.DataFrame) -> list[Bar]:
        bars = []
        for idx, row in df.iterrows():
            try:
                o, h, l, c, v = (float(row["open"]), float(row["high"]),
                                  float(row["low"]), float(row["close"]),
                                  float(row["volume"]))
                if not all(np.isfinite(x) for x in (o, h, l, c, v)):
                    continue
                bars.append(Bar(timestamp=idx, open=o, high=h, low=l, close=c, volume=v))
            except (ValueError, TypeError):
                continue
        return bars


# ── Strategy Base ───────────────────────────────────────────────────────────────

class Strategy:
    """Base strategy. Override next() to implement trading logic."""

    def __init__(self, config: BacktestConfig):
        self.config = config
        self._df: Optional[pd.DataFrame] = None

    def set_data(self, df: pd.DataFrame):
        self._df = df

    def next(self, i: int, bar: Bar, positions: list[Position]) -> Signal:
        return Signal(action="HOLD", confidence=0.0, stop_loss_pct=0.0, take_profit_pct=0.0)


# ── Portfolio ───────────────────────────────────────────────────────────────────

class Portfolio:
    """Tracks cash, positions, equity curve. Handles fills with slippage + commission."""

    def __init__(self, config: BacktestConfig):
        self.config = config
        self.cash = config.initial_capital
        self.positions: list[Position] = []
        self._equity_history: list[tuple[datetime, float]] = []
        self._fill_history: list[Fill] = []
        self._trade_log: list[dict] = []
        self._peak_equity = config.initial_capital
        self._max_drawdown = 0.0
        self.total_funding_paid = 0.0

    @property
    def position_count(self) -> int:
        return len(self.positions)

    def compute_equity(self, bar: Bar) -> float:
        value = self.cash
        for pos in self.positions:
            value += pos.quantity * bar.close
        return value

    def record_snapshot(self, timestamp: datetime, bar: Bar):
        eq = self.compute_equity(bar)
        self._equity_history.append((timestamp, eq))
        if eq > self._peak_equity:
            self._peak_equity = eq
        dd = (self._peak_equity - eq) / self._peak_equity if self._peak_equity > 0 else 0.0
        if dd > self._max_drawdown:
            self._max_drawdown = dd

    def check_exits(self, bar: Bar, bar_idx: int) -> list[Fill]:
        fills = []
        for pos in list(self.positions):
            exit_price = None
            exit_reason = None

            if pos.side == "BUY":
                if bar.low <= pos.stop_loss_price:
                    exit_price = pos.stop_loss_price
                    exit_reason = "stop_loss"
                elif bar.high >= pos.take_profit_price:
                    exit_price = pos.take_profit_price
                    exit_reason = "take_profit"
            else:
                if bar.high >= pos.stop_loss_price:
                    exit_price = pos.stop_loss_price
                    exit_reason = "stop_loss"
                elif bar.low <= pos.take_profit_price:
                    exit_price = pos.take_profit_price
                    exit_reason = "take_profit"

            if exit_price is not None:
                slippage = exit_price * self.config.slippage_pct
                fill_price = exit_price + slippage if pos.side == "SELL" else exit_price - slippage
                commission = fill_price * pos.quantity * self.config.commission_pct

                pnl = (fill_price - pos.entry_price) * pos.quantity
                if pos.side == "SELL":
                    pnl = (pos.entry_price - fill_price) * pos.quantity
                pnl -= commission
                pnl -= pos.entry_price * pos.quantity * self.config.commission_pct

                # Funding rate adjustment
                bars_held = bar_idx - pos.entry_bar
                adjusted_pnl, funding_cost = funding_aware_pnl(
                    pnl, pos.symbol, self.config.funding_rate_pct,
                    pos.entry_notional, bars_held
                )
                pnl = adjusted_pnl
                self.total_funding_paid += funding_cost

                fill = Fill(
                    timestamp=bar.timestamp, symbol=pos.symbol,
                    side="SELL" if pos.side == "BUY" else "BUY",
                    quantity=pos.quantity, price=fill_price,
                    commission=commission, bar_index=bar_idx,
                )
                fills.append(fill)
                self.cash += fill_price * pos.quantity - commission

                self._trade_log.append({
                    "entry_time": pos.entry_time.isoformat(),
                    "exit_time": bar.timestamp.isoformat(),
                    "symbol": pos.symbol,
                    "side": pos.side,
                    "entry_price": pos.entry_price,
                    "exit_price": fill_price,
                    "quantity": pos.quantity,
                    "pnl": round(pnl, 4),
                    "pnl_pct": round(pnl / (pos.entry_price * pos.quantity), 6),
                    "exit_reason": exit_reason,
                    "funding_cost": round(funding_cost, 4),
                    "bars_held": bars_held,
                })
                self.positions.remove(pos)

        self._fill_history.extend(fills)
        return fills

    def apply_fill(self, fill: Fill):
        cost = fill.price * fill.quantity
        self.cash -= cost + fill.commission

        if fill.side == "BUY":
            sl = fill.price * (1 - self.config.stop_loss_pct)
            tp = fill.price * (1 + self.config.take_profit_pct)
        else:
            sl = fill.price * (1 + self.config.stop_loss_pct)
            tp = fill.price * (1 - self.config.take_profit_pct)

        self.positions.append(Position(
            symbol=fill.symbol, side=fill.side,
            entry_price=fill.price, quantity=fill.quantity,
            stop_loss_price=sl, take_profit_price=tp,
            entry_time=fill.timestamp, entry_bar=fill.bar_index,
            entry_notional=fill.price * fill.quantity,
        ))
        self._fill_history.append(fill)

    def close_all(self, bar: Bar, bar_idx: int) -> list[Fill]:
        fills = []
        for pos in list(self.positions):
            fill_price = bar.close
            slippage = fill_price * self.config.slippage_pct
            fill_price += -slippage if pos.side == "BUY" else slippage
            commission = fill_price * pos.quantity * self.config.commission_pct

            pnl = (fill_price - pos.entry_price) * pos.quantity
            if pos.side == "SELL":
                pnl = (pos.entry_price - fill_price) * pos.quantity
            pnl -= commission
            pnl -= pos.entry_price * pos.quantity * self.config.commission_pct

            # Funding rate adjustment
            bars_held = bar_idx - pos.entry_bar
            adjusted_pnl, funding_cost = funding_aware_pnl(
                pnl, pos.symbol, self.config.funding_rate_pct,
                pos.entry_notional, bars_held
            )
            pnl = adjusted_pnl
            self.total_funding_paid += funding_cost

            fill = Fill(
                timestamp=bar.timestamp, symbol=pos.symbol,
                side="SELL" if pos.side == "BUY" else "BUY",
                quantity=pos.quantity, price=fill_price,
                commission=commission, bar_index=bar_idx,
            )
            fills.append(fill)
            self.cash += fill_price * pos.quantity - commission

            self._trade_log.append({
                "entry_time": pos.entry_time.isoformat(),
                "exit_time": bar.timestamp.isoformat(),
                "symbol": pos.symbol,
                "side": pos.side,
                "entry_price": pos.entry_price,
                "exit_price": fill_price,
                "quantity": pos.quantity,
                "pnl": round(pnl, 4),
                "pnl_pct": round(pnl / (pos.entry_price * pos.quantity), 6) if pos.quantity > 0 else 0.0,
                "exit_reason": "end_of_test",
                "funding_cost": round(funding_cost, 4),
                "bars_held": bars_held,
            })
        self.positions.clear()
        self._fill_history.extend(fills)
        return fills

    @property
    def equity_curve(self) -> list[tuple[datetime, float]]:
        return self._equity_history

    @property
    def trade_log(self) -> list[dict]:
        return self._trade_log

    @property
    def max_drawdown_pct(self) -> float:
        return self._max_drawdown


# ── Broker ──────────────────────────────────────────────────────────────────────

class Broker:
    """Simulates order execution. Market orders fill at next bar open."""

    def __init__(self, config: BacktestConfig):
        self.config = config
        self._pending: list[Order] = []

    def place_order(self, signal: Signal, bar: Bar, bar_idx: int,
                    portfolio: Portfolio, symbol: str) -> Optional[Order]:
        if signal.action == "HOLD":
            return None

        equity = portfolio.compute_equity(bar)
        position_value = equity * self.config.position_size_pct

        if signal.action == "BUY" and portfolio.position_count >= self.config.max_positions:
            return None

        if signal.action == "BUY":
            quantity = position_value / bar.close
            side = "BUY"
        elif signal.action == "SELL":
            existing = next((p for p in portfolio.positions if p.side == "BUY"), None)
            quantity = existing.quantity if existing else position_value / bar.close
            side = "SELL"
        else:
            return None

        if quantity <= 0:
            return None

        order = Order(
            symbol=symbol, side=side, quantity=quantity, order_type="MARKET",
            stop_loss_pct=signal.stop_loss_pct if signal.stop_loss_pct > 0 else self.config.stop_loss_pct,
            take_profit_pct=signal.take_profit_pct if signal.take_profit_pct > 0 else self.config.take_profit_pct,
            created_at=bar_idx,
        )
        self._pending.append(order)
        return order

    def process_orders(self, bar: Bar, bar_idx: int, portfolio: Portfolio,
                       symbol: str) -> list[Fill]:
        fills = []
        for order in list(self._pending):
            if order.symbol != symbol:
                continue

            fill_price = bar.open
            slippage = fill_price * self.config.slippage_pct
            fill_price += slippage if order.side == "BUY" else -slippage
            commission = fill_price * order.quantity * self.config.commission_pct

            cost = fill_price * order.quantity + commission
            if cost > portfolio.cash:
                self._pending.remove(order)
                continue

            fill = Fill(
                timestamp=bar.timestamp, symbol=order.symbol,
                side=order.side, quantity=order.quantity,
                price=fill_price, commission=commission, bar_index=bar_idx,
            )
            fills.append(fill)
            portfolio.apply_fill(fill)
            self._pending.remove(order)

        return fills


# ── Recorder ────────────────────────────────────────────────────────────────────

class Recorder:
    """Logs every event: signals, fills, equity snapshots."""

    def __init__(self):
        self.signals: list[dict] = []
        self.fills: list[dict] = []
        self.equity_snapshots: list[dict] = []

    def record_signal(self, bar_idx: int, timestamp: datetime, signal: Signal):
        self.signals.append({
            "bar_index": bar_idx,
            "timestamp": timestamp.isoformat(),
            "action": signal.action,
            "confidence": signal.confidence,
            "stop_loss_pct": signal.stop_loss_pct,
            "take_profit_pct": signal.take_profit_pct,
        })

    def record_fill(self, fill: Fill):
        self.fills.append({
            "timestamp": fill.timestamp.isoformat(),
            "symbol": fill.symbol,
            "side": fill.side,
            "quantity": fill.quantity,
            "price": fill.price,
            "commission": fill.commission,
            "bar_index": fill.bar_index,
        })

    def record_equity(self, timestamp: datetime, equity: float):
        self.equity_snapshots.append({"timestamp": timestamp.isoformat(), "equity": equity})


# ── Metrics ─────────────────────────────────────────────────────────────────────

def _annual_factor(timeframe: str = "1h") -> float:
    return {"1h": np.sqrt(24 * 365), "4h": np.sqrt(6 * 365), "1d": np.sqrt(365)}.get(
        timeframe, np.sqrt(8760))


def compute_sharpe(returns: list[float], annual_factor: float = np.sqrt(8760)) -> float:
    if len(returns) < 3:
        return 0.0
    arr = np.array(returns)
    mean = arr.mean()
    std = arr.std(ddof=1)
    return float(mean / std * annual_factor) if std > 0 else 0.0


def compute_sortino(returns: list[float], annual_factor: float = np.sqrt(8760)) -> float:
    if len(returns) < 3:
        return 0.0
    arr = np.array(returns)
    mean = arr.mean()
    downside = arr[arr < 0]
    if len(downside) < 2:
        return 0.0
    downside_std = downside.std(ddof=1)
    return float(mean / downside_std * annual_factor) if downside_std > 0 else 0.0


def compute_max_drawdown_pct(equity_values: list[float]) -> float:
    if not equity_values:
        return 0.0
    peak = equity_values[0]
    max_dd = 0.0
    for eq in equity_values:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    return max_dd


def compute_calmar(total_return_pct: float, max_dd_pct: float) -> float:
    if max_dd_pct <= 0:
        return 0.0
    return abs(total_return_pct / (max_dd_pct * 100))


def compute_profit_factor(trades: list[dict]) -> float:
    gross_profit = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gross_loss = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0))
    if gross_loss == 0:
        return 1.0 if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def compute_equity_returns(equity_curve: list[tuple[datetime, float]]) -> list[float]:
    if len(equity_curve) < 2:
        return []
    rets = []
    for i in range(1, len(equity_curve)):
        prev = equity_curve[i - 1][1]
        curr = equity_curve[i][1]
        if prev > 0:
            rets.append((curr - prev) / prev)
    return rets


def compute_aggregate_metrics(
    equity_curve: list[tuple[datetime, float]],
    trades: list[dict],
    config: BacktestConfig,
    total_funding_paid: float = 0.0,
    timeframe: str = "1h",
) -> dict:
    ann_factor = _annual_factor(timeframe)
    eq_rets = compute_equity_returns(equity_curve)
    eq_values = [e[1] for e in equity_curve]

    total_pnl = sum(t["pnl"] for t in trades)
    total_pnl_pct = (total_pnl / config.initial_capital) * 100
    max_dd = compute_max_drawdown_pct(eq_values)

    win_count = sum(1 for t in trades if t["pnl"] > 0)
    avg_win_rate = win_count / len(trades) if trades else 0.0

    avg_duration = 0.0
    if trades:
        durations = []
        for t in trades:
            try:
                entry = datetime.fromisoformat(t["entry_time"])
                exit_t = datetime.fromisoformat(t["exit_time"])
                durations.append((exit_t - entry).total_seconds() / 3600)
            except (ValueError, KeyError):
                pass
        avg_duration = float(np.mean(durations)) if durations else 0.0

    sharpe = compute_sharpe(eq_rets, ann_factor)
    sortino = compute_sortino(eq_rets, ann_factor)
    calmar = compute_calmar(total_pnl_pct, max_dd) if max_dd > 0 else 0.0
    profit_factor = compute_profit_factor(trades)

    final_equity = eq_values[-1] if eq_values else config.initial_capital

    return {
        "sharpe": round(sharpe, 4),
        "sortino": round(sortino, 4),
        "avg_win_rate": round(avg_win_rate, 4),
        "total_pnl": round(total_pnl, 4),
        "total_pnl_pct": round(total_pnl_pct, 4),
        "max_drawdown_pct": round(max_dd * 100, 4),
        "calmar": round(calmar, 4),
        "profit_factor": round(profit_factor, 4),
        "total_trades": len(trades),
        "avg_trade_duration_hours": round(avg_duration, 2),
        "final_equity": round(final_equity, 4),
        "total_funding_paid": total_funding_paid,
    }


# ── BacktestEngine ──────────────────────────────────────────────────────────────

class BacktestEngine:
    """Orchestrates the event-driven backtest loop."""

    def __init__(self, symbol: str, strategy: Strategy, config: BacktestConfig = None,
                 data_handler: DataHandler = None, timeframe: str = "1h"):
        self.symbol = symbol
        self.config = config or BacktestConfig()
        self.strategy = strategy
        self.data_handler = data_handler
        self.timeframe = timeframe
        self.portfolio = Portfolio(self.config)
        self.broker = Broker(self.config)
        self.recorder = Recorder()

    def run(self) -> dict:
        if self.data_handler is None:
            raise ValueError("data_handler is required")

        df = self.data_handler.load()
        if df is None or df.empty:
            log.error("No data for %s", self.symbol)
            return self._empty_result()

        self.strategy.set_data(df)
        bars = self.data_handler.to_bars(df)

        if len(bars) < 50:
            log.warning("Only %d bars for %s — insufficient for backtest", len(bars), self.symbol)
            return self._empty_result()

        log.info("Running backtest: %s, %d bars, capital=%.0f",
                 self.symbol, len(bars), self.config.initial_capital)

        for i, bar in enumerate(bars):
            # 1. Check exits (stop loss / take profit)
            exit_fills = self.portfolio.check_exits(bar, i)
            for fill in exit_fills:
                self.recorder.record_fill(fill)

            # 2. Process pending orders (market orders fill at bar open)
            entry_fills = self.broker.process_orders(bar, i, self.portfolio, self.symbol)
            for fill in entry_fills:
                self.recorder.record_fill(fill)

            # 3. Strategy evaluation
            signal = self.strategy.next(i, bar, self.portfolio.positions)
            self.recorder.record_signal(i, bar.timestamp, signal)

            # 4. Create orders from signal
            if signal.action in ("BUY", "SELL"):
                self.broker.place_order(signal, bar, i, self.portfolio, self.symbol)

            # 5. Record equity snapshot
            self.portfolio.record_snapshot(bar.timestamp, bar)

        # Close remaining positions at last bar
        if self.portfolio.positions and bars:
            self.portfolio.close_all(bars[-1], len(bars) - 1)

        metrics = compute_aggregate_metrics(
            self.portfolio.equity_curve, self.portfolio.trade_log,
            self.config, self.portfolio.total_funding_paid, self.timeframe,
        )

        log.info("Backtest complete: Sharpe=%.4f, PnL=%.2f, Trades=%d",
                 metrics["sharpe"], metrics["total_pnl"], metrics["total_trades"])

        return {
            "symbol": self.symbol,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "config": self.config.to_dict(),
            "aggregate": metrics,
            "windows": [],
            "equity_curve": [(ts.isoformat(), round(eq, 4)) for ts, eq in self.portfolio.equity_curve],
            "trades": self.portfolio.trade_log,
        }

    def _empty_result(self) -> dict:
        return {
            "symbol": self.symbol,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "config": self.config.to_dict(),
            "aggregate": {"sharpe": 0.0, "sortino": 0.0, "avg_win_rate": 0.0, "total_pnl": 0.0,
                          "total_pnl_pct": 0.0, "max_drawdown_pct": 0.0, "calmar": 0.0,
                          "profit_factor": 0.0, "total_trades": 0, "avg_trade_duration_hours": 0.0,
                          "final_equity": self.config.initial_capital, "total_funding_paid": 0.0},
            "windows": [],
            "equity_curve": [],
            "trades": [],
        }


# ── MLStrategy ──────────────────────────────────────────────────────────────────

def _base_symbol(symbol: str) -> str:
    """Extract base symbol from pair (e.g. 'BTC/USDT' → 'btc')."""
    return symbol.split("/")[0].lower()


class MLStrategy(Strategy):
    """Strategy using a LightGBM model to predict direction at each bar."""

    def __init__(self, config: BacktestConfig, symbol: str = "BTC",
                 model=None, prob_threshold: float = 0.55):
        super().__init__(config)
        self.symbol = _base_symbol(symbol)
        self.model = model
        self.prob_threshold = prob_threshold
        self._features: Optional[pd.DataFrame] = None
        self._feature_cols: Optional[list[str]] = None
        self._platt_a: Optional[float] = None
        self._platt_b: Optional[float] = None

    def set_data(self, df: pd.DataFrame):
        super().set_data(df)
        self._precompute_features(df)

    def _precompute_features(self, df: pd.DataFrame):
        from ml_predictor import generate_features
        feats = generate_features(df)
        self._features = feats
        skip = {"target_1h_return", "target_up"}
        self._feature_cols = [c for c in feats.columns
                              if feats[c].dtype in ("float64", "float32", "int64", "int32")
                              and c not in skip]

    def _load_model(self):
        if self.model is not None:
            return
        path = MODELS_DIR / f"{self.symbol}_lightgbm_latest.txt"
        if not path.exists():
            log.warning("No model at %s — MLStrategy stays HOLD", path)
            return
        import lightgbm as lgb
        self.model = lgb.Booster(model_file=str(path))

        cal_path = MODELS_DIR / f"{self.symbol}_lightgbm_latest.json"
        if cal_path.exists():
            meta = json.loads(cal_path.read_text())
            self._platt_a = meta.get("platt_a")
            self._platt_b = meta.get("platt_b")

    def init(self):
        self._load_model()

    def next(self, i: int, bar: Bar, positions: list[Position]) -> Signal:
        if self.model is None or self._features is None or self._feature_cols is None:
            return Signal(action="HOLD", confidence=0.0, stop_loss_pct=0.0, take_profit_pct=0.0)

        if i >= len(self._features):
            return Signal(action="HOLD", confidence=0.0, stop_loss_pct=0.0, take_profit_pct=0.0)

        row = self._features[self._feature_cols].iloc[i]
        if row.isna().any():
            return Signal(action="HOLD", confidence=0.0, stop_loss_pct=0.0, take_profit_pct=0.0)

        try:
            raw = self.model.predict(row.values.reshape(1, -1))[0]
            up_prob = float(1.0 / (1.0 + np.exp(-raw)))

            if self._platt_a is not None and self._platt_b is not None:
                eps = 1e-12
                logit = np.log((up_prob + eps) / (1 - up_prob + eps))
                up_prob = float(1.0 / (1.0 + np.exp(-(self._platt_a * logit + self._platt_b))))
        except Exception as e:
            log.debug("Prediction error at bar %d: %s", i, e)
            return Signal(action="HOLD", confidence=0.0, stop_loss_pct=0.0, take_profit_pct=0.0)

        if positions:
            return Signal(action="HOLD", confidence=up_prob, stop_loss_pct=0.0, take_profit_pct=0.0)

        if up_prob >= self.prob_threshold:
            return Signal(
                action="BUY",
                confidence=round(up_prob, 4),
                stop_loss_pct=self.config.stop_loss_pct,
                take_profit_pct=self.config.take_profit_pct,
            )
        return Signal(action="HOLD", confidence=round(up_prob, 4), stop_loss_pct=0.0, take_profit_pct=0.0)


# ── BaselineStrategy ────────────────────────────────────────────────────────────

class BaselineStrategy(Strategy):
    """Buy and hold at first bar, sell at last bar."""

    def __init__(self, config: BacktestConfig):
        super().__init__(config)
        self._entered = False

    def next(self, i: int, bar: Bar, positions: list[Position]) -> Signal:
        if not self._entered and i == 0:
            self._entered = True
            return Signal(action="BUY", confidence=1.0, stop_loss_pct=0.0, take_profit_pct=0.0)
        return Signal(action="HOLD", confidence=0.0, stop_loss_pct=0.0, take_profit_pct=0.0)
