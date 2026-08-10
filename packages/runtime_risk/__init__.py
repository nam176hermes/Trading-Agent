"""Pure canonical identity and projection helpers for runtime risk."""

from .canonical import canonical_model_digest, canonical_model_json
from .approval import (
    DurableApprovalError,
    record_runtime_risk_decision,
    verify_durable_order_approval,
)
from .evaluator import evaluate_runtime_order_risk
from .projections import ProjectionError, RuntimeRiskProjection, project_runtime_order
from .halt import (
    GlobalHaltAuthorityError,
    GlobalHaltRecoveryAuthorityVerifier,
    GlobalHaltRecoveryError,
    GlobalHaltReplay,
    evaluate_global_breaker,
    recover_global_halt,
    record_global_halt_observation,
    replay_global_halt_authority,
)
from .safety import global_safety_binding_digest, observe_global_safety
from .submit_authority import (
    SubmitPermitConsumptionError,
    SubmitPermitPreparationError,
    audit_submit_authority_stream,
    consume_submit_permit,
    prepare_submit_permit,
)

__all__ = [
    "ProjectionError",
    "RuntimeRiskProjection",
    "DurableApprovalError",
    "canonical_model_digest",
    "canonical_model_json",
    "evaluate_runtime_order_risk",
    "project_runtime_order",
    "record_runtime_risk_decision",
    "verify_durable_order_approval",
    "GlobalHaltAuthorityError",
    "GlobalHaltRecoveryAuthorityVerifier",
    "GlobalHaltRecoveryError",
    "GlobalHaltReplay",
    "evaluate_global_breaker",
    "global_safety_binding_digest",
    "observe_global_safety",
    "recover_global_halt",
    "record_global_halt_observation",
    "replay_global_halt_authority",
    "SubmitPermitPreparationError",
    "SubmitPermitConsumptionError",
    "audit_submit_authority_stream",
    "consume_submit_permit",
    "prepare_submit_permit",
]
