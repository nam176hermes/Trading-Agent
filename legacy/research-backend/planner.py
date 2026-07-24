"""
planner.py — Dynamic Task Decomposition for Trading Research
Adapted from Dexter (virattt).

Takes a research question and decomposes it into structured steps
before execution. Then validates each step's output and iterates
where data is missing or inconsistent.

Dexter pattern: "Decompose → Execute → Validate → Iterate"
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("planner")


# ── Task Plan Schema ──────────────────────────────────────────────────────────

@dataclass
class ResearchTask:
    """A single step in a research plan."""
    id: int
    description: str
    tool: str                       # tool to call
    inputs: dict = field(default_factory=dict)
    depends_on: list[int] = field(default_factory=list)  # task IDs this depends on
    validation: str = ""            # how to verify this step's output
    status: str = "pending"         # pending, running, done, failed, skipped
    output_summary: str = ""


@dataclass
class ResearchPlan:
    """A complete research plan with ordered tasks."""
    question: str
    symbols: list[str]
    mode: str = "snapshot"
    tasks: list[ResearchTask] = field(default_factory=list)
    current_step: int = 0

    @property
    def progress(self) -> str:
        done = sum(1 for t in self.tasks if t.status == "done")
        return f"{done}/{len(self.tasks)}"

    def next_task(self) -> Optional[ResearchTask]:
        """Get the next pending task whose dependencies are satisfied."""
        for task in self.tasks:
            if task.status != "pending":
                continue
            # Check dependencies
            deps_satisfied = all(
                self.tasks[d].status == "done"
                for d in task.depends_on
                if d < len(self.tasks)
            )
            if deps_satisfied:
                return task
        return None

    def all_done(self) -> bool:
        return all(t.status in ("done", "skipped", "failed") for t in self.tasks)


# ── Plan Templates ────────────────────────────────────────────────────────────

def build_snapshot_plan(symbols: list[str]) -> ResearchPlan:
    """Build a standard research plan for a snapshot analysis."""
    tasks = [
        ResearchTask(
            id=0, description="Fetch market data for all symbols",
            tool="data_collector.collect_all",
            inputs={"symbols": symbols},
            validation="All symbols have price and OHLCV data",
        ),
        ResearchTask(
            id=1, description="Calculate technical indicators",
            tool="ta_engine.calculate_indicators",
            inputs={"per_symbol": True},
            depends_on=[0],
            validation="RSI, MACD, SMA200 computed for each symbol",
        ),
        ResearchTask(
            id=2, description="Fetch market sentiment and on-chain data",
            tool="enrichment.fetch_all",
            inputs={"per_symbol": True},
            depends_on=[0],
            validation="Sentiment and on-chain stubs returned for each symbol",
        ),
        ResearchTask(
            id=3, description="Assemble per-asset reports",
            tool="assembly.assemble_asset_json",
            inputs={"per_symbol": True},
            depends_on=[1, 2],
            validation="Each asset has suggestion and confidence fields",
        ),
        ResearchTask(
            id=4, description="Run bull vs bear debate for active signals",
            tool="debate.build_debate_prompts",
            inputs={"filter": "suggestion != 'NO SIGNAL'"},
            depends_on=[3],
            validation="Each active signal has both bull and bear arguments",
        ),
        ResearchTask(
            id=5, description="Run 3-way risk persona assessment",
            tool="risk_personas.build_persona_prompts",
            inputs={"per_symbol": True},
            depends_on=[3],
            validation="Each symbol has position size and stop-loss recommendation",
        ),
        ResearchTask(
            id=6, description="Validate internal consistency",
            tool="validation.check_consistency",
            depends_on=[4, 5],
            validation="No contradictory signals, confidence scores are coherent",
        ),
        ResearchTask(
            id=7, description="Assemble final report and store decisions",
            tool="assembly.assemble_full_report",
            depends_on=[6],
            validation="Report JSON valid, decisions stored to memory",
        ),
    ]

    return ResearchPlan(
        question=f"Full market snapshot for {', '.join(symbols)}",
        symbols=symbols,
        mode="snapshot",
        tasks=tasks,
    )


def build_custom_plan(question: str, symbols: list[str]) -> ResearchPlan:
    """
    Build a custom research plan based on the user's question.
    Uses keyword matching to determine which analysis steps are needed.
    """
    lower = question.lower()
    tasks: list[ResearchTask] = []
    task_id = 0

    # Always start with data collection
    tasks.append(ResearchTask(
        id=task_id, description="Fetch market data for requested symbols",
        tool="data_collector.collect_all",
        inputs={"symbols": symbols},
        validation="Price and volume data retrieved",
    ))
    data_id = task_id
    task_id += 1

    # Technical analysis
    if any(w in lower for w in [
        "technical", "rsi", "macd", "indicator", "chart", "trend", "ma", "bollinger", "sma",
        "overbought", "oversold", "support", "resistance", "moving average", "volume",
        "candle", "pattern", "divergence", "momentum",
    ]):
        tasks.append(ResearchTask(
            id=task_id, description="Calculate technical indicators",
            tool="ta_engine.calculate_indicators",
            depends_on=[data_id],
            validation="RSI, MACD, moving averages computed",
        ))
        ta_id = task_id
        task_id += 1
    else:
        ta_id = data_id

    # Fundamental/financial data
    if any(w in lower for w in ["fundamental", "revenue", "earnings", "income", "balance", "cash flow", "valuation", "undervalued"]):
        tasks.append(ResearchTask(
            id=task_id, description="Analyze financial fundamentals",
            tool="enrichment.fetch_fundamentals",
            depends_on=[data_id],
            validation="Income statement and balance sheet data retrieved",
        ))
        task_id += 1

    # Sentiment analysis
    if any(w in lower for w in ["sentiment", "social", "twitter", "news", "fear", "greed"]):
        tasks.append(ResearchTask(
            id=task_id, description="Analyze market sentiment and news",
            tool="enrichment.fetch_sentiment",
            depends_on=[data_id],
            validation="Sentiment data and recent news retrieved",
        ))
        task_id += 1

    # On-chain
    if any(w in lower for w in ["on-chain", "onchain", "wallet", "whale", "exchange flow", "misttrack"]):
        tasks.append(ResearchTask(
            id=task_id, description="Fetch on-chain risk data",
            tool="enrichment.fetch_onchain_risk",
            depends_on=[data_id],
            validation="On-chain risk indicators retrieved",
        ))
        task_id += 1

    # Debate (for any buy/sell question)
    if any(w in lower for w in [
        "buy", "sell", "entry", "exit", "long", "short", "position", "trade", "invest",
        "profit", "take profit", "stop loss", "target", "risk",
    ]):
        assembly_id = task_id
        tasks.append(ResearchTask(
            id=task_id, description="Assemble initial analysis",
            tool="assembly.assemble_asset_json",
            depends_on=[ta_id],
            validation="Signal and confidence computed",
        ))
        task_id += 1

        tasks.append(ResearchTask(
            id=task_id, description="Run adversarial debate on trading signal",
            tool="debate.build_debate_prompts",
            depends_on=[assembly_id],
            validation="Bull and bear cases evaluated",
        ))
        task_id += 1

        tasks.append(ResearchTask(
            id=task_id, description="Run multi-persona risk assessment",
            tool="risk_personas.build_persona_prompts",
            depends_on=[assembly_id],
            validation="Position sizing and risk level determined",
        ))
        risk_id = task_id
        task_id += 1
    else:
        risk_id = task_id - 1

    # Validation
    tasks.append(ResearchTask(
        id=task_id, description="Validate analysis consistency",
        tool="validation.check_consistency",
        depends_on=[risk_id],
        validation="No contradictions, data sources verified",
    ))
    validate_id = task_id
    task_id += 1

    # Final
    tasks.append(ResearchTask(
        id=task_id, description="Compile final research report",
        tool="assembly.assemble_full_report",
        depends_on=[validate_id],
        validation="Complete, validated report ready",
    ))

    return ResearchPlan(
        question=question,
        symbols=symbols,
        mode="custom",
        tasks=tasks,
    )


# ── Plan Display ──────────────────────────────────────────────────────────────

def format_plan(plan: ResearchPlan) -> str:
    """Format a research plan for display in the terminal."""
    lines = [
        f"\n{'='*60}",
        f"  RESEARCH PLAN",
        f"  Question: {plan.question[:100]}",
        f"  Symbols: {', '.join(plan.symbols)}",
        f"  Mode: {plan.mode}",
        f"  Steps: {len(plan.tasks)}",
        f"{'='*60}\n",
    ]

    status_icons = {
        "pending": "⏳", "running": "🔄", "done": "✅",
        "failed": "❌", "skipped": "⏭️",
    }

    for task in plan.tasks:
        icon = status_icons.get(task.status, "  ")
        deps = f" ← depends on [{', '.join(str(d) for d in task.depends_on)}]" if task.depends_on else ""
        lines.append(f"  {icon} [{task.id}] {task.description}")
        if task.status == "done" and task.output_summary:
            lines.append(f"      → {task.output_summary[:100]}")
        if deps and task.status == "pending":
            lines.append(f"      {deps}")

    lines.append(f"\n  Progress: {plan.progress}")
    return "\n".join(lines)


# ── Validation Helpers ────────────────────────────────────────────────────────

def validate_data_completeness(asset_json: dict) -> tuple[bool, str]:
    """Check if an asset report has all required fields."""
    required = ["symbol", "price", "suggestion", "confidence"]
    missing = [f for f in required if f not in asset_json or asset_json[f] is None]

    if missing:
        return False, f"Missing fields: {', '.join(missing)}"
    return True, "All required fields present"


def validate_consistency(report: dict) -> tuple[bool, list[str]]:
    """
    Check internal consistency of the full report.
    Returns (is_consistent, list_of_warnings).
    """
    warnings = []
    assets = report.get("assets", [])

    for asset in assets:
        sym = asset.get("symbol", "?")
        suggestion = str(asset.get("suggestion", "")).lower()
        confidence = asset.get("confidence", 0)

        # Confidence should be numeric for non-wait signals
        if suggestion not in ("no signal", "wait", ""):
            if isinstance(confidence, str):
                warnings.append(f"[{sym}] Confidence is string '{confidence}' — should be numeric")

        # Risk assessment should match suggestion
        risk = asset.get("risk_assessment", {})
        risk_level = str(risk.get("risk_level", "")).lower()
        if suggestion in ("buy", "strong buy") and risk_level == "critical":
            warnings.append(f"[{sym}] Buy signal with CRITICAL risk — contradiction!")

        # Position size check
        pos_size = risk.get("position_size_pct", 0)
        if isinstance(pos_size, (int, float)) and pos_size > 50:
            warnings.append(f"[{sym}] Position size {pos_size}% exceeds 50% — possible over-leverage")

    is_consistent = len(warnings) == 0
    return is_consistent, warnings
