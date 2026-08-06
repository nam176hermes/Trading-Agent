"""Data-only, deterministic WS-04 research validation contracts."""

from .models import (
    ComparisonRecord,
    CostScenario,
    PointInTimeObservation,
    RecursiveIndicatorReplay,
    ResearchGateEvidenceV1,
    ResearchProvenanceV1,
    WalkForwardFold,
)
from .evaluator import (
    ResearchGateReportV1,
    ResearchGateResultV1,
    evaluate_research_gates,
)
from .closure import ResearchClosureError, Ws04ClosureV1, close_ws04_research

__all__ = [
    "ComparisonRecord",
    "CostScenario",
    "PointInTimeObservation",
    "RecursiveIndicatorReplay",
    "ResearchGateEvidenceV1",
    "ResearchGateReportV1",
    "ResearchGateResultV1",
    "ResearchClosureError",
    "ResearchProvenanceV1",
    "WalkForwardFold",
    "Ws04ClosureV1",
    "close_ws04_research",
    "evaluate_research_gates",
]
