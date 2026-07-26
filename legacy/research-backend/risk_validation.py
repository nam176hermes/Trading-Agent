"""
risk_validation.py — Monte Carlo Risk Validation Pipeline
=========================================================
Wires monte_carlo.py into the risk engine's validation gate.
Runs the full validation suite:

  1. Loads historical portfolio returns from paper trader or typed_decisions
  2. Runs monte_carlo.simulate_returns to generate 10,000 paths
  3. Computes VaR(95%), VaR(99%), CVaR(95%), max drawdown distribution
  4. Runs strategy_robustness to get confidence intervals on Sharpe
  5. Outputs a risk report to reports/risk_validation_*.json
  6. Leaves the validation decision UNKNOWN until genuine walk-forward windows exist

Usage:
  python risk_validation.py                           # full report
  python risk_validation.py --symbols BTC,ETH,SOL     # specific assets
  python risk_validation.py --horizon 60 --paths 5000 # custom simulation
  python risk_validation.py --help
"""
from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple
from uuid import uuid4

import numpy as np

from monte_carlo import simulate_returns, var_cvar_simulation, strategy_robustness
from runtime_paths import data_root, reports_dir

log = logging.getLogger("risk_validation")

REPORT_DIR = reports_dir()
REPORT_DIR.mkdir(parents=True, exist_ok=True)

MIN_HISTORICAL_RETURNS = 10


@dataclass(frozen=True)
class RiskInputResult:
    """Typed historical input state for risk validation."""

    status: str
    returns: np.ndarray
    portfolio_values: np.ndarray
    reason_code: str | None
    trace_id: str

    def __iter__(self) -> Iterator[np.ndarray]:
        yield self.returns
        yield self.portfolio_values

# ── Constants ────────────────────────────────────────────────────────────────

DEFAULT_SYMBOLS = ["BTC", "ETH", "SOL"]
DEFAULT_N_SIMULATIONS = 10_000
DEFAULT_HORIZON_DAYS = 30
DEFAULT_CONFIDENCE_LEVELS = [0.95, 0.99]


# ── Data loading ─────────────────────────────────────────────────────────────

def _load_returns_from_decisions(symbol: Optional[str] = None) -> Tuple[np.ndarray, np.ndarray]:
    """Load daily returns from typed_decisions.jsonl and trade_journal.

    Returns:
        (returns, equity_curve) — both numpy arrays.
        returns: daily log-returns (simple returns → log).
        equity_curve: cumulative NAV starting at 1.0.
    """
    returns_list: List[float] = []

    # Source 1: typed_decisions.jsonl — historical outcome data
    decisions_path = data_root() / "memory" / "typed_decisions.jsonl"
    if decisions_path.exists():
        try:
            with open(decisions_path) as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    pnl_pct = d.get("outcome_pnl_pct")
                    if pnl_pct is not None and isinstance(pnl_pct, (int, float)):
                        returns_list.append(float(pnl_pct) / 100.0)  # convert % to decimal
        except (OSError, UnicodeError) as exc:
            log.warning(
                "event=risk_history_source_unavailable source=typed_decisions "
                "error_type=%s",
                type(exc).__name__,
            )

    # Source 2: paper_trader trade journal
    journal_path = data_root() / "memory" / "trade_journal.jsonl"
    if journal_path.exists():
        try:
            with open(journal_path) as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    pnl = entry.get("pnl")
                    if pnl is not None and isinstance(pnl, (int, float)):
                        # Approximate return from PnL — assume 5% capital risk per trade
                        ret_pct = float(pnl) / (100_000 * 0.05)
                        returns_list.append(ret_pct)
        except (OSError, UnicodeError) as exc:
            log.warning(
                "event=risk_history_source_unavailable source=trade_journal "
                "error_type=%s",
                type(exc).__name__,
            )

    # Source 3: paper_trader portfolio history (equity snapshots)
    portfolio_path = data_root() / "memory" / "paper" / "portfolio.json"
    equity_curve = _equity_from_portfolio(portfolio_path)

    returns = np.array(
        [value for value in returns_list if np.isfinite(value) and value > -1.0],
        dtype=float,
    )

    # Convert simple returns to log-returns for monte_carlo
    log_returns = np.log1p(returns)

    # Build equity curve from returns if not loaded from portfolio
    if len(equity_curve) < 2 and len(returns) > 0:
        equity_curve = np.cumprod(1 + returns)
        equity_curve = np.insert(equity_curve, 0, 1.0)

    return log_returns, equity_curve


def _equity_from_portfolio(portfolio_path: Path) -> np.ndarray:
    """Extract equity curve from portfolio.json."""
    if not portfolio_path.exists():
        return np.array([1.0])

    try:
        portfolio = json.loads(portfolio_path.read_text())
        cash = float(portfolio.get("cash", 0))
        positions = portfolio.get("positions", {})
        # Simple approximation: cash + position values
        position_value = 0.0
        for pos in positions.values():
            if isinstance(pos, dict):
                qty = float(pos.get("shares", 0))
                price = float(pos.get("avg_cost", pos.get("avg_price", 0)))
                position_value += qty * price
        equity = cash + position_value
        if equity <= 0:
            return np.array([1.0])
        # We only get a single snapshot — return as single-point curve
        return np.array([INITIAL_EQUITY, equity])
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return np.array([1.0])


INITIAL_EQUITY = 100_000.0


def _load_portfolio_returns() -> RiskInputResult:
    """Load real historical inputs without manufacturing synthetic observations."""
    trace_id = uuid4().hex[:16]
    returns, portfolio_values = _load_returns_from_decisions()
    if len(returns) == 0:
        log.error(
            "event=risk_history_unavailable trace_id=%s "
            "reason_code=HISTORICAL_RETURNS_UNAVAILABLE",
            trace_id,
        )
        return RiskInputResult(
            status="UNAVAILABLE",
            returns=returns,
            portfolio_values=portfolio_values,
            reason_code="HISTORICAL_RETURNS_UNAVAILABLE",
            trace_id=trace_id,
        )
    return RiskInputResult(
        status="AVAILABLE",
        returns=returns,
        portfolio_values=portfolio_values,
        reason_code=None,
        trace_id=trace_id,
    )


# ── Core validation pipeline ─────────────────────────────────────────────────

def run_risk_validation(
    symbols: Optional[List[str]] = None,
    n_simulations: int = DEFAULT_N_SIMULATIONS,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    confidence_levels: Optional[List[float]] = None,
    random_state: int = 42,
    method: str = "bootstrap",
    output_file: Optional[str] = None,
) -> Dict:
    """Run the full Monte Carlo risk validation pipeline.

    Args:
        symbols: List of symbols (unused for now; uses whole portfolio returns).
        n_simulations: Number of Monte Carlo paths.
        horizon_days: Projection horizon in days.
        confidence_levels: Confidence levels for VaR/CVaR.
        random_state: Random seed.
        method: 'bootstrap' or 'parametric'.
        output_file: Override output filepath.

    Returns:
        Full risk validation report dict.
    """
    if confidence_levels is None:
        confidence_levels = DEFAULT_CONFIDENCE_LEVELS

    # Step 1: Load only real historical returns and equity observations.
    input_result = _load_portfolio_returns()
    log_returns = input_result.returns
    equity_curve = input_result.portfolio_values
    log.info(
        "event=risk_history_loaded trace_id=%s status=%s observations=%d "
        "equity_points=%d",
        input_result.trace_id,
        input_result.status,
        len(log_returns),
        len(equity_curve),
    )

    if len(log_returns) < MIN_HISTORICAL_RETURNS:
        report = {
            "status": "UNAVAILABLE",
            "reason_code": "INSUFFICIENT_HISTORICAL_RETURNS",
            "trace_id": input_result.trace_id,
            "validation_decision": "UNKNOWN",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "configuration": {
                "n_simulations": n_simulations,
                "horizon_days": horizon_days,
                "confidence_levels": confidence_levels,
                "method": method,
                "n_observations": len(log_returns),
                "minimum_observations": MIN_HISTORICAL_RETURNS,
                "synthetic_fallback_used": False,
            },
            "var_cvar": None,
            "max_drawdown_distribution": None,
            "strategy_robustness": None,
            "walk_forward_validation": None,
        }
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filepath = output_file or str(
            REPORT_DIR / f"risk_validation_{timestamp}.json"
        )
        with open(filepath, "w") as file_handle:
            json.dump(report, file_handle, indent=2, default=str)
        report["_filepath"] = filepath
        log.error(
            "event=risk_validation_unavailable trace_id=%s "
            "reason_code=INSUFFICIENT_HISTORICAL_RETURNS observations=%d",
            input_result.trace_id,
            len(log_returns),
        )
        return report

    # Step 2: Simulate return paths
    paths = simulate_returns(
        returns=log_returns,
        n_simulations=n_simulations,
        horizon_days=horizon_days,
        method=method,
        random_state=random_state,
    )

    # Step 3: Compute VaR/CVaR at each confidence level
    var_cvar_results = {}
    for conf in confidence_levels:
        result = var_cvar_simulation(paths, confidence=conf)
        c_label = str(int(conf * 100))
        var_key = f"var_{c_label}"
        cvar_key = f"cvar_{c_label}"
        drawdown_key = f"max_drawdown_{c_label}"
        if not all(
            key in result for key in (var_key, cvar_key, drawdown_key)
        ):
            raise ValueError(
                f"risk metric output missing required keys for {c_label}"
            )
        var_cvar_results[var_key] = {
            "log_return": result[var_key],
            "simple_return": round(1.0 - np.exp(result[var_key]), 6),
        }
        var_cvar_results[cvar_key] = {
            "log_return": result[cvar_key],
            "simple_return": round(1.0 - np.exp(result[cvar_key]), 6),
        }
        var_cvar_results[drawdown_key] = {
            "log_return": result[drawdown_key],
        }

    # Step 3b: Compute max drawdown distribution across all paths
    peak = np.maximum.accumulate(paths, axis=1)
    drawdowns = peak - paths
    max_dd_per_path = drawdowns.max(axis=1)
    dd_distribution = {
        "mean": round(float(np.mean(max_dd_per_path)), 6),
        "median": round(float(np.median(max_dd_per_path)), 6),
        "p95": round(float(np.quantile(max_dd_per_path, 0.95)), 6),
        "p99": round(float(np.quantile(max_dd_per_path, 0.99)), 6),
        "max": round(float(np.max(max_dd_per_path)), 6),
    }

    # Step 4: Strategy robustness (bootstrap equity curve)
    robustness = None
    if len(equity_curve) >= 20:
        # Scale equity curve to match paper trader starting capital
        equity_scaled = equity_curve * INITIAL_EQUITY
        robustness = strategy_robustness(
            equity_curve=equity_scaled,
            n_simulations=min(n_simulations, 2000),  # bootstrap samples, not paths
            random_state=random_state + 1,
        )

    # Step 5: Foundation package has no genuine walk-forward windows. Do not
    # manufacture them from bootstrap confidence intervals and do not emit PASS.
    wf_validation = None

    # Step 6: Build report
    report = {
        "status": "PARTIAL",
        "reason_code": "WALK_FORWARD_OBSERVATIONS_UNAVAILABLE",
        "trace_id": input_result.trace_id,
        "validation_decision": "UNKNOWN",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "configuration": {
            "n_simulations": n_simulations,
            "horizon_days": horizon_days,
            "confidence_levels": confidence_levels,
            "method": method,
            "n_observations": len(log_returns),
            "minimum_observations": MIN_HISTORICAL_RETURNS,
            "synthetic_fallback_used": False,
        },
        "var_cvar": var_cvar_results,
        "max_drawdown_distribution": dd_distribution,
        "strategy_robustness": robustness,
        "walk_forward_validation": (
            {
                "passed": wf_validation.passed,
                "consistency": wf_validation.consistency,
                "aggregate_sharpe": wf_validation.aggregate_sharpe,
                "aggregate_sortino": wf_validation.aggregate_sortino,
                "profitable_windows": wf_validation.profitable_windows,
                "total_windows": wf_validation.total_windows,
                "max_drawdown_pct": wf_validation.max_drawdown_pct,
                "rejection_reasons": wf_validation.rejection_reasons,
            }
            if wf_validation
            else None
        ),
    }

    # Save report
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filepath = output_file or str(REPORT_DIR / f"risk_validation_{ts}.json")
    with open(filepath, "w") as f:
        json.dump(report, f, indent=2, default=str)

    log.info("Risk validation report saved → %s", filepath)
    report["_filepath"] = filepath
    return report


def print_summary(report: Dict) -> None:
    """Print a human-readable summary of the risk validation report."""
    print("=" * 60)
    print("  MONTE CARLO RISK VALIDATION REPORT")
    print("=" * 60)
    print(f"  Status        : {report.get('status', 'UNKNOWN')}")
    print(f"  Decision      : {report.get('validation_decision', 'UNKNOWN')}")
    if report.get("reason_code"):
        print(f"  Reason        : {report['reason_code']}")
    print()

    cfg = report.get("configuration", {})
    print(f"  Simulations   : {cfg.get('n_simulations', 'N/A'):,}")
    print(f"  Horizon       : {cfg.get('horizon_days', 'N/A')} days")
    print(f"  Method        : {cfg.get('method', 'N/A')}")
    print(f"  Observations  : {cfg.get('n_observations', 'N/A')}")
    print()

    print("  ── VaR / CVaR ──")
    for key, val in (report.get("var_cvar") or {}).items():
        if isinstance(val, dict) and "simple_return" in val:
            sr = val["simple_return"]
            print(f"  {key:>18s}: {sr:+.4%}")
    print()

    print("  ── Max Drawdown Distribution (log-return) ──")
    dd = report.get("max_drawdown_distribution") or {}
    for key in ("mean", "median", "p95", "p99", "max"):
        v = dd.get(key, "N/A")
        if isinstance(v, float):
            print(f"  {key:>18s}: {v:.4f}")
    print()

    sr = report.get("strategy_robustness", {})
    if sr:
        print("  ── Strategy Robustness (95% CI) ──")
        for metric in ("sharpe", "cvar_95", "max_drawdown"):
            entry = sr.get(metric, {})
            if entry:
                print(
                    f"  {metric:>18s}: median={entry.get('median', 0):.4f} "
                    f"[{entry.get('ci_lower', 0):.4f}, {entry.get('ci_upper', 0):.4f}]"
                )
        print()

    wf = report.get("walk_forward_validation")
    if wf:
        status = "✅ PASSED" if wf["passed"] else "❌ REJECTED"
        print(f"  ── Walk-Forward Gate: {status} ──")
        print(f"  Consistency    : {wf['consistency']:.0%}")
        print(f"  Sharpe         : {wf['aggregate_sharpe']:.2f}")
        print(f"  Sortino        : {wf['aggregate_sortino']:.2f}")
        print(f"  Profitable Win : {wf['profitable_windows']}/{wf['total_windows']}")
        if wf["rejection_reasons"]:
            for reason in wf["rejection_reasons"]:
                print(f"  ⚠  {reason}")
        print()

    fp = report.get("_filepath", "unknown")
    print(f"  Report saved → {fp}")


# ── CLI ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Monte Carlo Risk Validation — wire monte_carlo → risk_engine validation gate",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python risk_validation.py
  python risk_validation.py --symbols BTC,ETH --horizon 60
  python risk_validation.py --paths 20000 --method parametric
        """,
    )
    parser.add_argument(
        "--symbols", type=str, default=None,
        help="Comma-separated symbols (default: BTC,ETH,SOL — unused in current version)",
    )
    parser.add_argument(
        "--horizon", type=int, default=DEFAULT_HORIZON_DAYS,
        help=f"Simulation horizon in days (default: {DEFAULT_HORIZON_DAYS})",
    )
    parser.add_argument(
        "--paths", type=int, default=DEFAULT_N_SIMULATIONS,
        help=f"Number of simulation paths (default: {DEFAULT_N_SIMULATIONS})",
    )
    parser.add_argument(
        "--method", type=str, choices=["bootstrap", "parametric"],
        default="bootstrap", help="Simulation method (default: bootstrap)",
    )
    parser.add_argument(
        "--confidence", type=str, default="0.95,0.99",
        help="Comma-separated confidence levels (default: 0.95,0.99)",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Output JSON filepath (default: auto-generated in reports/)",
    )
    parser.add_argument(
        "--summary-only", action="store_true",
        help="Print summary only, don't save full report",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    symbols = None
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",")]

    confidence_levels = [float(c.strip()) for c in args.confidence.split(",")]

    report = run_risk_validation(
        symbols=symbols,
        n_simulations=args.paths,
        horizon_days=args.horizon,
        confidence_levels=confidence_levels,
        method=args.method,
        output_file=args.output,
    )

    print_summary(report)


if __name__ == "__main__":
    main()
