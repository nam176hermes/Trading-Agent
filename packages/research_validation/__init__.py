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
    PHASE4_SCENARIO_IDS,
    ResearchCampaignEvidenceV2,
    VerifiedScenarioComparisonV1,
    campaign_analysis_output_sha256,
)
from .evaluator import (
    ResearchGateReportV1,
    ResearchGateResultV1,
    evaluate_research_gates,
    evaluate_research_campaign,
)
from .closure import (
    ResearchClosureError,
    Ws04CampaignClosureV2,
    Ws04ClosureV1,
    Ws04ScenarioClosureV2,
    close_ws04_research,
    close_ws04_research_campaign,
)
from .artifacts import (
    ResearchEvidenceArtifactError,
    ResearchEvidenceArtifactReference,
    canonical_evidence_artifact_bytes,
    load_verified_evidence,
)
from .producers import (
    CAMPAIGN_ARTIFACTS,
    CampaignEvidenceError,
    VerifiedCampaignScenarioV1,
    VerifiedCampaignV1,
    load_verified_campaign,
    materialize_phase4_campaign,
    produce_research_campaign_evidence,
)
from packages.nautilus_backtest import PaperCompatibilityResultV1

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
    "PHASE4_SCENARIO_IDS",
    "PaperCompatibilityResultV1",
    "ResearchCampaignEvidenceV2",
    "VerifiedScenarioComparisonV1",
    "analysis_output_sha256",
    "campaign_analysis_output_sha256",
    "Ws04ClosureV1",
    "canonical_evidence_artifact_bytes",
    "close_ws04_research",
    "close_ws04_research_campaign",
    "evaluate_research_gates",
    "evaluate_research_campaign",
    "load_verified_evidence",
    "CAMPAIGN_ARTIFACTS",
    "CampaignEvidenceError",
    "VerifiedCampaignScenarioV1",
    "VerifiedCampaignV1",
    "load_verified_campaign",
    "materialize_phase4_campaign",
    "produce_research_campaign_evidence",
    "Ws04CampaignClosureV2",
    "Ws04ScenarioClosureV2",
]
