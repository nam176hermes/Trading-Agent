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
    ConsumedSubmitAuthority,
    GlobalHaltState,
    GlobalHaltStatus,
    GlobalSafetyObservation,
    PreparedSubmitPermit,
    SubmitPermitConsumed,
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
from packages.event_ledger.repository import EventConflictError, EventLedgerRepository
from packages.event_ledger.reducer import SequenceError

from .approval import DurableApprovalError, verify_durable_order_approval
from .canonical import canonical_model_digest, canonical_model_json
from .halt import (
    GlobalHaltAuthorityError,
    GlobalHaltReplay,
    evaluate_global_breaker,
    replay_global_halt_authority,
)
from .safety import (
    GlobalSafetyAuthorityVerifier,
    global_safety_binding_digest,
    verify_global_safety_observation,
)


_EVENT_SCHEMA_VERSION = "submit-permit-prepared-event-v1"
_CONSUMED_EVENT_SCHEMA_VERSION = "submit-permit-consumed-event-v1"
_EVENT_SOURCE = "runtime-risk"
_OUTBOX_TOPIC = "runtime-risk.submit-permits-prepared"
_CONSUMED_OUTBOX_TOPIC = "runtime-risk.submit-permits-consumed"
_PERMIT_LIFETIME = timedelta(seconds=5)
_DECISION_LIFETIME = timedelta(minutes=5)
_CONSUMED_EVENT_LIFETIME = timedelta(minutes=5)
_CANONICAL_ERRORS = (AttributeError, OverflowError, ReplayError, TypeError, ValueError)
_REPOSITORY_ERRORS = (AttributeError, OSError, RuntimeError, TypeError, ValueError)
_PREPARATION_FAILURE = "submit permit preparation failed"
_CONSUMPTION_FAILURE = "submit permit consumption failed"
_ModelT = TypeVar("_ModelT", bound=BaseModel)


class SubmitPermitPreparationError(RuntimeError):
    """Exact durable submit authority could not be prepared."""


class SubmitPermitConsumptionError(RuntimeError):
    """One-shot submit authority could not be proven and consumed."""


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
        or state.transitioned_at > prepared_at
        or observation.observed_at > prepared_at
        or safety.observed_at >= prepared_at
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
    if (
        payload.permit_id in replay.consumed_permit_ids
        or payload.permit_id in replay.retired_permit_ids
    ):
        raise SubmitPermitPreparationError(_PREPARATION_FAILURE)
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
    safety_verifier: GlobalSafetyAuthorityVerifier,
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
    try:
        current_safety = verify_global_safety_observation(
            verifier=safety_verifier,
            observation=current_safety,
        )
    except GlobalHaltAuthorityError as exc:
        raise SubmitPermitPreparationError(_PREPARATION_FAILURE) from exc
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
    if (
        replay.head_authority_at is not None
        and prepared_at < replay.head_authority_at
    ):
        raise SubmitPermitPreparationError(_PREPARATION_FAILURE)
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


def _canonical_consumption_model(
    value: object,
    expected_type: type[_ModelT],
    field: str,
) -> _ModelT:
    try:
        if type(value) is not expected_type:
            raise ValueError(f"{field} has the wrong concrete type")
        return expected_type.model_validate_json(canonical_model_json(value))
    except _CANONICAL_ERRORS as exc:
        raise SubmitPermitConsumptionError(_CONSUMPTION_FAILURE) from exc


def _require_consume_arguments(
    *, consumed_event_id: object, consumed_at: object
) -> None:
    try:
        if type(consumed_event_id) is not UUID or type(consumed_at) is not datetime:
            raise ValueError("consume arguments have invalid concrete types")
        require_utc(consumed_at)
    except _CANONICAL_ERRORS as exc:
        raise SubmitPermitConsumptionError(_CONSUMPTION_FAILURE) from exc


def _load_consumption_history(
    repository: EventLedgerRepository,
    stream_id: UUID,
) -> tuple[tuple[EventEnvelope[object], ...], GlobalHaltReplay]:
    try:
        events = repository.load_events()
        if type(events) is not tuple:
            raise ValueError("ledger read-back must be a tuple")
    except _REPOSITORY_ERRORS as exc:
        raise SubmitPermitConsumptionError(_CONSUMPTION_FAILURE) from exc
    try:
        replay = replay_global_halt_authority(events=events, stream_id=stream_id)
    except GlobalHaltAuthorityError as exc:
        raise SubmitPermitConsumptionError(_CONSUMPTION_FAILURE) from exc
    return events, replay


def _require_exact_prepared_authority(
    *,
    events: tuple[EventEnvelope[object], ...],
    replay: GlobalHaltReplay,
    permit: PreparedSubmitPermit,
) -> None:
    if permit.permit_id in replay.consumed_permit_ids:
        raise SubmitPermitConsumptionError(_CONSUMPTION_FAILURE)
    references = tuple(
        reference
        for reference in replay.prepared
        if reference.permit_id == permit.permit_id
    )
    if len(references) != 1 or references[0] != permit:
        raise SubmitPermitConsumptionError(_CONSUMPTION_FAILURE)
    try:
        matches = tuple(
            event
            for event in events
            if getattr(event, "event_id", None) == permit.prepared_event_id
        )
        if len(matches) != 1:
            raise ValueError("prepared event identity is not unique")
        loaded, canonical_text = _canonical_prepared_event(matches[0])
        if (
            loaded.stream_id != permit.halt_stream_id
            or event_digest(canonical_text) != permit.prepared_event_digest
            or _payload_from_reference(permit) != loaded.payload
        ):
            raise ValueError("prepared event does not match its reference")
    except _CANONICAL_ERRORS as exc:
        raise SubmitPermitConsumptionError(_CONSUMPTION_FAILURE) from exc


def _require_current_consumption_authority(
    *,
    replay: GlobalHaltReplay,
    permit: PreparedSubmitPermit,
    observation: RuntimeRiskObservation,
    policy: RuntimeRiskPolicy,
    safety: GlobalSafetyObservation,
    consumed_at: datetime,
) -> None:
    state = replay.state
    try:
        if not (permit.prepared_at <= consumed_at <= permit.expires_at):
            raise ValueError("permit is outside its consumption window")
        if not (permit.prepared_at <= safety.observed_at <= consumed_at):
            raise ValueError("safety observation is outside the consumption window")
        if (
            replay.head_authority_at is not None
            and consumed_at < replay.head_authority_at
        ):
            raise ValueError("consumption time is behind the authority stream")
        observation_digest = canonical_model_digest(observation)
        policy_digest = canonical_model_digest(policy)
        portfolio_digest = canonical_model_digest(observation.portfolio)
        safety_binding = global_safety_binding_digest(safety)
        if (
            state is None
            or state.status is not GlobalHaltStatus.ACTIVE
            or state.stream_id != permit.halt_stream_id
            or state.generation != permit.halt_generation
            or state.transition_event_id != permit.halt_transition_event_id
            or state.transition_digest != permit.halt_transition_digest
            or policy_digest != permit.runtime_policy_digest
            or observation_digest != permit.runtime_observation_digest
            or portfolio_digest != permit.portfolio_digest
            or safety_binding != permit.safety_binding_digest
            or evaluate_global_breaker(
                observation=observation,
                policy=policy,
                safety=safety,
            )
        ):
            raise ValueError("current submit authority bindings changed")
    except (GlobalHaltAuthorityError, *_CANONICAL_ERRORS) as exc:
        raise SubmitPermitConsumptionError(_CONSUMPTION_FAILURE) from exc


def _consumed_payload(
    permit: PreparedSubmitPermit,
    *,
    consumed_at: datetime,
) -> SubmitPermitConsumed:
    try:
        return SubmitPermitConsumed(
            permit_id=permit.permit_id,
            prepared_event_digest=permit.prepared_event_digest,
            halt_stream_id=permit.halt_stream_id,
            halt_generation=permit.halt_generation,
            halt_transition_digest=permit.halt_transition_digest,
            consumed_at=consumed_at,
            schema_version="submit-permit-consumed-v1",
        )
    except _CANONICAL_ERRORS as exc:
        raise SubmitPermitConsumptionError(_CONSUMPTION_FAILURE) from exc


def _consumed_event(
    payload: SubmitPermitConsumed,
    *,
    sequence: int,
    event_id: UUID,
) -> EventEnvelope[SubmitPermitConsumed]:
    try:
        return EventEnvelope[SubmitPermitConsumed](
            event_id=event_id,
            event_type="SubmitPermitConsumed",
            schema_version=_CONSUMED_EVENT_SCHEMA_VERSION,
            source=_EVENT_SOURCE,
            stream_id=payload.halt_stream_id,
            sequence=sequence,
            observed_at=payload.consumed_at,
            ingested_at=payload.consumed_at,
            produced_at=payload.consumed_at,
            effective_at=payload.consumed_at,
            expires_at=payload.consumed_at + _CONSUMED_EVENT_LIFETIME,
            correlation_id=payload.permit_id,
            causation_id=payload.permit_id,
            trace_id=payload.permit_id,
            payload=payload,
        )
    except _CANONICAL_ERRORS as exc:
        raise SubmitPermitConsumptionError(_CONSUMPTION_FAILURE) from exc


def _consumed_outbox(event: EventEnvelope[object]) -> OutboxIntent:
    try:
        payload = event.payload
        if type(payload) is not SubmitPermitConsumed:
            raise ValueError("consumed outbox requires a consumed payload")
        return OutboxIntent(
            event_id=event.event_id,
            topic=_CONSUMED_OUTBOX_TOPIC,
            payload_json=json.dumps(
                {"permit_id": str(payload.permit_id)},
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    except _CANONICAL_ERRORS as exc:
        raise SubmitPermitConsumptionError(_CONSUMPTION_FAILURE) from exc


def _canonical_consumed_event(
    event: object,
) -> tuple[EventEnvelope[object], str]:
    canonical_text = serialize_event(event)
    canonical = deserialize_event(canonical_text)
    payload = canonical.payload
    if type(payload) is not SubmitPermitConsumed:
        raise ValueError("consumed event payload has the wrong concrete type")
    if (
        canonical.event_type != "SubmitPermitConsumed"
        or canonical.schema_version != _CONSUMED_EVENT_SCHEMA_VERSION
        or canonical.source != _EVENT_SOURCE
        or canonical.stream_id != payload.halt_stream_id
        or canonical.observed_at != payload.consumed_at
        or canonical.ingested_at != payload.consumed_at
        or canonical.produced_at != payload.consumed_at
        or canonical.effective_at != payload.consumed_at
        or canonical.expires_at != payload.consumed_at + _CONSUMED_EVENT_LIFETIME
        or canonical.correlation_id != payload.permit_id
        or canonical.causation_id != payload.permit_id
        or canonical.trace_id != payload.permit_id
    ):
        raise ValueError("consumed event authority bindings are invalid")
    return canonical, canonical_text


def _consumed_reference(
    loaded: EventEnvelope[object], canonical_text: str
) -> ConsumedSubmitAuthority:
    payload = loaded.payload
    if type(payload) is not SubmitPermitConsumed:
        raise RuntimeError("consumed reference requires a consumed payload")
    return ConsumedSubmitAuthority(
        **{
            **payload.model_dump(mode="python"),
            "consumed_event_id": loaded.event_id,
            "consumed_event_digest": event_digest(canonical_text),
            "schema_version": "consumed-submit-authority-v1",
        }
    )


def _append_consumption_against(
    *,
    repository: EventLedgerRepository,
    replay: GlobalHaltReplay,
    payload: SubmitPermitConsumed,
    consumed_event_id: UUID,
) -> ConsumedSubmitAuthority:
    event = _consumed_event(
        payload,
        sequence=replay.head_sequence + 1,
        event_id=consumed_event_id,
    )
    outbox = _consumed_outbox(event)
    try:
        outcome = repository.append(event, outbox)
        if type(outcome) is not AppendOutcome:
            raise ValueError("append receipt has the wrong concrete type")
        canonical_outcome = AppendOutcome.model_validate(
            outcome.model_dump(mode="python")
        )
        if (
            canonical_outcome.event_id != event.event_id
            or not canonical_outcome.inserted
        ):
            raise ValueError("append receipt does not match event")
    except (EventConflictError, SequenceError):
        raise
    except _REPOSITORY_ERRORS as exc:
        raise SubmitPermitConsumptionError(_CONSUMPTION_FAILURE) from exc
    try:
        events = repository.load_events()
        if type(events) is not tuple:
            raise ValueError("ledger read-back must be a tuple")
        matches = tuple(
            item for item in events if getattr(item, "event_id", None) == event.event_id
        )
        if len(matches) != 1:
            raise ValueError("ledger must contain exactly one matching event")
        expected, expected_text = _canonical_consumed_event(event)
        loaded, actual_text = _canonical_consumed_event(matches[0])
        if (
            actual_text.encode("utf-8") != expected_text.encode("utf-8")
            or event_digest(actual_text) != event_digest(expected_text)
            or loaded != expected
        ):
            raise ValueError("consumed event read-back is not byte-identical")
        read_back = replay_global_halt_authority(
            events=events,
            stream_id=payload.halt_stream_id,
        )
        if (
            payload.permit_id not in read_back.consumed_permit_ids
            or any(
                item.permit_id == payload.permit_id for item in read_back.prepared
            )
        ):
            raise ValueError("consumed event was not durably replayed")
        return _consumed_reference(loaded, actual_text)
    except (GlobalHaltAuthorityError, *_CANONICAL_ERRORS) as exc:
        raise SubmitPermitConsumptionError(_CONSUMPTION_FAILURE) from exc


def _event_text_by_id(
    events: tuple[EventEnvelope[object], ...],
) -> dict[UUID, str]:
    result: dict[UUID, str] = {}
    for supplied in events:
        canonical, text = _canonical_consumed_history_event(supplied)
        if canonical.event_id in result:
            raise ValueError("event identity is duplicated")
        result[canonical.event_id] = text
    return result


def _canonical_consumed_history_event(
    event: object,
) -> tuple[EventEnvelope[object], str]:
    text = serialize_event(event)
    return deserialize_event(text), text


def _only_unrelated_permit_events_advanced(
    *,
    before_events: tuple[EventEnvelope[object], ...],
    before: GlobalHaltReplay,
    after_events: tuple[EventEnvelope[object], ...],
    after: GlobalHaltReplay,
    permit: PreparedSubmitPermit,
) -> bool:
    try:
        if before.state != after.state or after.head_sequence <= before.head_sequence:
            return False
        before_by_id = _event_text_by_id(before_events)
        after_by_id = _event_text_by_id(after_events)
        if any(after_by_id.get(event_id) != text for event_id, text in before_by_id.items()):
            return False
        added_ids = set(after_by_id) - set(before_by_id)
        suffix: list[EventEnvelope[object]] = []
        for supplied in after_events:
            event, _ = _canonical_consumed_history_event(supplied)
            if (
                event.stream_id == permit.halt_stream_id
                and event.sequence > before.head_sequence
            ):
                suffix.append(event)
        if not suffix or added_ids != {event.event_id for event in suffix}:
            return False
        if tuple(event.sequence for event in suffix) != tuple(
            range(before.head_sequence + 1, after.head_sequence + 1)
        ):
            return False
        if any(
            type(event.payload) not in (SubmitPermitPrepared, SubmitPermitConsumed)
            or event.payload.permit_id == permit.permit_id
            for event in suffix
        ):
            return False
        refreshed = tuple(
            reference
            for reference in after.prepared
            if reference.permit_id == permit.permit_id
        )
        return len(refreshed) == 1 and refreshed[0] == permit
    except _CANONICAL_ERRORS:
        return False


def consume_submit_permit(
    *,
    repository: EventLedgerRepository,
    permit: PreparedSubmitPermit,
    current_observation: RuntimeRiskObservation,
    current_policy: RuntimeRiskPolicy,
    current_safety: GlobalSafetyObservation,
    safety_verifier: GlobalSafetyAuthorityVerifier,
    consumed_event_id: UUID,
    consumed_at: datetime,
) -> ConsumedSubmitAuthority:
    """Consume one exact five-second permit after a fresh safety observation."""

    permit = _canonical_consumption_model(permit, PreparedSubmitPermit, "permit")
    observation = _canonical_consumption_model(
        current_observation, RuntimeRiskObservation, "current_observation"
    )
    policy = _canonical_consumption_model(
        current_policy, RuntimeRiskPolicy, "current_policy"
    )
    safety = _canonical_consumption_model(
        current_safety, GlobalSafetyObservation, "current_safety"
    )
    try:
        safety = verify_global_safety_observation(
            verifier=safety_verifier,
            observation=safety,
        )
    except GlobalHaltAuthorityError as exc:
        raise SubmitPermitConsumptionError(_CONSUMPTION_FAILURE) from exc
    _require_consume_arguments(
        consumed_event_id=consumed_event_id,
        consumed_at=consumed_at,
    )
    events, replay = _load_consumption_history(repository, permit.halt_stream_id)
    _require_exact_prepared_authority(
        events=events,
        replay=replay,
        permit=permit,
    )
    _require_current_consumption_authority(
        replay=replay,
        permit=permit,
        observation=observation,
        policy=policy,
        safety=safety,
        consumed_at=consumed_at,
    )
    payload = _consumed_payload(permit, consumed_at=consumed_at)
    try:
        return _append_consumption_against(
            repository=repository,
            replay=replay,
            payload=payload,
            consumed_event_id=consumed_event_id,
        )
    except (EventConflictError, SequenceError) as first_conflict:
        refreshed_events, refreshed = _load_consumption_history(
            repository,
            permit.halt_stream_id,
        )
        if (
            permit.permit_id in refreshed.consumed_permit_ids
            or not _only_unrelated_permit_events_advanced(
                before_events=events,
                before=replay,
                after_events=refreshed_events,
                after=refreshed,
                permit=permit,
            )
        ):
            raise SubmitPermitConsumptionError(
                "submit permit authority changed"
            ) from first_conflict
        _require_current_consumption_authority(
            replay=refreshed,
            permit=permit,
            observation=observation,
            policy=policy,
            safety=safety,
            consumed_at=consumed_at,
        )
        try:
            return _append_consumption_against(
                repository=repository,
                replay=refreshed,
                payload=payload,
                consumed_event_id=consumed_event_id,
            )
        except (EventConflictError, SequenceError) as second_conflict:
            raise SubmitPermitConsumptionError(
                _CONSUMPTION_FAILURE
            ) from second_conflict


def _canonical_audit_model(
    value: object,
    expected_type: type[_ModelT],
) -> _ModelT:
    if type(value) is not expected_type:
        raise ValueError("audit replay model has the wrong concrete type")
    canonical = expected_type.model_validate_json(canonical_model_json(value))
    if canonical != value:
        raise ValueError("audit replay model drifted during canonical validation")
    return canonical


def audit_submit_authority_stream(
    *,
    repository: EventLedgerRepository,
    stream_id: UUID,
) -> GlobalHaltReplay:
    """Read and strictly revalidate one complete submit-authority stream once."""

    if type(stream_id) is not UUID:
        raise GlobalHaltAuthorityError("global halt authority is invalid")
    try:
        events = repository.load_events()
        if type(events) is not tuple:
            raise ValueError("ledger read-back must be a tuple")
    except _REPOSITORY_ERRORS as exc:
        raise GlobalHaltAuthorityError(
            "global halt authority is invalid"
        ) from exc

    replay = replay_global_halt_authority(events=events, stream_id=stream_id)
    try:
        if replay.state is not None:
            _canonical_audit_model(replay.state, GlobalHaltState)
        for reference in replay.prepared:
            _canonical_audit_model(reference, PreparedSubmitPermit)
        if any(type(permit_id) is not UUID for permit_id in replay.consumed_permit_ids):
            raise ValueError("consumed permit identity is malformed")
        selected = tuple(
            _canonical_consumed_history_event(event)
            for event in events
            if getattr(event, "stream_id", None) == stream_id
        )
        if len(selected) != replay.head_sequence:
            raise ValueError("audit replay head count drifted")
        if selected:
            head, head_text = selected[-1]
            if (
                replay.head_event_id != head.event_id
                or replay.head_event_digest != event_digest(head_text)
            ):
                raise ValueError("audit replay head identity drifted")
        elif replay.head_event_id is not None or replay.head_event_digest is not None:
            raise ValueError("empty audit replay has a head identity")
        return replay
    except (ReplayError, ValueError) as exc:
        raise GlobalHaltAuthorityError(
            "global halt authority is invalid"
        ) from exc


__all__ = [
    "SubmitPermitConsumptionError",
    "SubmitPermitPreparationError",
    "audit_submit_authority_stream",
    "consume_submit_permit",
    "prepare_submit_permit",
]
