"""Root-side result validation for the isolated Nautilus backtest engine."""

from .result import (
    BacktestExpectedOutcomeV1,
    NautilusBacktestError,
    NautilusBacktestResult,
    validate_isolated_backtest_result,
    validate_isolated_simulation_result,
)
from .fixtures import (
    SCENARIO_IDS,
    CanonicalSimulationFixtureV1,
    build_canonical_simulation_fixture,
    build_simulation_envelope,
)
from .scenarios import BacktestScenarioError, BacktestScenarioV1
from .reference import calculate_reference_outcome
from .runtime_process import (
    CapturedEngineProcess,
    capture_prepared_engine_process,
)
from .paper_compat import PaperCompatibilityResultV1

__all__ = [
    "BacktestExpectedOutcomeV1",
    "BacktestScenarioError",
    "BacktestScenarioV1",
    "NautilusBacktestError",
    "NautilusBacktestResult",
    "validate_isolated_backtest_result",
    "validate_isolated_simulation_result",
    "calculate_reference_outcome",
    "SCENARIO_IDS",
    "CanonicalSimulationFixtureV1",
    "build_canonical_simulation_fixture",
    "build_simulation_envelope",
    "CapturedEngineProcess",
    "capture_prepared_engine_process",
    "PaperCompatibilityResultV1",
]
