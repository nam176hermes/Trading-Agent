from __future__ import annotations

import json
from datetime import timedelta
from typing import Callable
from uuid import UUID

import pytest
from pydantic import BaseModel

from packages.domain import EventEnvelope, OrderIntent, RiskDecision
from packages.domain.runtime_risk import (
    DurableOrderApprovalRef,
    RuntimeOrderRiskDecision,
    RuntimeRiskObservation,
    RuntimeRiskOutcome,
    RuntimeRiskPolicy,
)
from packages.event_ledger import (
    AppendOutcome,
    EventLedgerRepository,
    InMemoryEventLedger,
    OutboxIntent,
    SequenceError,
    deserialize_event,
    serialize_event,
)
from packages.event_ledger.replay import event_digest
from packages.runtime_risk import (
    DurableApprovalError,
    canonical_model_digest,
    record_runtime_risk_decision,
    verify_durable_order_approval,
)

from tests.runtime_risk.test_evaluator import EvaluatorCase, evaluator_case, uid


def runtime_risk_event(
    case: EvaluatorCase | None = None,
    *,
    event_id: UUID | None = None,
    stream_id: UUID | None = None,
    sequence: int = 1,
    decision: RuntimeOrderRiskDecision | None = None,
) -> EventEnvelope[RuntimeOrderRiskDecision]:
    selected = case or evaluator_case()
    payload = decision or selected.evaluate()
    return EventEnvelope[RuntimeOrderRiskDecision](
        event_id=event_id or uid(100),
        event_type="RuntimeOrderRiskDecision",
        schema_version="runtime-order-risk-event-v1",
        source="runtime-risk",
        stream_id=stream_id or uid(101),
        sequence=sequence,
        observed_at=payload.decided_at,
        ingested_at=payload.decided_at,
        produced_at=payload.decided_at,
        effective_at=payload.decided_at,
        expires_at=payload.decided_at + timedelta(minutes=5),
        correlation_id=selected.intent.intent_id,
        causation_id=selected.intent.risk_decision_id,
        trace_id=uid(102),
        payload=payload,
    )


def record_approved(
    repository: EventLedgerRepository | None = None,
) -> tuple[
    InMemoryEventLedger | EventLedgerRepository,
    EvaluatorCase,
    EventEnvelope[RuntimeOrderRiskDecision],
    DurableOrderApprovalRef,
]:
    selected_repository = repository or InMemoryEventLedger()
    case = evaluator_case()
    event = runtime_risk_event(case)
    reference = record_runtime_risk_decision(
        repository=selected_repository,
        event=event,
    )
    assert reference is not None
    return selected_repository, case, event, reference


class BoundedRepository:
    def __init__(
        self,
        *,
        events: tuple[EventEnvelope[object], ...] = (),
        append_error: RuntimeError | ValueError | None = None,
        load_error: RuntimeError | ValueError | None = None,
    ) -> None:
        self.events = events
        self.append_error = append_error
        self.load_error = load_error
        self.appended = False
        self.outbox: OutboxIntent | None = None

    def append(
        self, event: EventEnvelope[object], outbox: OutboxIntent
    ) -> AppendOutcome:
        if self.append_error is not None:
            raise self.append_error
        self.appended = True
        self.outbox = outbox
        return AppendOutcome(event_id=event.event_id, inserted=True)

    def load_events(self) -> tuple[EventEnvelope[object], ...]:
        if not self.appended:
            raise AssertionError("read-back happened before persistence")
        if self.load_error is not None:
            raise self.load_error
        return self.events

    def claim_inbox(self, consumer: str, event_id: UUID) -> bool:
        raise NotImplementedError

    def acknowledge_outbox(self, event_id: UUID) -> bool:
        raise NotImplementedError

    def save_snapshot(self, snapshot: object) -> None:
        raise NotImplementedError

    def load_snapshot(self, state_hash: str) -> None:
        raise NotImplementedError


class EventIdOnly:
    def __init__(self, event_id: UUID) -> None:
        self.event_id = event_id


def model_fields(value: BaseModel) -> dict[str, object]:
    return {name: getattr(value, name) for name in type(value).model_fields}


def forged_model(value: BaseModel, **updates: object) -> object:
    return type(value).model_construct(**{**model_fields(value), **updates})


def matching_reference(
    event: EventEnvelope[RuntimeOrderRiskDecision],
) -> DurableOrderApprovalRef:
    decision = event.payload
    canonical_event = serialize_event(event)
    return DurableOrderApprovalRef(
        decision_outcome=RuntimeRiskOutcome.APPROVED,
        event_id=event.event_id,
        stream_id=event.stream_id,
        sequence=event.sequence,
        event_digest=event_digest(canonical_event),
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
        schema_version="durable-order-approval-v1",
    )


def verify(
    repository: EventLedgerRepository,
    reference: DurableOrderApprovalRef,
    case: EvaluatorCase,
    **changes: object,
) -> RuntimeOrderRiskDecision:
    arguments: dict[str, object] = {
        "repository": repository,
        "reference": reference,
        "intent": case.intent,
        "policy_decision": case.policy_decision,
        "observation": case.observation,
        "policy": case.policy,
    }
    arguments.update(changes)
    return verify_durable_order_approval(**arguments)  # type: ignore[arg-type]


def test_typed_runtime_risk_event_codec_preserves_concrete_payload_and_bytes() -> None:
    event = runtime_risk_event()

    encoded = serialize_event(event)
    restored = deserialize_event(encoded)

    assert type(restored.payload) is RuntimeOrderRiskDecision
    assert restored == event
    assert serialize_event(restored) == encoded


def test_approved_decision_is_durable_before_reference_and_verifies_exactly() -> None:
    case = evaluator_case()
    event = runtime_risk_event(case)
    repository = BoundedRepository(events=(event,))

    reference = record_runtime_risk_decision(repository=repository, event=event)

    assert repository.appended is True
    assert reference is not None
    assert verify(repository, reference, case) == event.payload


def test_record_builds_the_exact_canonical_runtime_risk_outbox() -> None:
    ledger, _, event, reference = record_approved()
    assert reference is not None
    assert isinstance(ledger, InMemoryEventLedger)

    assert ledger.load_outbox() == (
        OutboxIntent(
            event_id=event.event_id,
            topic="runtime-risk.decisions",
            payload_json=json.dumps(
                {"decision_id": str(event.payload.decision_id)},
                sort_keys=True,
                separators=(",", ":"),
            ),
        ),
    )


def test_exact_idempotent_append_returns_the_same_reference() -> None:
    ledger = InMemoryEventLedger()
    event = runtime_risk_event()

    first = record_runtime_risk_decision(repository=ledger, event=event)
    second = record_runtime_risk_decision(repository=ledger, event=event)

    assert first is not None
    assert second == first
    assert ledger.load_events() == (event,)


def test_rejected_decision_is_appended_for_audit_but_never_returns_authority() -> None:
    case = evaluator_case()
    observation = case.observation.model_copy(update={"engine_ready": False})
    rejected = case.evaluate(observation=observation)
    event = runtime_risk_event(case, decision=rejected)
    ledger = InMemoryEventLedger()

    assert record_runtime_risk_decision(repository=ledger, event=event) is None
    assert ledger.load_events() == (event,)
    assert ledger.load_outbox()[0].topic == "runtime-risk.decisions"


@pytest.mark.parametrize(
    "error",
    [RuntimeError("append failed"), SequenceError("sequence conflict")],
)
def test_append_failures_are_bounded_and_never_return_a_reference(
    error: RuntimeError | ValueError,
) -> None:
    repository = BoundedRepository(append_error=error)

    with pytest.raises(DurableApprovalError) as caught:
        record_runtime_risk_decision(
            repository=repository,
            event=runtime_risk_event(),
        )

    assert caught.value.__cause__ is error
    assert "append failed" not in str(caught.value)
    assert "sequence conflict" not in str(caught.value)


def test_conflicting_content_never_returns_the_prior_reference() -> None:
    ledger = InMemoryEventLedger()
    event = runtime_risk_event()
    assert record_runtime_risk_decision(repository=ledger, event=event) is not None
    conflicting = event.model_copy(update={"source": "other-runtime-risk"})

    with pytest.raises(DurableApprovalError):
        record_runtime_risk_decision(repository=ledger, event=conflicting)


@pytest.mark.parametrize(
    "events_factory",
    [
        lambda event: (),
        lambda event: (event, event),
        lambda event: (event, EventIdOnly(event.event_id)),
        lambda event: (event.model_construct(**{**model_fields(event), "event_type": "SignalProposal"}),),
        lambda event: (event.model_construct(**{**model_fields(event), "payload": object()}),),
        lambda event: (event.model_copy(update={"source": "mutated-read-back"}),),
    ],
    ids=(
        "absent",
        "duplicate-id",
        "malformed-duplicate-id",
        "wrong-event-type",
        "wrong-payload",
        "mutated",
    ),
)
def test_record_rejects_non_exact_read_back(
    events_factory: Callable[
        [EventEnvelope[RuntimeOrderRiskDecision]], tuple[EventEnvelope[object], ...]
    ],
) -> None:
    event = runtime_risk_event()
    repository = BoundedRepository(events=events_factory(event))

    with pytest.raises(DurableApprovalError):
        record_runtime_risk_decision(repository=repository, event=event)


def test_read_back_exception_is_bounded_and_chained() -> None:
    error = RuntimeError("private repository detail")
    repository = BoundedRepository(load_error=error)

    with pytest.raises(DurableApprovalError) as caught:
        record_runtime_risk_decision(repository=repository, event=runtime_risk_event())

    assert caught.value.__cause__ is error
    assert "private repository detail" not in str(caught.value)


def test_verification_rejects_each_forged_reference_binding_one_at_a_time() -> None:
    ledger, case, _, reference = record_approved()
    changes: tuple[dict[str, object], ...] = (
        {"event_id": uid(201)},
        {"stream_id": uid(202)},
        {"sequence": 2},
        {"event_digest": "0" * 64},
        {"decision_id": uid(203)},
        {"decision_digest": "1" * 64},
        {"intent_id": uid(204)},
        {"intent_digest": "2" * 64},
        {"risk_decision_id": uid(205)},
        {"policy_risk_decision_digest": "3" * 64},
        {"portfolio_snapshot_id": uid(206)},
        {"portfolio_digest": "4" * 64},
        {"observation_id": uid(207)},
        {"observation_version": 2},
        {"observation_digest": "5" * 64},
        {"policy_id": uid(208)},
        {"policy_version": "forged-policy"},
        {"policy_digest": "6" * 64},
        {"decision_outcome": RuntimeRiskOutcome.REJECTED},
        {"schema_version": "forged-approval-v1"},
    )

    for update in changes:
        forged = DurableOrderApprovalRef.model_construct(
            **{**model_fields(reference), **update}
        )
        with pytest.raises(DurableApprovalError):
            verify(ledger, forged, case)


@pytest.mark.parametrize(
    ("field", "mutate"),
    [
        (
            "intent",
            lambda case: case.intent.model_copy(
                update={"client_order_id": "different-client-order"}
            ),
        ),
        (
            "policy_decision",
            lambda case: case.policy_decision.model_copy(
                update={"policy_version": "different-target-policy"}
            ),
        ),
        (
            "observation",
            lambda case: case.observation.model_copy(
                update={
                    "portfolio": case.observation.portfolio.model_copy(
                        update={"snapshot_id": uid(301)}
                    )
                }
            ),
        ),
        (
            "observation",
            lambda case: case.observation.model_copy(
                update={"observation_id": uid(302)}
            ),
        ),
        (
            "policy",
            lambda case: case.policy.model_copy(
                update={"policy_version": "different-runtime-policy"}
            ),
        ),
    ],
    ids=("intent", "target-policy", "portfolio", "observation", "runtime-policy"),
)
def test_verification_recomputes_every_input_binding(
    field: str, mutate: Callable[[EvaluatorCase], object]
) -> None:
    ledger, case, _, reference = record_approved()

    with pytest.raises(DurableApprovalError):
        verify(ledger, reference, case, **{field: mutate(case)})


@pytest.mark.parametrize("field", ("intent", "policy_decision", "observation", "policy"))
def test_verification_revalidates_forged_input_models(field: str) -> None:
    ledger, case, _, reference = record_approved()
    original = getattr(case, field)
    forged = forged_model(original, schema_version="forged-schema")

    with pytest.raises(DurableApprovalError):
        verify(ledger, reference, case, **{field: forged})


def test_verification_recomputes_the_complete_runtime_decision() -> None:
    ledger, case, event, reference = record_approved()
    forged_decision = RuntimeOrderRiskDecision.model_construct(
        **{
            **model_fields(event.payload),
            "projected_gross": event.payload.projected_pending,
        }
    )
    forged_event = event.model_construct(
        **{**model_fields(event), "payload": forged_decision}
    )
    repository = BoundedRepository(events=(forged_event,))
    repository.appended = True
    forged_reference = DurableOrderApprovalRef.model_construct(
        **{
            **model_fields(reference),
            "event_digest": event_digest(serialize_event(forged_event)),
            "decision_digest": canonical_model_digest(forged_decision),
        }
    )

    with pytest.raises(DurableApprovalError):
        verify(repository, forged_reference, case)


@pytest.mark.parametrize(
    "update",
    [
        {"source": "forged-source"},
        {"schema_version": "forged-event-schema"},
        {"correlation_id": uid(401)},
        {"causation_id": uid(402)},
    ],
)
def test_verification_rejects_forged_event_authority_bindings(
    update: dict[str, object],
) -> None:
    _, case, event, reference = record_approved()
    forged_event = event.model_copy(update=update)
    repository = BoundedRepository(events=(forged_event,))
    repository.appended = True
    forged_reference = DurableOrderApprovalRef.model_construct(
        **{
            **model_fields(reference),
            "event_digest": event_digest(serialize_event(forged_event)),
        }
    )

    with pytest.raises(DurableApprovalError):
        verify(repository, forged_reference, case)


def test_verification_rejects_a_reference_to_an_exact_rejected_decision() -> None:
    case = evaluator_case()
    rejected_observation = case.observation.model_copy(update={"engine_ready": False})
    rejected_case = EvaluatorCase(
        case.intent,
        case.policy_decision,
        rejected_observation,
        case.policy,
    )
    event = runtime_risk_event(rejected_case)
    ledger = InMemoryEventLedger()
    assert record_runtime_risk_decision(repository=ledger, event=event) is None
    reference = matching_reference(event)

    with pytest.raises(DurableApprovalError):
        verify(ledger, reference, rejected_case)


def test_verification_rejects_a_different_repository() -> None:
    _, case, _, reference = record_approved()

    with pytest.raises(DurableApprovalError):
        verify(InMemoryEventLedger(), reference, case)


@pytest.mark.parametrize(
    "events_factory",
    [
        lambda event: (),
        lambda event: (event, event),
        lambda event: (event, EventIdOnly(event.event_id)),
        lambda event: (event.model_construct(**{**model_fields(event), "event_type": "SignalProposal"}),),
        lambda event: (event.model_construct(**{**model_fields(event), "payload": object()}),),
        lambda event: (event.model_copy(update={"source": "mutated-read-back"}),),
    ],
    ids=(
        "absent",
        "duplicate-id",
        "malformed-duplicate-id",
        "wrong-event-type",
        "wrong-payload",
        "mutated",
    ),
)
def test_verification_rejects_non_exact_read_back(
    events_factory: Callable[
        [EventEnvelope[RuntimeOrderRiskDecision]], tuple[EventEnvelope[object], ...]
    ],
) -> None:
    _, case, event, reference = record_approved()
    repository = BoundedRepository(events=events_factory(event))
    repository.appended = True

    with pytest.raises(DurableApprovalError):
        verify(repository, reference, case)
