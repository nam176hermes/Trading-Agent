"""Durable preparation of short-lived, halt-bound submit permits."""

from __future__ import annotations

from datetime import datetime, timedelta
import json
from typing import TypeVar
from uuid import UUID

from pydantic import BaseModel

from packages.domain.clock import require_utc
from packages.domain.events import EventEnvelope
from packages.domain.orders import OrderIntent
from packages.domain.risk import RiskDecision
from packages.domain.runtime_halt import (
    GlobalHaltState,
    GlobalHaltStatus,
    GlobalSafetyObservation,
    PreparedSubmitPermit,
    SubmitPermitPrepared,
)
from packages.domain.runtime_risk import (
    DurableOrderApprovalRef,
    RuntimeOrderRiskDecision,
    RuntimeRiskObservation,
    RuntimeRiskPolicy,
)
from packages.event_ledger.models import AppendOutcome, OutboxIntent
from packages.event_ledger.replay import (
    ReplayError,
    deserialize_event,
    event_digest,
    serialize_event,
)
from packages.event_ledger.repository import EventLedgerRepository

from .approval import DurableApprovalError, verify_durable_order_approval
from .canonical import canonical_model_digest, canonical_model_json
from .halt import (
    GlobalHaltAuthorityError,
    GlobalHaltReplay,
    evaluate_global_breaker,
    replay_global_halt_authority,
)
from .safety import global_safety_binding_digest


_EVENT_SCHEMA_VERSION = "submit-permit-prepared-event-v1"
_EVENT_SOURCE = "runtime-risk"
_OUTBOX_TOPIC = "runtime-risk.submit-permits-prepared"
_PERMIT_LIFETIME = timedelta(seconds=5)
_DECISION_LIFETIME = timedelta(minutes=5)
_CANONICAL_ERRORS = (AttributeError, OverflowError, ReplayError, TypeError, ValueError)
_REPOSITORY_ERRORS = (AttributeError, OSError, RuntimeError, TypeError, ValueError)
_PREPARATION_FAILURE = "submit permit preparation failed"
_ModelT = TypeVar("_ModelT", bound=BaseModel)


class SubmitPermitPreparationError(RuntimeError):
    """Exact durable submit authority could not be prepared."""


def _canonical_model(
    value: object,
    expected_type: type[_ModelT],
    field: str,
) -> _ModelT:
    try:
        if type(value) is not expected_type:
            raise ValueError(f"{field} has the wrong concrete type")
        return expected_type.model_validate_json(canonical_model_json(value))
    except _CANONICAL_ERRORS as exc:
        raise SubmitPermitPreparationError(_PREPARATION_FAILURE) from exc


def _require_prepare_arguments(
    *, halt_stream_id: object, permit_id: object, event_id: object, prepared_at: object
) -> None:
    try:
        if (
            type(halt_stream_id) is not UUID
            or type(permit_id) is not UUID
            or type(event_id) is not UUID
            or type(prepared_at) is not datetime
        ):
            raise ValueError("prepare arguments have invalid concrete types")
        require_utc(prepared_at)
    except _CANONICAL_ERRORS as exc:
        raise SubmitPermitPreparationError(_PREPARATION_FAILURE) from exc


def _require_exact_current_bindings(
    *,
    verified_decision: RuntimeOrderRiskDecision,
    approval_observation: RuntimeRiskObservation,
    approval_policy: RuntimeRiskPolicy,
    current_observation: RuntimeRiskObservation,
    current_policy: RuntimeRiskPolicy,
) -> None:
    try:
        approval_observation_digest = canonical_model_digest(approval_observation)
        approval_policy_digest = canonical_model_digest(approval_policy)
        current_observation_digest = canonical_model_digest(current_observation)
        current_policy_digest = canonical_model_digest(current_policy)
        current_portfolio_digest = canonical_model_digest(current_observation.portfolio)
        if (
            approval_observation_digest != verified_decision.observation_digest
            or approval_policy_digest != verified_decision.policy_digest
            or current_observation_digest != approval_observation_digest
            or current_policy_digest != approval_policy_digest
            or current_portfolio_digest != verified_decision.portfolio_digest
            or current_observation.observation_id != verified_decision.observation_id
            or current_observation.state_version
            != verified_decision.observation_version
            or current_observation.portfolio.snapshot_id
            != verified_decision.portfolio_snapshot_id
            or current_policy.policy_id != verified_decision.policy_id
            or current_policy.policy_version != verified_decision.policy_version
        ):
            raise ValueError("current authority differs from durable approval")
    except _CANONICAL_ERRORS as exc:
        raise SubmitPermitPreparationError(_PREPARATION_FAILURE) from exc


def _load_and_replay(
    repository: EventLedgerRepository, halt_stream_id: UUID
) -> GlobalHaltReplay:
    try:
        events = repository.load_events()
        if type(events) is not tuple:
            raise ValueError("ledger read-back must be a tuple")
    except _REPOSITORY_ERRORS as exc:
        raise SubmitPermitPreparationError(_PREPARATION_FAILURE) from exc
    try:
        return replay_global_halt_authority(
            events=events,
            stream_id=halt_stream_id,
        )
    except GlobalHaltAuthorityError as exc:
        raise SubmitPermitPreparationError(_PREPARATION_FAILURE) from exc


def _require_active_exact_authority(
    *,
    state: GlobalHaltState | None,
    observation: RuntimeRiskObservation,
    policy: RuntimeRiskPolicy,
    safety: GlobalSafetyObservation,
    prepared_at: datetime,
) -> None:
    if (
        state is None
        or state.status is not GlobalHaltStatus.ACTIVE
        or observation.observed_at > prepared_at
        or safety.observed_at > prepared_at
    ):
        raise SubmitPermitPreparationError(_PREPARATION_FAILURE)
    try:
        reasons = evaluate_global_breaker(
            observation=observation,
            policy=policy,
            safety=safety,
        )
    except GlobalHaltAuthorityError as exc:
        raise SubmitPermitPreparationError(_PREPARATION_FAILURE) from exc
    if reasons:
        raise SubmitPermitPreparationError(_PREPARATION_FAILURE)


def _prepared_payload(
    *,
    permit_id: UUID,
    approval_reference: DurableOrderApprovalRef,
    verified_decision: RuntimeOrderRiskDecision,
    observation: RuntimeRiskObservation,
    policy: RuntimeRiskPolicy,
    safety_binding_digest: str,
    halt_state: GlobalHaltState | None,
    prepared_at: datetime,
) -> SubmitPermitPrepared:
    try:
        if halt_state is None:
            raise ValueError("halt authority is uninitialized")
        decision_age = prepared_at - verified_decision.decided_at
        if decision_age < timedelta(0) or decision_age >= _DECISION_LIFETIME:
            raise ValueError("durable decision authority is expired")
        return SubmitPermitPrepared(
            permit_id=permit_id,
            approval_event_id=approval_reference.event_id,
            approval_reference_digest=canonical_model_digest(approval_reference),
            intent_digest=verified_decision.intent_digest,
            policy_risk_decision_digest=verified_decision.policy_risk_decision_digest,
            runtime_risk_decision_digest=canonical_model_digest(verified_decision),
            runtime_policy_digest=canonical_model_digest(policy),
            runtime_observation_digest=canonical_model_digest(observation),
            portfolio_digest=canonical_model_digest(observation.portfolio),
            safety_binding_digest=safety_binding_digest,
            halt_stream_id=halt_state.stream_id,
            halt_generation=halt_state.generation,
            halt_transition_event_id=halt_state.transition_event_id,
            halt_transition_digest=halt_state.transition_digest,
            prepared_at=prepared_at,
            expires_at=prepared_at + _PERMIT_LIFETIME,
            schema_version="submit-permit-prepared-v1",
        )
    except _CANONICAL_ERRORS as exc:
        raise SubmitPermitPreparationError(_PREPARATION_FAILURE) from exc


def _prepared_event(
    payload: SubmitPermitPrepared, *, sequence: int, event_id: UUID
) -> EventEnvelope[SubmitPermitPrepared]:
    try:
        return EventEnvelope[SubmitPermitPrepared](
            event_id=event_id,
            event_type="SubmitPermitPrepared",
            schema_version=_EVENT_SCHEMA_VERSION,
            source=_EVENT_SOURCE,
            stream_id=payload.halt_stream_id,
            sequence=sequence,
            observed_at=payload.prepared_at,
            ingested_at=payload.prepared_at,
            produced_at=payload.prepared_at,
            effective_at=payload.prepared_at,
            expires_at=payload.expires_at,
            correlation_id=payload.permit_id,
            causation_id=payload.approval_event_id,
            trace_id=payload.permit_id,
            payload=payload,
        )
    except _CANONICAL_ERRORS as exc:
        raise SubmitPermitPreparationError(_PREPARATION_FAILURE) from exc


def _prepared_outbox(event: EventEnvelope[object]) -> OutboxIntent:
    try:
        payload = event.payload
        if type(payload) is not SubmitPermitPrepared:
            raise ValueError("prepared outbox requires a prepared payload")
        return OutboxIntent(
            event_id=event.event_id,
            topic=_OUTBOX_TOPIC,
            payload_json=json.dumps(
                {"permit_id": str(payload.permit_id)},
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    except _CANONICAL_ERRORS as exc:
        raise SubmitPermitPreparationError(_PREPARATION_FAILURE) from exc


def _canonical_prepared_event(
    event: object,
) -> tuple[EventEnvelope[object], str]:
    text = serialize_event(event)
    canonical = deserialize_event(text)
    payload = canonical.payload
    if type(payload) is not SubmitPermitPrepared:
        raise ValueError("prepared event payload has the wrong concrete type")
    if (
        canonical.event_type != "SubmitPermitPrepared"
        or canonical.schema_version != _EVENT_SCHEMA_VERSION
        or canonical.source != _EVENT_SOURCE
        or canonical.stream_id != payload.halt_stream_id
        or canonical.observed_at != payload.prepared_at
        or canonical.ingested_at != payload.prepared_at
        or canonical.produced_at != payload.prepared_at
        or canonical.effective_at != payload.prepared_at
        or canonical.expires_at != payload.expires_at
        or canonical.correlation_id != payload.permit_id
        or canonical.causation_id != payload.approval_event_id
        or canonical.trace_id != payload.permit_id
    ):
        raise ValueError("prepared event authority bindings are invalid")
    return canonical, text


def _append_and_read_back(
    repository: EventLedgerRepository,
    event: EventEnvelope[SubmitPermitPrepared],
    outbox: OutboxIntent,
) -> tuple[EventEnvelope[object], str]:
    try:
        outcome = repository.append(event, outbox)
        canonical_outcome = AppendOutcome.model_validate(
            outcome.model_dump(mode="python")
        )
        if canonical_outcome.event_id != event.event_id:
            raise ValueError("append receipt does not match event")
    except _REPOSITORY_ERRORS as exc:
        raise SubmitPermitPreparationError(_PREPARATION_FAILURE) from exc
    try:
        events = repository.load_events()
        if type(events) is not tuple:
            raise ValueError("ledger read-back must be a tuple")
        matches = tuple(
            item for item in events if getattr(item, "event_id", None) == event.event_id
        )
        if len(matches) != 1:
            raise ValueError("ledger must contain exactly one matching event")
    except _REPOSITORY_ERRORS as exc:
        raise SubmitPermitPreparationError(_PREPARATION_FAILURE) from exc
    try:
        expected, expected_text = _canonical_prepared_event(event)
        loaded, actual_text = _canonical_prepared_event(matches[0])
        if (
            actual_text.encode("utf-8") != expected_text.encode("utf-8")
            or event_digest(actual_text) != event_digest(expected_text)
            or loaded != expected
        ):
            raise ValueError("prepared event read-back is not byte-identical")
        return loaded, actual_text
    except _CANONICAL_ERRORS as exc:
        raise SubmitPermitPreparationError(_PREPARATION_FAILURE) from exc


def _payload_from_reference(reference: PreparedSubmitPermit) -> SubmitPermitPrepared:
    return SubmitPermitPrepared(
        **{
            **{
                name: getattr(reference, name)
                for name in SubmitPermitPrepared.model_fields
            },
            "schema_version": "submit-permit-prepared-v1",
        }
    )


def _exact_existing_prepared(
    *,
    replay: GlobalHaltReplay,
    payload: SubmitPermitPrepared,
    event_id: UUID,
) -> PreparedSubmitPermit | None:
    matches = tuple(
        item for item in replay.prepared if item.permit_id == payload.permit_id
    )
    if not matches:
        return None
    if len(matches) != 1:
        raise SubmitPermitPreparationError(_PREPARATION_FAILURE)
    existing = matches[0]
    if (
        existing.prepared_event_id != event_id
        or _payload_from_reference(existing) != payload
    ):
        raise SubmitPermitPreparationError(_PREPARATION_FAILURE)
    return existing


def _prepared_reference(
    loaded: EventEnvelope[object], canonical_text: str
) -> PreparedSubmitPermit:
    payload = loaded.payload
    if type(payload) is not SubmitPermitPrepared:
        raise RuntimeError("prepared reference requires a prepared payload")
    return PreparedSubmitPermit(
        **{
            **payload.model_dump(mode="python"),
            "prepared_event_id": loaded.event_id,
            "prepared_event_digest": event_digest(canonical_text),
            "schema_version": "prepared-submit-permit-v1",
        }
    )


def prepare_submit_permit(
    *,
    repository: EventLedgerRepository,
    halt_stream_id: UUID,
    approval_reference: DurableOrderApprovalRef,
    intent: OrderIntent,
    policy_decision: RiskDecision,
    approval_observation: RuntimeRiskObservation,
    approval_policy: RuntimeRiskPolicy,
    current_observation: RuntimeRiskObservation,
    current_policy: RuntimeRiskPolicy,
    current_safety: GlobalSafetyObservation,
    permit_id: UUID,
    event_id: UUID,
    prepared_at: datetime,
) -> PreparedSubmitPermit:
    """Prepare one durable five-second submit permit after exact re-verification."""

    reference = _canonical_model(
        approval_reference, DurableOrderApprovalRef, "approval_reference"
    )
    intent = _canonical_model(intent, OrderIntent, "intent")
    policy_decision = _canonical_model(
        policy_decision, RiskDecision, "policy_decision"
    )
    approval_observation = _canonical_model(
        approval_observation, RuntimeRiskObservation, "approval_observation"
    )
    approval_policy = _canonical_model(
        approval_policy, RuntimeRiskPolicy, "approval_policy"
    )
    current_observation = _canonical_model(
        current_observation, RuntimeRiskObservation, "current_observation"
    )
    current_policy = _canonical_model(
        current_policy, RuntimeRiskPolicy, "current_policy"
    )
    current_safety = _canonical_model(
        current_safety, GlobalSafetyObservation, "current_safety"
    )
    _require_prepare_arguments(
        halt_stream_id=halt_stream_id,
        permit_id=permit_id,
        event_id=event_id,
        prepared_at=prepared_at,
    )
    try:
        verified_decision = verify_durable_order_approval(
            repository=repository,
            reference=reference,
            intent=intent,
            policy_decision=policy_decision,
            observation=approval_observation,
            policy=approval_policy,
        )
    except DurableApprovalError as exc:
        raise SubmitPermitPreparationError(_PREPARATION_FAILURE) from exc
    _require_exact_current_bindings(
        verified_decision=verified_decision,
        approval_observation=approval_observation,
        approval_policy=approval_policy,
        current_observation=current_observation,
        current_policy=current_policy,
    )
    replay = _load_and_replay(repository, halt_stream_id)
    try:
        safety_binding = global_safety_binding_digest(current_safety)
    except GlobalHaltAuthorityError as exc:
        raise SubmitPermitPreparationError(_PREPARATION_FAILURE) from exc
    _require_active_exact_authority(
        state=replay.state,
        observation=current_observation,
        policy=current_policy,
        safety=current_safety,
        prepared_at=prepared_at,
    )
    payload = _prepared_payload(
        permit_id=permit_id,
        approval_reference=reference,
        verified_decision=verified_decision,
        observation=current_observation,
        policy=current_policy,
        safety_binding_digest=safety_binding,
        halt_state=replay.state,
        prepared_at=prepared_at,
    )
    existing = _exact_existing_prepared(
        replay=replay,
        payload=payload,
        event_id=event_id,
    )
    if existing is not None:
        return existing
    event = _prepared_event(
        payload,
        sequence=replay.head_sequence + 1,
        event_id=event_id,
    )
    loaded, text = _append_and_read_back(
        repository,
        event,
        _prepared_outbox(event),
    )
    return _prepared_reference(loaded, text)


__all__ = ["SubmitPermitPreparationError", "prepare_submit_permit"]
