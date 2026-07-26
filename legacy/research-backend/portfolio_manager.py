"""
portfolio_manager.py — Portfolio Manager / Final Judge

Acts as the FINAL WORD in the trading pipeline. Reviews debate results,
risk assessments, and analyst reports to decide whether to:
  - RATIFY: Accept the signal as-is
  - MODIFY: Adjust position size, stops, or action
  - REJECT: Override the signal entirely

This is the portfolio manager's desk — conservative, risk-aware,
and empowered to say NO to the trading floor's recommendations.
"""

import logging
from typing import Optional
from pydantic import ValidationError
import pandas as pd

from schemas import (
    TradingDecision,
    TradingSignal,
    PortfolioDecision,
    DebateRound,
    RiskAssessment,
)
from signal_parser import parse_structured, build_schema_prompt
from runtime_paths import data_root, reports_dir as runtime_reports_dir

log = logging.getLogger("portfolio_manager")

MIN_ACCEPTANCE_RATIO = 0.33  # At least 1 of 3 risk personas must accept
MIN_PM_CONVICTION = 0.35  # Portfolio manager's own conviction threshold (lowered for low-confidence trades)


def adjust_for_fundamentals(symbol: str, base_size: float) -> tuple[float, list[str]]:
    """
    Fetch live fundamentals from yfinance and apply earnings-aware position sizing.
    Returns (adjusted_size, adjustment_reasons).
    """
    from live_data import fetch_fundamentals

    reasons = []
    size = base_size

    data = fetch_fundamentals([symbol])
    fund = data.get(symbol)
    if not fund:
        return size, reasons

    pe = fund.get("trailingPE")
    if pe is not None and pe > 50:
        reduction = size * 0.25
        size -= reduction
        reasons.append("P/E {:.1f} > 50 → −25% size".format(pe))

    rev_growth = fund.get("revenueGrowth")
    if rev_growth is not None and rev_growth < 0:
        reduction = size * 0.15
        size -= reduction
        reasons.append("Revenue growth {:.1%} < 0 → −15% size".format(rev_growth))

    debt_equity = fund.get("debtToEquity")
    if debt_equity is not None and debt_equity > 2.0:
        reduction = size * 0.20
        size -= reduction
        reasons.append("Debt/Equity {:.2f} > 2.0 → −20% size".format(debt_equity))

    roe = fund.get("returnOnEquity")
    if roe is not None and roe > 0.20:
        increase = size * 0.10
        size += increase
        reasons.append("ROE {:.1%} > 20% → +10% size".format(roe))

    return max(size, 0.0), reasons


CRYPTO_SYMBOLS = {"BTC", "ETH", "SOL", "TON", "DOGE", "ADA", "AVAX", "DOT", "LINK", "MATIC"}
STOCK_SYMBOLS = {"AAPL", "NVDA", "MSFT", "GOOGL", "AMZN", "META", "TSLA"}
ETF_SYMBOLS = {"SPY", "QQQ"}
FOREX_SYMBOLS = {"EURUSD=X"}

CONCENTRATION_THRESHOLD = 0.40  # 40% in one class triggers reduction
CONCENTRATION_REDUCTION = 0.30  # 30% reduction for new signals in concentrated class

# GAP 3: Correlation computation using real rolling-window Pearson correlation
# Replaces static correlation groups with data-driven computation

def compute_correlation_matrix(positions: dict, lookback_days: int = 90) -> dict:
    """
    Download 90d daily closes for all watchlist+position symbols, compute
    Pearson correlation matrix (rolling 30d). Store in memory/correlation_matrix.json.
    Returns dict with correlation data.
    """
    import json as _json

    try:
        import yfinance as yf
        import numpy as np
    except ImportError:
        return {}

    # Collect all symbols: watchlist + existing positions
    all_symbols = set()
    for sym in positions:
        all_symbols.add(sym.upper())
    for sym in list(CRYPTO_SYMBOLS) + list(STOCK_SYMBOLS) + list(ETF_SYMBOLS) + list(FOREX_SYMBOLS):
        all_symbols.add(sym)

    all_symbols = sorted(all_symbols)
    if len(all_symbols) < 2:
        return {}

    # Map symbols to yfinance tickers
    yf_tickers = []
    symbol_map = {}
    for sym in all_symbols:
        t = sym
        if sym in CRYPTO_SYMBOLS:
            t = sym + "-USD"  # yfinance crypto
        elif sym in FOREX_SYMBOLS:
            t = sym  # already has =X suffix
        elif sym in ETF_SYMBOLS or sym in STOCK_SYMBOLS:
            t = sym
        yf_tickers.append(t)
        symbol_map[t] = sym

    try:
        data = yf.download(yf_tickers, period=f"{lookback_days}d", interval="1d", progress=False, auto_adjust=True)
    except Exception:
        return {}

    if data is None or data.empty:
        return {}

    # Extract close prices
    closes = None
    if isinstance(data.columns, pd.MultiIndex):
        if "Close" in data.columns:
            closes = data["Close"]
    elif len(yf_tickers) == 1:
        closes = pd.DataFrame({"Close": data["Close"]}) if "Close" in data.columns else data
    else:
        closes = data

    if closes is None or closes.empty:
        return {}

    # Rename columns to symbols
    rename_map = {c: symbol_map.get(c, c) for c in closes.columns}
    closes = closes.rename(columns=rename_map)

    # Drop columns with all NaN
    closes = closes.dropna(axis=1, how="all")

    # Compute daily returns
    returns = closes.pct_change().dropna()

    # Compute Pearson correlation matrix
    if returns.shape[1] < 2:
        return {}

    corr_matrix = returns.corr(method="pearson")

    # Convert to dict for JSON storage
    result = {}
    for sym_a in corr_matrix.index:
        result[sym_a] = {}
        for sym_b in corr_matrix.columns:
            if sym_a != sym_b:
                val = corr_matrix.loc[sym_a, sym_b]
                if not pd.isna(val):
                    result[sym_a][sym_b] = round(float(val), 3)

    # Store to file
    memory_dir = data_root() / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    matrix_path = memory_dir / "correlation_matrix.json"
    try:
        with open(matrix_path, "w") as f:
            _json.dump(result, f, indent=2)
    except Exception:
        pass

    return result


_corr_cache = {}
_corr_cache_ts = None


def _load_correlation_matrix() -> dict:
    """Load cached correlation matrix, refresh if >24h old."""
    global _corr_cache, _corr_cache_ts
    import json as _json, time as _time

    now = _time.time()
    if _corr_cache_ts and (now - _corr_cache_ts) < 3600:  # 1h cache
        return _corr_cache

    matrix_path = data_root() / "memory" / "correlation_matrix.json"
    if matrix_path.exists():
        try:
            _corr_cache = _json.loads(matrix_path.read_text())
            _corr_cache_ts = now
            return _corr_cache
        except (OSError, UnicodeError, _json.JSONDecodeError, TypeError) as exc:
            log.error(
                "event=correlation_state_unavailable "
                "reason_code=CORRELATION_STATE_INVALID error_type=%s",
                type(exc).__name__,
            )
            raise
    return {}


def check_correlation(new_symbol: str, existing_positions: dict) -> dict:
    """
    Check if new_symbol is too correlated with existing positions.
    Returns dict with warning bool, reason, and correlation value.
    Uses real Pearson correlation data when available, falls back to group-based.
    """
    # Try real correlation first
    corr_matrix = _load_correlation_matrix()
    sym = new_symbol.upper()

    if corr_matrix and sym in corr_matrix:
        max_corr = 0.0
        correlated_with = []
        for pos_sym in existing_positions:
            pos_sym_u = pos_sym.upper()
            if pos_sym_u in corr_matrix.get(sym, {}):
                c = corr_matrix[sym][pos_sym_u]
                if c > max_corr:
                    max_corr = c
                if c > 0.8:
                    correlated_with.append((pos_sym_u, c))

        if correlated_with:
            # Sort by correlation descending
            correlated_with.sort(key=lambda x: x[1], reverse=True)
            names = [f"{s}({c:.2f})" for s, c in correlated_with[:3]]
            return {
                "warning": True,
                "reason": f"{new_symbol} is highly correlated with: {', '.join(names)}. Pearson r > 0.8.",
                "correlation": round(max_corr, 3),
            }
        elif max_corr > 0.6:
            return {
                "warning": True,
                "reason": f"{new_symbol} moderately correlated (r={max_corr:.2f}) with portfolio.",
                "correlation": round(max_corr, 3),
            }
        return {"warning": False, "reason": "", "correlation": round(max_corr, 3)}

    # Fallback: static groups (no correlation data)
    FALLBACK_CORRELATION_GROUPS = {
        "tech_stocks": ["AAPL", "MSFT", "NVDA", "GOOGL", "META"],
        "consumer_stocks": ["TSLA", "AMZN"],
        "mega_cap_crypto": ["BTC", "ETH"],
        "alt_crypto": ["SOL", "TON", "DOGE", "ADA", "AVAX", "DOT", "LINK", "MATIC"],
        "etfs": ["SPY", "QQQ"],
    }
    for group_name, symbols in FALLBACK_CORRELATION_GROUPS.items():
        if sym in symbols:
            existing_in_group = [s for s in existing_positions if s.upper() in symbols]
            if existing_in_group:
                return {
                    "warning": True,
                    "reason": f"{new_symbol} is highly correlated with existing position(s): {existing_in_group}. Same group: {group_name}",
                    "correlation": 0.85,
                }
    return {"warning": False, "reason": "", "correlation": 0.0}


def get_allocation_context() -> tuple[str, dict[str, float]]:
    """
    Read portfolio positions and compute allocation by asset class.
    Returns (context_string, allocation_pct_by_class).
    """
    import json

    pf_path = data_root() / "memory" / "paper" / "portfolio.json"
    if not pf_path.exists():
        return "", {}

    try:
        pf = json.loads(pf_path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as exc:
        log.error(
            "event=portfolio_state_unavailable "
            "reason_code=PORTFOLIO_STATE_INVALID error_type=%s",
            type(exc).__name__,
        )
        raise

    positions = pf.get("positions", {})
    if not positions:
        return "Current allocation: cash 100%, crypto 0%, stocks 0%.", {"crypto": 0.0, "stocks": 0.0}

    # Approximate: use a fixed notional value (initial cash) as denominator
    total_value = float(pf.get("cash", 0))
    class_values: dict[str, float] = {}

    for sym, pos in positions.items():
        if sym.upper() in CRYPTO_SYMBOLS:
            cls = "crypto"
        elif sym.upper() in STOCK_SYMBOLS:
            cls = "stocks"
        elif sym.upper() in ETF_SYMBOLS:
            cls = "etfs"
        elif sym.upper() in FOREX_SYMBOLS:
            cls = "forex"
        else:
            cls = "other"
        class_values[cls] = class_values.get(cls, 0) + float(pos.get("shares", 0))

    # Recalculate with notionally estimated current value
    crypto_value = 0.0
    stock_value = 0.0
    etf_value = 0.0
    forex_value = 0.0
    for sym, pos in positions.items():
        notional = float(pos.get("shares", 0)) * float(pos.get("avg_cost", 0))
        if sym.upper() in CRYPTO_SYMBOLS:
            crypto_value += notional
        elif sym.upper() in ETF_SYMBOLS:
            etf_value += notional
        elif sym.upper() in FOREX_SYMBOLS:
            forex_value += notional
        else:
            stock_value += notional

    equity = total_value + crypto_value + stock_value + etf_value + forex_value
    crypto_pct = (crypto_value / equity * 100) if equity > 0 else 0
    stocks_pct = (stock_value / equity * 100) if equity > 0 else 0
    etfs_pct = (etf_value / equity * 100) if equity > 0 else 0
    forex_pct = (forex_value / equity * 100) if equity > 0 else 0
    cash_pct = (total_value / equity * 100) if equity > 0 else 100

    allocation = {"crypto": round(crypto_pct, 1), "stocks": round(stocks_pct, 1), "etfs": round(etfs_pct, 1), "forex": round(forex_pct, 1), "cash": round(cash_pct, 1)}
    ctx = "Current allocation: cash {:.0f}%, crypto {:.0f}%, stocks {:.0f}%, etfs {:.0f}%, forex {:.0f}%.".format(
        cash_pct, crypto_pct, stocks_pct, etfs_pct, forex_pct)

    return ctx, allocation


def check_concentration_risk(symbol: str, proposed_action: str, proposed_size_pct: float) -> tuple[float, list[str]]:
    """
    Check concentration risk before approving a trade.
    Returns (adjusted_size_pct, warnings).

    If portfolio already has >40% in the asset class of the new signal,
    reduce the position size by 30%.
    """
    ctx, allocation = get_allocation_context()
    warnings = []
    size = proposed_size_pct

    sym_class = (
        "crypto" if symbol.upper() in CRYPTO_SYMBOLS
        else "etfs" if symbol.upper() in ETF_SYMBOLS
        else "forex" if symbol.upper() in FOREX_SYMBOLS
        else "stocks"
    )
    current_pct = allocation.get(sym_class, 0)
    proposed_new_pct = current_pct + proposed_size_pct

    if current_pct > CONCENTRATION_THRESHOLD * 100:
        reduction = size * CONCENTRATION_REDUCTION
        size -= reduction
        warnings.append(
            "{cls} allocation at {cur:.0f}% > {thresh:.0f}% — reducing new {sym} {action} from {orig:.0f}% to {new:.0f}%".format(
                cls=sym_class.capitalize(), cur=current_pct, thresh=CONCENTRATION_THRESHOLD * 100,
                sym=symbol, action=proposed_action, orig=proposed_size_pct, new=size
            )
        )

    if warnings:
        diversification_note = (
            "Current allocation: crypto {crypto:.0f}%, stocks {stocks:.0f}%. "
            "A new {cls} {action} would push {cls} to {proposed:.0f}%. Consider diversification.".format(
                crypto=allocation.get("crypto", 0),
                stocks=allocation.get("stocks", 0),
                cls=sym_class,
                action=proposed_action,
                proposed=proposed_new_pct,
            )
        )
        warnings.append(diversification_note)

    return max(size, 0.0), warnings


def _load_macro_news_context() -> str:
    import glob, json
    from pathlib import Path as _Path
    out = []
    reports_dir = runtime_reports_dir()
    macro_files = sorted(glob.glob(str(reports_dir / "macro_report_*.json")))
    if macro_files:
        d = json.loads(_Path(macro_files[-1]).read_text())
        ind = d.get("indicators", {})
        out.append(
            "MACRO: GDP {}% | CPI {}% | Fed {}% | Regime: {} ({})".format(
                ind.get("gdp_growth", {}).get("value", "?"),
                ind.get("cpi_inflation", {}).get("value", "?"),
                ind.get("fed_funds_rate", {}).get("value", "?"),
                d.get("regime", "?"),
                str(d.get("regime_rationale", ""))[:150],
            )
        )
    news_files = sorted(glob.glob(str(reports_dir / "news_report_*.json")))
    if news_files:
        d = json.loads(_Path(news_files[-1]).read_text())
        s = d.get("sentiment_summary", "")
        if s:
            out.append("NEWS SENTIMENT: {}".format(str(s)[:300]))
    return "\n".join(out) if out else ""


PORTFOLIO_MANAGER_SYSTEM = """You are a Portfolio Manager with final authority over all trading decisions.
Your job is to protect capital while allowing profitable trades.

You review:
- Initial trading signal
- Bull/bear debate results
- Risk persona assessments
- Analyst reports (technical, sentiment, on-chain, macro)

Your options:
1. RATIFY: Signal looks good — execute as proposed
2. MODIFY: Adjust parameters (reduce size, tighten stops, change action)
3. REJECT: Signal is too risky or flawed — do not trade

Decision framework:
- If at least 1 of 3 risk personas ACCEPTS the signal, lean toward RATIFY or MODIFY.
- REJECT only when ALL risk personas reject or conviction is below 0.5.
- When uncertain, MODIFY to a smaller position instead of rejecting outright.
- A trade with 1 accepting persona and moderate conviction is worth taking at reduced size.
Focus on risk-adjusted returns, not just upside."""

# ── Portfolio Manager ────────────────────────────────────────────────────────────

class PortfolioManager:
    """
    Portfolio manager with final decision authority.

    Reviews all inputs and makes the final call on every trade.
    """

    async def decide(
        self,
        llm_client,
        signal: TradingSignal,
        debate_rounds: list[DebateRound],
        bull_synthesis: str,
        bear_synthesis: str,
        risk_assessments: list[RiskAssessment],
        analyst_context: Optional[str] = None,
        execution_mode: Optional[str] = None,
    ) -> PortfolioDecision:
        """
        Make final portfolio decision.

        Args:
            llm_client: LLM client with call() method
            signal: Initial trading signal from analyst
            debate_rounds: List of debate rounds with bull/bear arguments
            bull_synthesis: Final bull thesis
            bear_synthesis: Final bear thesis
            risk_assessments: List of risk persona evaluations
            analyst_context: Optional formatted analyst reports

        Returns:
            PortfolioDecision with final action (ratify/modify/reject)
        """
        log.info("[%s] Portfolio Manager evaluating signal: %s (confidence=%.2f)",
                 signal.asset, signal.action, signal.confidence)

        # R:R gate — block BUY entries with reward-to-risk < 2:1
        if (signal.action.upper() == "BUY"
                and signal.entry_price and signal.stop_loss and signal.take_profit
                and signal.entry_price > signal.stop_loss > 0):
            reward = signal.take_profit - signal.entry_price
            risk = signal.entry_price - signal.stop_loss
            rr_ratio = reward / risk if risk > 0 else 0.0
            if rr_ratio < 2.0:
                log.info("[%s] R:R gate: %.2f:1 < 2:1 — rejecting BUY",
                         signal.asset, rr_ratio)
                return PortfolioDecision(
                    asset=signal.asset,
                    original_signal=signal,
                    action="reject",
                    modified_action=None,
                    modified_position_size_pct=0.0,
                    modified_stop_loss=signal.stop_loss,
                    modified_take_profit=signal.take_profit,
                    rationale=(
                        f"R:R ratio {rr_ratio:.2f}:1 below minimum 2:1 "
                        f"(reward ${reward:.2f} / risk ${risk:.2f})."
                    ),
                    risk_adjusted_return=None,
                    conviction=0.0,
                )

        prompt = self._build_prompt(
            signal, debate_rounds, bull_synthesis, bear_synthesis,
            risk_assessments, analyst_context, execution_mode
        )

        full_prompt = prompt + build_schema_prompt(PortfolioDecision)

        try:
            primary_response = await llm_client(
                full_prompt, PORTFOLIO_MANAGER_SYSTEM, task_type="trader_decision"
            )
            decision = parse_structured(
                llm_client, primary_response, PortfolioDecision,
                context_hint=f"Portfolio decision for {signal.asset}",
            )

            # ── Moon Dev swarm consensus for borderline signals ──────────────
            # When confidence is borderline (0.50–0.65), call a second "devil's
            # advocate" pass and take the more conservative of the two outcomes.
            if float(signal.confidence) < 0.65 and decision.action in ("ratify", "modify"):
                decision = await self._swarm_decide(
                    llm_client, full_prompt, decision, signal
                )

            log.info("[%s] Portfolio decision: %s -> %s (conviction=%.2f)",
                     signal.asset, signal.action, decision.action, decision.conviction)

            return decision

        except ValidationError as exc:
            log.error(
                "event=portfolio_decision_unavailable asset=%s "
                "reason_code=PORTFOLIO_DECISION_INVALID error_type=%s",
                signal.asset,
                type(exc).__name__,
            )
            raise

    async def _swarm_decide(
        self,
        llm_client,
        prompt: str,
        primary: PortfolioDecision,
        signal: TradingSignal,
    ) -> PortfolioDecision:
        """
        Moon Dev swarm consensus — second LLM call with a devil's advocate
        system prompt. Takes the more conservative of the two decisions.
        Only fires when primary confidence is borderline (< 0.65).
        """
        DEVIL_ADVOCATE_SYSTEM = (
            PORTFOLIO_MANAGER_SYSTEM
            + "\n\nIMPORTANT: You are acting as the devil's advocate. "
            "Question every assumption in the bull case. "
            "Prefer MODIFY or REJECT over RATIFY when uncertain. "
            "Protect capital above all else."
        )
        try:
            alt_response = await llm_client(
                prompt, DEVIL_ADVOCATE_SYSTEM, task_type="trader_decision"
            )
            alt_decision = parse_structured(
                llm_client, alt_response, PortfolioDecision,
                context_hint=f"Devil's advocate for {signal.asset}",
            )
        except Exception as exc:
            log.error(
                "event=portfolio_swarm_unavailable asset=%s "
                "reason_code=PORTFOLIO_SWARM_UNAVAILABLE error_type=%s",
                signal.asset,
                type(exc).__name__,
            )
            raise

        # Conservative consensus: reject > modify > ratify
        ACTION_RANK = {"reject": 0, "modify": 1, "ratify": 2}
        primary_rank = ACTION_RANK.get(primary.action, 2)
        alt_rank = ACTION_RANK.get(alt_decision.action, 2)

        if alt_rank < primary_rank:
            log.info("[%s] Swarm: primary=%s alt=%s → taking conservative alt",
                     signal.asset, primary.action, alt_decision.action)
            return alt_decision

        if primary.action == alt_decision.action:
            log.info("[%s] Swarm consensus: both agree on %s", signal.asset, primary.action)
        return primary

    def _build_prompt(
        self,
        signal: TradingSignal,
        debate_rounds: list[DebateRound],
        bull_synthesis: str,
        bear_synthesis: str,
        risk_assessments: list[RiskAssessment],
        analyst_context: Optional[str],
        execution_mode: Optional[str] = None,
    ) -> str:
        """Build portfolio manager prompt."""
        if execution_mode is None:
            from exchange.ccxt_bridge import get_mode
            execution_mode = get_mode()
        mode_note = ""
        if execution_mode == "live":
            mode_note = ("\n**CRITICAL: You are in LIVE trading mode with REAL money.** "
                         "Be extra conservative. Prefer MODIFY over RATIFY. "
                         "Reduce position sizes by at least 50% vs what you would in paper.\n")
        elif execution_mode == "dryrun":
            mode_note = "\n**You are in DRYRUN mode — trades go to exchange sandbox (testnet).**\n"

        lines = [
            f"You are reviewing a trading decision for {signal.asset}.",
            mode_note,
            "",
            "**Initial Trading Signal:**",
            f"  Action: {signal.action}",
            f"  Confidence: {signal.confidence:.2f}",
            f"  Entry Price: ${signal.entry_price}" if signal.entry_price else "  Entry Price: N/A",
            f"  Stop Loss: ${signal.stop_loss}" if signal.stop_loss else "  Stop Loss: N/A",
            f"  Take Profit: ${signal.take_profit}" if signal.take_profit else "  Take Profit: N/A",
            f"  Time Horizon: {signal.time_horizon}",
            f"  Reasoning: {signal.reasoning}",
            "",
            "",
        ]

        # Add debate context
        if debate_rounds:
            lines.extend([
                "**Debate Results:**",
                f"  Rounds: {len(debate_rounds)}",
                f"  Bull Synthesis: {bull_synthesis[:300]}...",
                f"  Bear Synthesis: {bear_synthesis[:300]}...",
                "",
            ])

        # Add risk assessments
        if risk_assessments:
            lines.append("**Risk Assessments:**")
            for assessment in risk_assessments:
                lines.append(
                    f"  {assessment.persona.title()}: "
                    f"{'ACCEPT' if assessment.accept_signal else 'REJECT'} | "
                    f"Position: {assessment.position_size_pct:.0f}% | "
                    f"Rationale: {assessment.rationale[:200]}"
                )
            lines.append("")

        # Add analyst context
        if analyst_context:
            lines.append(analyst_context)
            lines.append("")

        macro_news = _load_macro_news_context()
        if macro_news:
            lines.extend(["**Market Context (Macro + News):**", macro_news, ""])

        # Add allocation and concentration context
        alloc_ctx, _alloc = get_allocation_context()
        if alloc_ctx:
            # Run concentration check
            _, conc_warnings = check_concentration_risk(
                signal.asset, signal.action, 20.0  # default 20% position size for check
            )
            lines.append("**Portfolio Allocation & Concentration Risk:**")
            lines.append(f"  {alloc_ctx}")
            for w in conc_warnings:
                lines.append(f"  WARNING: {w}")
            lines.append("")

        # GAP 3: Correlation check
        import json as _json
        pf_path = data_root() / "memory" / "paper" / "portfolio.json"
        positions = {}
        if pf_path.exists():
            try:
                positions = _json.loads(pf_path.read_text()).get("positions", {})
            except (OSError, UnicodeError, _json.JSONDecodeError, TypeError) as exc:
                log.error(
                    "event=portfolio_state_unavailable "
                    "reason_code=PORTFOLIO_STATE_INVALID error_type=%s",
                    type(exc).__name__,
                )
                raise
        if signal.action.upper() == "BUY" and positions:
            corr = check_correlation(signal.asset, positions)
            if corr["warning"]:
                lines.extend([
                    "**Correlation Warning:**",
                    f"  {corr['reason']}",
                    f"  Estimated correlation: {corr['correlation']:.2f}",
                    f"  RECOMMENDATION: Reduce position size by 50% or reject.",
                    "",
                ])

        lines.extend([
            "**Your Decision:**",
            "",
            "Based on ALL the above, decide:",
            "1. Should this trade be RATIFIED, MODIFIED, or REJECTED?",
            "2. If MODIFY: what changes (action, position size, stops, targets)?",
            "3. What is your conviction in this final decision (0.0-1.0)?",
            "4. What is your rationale?",
            "",
            "Remember: You are the final risk manager. When uncertain, reduce size or reject.",
        ])

        return "\n".join(lines)

    def _fallback_decision(
        self,
        signal: TradingSignal,
        risk_assessments: list[RiskAssessment],
    ) -> PortfolioDecision:
        """Return a defensive rejection if a compatibility caller invokes it."""
        return PortfolioDecision(
            asset=signal.asset,
            original_signal=signal,
            action="reject",
            modified_action="HOLD",
            modified_position_size_pct=0.0,
            modified_stop_loss=signal.stop_loss,
            modified_take_profit=signal.take_profit,
            rationale=(
                "Portfolio decision unavailable; compatibility fallback rejects "
                "the signal and assigns zero size."
            ),
            risk_adjusted_return=None,
            conviction=0.0,
        )

    def apply_decision(
        self,
        signal: TradingSignal,
        decision: PortfolioDecision,
    ) -> TradingSignal:
        """
        Apply portfolio decision to modify signal.

        Returns the final signal that should be executed.
        """
        if decision.action == "ratify":
            log.info("[%s] Signal ratified — executing as proposed", signal.asset)
            return signal

        if decision.action == "reject":
            log.info("[%s] Signal rejected — no trade", signal.asset)
            # Return HOLD signal with zero position
            return TradingSignal(
                asset=signal.asset,
                action="HOLD",
                confidence=0.0,
                entry_price=signal.entry_price,
                stop_loss=None,
                take_profit=None,
                time_horizon=signal.time_horizon,
                reasoning=f"Portfolio Manager rejected trade: {decision.rationale[:200]}",
            )

        # MODIFY case
        modified = TradingSignal(
            asset=signal.asset,
            action=decision.modified_action or signal.action,
            confidence=signal.confidence * decision.conviction,  # Reduce by conviction
            entry_price=signal.entry_price,
            stop_loss=decision.modified_stop_loss or signal.stop_loss,
            take_profit=decision.modified_take_profit or signal.take_profit,
            time_horizon=signal.time_horizon,
            reasoning=f"Portfolio Manager modified trade: {decision.rationale[:200]}. Original: {signal.reasoning[:200]}",
        )

        log.info("[%s] Signal modified: %s -> %s, size %.1f%% -> %.1f%%",
                 signal.asset, signal.action, modified.action,
                 signal.take_profit or 0, modified.take_profit or 0)

        return modified
