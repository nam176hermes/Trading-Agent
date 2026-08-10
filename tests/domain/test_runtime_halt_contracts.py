from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

from packages.domain import (
    ConsumedSubmitAuthority,
    GlobalHaltReasonCode,
    GlobalHaltRecoveryAuthorization,
    GlobalHaltState,
    GlobalHaltStatus,
    GlobalHaltTransition,
    GlobalSafetyObservation,
    PreparedSubmitPermit,
    SubmitPermitConsumed,
    SubmitPermitPrepared,
)
from packages.safety_evidence import CanonicalKillSwitchState


NOW = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)


def safety_observation(**changes: object) -> GlobalSafetyObservation:
    values: dict[str, object] = {
        "source_fingerprint": "1" * 64,
        "kill_switch_state": CanonicalKillSwitchState.INACTIVE,
        "observed_at": NOW,
        "schema_version": "global-safety-observation-v1",
    }
    values.update(changes)
    return GlobalSafetyObservation(**values)


def halt_state(**changes: object) -> GlobalHaltState:
    values: dict[str, object] = {
        "stream_id": UUID(int=100),
        "generation": 1,
        "status": GlobalHaltStatus.ACTIVE,
        "transition_event_id": UUID(int=101),
        "transition_digest": "2" * 64,
        "prior_transition_event_id": None,
        "prior_transition_digest": None,
        "runtime_policy_digest": "3" * 64,
        "runtime_observation_digest": "4" * 64,
        "portfolio_digest": "5" * 64,
        "safety_observation_digest": "6" * 64,
        "reason_codes": (GlobalHaltReasonCode.INITIALIZED_SAFE,),
        "transitioned_at": NOW,
        "schema_version": "global-halt-state-v1",
    }
    values.update(changes)
    return GlobalHaltState(**values)


def recovery_authorization(**changes: object) -> GlobalHaltRecoveryAuthorization:
    values: dict[str, object] = {
        "authorization_id": UUID(int=110),
        "authorization_digest": "7" * 64,
        "halted_generation": 2,
        "halted_transition_digest": "8" * 64,
        "runtime_policy_digest": "3" * 64,
        "runtime_observation_digest": "4" * 64,
        "portfolio_digest": "5" * 64,
        "safety_binding_digest": "9" * 64,
        "issued_at": NOW,
        "expires_at": NOW + timedelta(minutes=1),
        "operator_authority_digest": "a" * 64,
        "schema_version": "global-halt-recovery-authorization-v1",
    }
    values.update(changes)
    return GlobalHaltRecoveryAuthorization(**values)


def halt_transition(**changes: object) -> GlobalHaltTransition:
    values: dict[str, object] = {
        "transition_id": UUID(int=120),
        "prior_generation": 0,
        "prior_transition_digest": None,
        "next_generation": 1,
        "next_status": GlobalHaltStatus.ACTIVE,
        "reason_codes": (GlobalHaltReasonCode.INITIALIZED_SAFE,),
        "runtime_policy_digest": "3" * 64,
        "runtime_observation_digest": "4" * 64,
        "portfolio_digest": "5" * 64,
        "safety_observation_digest": "6" * 64,
        "recovery_authorization_digest": None,
        "decided_at": NOW,
        "schema_version": "global-halt-transition-v1",
    }
    values.update(changes)
    return GlobalHaltTransition(**values)


def prepared_payload(**changes: object) -> SubmitPermitPrepared:
    values: dict[str, object] = {
        "permit_id": UUID(int=101),
        "approval_event_id": UUID(int=102),
        "approval_reference_digest": "2" * 64,
        "intent_digest": "3" * 64,
        "policy_risk_decision_digest": "4" * 64,
        "runtime_risk_decision_digest": "5" * 64,
        "runtime_policy_digest": "6" * 64,
        "runtime_observation_digest": "7" * 64,
        "portfolio_digest": "8" * 64,
        "safety_binding_digest": "9" * 64,
        "halt_stream_id": UUID(int=103),
        "halt_generation": 3,
        "halt_transition_event_id": UUID(int=104),
        "halt_transition_digest": "a" * 64,
        "prepared_at": NOW,
        "expires_at": NOW + timedelta(seconds=5),
        "schema_version": "submit-permit-prepared-v1",
    }
    values.update(changes)
    return SubmitPermitPrepared(**values)


def prepared_permit(**changes: object) -> PreparedSubmitPermit:
    values = prepared_payload().model_dump()
    values.update(
        prepared_event_id=UUID(int=105),
        prepared_event_digest="b" * 64,
        schema_version="prepared-submit-permit-v1",
    )
    values.update(changes)
    return PreparedSubmitPermit(**values)


def consumed_payload(**changes: object) -> SubmitPermitConsumed:
    values: dict[str, object] = {
        "permit_id": UUID(int=101),
        "prepared_event_digest": "b" * 64,
        "halt_stream_id": UUID(int=103),
        "halt_generation": 3,
        "halt_transition_digest": "a" * 64,
        "consumed_at": NOW + timedelta(seconds=2),
        "schema_version": "submit-permit-consumed-v1",
    }
    values.update(changes)
    return SubmitPermitConsumed(**values)


def consumed_authority(**changes: object) -> ConsumedSubmitAuthority:
    values = consumed_payload().model_dump()
    values.update(
        consumed_event_id=UUID(int=106),
        consumed_event_digest="c" * 64,
        schema_version="consumed-submit-authority-v1",
    )
    values.update(changes)
    return ConsumedSubmitAuthority(**values)


@pytest.mark.parametrize(
    "factory",
    [
        safety_observation,
        halt_state,
        recovery_authorization,
        halt_transition,
        prepared_payload,
        prepared_permit,
        consumed_payload,
        consumed_authority,
    ],
)
def test_runtime_halt_contracts_construct_exact_public_models(factory: object) -> None:
    assert callable(factory)
    assert factory() is not None


def test_runtime_halt_public_enums_are_complete_and_stable() -> None:
    assert tuple(status.value for status in GlobalHaltStatus) == ("ACTIVE", "HALTED")
    assert tuple(reason.value for reason in GlobalHaltReasonCode) == (
        "SAFETY_AUTHORITY_UNKNOWN",
        "KILL_SWITCH_ACTIVE",
        "DAILY_LOSS_LIMIT",
        "DRAWDOWN_LIMIT",
        "RECOVERY_AUTHORIZED",
        "INITIALIZED_SAFE",
    )


def test_global_halt_state_represents_only_canonical_recovered_active_state() -> None:
    recovered = halt_state(
        generation=2,
        status=GlobalHaltStatus.ACTIVE,
        prior_transition_event_id=UUID(int=99),
        prior_transition_digest="d" * 64,
        reason_codes=(GlobalHaltReasonCode.RECOVERY_AUTHORIZED,),
    )

    assert recovered.generation == 2
    assert recovered.status is GlobalHaltStatus.ACTIVE
    assert recovered.reason_codes == (GlobalHaltReasonCode.RECOVERY_AUTHORIZED,)

    with pytest.raises(ValidationError):
        halt_state(
            generation=2,
            status=GlobalHaltStatus.ACTIVE,
            prior_transition_event_id=UUID(int=99),
            prior_transition_digest="d" * 64,
            reason_codes=(GlobalHaltReasonCode.INITIALIZED_SAFE,),
        )


@pytest.mark.parametrize(
    ("factory", "changes"),
    [
        (safety_observation, {"extra": "forged"}),
        (halt_state, {"generation": "1"}),
        (halt_state, {"transitioned_at": NOW.replace(tzinfo=None)}),
        (halt_state, {"transition_digest": "NOT-A-DIGEST"}),
        (halt_state, {"generation": 0}),
        (halt_state, {"generation": 2, "prior_transition_event_id": None, "prior_transition_digest": "d" * 64}),
        (halt_transition, {"next_generation": 1, "prior_generation": 1, "prior_transition_digest": "d" * 64}),
        (halt_transition, {"event_digest": "d" * 64}),
        (halt_state, {"reason_codes": (GlobalHaltReasonCode.INITIALIZED_SAFE,) * 2}),
        (halt_state, {"reason_codes": (GlobalHaltReasonCode.RECOVERY_AUTHORIZED,)}),
        (halt_transition, {"next_status": GlobalHaltStatus.HALTED, "reason_codes": (GlobalHaltReasonCode.RECOVERY_AUTHORIZED,), "recovery_authorization_digest": "d" * 64}),
        (halt_transition, {"next_generation": 2, "prior_generation": 1, "prior_transition_digest": "d" * 64, "reason_codes": (GlobalHaltReasonCode.RECOVERY_AUTHORIZED,), "recovery_authorization_digest": None}),
        (prepared_payload, {"expires_at": NOW + timedelta(seconds=4)}),
        (consumed_authority, {"prepared_event_digest": "not-a-digest"}),
    ],
)
def test_runtime_halt_contracts_reject_invalid_authority_shapes(
    factory: object, changes: dict[str, object]
) -> None:
    assert callable(factory)
    with pytest.raises(ValidationError):
        factory(**changes)


def test_runtime_halt_copy_and_construct_ingress_cannot_bypass_nested_validation() -> None:
    permit = prepared_permit()
    with pytest.raises(ValidationError):
        permit.model_copy(update={"halt_generation": "forged"})
    forged_construct = PreparedSubmitPermit.model_construct(
        **{**permit.model_dump(), "halt_generation": "forged"}
    )

    with pytest.raises(ValidationError):
        PreparedSubmitPermit.model_validate(forged_construct)
    with pytest.raises(ValueError):
        forged_construct.model_dump()


def test_runtime_halt_deep_copy_preserves_safety_enum_identity() -> None:
    copied = safety_observation().model_copy(deep=True)
    assert copied.kill_switch_state is CanonicalKillSwitchState.INACTIVE
