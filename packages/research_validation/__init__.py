"""Data-only, deterministic WS-04 research validation contracts."""

from .models import (
    ComparisonRecord,
    CostScenario,
    PointInTimeObservation,
    RecursiveIndicatorReplay,
    ResearchGateEvidenceV1,
    ResearchProvenanceV1,
    WalkForwardFold,
    analysis_output_sha256,
)
from .evaluator import (
    ResearchGateReportV1,
    ResearchGateResultV1,
    evaluate_research_gates,
)
from .closure import ResearchClosureError, Ws04ClosureV1, close_ws04_research
from .artifacts import (
    ResearchEvidenceArtifactError,
    ResearchEvidenceArtifactReference,
    canonical_evidence_artifact_bytes,
    load_verified_evidence,
)

__all__ = [
    "ComparisonRecord",
    "CostScenario",
    "PointInTimeObservation",
    "RecursiveIndicatorReplay",
    "ResearchGateEvidenceV1",
    "ResearchGateReportV1",
    "ResearchGateResultV1",
    "ResearchClosureError",
    "ResearchEvidenceArtifactError",
    "ResearchEvidenceArtifactReference",
    "ResearchProvenanceV1",
    "WalkForwardFold",
    "analysis_output_sha256",
    "Ws04ClosureV1",
    "canonical_evidence_artifact_bytes",
    "close_ws04_research",
    "evaluate_research_gates",
    "load_verified_evidence",
]
