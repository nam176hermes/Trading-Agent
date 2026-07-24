"""Exact reviewed file authority for the Phase 4B legacy research backend."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from .manifest import (
    ReleasePolicy, create_manifest, phase4_backend_release_policy, verify_release,
)


APPROVED_PHASE4_BACKEND_COMMIT = "41f055b48033714c660f44cc20498b7545366e75"


# Static AST import closure of ``main.py`` plus package initializers and the one
# immutable prompt/config document read by the research-only entrypoint. Runtime
# data, credentials, tests, models, reports and deployment scripts are absent by
# construction rather than filtered after export.
PHASE4_BACKEND_AUDITED_PATHS = (
    "SOUL.md",
    "adanos_collector.py", "adanos_signals.py", "alert_manager.py",
    "allocation_engine.py", "analysts.py", "assembly.py", "asset_registry.py",
    "atr_stops.py", "backtest_engine.py", "backtest_gate.py",
    "bayesian_weighting.py", "broker.py", "candlestick_patterns.py",
    "collector_utils.py", "cra_tracker.py", "data_collector.py",
    "data_vendors.py", "debate.py", "derivatives_collector.py", "dl_predictor.py",
    "exchange/__init__.py", "exchange/ccxt_bridge.py", "exchange/secrets.py",
    "exchange_health.py", "execute_live.py", "exit_strategies.py", "fallback.py",
    "garch_vol.py", "incubation_tracker.py", "job_attribution.py",
    "kalshi_collector.py", "kill_switch.py", "live_data.py",
    "live_execution_policy.py", "macro.py", "macro_data.py", "main.py",
    "memory.py", "memory_search.py", "ml_predictor.py", "ml_regime.py",
    "ml_toolkit.py", "model_config.py", "monte_carlo.py", "news_collector.py",
    "onchain_collector.py", "orderflow_collector.py", "pairs_strategy.py",
    "pairs_trader.py", "paper_trader.py", "planner.py", "polymarket_collector.py",
    "portfolio_manager.py", "portfolio_optimizer.py", "predscope_signals.py",
    "prompts.py", "pyproject.toml", "reflection_engine.py", "regime_detector.py",
    "research_semantics.py", "risk_engine.py", "risk_personas.py",
    "risk_validation.py", "rl_agent.py", "runtime_paths.py", "schemas.py",
    "scratchpad.py", "sentiment_collector.py", "sentiment_filter.py",
    "signal_filters.py", "signal_parser.py", "signal_quality.py",
    "strategy_risk_manager.py", "ta_engine.py", "ta_shim.py", "twelve_data.py",
    "uv.lock", "yfinance_collector.py",
)


def phase4_backend_policy(**overrides: object) -> ReleasePolicy:
    return phase4_backend_release_policy(
        audited_paths=PHASE4_BACKEND_AUDITED_PATHS,
        **overrides,
    )


def require_approved_backend_commit(commit: str) -> str:
    if commit != APPROVED_PHASE4_BACKEND_COMMIT:
        raise ValueError("backend commit is not approved")
    return commit


def verify_phase4_backend_release(
    release_root: Path, manifest_path: Path, expected_digest: str, *,
    expected_commit: str, expected_python_identity: str,
    expected_uid: int = 0, expected_gid: int = 0,
) -> bool:
    """Verify both the complete release and its exact non-venv source set."""

    policy = phase4_backend_policy(
        expected_git_commit=expected_commit,
        expected_python_identity=expected_python_identity,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    )
    if verify_release(release_root, manifest_path, expected_digest, policy) is not True:
        return False
    observed = create_manifest(release_root, policy)
    source_entries = {
        (entry["path"], entry["type"])
        for entry in observed
        if PurePosixPath(entry["path"]).parts[0] != ".venv"
    }
    expected_entries = {(path, "file") for path in PHASE4_BACKEND_AUDITED_PATHS}
    for path in PHASE4_BACKEND_AUDITED_PATHS:
        pure = PurePosixPath(path)
        for index in range(1, len(pure.parts)):
            expected_entries.add((PurePosixPath(*pure.parts[:index]).as_posix(), "directory"))
    if source_entries != expected_entries:
        raise ValueError("backend release source set mismatch")
    return True


__all__ = [
    "PHASE4_BACKEND_AUDITED_PATHS", "phase4_backend_policy",
    "APPROVED_PHASE4_BACKEND_COMMIT", "require_approved_backend_commit",
    "verify_phase4_backend_release",
]
