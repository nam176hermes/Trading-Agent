"""Durable event-ledger authority for approved runtime-risk decisions."""

from __future__ import annotations

from datetime import timedelta
import json
from typing import TypeVar
from uuid import UUID

from pydantic import BaseModel

from packages.domain.events import EventEnvelope
from packages.domain.orders import OrderIntent
from packages.domain.risk import RiskDecision
from packages.domain.runtime_risk import (
    DurableOrderApprovalRef,
    RuntimeOrderRiskDecision,
    RuntimeRiskObservation,
    RuntimeRiskOutcome,
    RuntimeRiskPolicy,
)
from packages.event_ledger.models import AppendOutcome, OutboxIntent
from packages.event_ledger.replay import (
    deserialize_event,
    event_digest,
    serialize_event,
)
from packages.event_ledger.repository import EventLedgerRepository

from .canonical import canonical_model_digest, canonical_model_json
from .evaluator import evaluate_runtime_order_risk


_EVENT_TYPE = "RuntimeOrderRiskDecision"
_EVENT_SCHEMA_VERSION = "runtime-order-risk-event-v1"
_EVENT_SOURCE = "runtime-risk"
_OUTBOX_TOPIC = "runtime-risk.decisions"
_APPROVAL_SCHEMA_VERSION = "durable-order-approval-v1"
_EVENT_LIFETIME = timedelta(minutes=5)
_CANONICALIZATION_ERRORS = (AttributeError, OverflowError, TypeError, ValueError)
_REPOSITORY_ERRORS = (AttributeError, OSError, RuntimeError, TypeError, ValueError)
_RECORDING_FAILURE = "durable runtime-risk decision recording failed"
_VERIFICATION_FAILURE = "durable runtime-risk approval verification failed"
_ModelT = TypeVar("_ModelT", bound=BaseModel)


class DurableApprovalError(RuntimeError):
    """The ledger cannot prove exact durable approval authority."""


def _canonical_model(
    value: object,
    expected_type: type[_ModelT],
    *,
    field: str,
) -> _ModelT:
    if type(value) is not expected_type:
        raise ValueError(f"{field} has the wrong concrete type")
    encoded = canonical_model_json(value)
    try:
        return expected_type.model_validate_json(encoded)
    except ValueError as exc:
        raise ValueError(f"{field} is not canonical") from exc


def _canonical_runtime_event(
    event: object,
) -> tuple[EventEnvelope[object], str]:
    canonical_text = serialize_event(event)
    canonical = deserialize_event(canonical_text)
    if type(canonical.payload) is not RuntimeOrderRiskDecision:
        raise ValueError("event payload has the wrong concrete type")
    payload = canonical.payload
    if (
        canonical.event_type != _EVENT_TYPE
        or canonical.schema_version != _EVENT_SCHEMA_VERSION
        or canonical.source != _EVENT_SOURCE
        or canonical.correlation_id != payload.intent_id
        or canonical.causation_id != payload.risk_decision_id
        or canonical.observed_at != payload.decided_at
        or canonical.ingested_at != payload.decided_at
        or canonical.produced_at != payload.decided_at
        or canonical.effective_at != payload.decided_at
        or canonical.expires_at - payload.decided_at != _EVENT_LIFETIME
    ):
        raise ValueError("event authority bindings are invalid")
    return canonical, canonical_text


def _load_one_event(
    repository: EventLedgerRepository,
    event_id: UUID,
    *,
    failure_message: str,
) -> EventEnvelope[object]:
    try:
        events = repository.load_events()
    except _REPOSITORY_ERRORS as exc:
        raise DurableApprovalError(failure_message) from exc
    try:
        if type(events) is not tuple:
            raise ValueError("ledger read-back must be a tuple")
        matches = tuple(
            event for event in events if getattr(event, "event_id", None) == event_id
        )
        if len(matches) != 1:
            raise ValueError("ledger must contain exactly one matching event")
        match = matches[0]
        if not isinstance(match, EventEnvelope):
            raise ValueError("matching ledger record must be an event envelope")
        return match
    except _REPOSITORY_ERRORS as exc:
        raise DurableApprovalError(failure_message) from exc


def _canonical_event_boundary(
    event: object,
    *,
    failure_message: str,
) -> tuple[EventEnvelope[object], str]:
    try:
        return _canonical_runtime_event(event)
    except _CANONICALIZATION_ERRORS as exc:
        raise DurableApprovalError(failure_message) from exc


def _canonical_model_boundary(
    value: object,
    expected_type: type[_ModelT],
    *,
    field: str,
) -> _ModelT:
    try:
        return _canonical_model(value, expected_type, field=field)
    except _CANONICALIZATION_ERRORS as exc:
        raise DurableApprovalError(_VERIFICATION_FAILURE) from exc


def _append_boundary(
    repository: EventLedgerRepository,
    event: EventEnvelope[object],
    outbox: OutboxIntent,
) -> None:
    try:
        append_result = repository.append(event, outbox)
    except _REPOSITORY_ERRORS as exc:
        raise DurableApprovalError(_RECORDING_FAILURE) from exc
    try:
        canonical_result = AppendOutcome.model_validate(
            append_result.model_dump(mode="python")
        )
    except _REPOSITORY_ERRORS as exc:
        raise DurableApprovalError(_RECORDING_FAILURE) from exc
    if canonical_result.event_id != event.event_id:
        raise DurableApprovalError(_RECORDING_FAILURE)


def _canonical_outbox(event: EventEnvelope[object]) -> OutboxIntent:
    payload = event.payload
    if type(payload) is not RuntimeOrderRiskDecision:
        raise ValueError("event payload has the wrong concrete type")
    return OutboxIntent(
        event_id=event.event_id,
        topic=_OUTBOX_TOPIC,
        payload_json=json.dumps(
            {"decision_id": str(payload.decision_id)},
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


def _approval_reference(
    event: EventEnvelope[object],
    canonical_event_text: str,
) -> DurableOrderApprovalRef:
    decision = event.payload
    if type(decision) is not RuntimeOrderRiskDecision:
        raise ValueError("event payload has the wrong concrete type")
    return DurableOrderApprovalRef(
        decision_outcome=RuntimeRiskOutcome.APPROVED,
        event_id=event.event_id,
        stream_id=event.stream_id,
        sequence=event.sequence,
        event_digest=event_digest(canonical_event_text),
        decision_id=decision.decision_id,
        decision_digest=canonical_model_digest(decision),
        intent_id=decision.intent_id,
        intent_digest=decision.intent_digest,
        risk_decision_id=decision.risk_decision_id,
        policy_risk_decision_digest=decision.policy_risk_decision_digest,
        portfolio_snapshot_id=decision.portfolio_snapshot_id,
        portfolio_digest=decision.portfolio_digest,
        observation_id=decision.observation_id,
        observation_version=decision.observation_version,
        observation_digest=decision.observation_digest,
        policy_id=decision.policy_id,
        policy_version=decision.policy_version,
        policy_digest=decision.policy_digest,
        schema_version=_APPROVAL_SCHEMA_VERSION,
    )


def record_runtime_risk_decision(
    *,
    repository: EventLedgerRepository,
    event: EventEnvelope[RuntimeOrderRiskDecision],
) -> DurableOrderApprovalRef | None:
    """Atomically append, exactly read back, and authorize an approved decision."""

    canonical_event, expected_text = _canonical_event_boundary(
        event,
        failure_message=_RECORDING_FAILURE,
    )
    outbox = _canonical_outbox(canonical_event)
    _append_boundary(repository, canonical_event, outbox)
    loaded = _load_one_event(
        repository,
        canonical_event.event_id,
        failure_message=_RECORDING_FAILURE,
    )
    loaded_event, actual_text = _canonical_event_boundary(
        loaded,
        failure_message=_RECORDING_FAILURE,
    )
    if (
        actual_text.encode("utf-8") != expected_text.encode("utf-8")
        or event_digest(actual_text) != event_digest(expected_text)
        or loaded_event != canonical_event
    ):
        raise DurableApprovalError(_RECORDING_FAILURE)
    if loaded_event.payload.outcome is RuntimeRiskOutcome.REJECTED:
        return None
    if loaded_event.payload.outcome is not RuntimeRiskOutcome.APPROVED:
        raise DurableApprovalError(_RECORDING_FAILURE)
    return _approval_reference(loaded_event, actual_text)


def _reference_matches(
    reference: DurableOrderApprovalRef,
    event: EventEnvelope[object],
    canonical_event_text: str,
    decision: RuntimeOrderRiskDecision,
) -> bool:
    return (
        reference.decision_outcome is RuntimeRiskOutcome.APPROVED
        and reference.event_id == event.event_id
        and reference.stream_id == event.stream_id
        and reference.sequence == event.sequence
        and reference.event_digest == event_digest(canonical_event_text)
        and reference.decision_id == decision.decision_id
        and reference.decision_digest == canonical_model_digest(decision)
        and reference.intent_id == decision.intent_id
        and reference.intent_digest == decision.intent_digest
        and reference.risk_decision_id == decision.risk_decision_id
        and reference.policy_risk_decision_digest
        == decision.policy_risk_decision_digest
        and reference.portfolio_snapshot_id == decision.portfolio_snapshot_id
        and reference.portfolio_digest == decision.portfolio_digest
        and reference.observation_id == decision.observation_id
        and reference.observation_version == decision.observation_version
        and reference.observation_digest == decision.observation_digest
        and reference.policy_id == decision.policy_id
        and reference.policy_version == decision.policy_version
        and reference.policy_digest == decision.policy_digest
        and reference.schema_version == _APPROVAL_SCHEMA_VERSION
    )


def verify_durable_order_approval(
    *,
    repository: EventLedgerRepository,
    reference: DurableOrderApprovalRef,
    intent: OrderIntent,
    policy_decision: RiskDecision,
    observation: RuntimeRiskObservation,
    policy: RuntimeRiskPolicy,
) -> RuntimeOrderRiskDecision:
    """Recompute and verify every input, decision, event, and ledger binding."""

    canonical_reference = _canonical_model_boundary(
        reference,
        DurableOrderApprovalRef,
        field="reference",
    )
    canonical_intent = _canonical_model_boundary(
        intent,
        OrderIntent,
        field="intent",
    )
    canonical_policy_decision = _canonical_model_boundary(
        policy_decision,
        RiskDecision,
        field="policy_decision",
    )
    canonical_observation = _canonical_model_boundary(
        observation,
        RuntimeRiskObservation,
        field="observation",
    )
    canonical_policy = _canonical_model_boundary(
        policy,
        RuntimeRiskPolicy,
        field="policy",
    )
    loaded = _load_one_event(
        repository,
        canonical_reference.event_id,
        failure_message=_VERIFICATION_FAILURE,
    )
    canonical_event, canonical_event_text = _canonical_event_boundary(
        loaded,
        failure_message=_VERIFICATION_FAILURE,
    )
    decision = canonical_event.payload
    if type(decision) is not RuntimeOrderRiskDecision:
        raise DurableApprovalError(_VERIFICATION_FAILURE)
    if decision.outcome is not RuntimeRiskOutcome.APPROVED:
        raise DurableApprovalError(_VERIFICATION_FAILURE)
    try:
        expected_decision = evaluate_runtime_order_risk(
            decision_id=decision.decision_id,
            intent=canonical_intent,
            policy_decision=canonical_policy_decision,
            observation=canonical_observation,
            policy=canonical_policy,
            decided_at=decision.decided_at,
        )
    except ValueError as exc:
        raise DurableApprovalError(_VERIFICATION_FAILURE) from exc
    if (
        expected_decision.outcome is not RuntimeRiskOutcome.APPROVED
        or canonical_model_json(expected_decision) != canonical_model_json(decision)
        or canonical_model_digest(canonical_intent) != decision.intent_digest
        or canonical_model_digest(canonical_policy_decision)
        != decision.policy_risk_decision_digest
        or canonical_model_digest(canonical_observation.portfolio)
        != decision.portfolio_digest
        or canonical_model_digest(canonical_observation)
        != decision.observation_digest
        or canonical_model_digest(canonical_policy) != decision.policy_digest
        or not _reference_matches(
            canonical_reference,
            canonical_event,
            canonical_event_text,
            decision,
        )
    ):
        raise DurableApprovalError(_VERIFICATION_FAILURE)
    return expected_decision
