"""Root-side result validation for the isolated Nautilus backtest engine."""

from .result import (
    BacktestExpectedOutcomeV1,
    NautilusBacktestError,
    NautilusBacktestResult,
    validate_isolated_backtest_result,
    validate_isolated_simulation_result,
)
from .scenarios import BacktestScenarioError, BacktestScenarioV1
from .reference import calculate_reference_outcome

__all__ = [
    "BacktestExpectedOutcomeV1",
    "BacktestScenarioError",
    "BacktestScenarioV1",
    "NautilusBacktestError",
    "NautilusBacktestResult",
    "validate_isolated_backtest_result",
    "validate_isolated_simulation_result",
    "calculate_reference_outcome",
]
