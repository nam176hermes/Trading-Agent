"""Root-runtime authority models for the sealed Nautilus upgrade."""

from packages.nautilus_upgrade_authority.generation import (
    CandidateGeneration,
    CandidateGenerationError,
    load_candidate_generation,
)
from packages.nautilus_upgrade_authority.lts import (
    CheckpointCompatibility,
    EngineLifecycle,
    EngineRegistryEntry,
    EventApiEpoch,
    LineageRole,
    P1ChangeClass,
    P1CompatibilityTupleV1,
    P1ImpactDecisionV1,
    P1ImpactDisposition,
    P1LtsPolicyError,
    P1LtsPolicyV1,
    SourceQualification,
    classify_changed_paths,
    classify_checkpoint_compatibility,
    classify_p1_change,
    golden_registry_sha256,
    load_p1_lts_policy,
    validate_p1_lts_identity,
    validate_engine_registry,
)


__all__ = [
    "CandidateGeneration",
    "CandidateGenerationError",
    "CheckpointCompatibility",
    "EngineLifecycle",
    "EngineRegistryEntry",
    "EventApiEpoch",
    "LineageRole",
    "P1ChangeClass",
    "P1CompatibilityTupleV1",
    "P1ImpactDecisionV1",
    "P1ImpactDisposition",
    "P1LtsPolicyError",
    "P1LtsPolicyV1",
    "SourceQualification",
    "classify_changed_paths",
    "classify_checkpoint_compatibility",
    "classify_p1_change",
    "golden_registry_sha256",
    "load_candidate_generation",
    "load_p1_lts_policy",
    "validate_p1_lts_identity",
    "validate_engine_registry",
]
