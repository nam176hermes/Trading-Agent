"""
strategy_optimizer.py — LLM-powered strategy optimization

Reads backtest performance data and generates optimized trading rules.
Uses LLM to find edge cases and refinements beyond statistical analysis.

Strategy v5.0 will replace the simple regime filter with:
  - Symbol-specific rules (AVAX ≠ DOGE)
  - Entry timing refinement (RSI/MACD confluence)
  - Dynamic stop/target optimization per regime
  - Position sizing by symbol volatility
  - Anti-correlation pairing

Output: optimized_strategy.json (machine-readable) + STRATEGY.md (documentation)
"""
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import yfinance as yf
from runtime_paths import data_root

log = logging.getLogger("strategy_optimizer")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

RESULTS_DIR = data_root() / "backtest_results"
REPORT_PATH = RESULTS_DIR / "performance_report.json"


def strategy_path() -> Path:
    return data_root() / "strategy.json"


def strategy_markdown_path() -> Path:
    return data_root() / "STRATEGY.md"

# ── Load backtest data ───────────────────────────────────────────────────

def load_backtest_data() -> dict:
    """Load full backtest performance report."""
    if not REPORT_PATH.exists():
        raise FileNotFoundError(f"Backtest report not found: {REPORT_PATH}")
    with open(REPORT_PATH) as f:
        return json.load(f)


def build_context(report: dict) -> str:
    """Build optimization context from backtest report."""
    lines = ["# Backtest Performance Summary (8 symbols, 2023-2025)", ""]

    # Overall
    overall = report.get("overall", {})
    lines.append("## Overall")
    lines.append(f"- Total records: {report.get('total_records', '?')}")
    lines.append(f"- Win rate: {overall.get('win_rate_pct', '?')}%")
    lines.append("")

    # By symbol
    lines.append("## Win Rate by Symbol")
    for sym, data in report.get("win_rate_by_symbol", {}).items():
        wr = data.get("win_rate", data) if isinstance(data, dict) else data
        sig_count = data.get("total", "?") if isinstance(data, dict) else "?"
        lines.append(f"- {sym}: {wr:.1f}% ({sig_count} signals)")
    lines.append("")

    # By regime
    lines.append("## Win Rate by Regime")
    for regime, data in report.get("win_rate_by_regime", {}).items():
        wr = data.get("win_rate", data) if isinstance(data, dict) else data
        sig_count = data.get("total", "?") if isinstance(data, dict) else "?"
        lines.append(f"- {regime}: {wr:.1f}% ({sig_count} signals)")
    lines.append("")

    # Forward returns
    lines.append("## Forward Returns (avg %)")
    fwd = report.get("forward_returns", {})
    for label, returns in fwd.items():
        r_str = " | ".join(f"{k}: {v:+.2f}%" for k, v in returns.items())
        lines.append(f"- {label}: {r_str}")
    lines.append("")

    # Go/no-go
    lines.append("## Quality Checks")
    for check, result in report.get("go_nogo", {}).items():
        status = result.get("status", "?")
        value = result.get("value", "?")
        lines.append(f"- {check}: {value} → {status}")
    lines.append("")

    return "\n".join(lines)


def build_optimizer_prompt(context: str) -> str:
    """Build the LLM prompt for strategy optimization."""
    return f"""You are a quantitative crypto trading strategy optimizer. Given backtest performance data across 8 symbols and 3 years (2023-2025), generate optimized trading rules.

{context}

## Optimization Task

Based on the data above, generate an optimized trading strategy in valid JSON format. Focus on what the data actually shows — do NOT fabricate rules without evidence.

### Rules to generate:

1. **Symbol Selection**: Which symbols are worth trading? Rank by win rate. Set minimum threshold.
2. **Regime Filter**: Which regimes to trade in? Which to avoid? Set confidence requirements.
3. **Signal Direction**: LONG vs SHORT vs HOLD. Based on forward returns data.
4. **Confidence Threshold**: Minimum confidence for entry. Based on high vs low confidence win rates.
5. **Position Sizing**: How to size positions based on confidence, regime, and symbol volatility.
6. **Entry Refinement**: Any additional entry conditions (RSI range, MACD confirmation, volume).
7. **Stop/Target Optimization**: Dynamic stops based on ATR and regime. Risk-reward targets.
8. **Correlation Rules**: Max exposure to correlated assets. Anti-correlation pairs.

### Output Format

Return ONLY valid JSON — no markdown, no explanation:

{{
  "version": "5.0",
  "generated_at": "ISO timestamp",
  "symbols": {{
    "trade": ["SYM1", "SYM2"],  // symbols meeting threshold
    "avoid": ["SYM3"],          // symbols below threshold
    "min_win_rate": 0.45        // minimum win rate to trade
  }},
  "regime": {{
    "allow": ["trending_up"],
    "avoid": ["unclear"],
    "short_only_in": ["trending_down"],
    "require_strong_confirm": true
  }},
  "direction": {{
    "allow_long": true,
    "allow_short": false,
    "max_short_allocation_pct": 0
  }},
  "confidence": {{
    "min_entry": 0.55,
    "min_high_conviction": 0.70,
    "scale_size_with_confidence": true
  }},
  "position_sizing": {{
    "base_size_pct": 3.0,
    "max_size_pct": 10.0,
    "min_size_pct": 1.0,
    "scale_by_confidence": true,
    "correlation_cap_pct": 5.0,
    "per_symbol_caps": {{}}
  }},
  "entry_refinement": {{
    "rsi_min": 30,
    "rsi_max": 70,
    "require_macd_confirmation": true,
    "require_volume_confirmation": false,
    "min_volume_ratio": 1.0
  }},
  "stops": {{
    "atr_multiplier_long": 2.0,
    "atr_multiplier_short": 2.0,
    "trailing_stop_pct": 2.0,
    "min_risk_reward": 2.0,
    "max_stop_distance_pct": 10.0
  }},
  "correlation": {{
    "max_correlated_exposure_pct": 15.0,
    "correlation_threshold": 0.7,
    "anti_correlation_pairs": []
  }},
  "rationale": {{
    "symbol_selection": "Explanation of symbol choices based on win rates",
    "regime_filter": "Explanation of regime choices based on win rates",
    "direction": "Explanation of direction choices based on forward returns",
    "confidence": "Explanation of confidence threshold based on high/low performance",
    "position_sizing": "Explanation of sizing rules",
    "entry": "Explanation of entry refinement choices",
    "stops": "Explanation of stop/target optimization"
  }}
}}

Fill in ALL fields based on the data. Use exact numbers from the report where available. For fields without direct data, use reasonable crypto trading defaults with brief justification in the rationale.
"""


# ── LLM Interface ────────────────────────────────────────────────────────

async def optimize_strategy(llm_client) -> dict:
    """Run full strategy optimization using LLM."""
    log.info("── Loading backtest data ──")
    report = load_backtest_data()

    log.info("── Building optimization context ──")
    context = build_context(report)

    log.info("── Sending to LLM optimizer ──")
    prompt = build_optimizer_prompt(context)

    # Call LLM
    response = await llm_client(prompt)

    # Parse JSON from response
    try:
        # Find JSON block
        start = response.find("{")
        end = response.rfind("}") + 1
        if start >= 0 and end > start:
            strategy = json.loads(response[start:end])
        else:
            strategy = json.loads(response)
    except json.JSONDecodeError:
        log.error("Failed to parse LLM response as JSON")
        log.error("Raw response (first 500 chars): %s", response[:500])
        return _fallback_strategy()

    # Validate required fields
    required = ["version", "symbols", "regime", "direction", "confidence",
                "position_sizing", "stops"]
    missing = [k for k in required if k not in strategy]
    if missing:
        log.warning("Strategy missing fields: %s", missing)

    return strategy


def _fallback_strategy() -> dict:
    """Fallback strategy based purely on statistical analysis (no LLM)."""
    report = load_backtest_data()
    wr_sym = report.get("win_rate_by_symbol", {})
    wr_regime = report.get("win_rate_by_regime", {})

    trade_symbols = [s for s, wr in wr_sym.items() 
                     if (wr.get("win_rate", 0) if isinstance(wr, dict) else wr) >= 0.45]
    avoid_symbols = [s for s, wr in wr_sym.items() 
                     if (wr.get("win_rate", 0) if isinstance(wr, dict) else wr) < 0.45]

    return {
        "version": "5.0-fallback",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "statistical_only",
        "symbols": {
            "trade": trade_symbols,
            "avoid": avoid_symbols,
            "min_win_rate": 0.45
        },
        "regime": {
            "allow": ["trending_up"],
            "avoid": ["unclear"],
            "short_only_in": [],
            "require_strong_confirm": True
        },
        "direction": {
            "allow_long": True,
            "allow_short": False,
            "max_short_allocation_pct": 0
        },
        "confidence": {
            "min_entry": 0.55,
            "min_high_conviction": 0.70,
            "scale_size_with_confidence": True
        },
        "position_sizing": {
            "base_size_pct": 3.0,
            "max_size_pct": 10.0,
            "min_size_pct": 1.0,
            "scale_by_confidence": True,
            "correlation_cap_pct": 5.0,
            "per_symbol_caps": {}
        },
        "entry_refinement": {
            "rsi_min": 30,
            "rsi_max": 70,
            "require_macd_confirmation": True,
            "require_volume_confirmation": False,
            "min_volume_ratio": 1.0
        },
        "stops": {
            "atr_multiplier_long": 2.0,
            "atr_multiplier_short": 2.0,
            "trailing_stop_pct": 2.0,
            "min_risk_reward": 2.0,
            "max_stop_distance_pct": 10.0
        },
        "correlation": {
            "max_correlated_exposure_pct": 15.0,
            "correlation_threshold": 0.7,
            "anti_correlation_pairs": []
        },
        "rationale": {
            "symbol_selection": f"Trading symbols with ≥45% win rate: {trade_symbols}. Avoiding: {avoid_symbols}",
            "regime_filter": "Only trending_up (highest win rate). Avoid unclear (lowest).",
            "direction": "LONG only — SHORT signals show negative forward returns across all regimes.",
            "confidence": "Min 55% confidence for entry based on win rate differential.",
        }
    }


def save_strategy(strategy: dict):
    """Save optimized strategy to disk."""
    out_path = strategy_path()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(strategy, f, indent=2, default=str)
    log.info("Strategy saved → %s", out_path)

    # Also generate human-readable markdown
    md_path = strategy_markdown_path()
    lines = [
        f"# Trading Strategy v{strategy.get('version', '?')}",
        f"Generated: {strategy.get('generated_at', '?')}",
        f"Source: {strategy.get('source', 'LLM-optimized')}",
        "",
        "## Symbols",
    ]
    sym = strategy.get("symbols", {})
    lines.append(f"- Trade: {sym.get('trade', [])}")
    lines.append(f"- Avoid: {sym.get('avoid', [])}")
    lines.append(f"- Min win rate: {sym.get('min_win_rate', '?')*100:.0f}%")
    lines.append("")

    lines.append("## Regime Rules")
    reg = strategy.get("regime", {})
    lines.append(f"- Allow: {reg.get('allow', [])}")
    lines.append(f"- Avoid: {reg.get('avoid', [])}")
    lines.append("")

    lines.append("## Direction")
    d = strategy.get("direction", {})
    lines.append(f"- LONG: {'✅' if d.get('allow_long') else '❌'}")
    lines.append(f"- SHORT: {'✅' if d.get('allow_short') else '❌'}")
    lines.append("")

    lines.append("## Position Sizing")
    ps = strategy.get("position_sizing", {})
    lines.append(f"- Base: {ps.get('base_size_pct', '?')}%")
    lines.append(f"- Max: {ps.get('max_size_pct', '?')}%")
    lines.append(f"- Min: {ps.get('min_size_pct', '?')}%")
    lines.append("")

    lines.append("## Stops & Targets")
    st = strategy.get("stops", {})
    lines.append(f"- ATR multiplier (long): {st.get('atr_multiplier_long', '?')}x")
    lines.append(f"- Trailing stop: {st.get('trailing_stop_pct', '?')}%")
    lines.append(f"- Min R:R: {st.get('min_risk_reward', '?')}:1")
    lines.append("")

    if strategy.get("rationale"):
        lines.append("## Rationale")
        for key, value in strategy["rationale"].items():
            lines.append(f"### {key.replace('_', ' ').title()}")
            lines.append(str(value))
            lines.append("")

    with open(md_path, "w") as f:
        f.write("\n".join(lines))
    log.info("Strategy doc saved → %s", md_path)


# ── CLI ──────────────────────────────────────────────────────────────────

async def main(llm_client=None):
    """Run strategy optimization. Falls back to statistical if no LLM."""
    try:
        if llm_client:
            strategy = await optimize_strategy(llm_client)
        else:
            log.warning("No LLM client — using statistical fallback")
            strategy = _fallback_strategy()
    except Exception as e:
        log.error("Optimization failed: %s", e)
        strategy = _fallback_strategy()

    save_strategy(strategy)
    return strategy


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
