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

__all__ = [
    "ComparisonRecord",
    "CostScenario",
    "PointInTimeObservation",
    "RecursiveIndicatorReplay",
    "ResearchGateEvidenceV1",
    "ResearchGateReportV1",
    "ResearchGateResultV1",
    "ResearchProvenanceV1",
    "WalkForwardFold",
    "evaluate_research_gates",
]
