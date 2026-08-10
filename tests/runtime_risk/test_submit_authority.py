from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable
from uuid import UUID

import pytest

import packages.runtime_risk.submit_authority as submit_authority_module
from packages.domain import EventEnvelope
from packages.domain.runtime_halt import (
    GlobalHaltRecoveryAuthorization,
    GlobalHaltStatus,
    GlobalSafetyObservation,
    PreparedSubmitPermit,
    SubmitPermitConsumed,
    SubmitPermitPrepared,
)
from packages.domain.runtime_risk import DurableOrderApprovalRef, RuntimeRiskOutcome
from packages.event_ledger import (
    AppendOutcome,
    EventConflictError,
    EventLedgerRepository,
    InMemoryEventLedger,
    OutboxIntent,
    deserialize_event,
    serialize_event,
)
from packages.event_ledger.replay import event_digest
from packages.runtime_risk import (
    SubmitPermitPreparationError,
    canonical_model_digest,
    global_safety_binding_digest,
    prepare_submit_permit,
    record_global_halt_observation,
    record_runtime_risk_decision,
    recover_global_halt,
    replay_global_halt_authority,
)
from packages.safety_evidence import CanonicalKillSwitchState

from tests.runtime_risk.test_approval import matching_reference, runtime_risk_event
from tests.runtime_risk.test_evaluator import EvaluatorCase, NOW, evaluator_case, uid


HALT_STREAM_ID = uid(800)
PREPARED_AT = NOW + timedelta(seconds=1)
PERMIT_ID = uid(801)
PREPARED_EVENT_ID = uid(802)


def safety(
    *,
    state: CanonicalKillSwitchState = CanonicalKillSwitchState.INACTIVE,
    source_fingerprint: str = "a" * 64,
    observed_at: datetime = PREPARED_AT,
) -> GlobalSafetyObservation:
    return GlobalSafetyObservation(
        source_fingerprint=source_fingerprint,
        kill_switch_state=state,
        observed_at=observed_at,
        schema_version="global-safety-observation-v1",
    )


@dataclass(frozen=True)
class PrepareCase:
    ledger: InMemoryEventLedger
    evaluator: EvaluatorCase
    approval_reference: DurableOrderApprovalRef
    approval_event: EventEnvelope[object]
    initial_safety: GlobalSafetyObservation


def approved_active_case() -> PrepareCase:
    ledger = InMemoryEventLedger()
    selected = evaluator_case()
    approval_event = runtime_risk_event(
        selected,
        event_id=uid(803),
        stream_id=uid(804),
    )
    approval_reference = record_runtime_risk_decision(
        repository=ledger,
        event=approval_event,
    )
    assert approval_reference is not None
    initial_safety = safety(observed_at=NOW)
    state = record_global_halt_observation(
        repository=ledger,
        stream_id=HALT_STREAM_ID,
        observation=selected.observation,
        policy=selected.policy,
        safety=initial_safety,
        transition_id=uid(805),
        event_id=uid(806),
        decided_at=NOW,
    )
    assert state.status is GlobalHaltStatus.ACTIVE
    return PrepareCase(
        ledger=ledger,
        evaluator=selected,
        approval_reference=approval_reference,
        approval_event=approval_event,
        initial_safety=initial_safety,
    )


class ExactRecoveryVerifier:
    def verify(self, **kwargs: object) -> GlobalHaltRecoveryAuthorization:
        authorization = kwargs["authorization"]
        assert type(authorization) is GlobalHaltRecoveryAuthorization
        return authorization


def active_case_with_transition_at(
    transitioned_at: datetime, *, recovered: bool
) -> PrepareCase:
    ledger = InMemoryEventLedger()
    selected = evaluator_case()
    approval_event = runtime_risk_event(
        selected,
        event_id=uid(850),
        stream_id=uid(851),
    )
    approval_reference = record_runtime_risk_decision(
        repository=ledger,
        event=approval_event,
    )
    assert approval_reference is not None
    initial_safety = safety(
        state=(
            CanonicalKillSwitchState.ACTIVE
            if recovered
            else CanonicalKillSwitchState.INACTIVE
        ),
        observed_at=NOW,
    )
    state = record_global_halt_observation(
        repository=ledger,
        stream_id=HALT_STREAM_ID,
        observation=selected.observation,
        policy=selected.policy,
        safety=initial_safety,
        transition_id=uid(852),
        event_id=uid(853),
        decided_at=NOW if recovered else transitioned_at,
    )
    if recovered:
        assert state.status is GlobalHaltStatus.HALTED
        safe = safety(observed_at=NOW)
        authorization = GlobalHaltRecoveryAuthorization(
            authorization_id=uid(854),
            authorization_digest="b" * 64,
            halted_generation=state.generation,
            halted_transition_digest=state.transition_digest,
            runtime_policy_digest=canonical_model_digest(selected.policy),
            runtime_observation_digest=canonical_model_digest(selected.observation),
            portfolio_digest=canonical_model_digest(selected.observation.portfolio),
            safety_binding_digest=global_safety_binding_digest(safe),
            issued_at=NOW,
            expires_at=NOW + timedelta(minutes=10),
            operator_authority_digest="c" * 64,
            schema_version="global-halt-recovery-authorization-v1",
        )
        state = recover_global_halt(
            repository=ledger,
            stream_id=HALT_STREAM_ID,
            observation=selected.observation,
            policy=selected.policy,
            safety=safe,
            authorization=authorization,
            verifier=ExactRecoveryVerifier(),
            transition_id=uid(855),
            event_id=uid(856),
            decided_at=transitioned_at,
        )
    assert state.status is GlobalHaltStatus.ACTIVE
    return PrepareCase(
        ledger=ledger,
        evaluator=selected,
        approval_reference=approval_reference,
        approval_event=approval_event,
        initial_safety=initial_safety,
    )


def prepare(
    case: PrepareCase,
    *,
    repository: EventLedgerRepository | None = None,
    **changes: object,
) -> PreparedSubmitPermit:
    arguments: dict[str, object] = {
        "repository": repository or case.ledger,
        "halt_stream_id": HALT_STREAM_ID,
        "approval_reference": case.approval_reference,
        "intent": case.evaluator.intent,
        "policy_decision": case.evaluator.policy_decision,
        "approval_observation": case.evaluator.observation,
        "approval_policy": case.evaluator.policy,
        "current_observation": case.evaluator.observation,
        "current_policy": case.evaluator.policy,
        "current_safety": safety(),
        "permit_id": PERMIT_ID,
        "event_id": PREPARED_EVENT_ID,
        "prepared_at": PREPARED_AT,
    }
    arguments.update(changes)
    return prepare_submit_permit(**arguments)  # type: ignore[arg-type]


def prepared_envelope(ledger: InMemoryEventLedger) -> EventEnvelope[object]:
    return next(
        event
        for event in ledger.load_events()
        if event.event_id == PREPARED_EVENT_ID
    )


class RepositoryProxy:
    def __init__(
        self,
        ledger: InMemoryEventLedger,
        *,
        append_error: Exception | None = None,
        wrong_receipt: bool = False,
        load_transform: Callable[
            [tuple[EventEnvelope[object], ...]], tuple[EventEnvelope[object], ...]
        ]
        | None = None,
    ) -> None:
        self.ledger = ledger
        self.append_error = append_error
        self.wrong_receipt = wrong_receipt
        self.load_transform = load_transform

    def append(
        self, event: EventEnvelope[object], outbox: OutboxIntent
    ) -> AppendOutcome:
        if event.event_type == "SubmitPermitPrepared" and self.append_error is not None:
            raise self.append_error
        outcome = self.ledger.append(event, outbox)
        if event.event_type == "SubmitPermitPrepared" and self.wrong_receipt:
            return AppendOutcome(event_id=uid(899), inserted=True)
        return outcome

    def load_events(self) -> tuple[EventEnvelope[object], ...]:
        events = self.ledger.load_events()
        return events if self.load_transform is None else self.load_transform(events)

    def claim_inbox(self, consumer: str, event_id: UUID) -> bool:
        return self.ledger.claim_inbox(consumer, event_id)

    def acknowledge_outbox(self, event_id: UUID) -> bool:
        return self.ledger.acknowledge_outbox(event_id)

    def save_snapshot(self, snapshot: object) -> None:
        raise NotImplementedError

    def load_snapshot(self, state_hash: str) -> None:
        raise NotImplementedError


def test_prepare_submit_permit_persists_every_exact_binding_and_flat_reference() -> None:
    case = approved_active_case()
    halt = replay_global_halt_authority(
        events=case.ledger.load_events(), stream_id=HALT_STREAM_ID
    ).state
    assert halt is not None

    permit = prepare(case)

    envelope = prepared_envelope(case.ledger)
    decision = case.approval_event.payload
    assert permit == PreparedSubmitPermit(
        permit_id=PERMIT_ID,
        approval_event_id=case.approval_reference.event_id,
        approval_reference_digest=canonical_model_digest(case.approval_reference),
        intent_digest=canonical_model_digest(case.evaluator.intent),
        policy_risk_decision_digest=canonical_model_digest(
            case.evaluator.policy_decision
        ),
        runtime_risk_decision_digest=canonical_model_digest(decision),
        runtime_policy_digest=canonical_model_digest(case.evaluator.policy),
        runtime_observation_digest=canonical_model_digest(case.evaluator.observation),
        portfolio_digest=canonical_model_digest(case.evaluator.observation.portfolio),
        safety_binding_digest=global_safety_binding_digest(safety()),
        halt_stream_id=HALT_STREAM_ID,
        halt_generation=halt.generation,
        halt_transition_event_id=halt.transition_event_id,
        halt_transition_digest=halt.transition_digest,
        prepared_at=PREPARED_AT,
        expires_at=PREPARED_AT + timedelta(seconds=5),
        prepared_event_id=envelope.event_id,
        prepared_event_digest=event_digest(serialize_event(envelope)),
        schema_version="prepared-submit-permit-v1",
    )
    assert type(envelope.payload) is SubmitPermitPrepared
    assert "prepared_event_id" not in type(envelope.payload).model_fields
    assert "prepared_event_digest" not in type(envelope.payload).model_fields
    prepared_outbox = next(
        item for item in case.ledger.load_outbox() if item.event_id == PREPARED_EVENT_ID
    )
    assert prepared_outbox == OutboxIntent(
        event_id=PREPARED_EVENT_ID,
        topic="runtime-risk.submit-permits-prepared",
        payload_json=json.dumps(
            {"permit_id": str(PERMIT_ID)},
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


@pytest.mark.parametrize(
    ("field", "mutation"),
    (
        (
            "intent",
            lambda case: case.evaluator.intent.model_copy(
                update={"client_order_id": "changed-client-order"}
            ),
        ),
        (
            "policy_decision",
            lambda case: case.evaluator.policy_decision.model_copy(
                update={"policy_version": "changed-target-policy"}
            ),
        ),
        (
            "current_observation",
            lambda case: case.evaluator.observation.model_copy(
                update={"observation_id": uid(820)}
            ),
        ),
        (
            "current_policy",
            lambda case: case.evaluator.policy.model_copy(
                update={"policy_version": "changed-runtime-policy"}
            ),
        ),
        (
            "current_observation",
            lambda case: case.evaluator.observation.model_copy(
                update={
                    "portfolio": case.evaluator.observation.portfolio.model_copy(
                        update={"snapshot_id": uid(821)}
                    )
                }
            ),
        ),
        (
            "current_safety",
            lambda case: safety(state=CanonicalKillSwitchState.ACTIVE),
        ),
    ),
    ids=(
        "intent",
        "target-policy",
        "observation",
        "runtime-policy",
        "portfolio",
        "safety-state",
    ),
)
def test_prepare_submit_permit_rejects_one_changed_current_binding(
    field: str, mutation: Callable[[PrepareCase], object]
) -> None:
    case = approved_active_case()

    with pytest.raises(SubmitPermitPreparationError):
        prepare(case, **{field: mutation(case)})


def test_prepare_submit_permit_changed_safety_source_cannot_reuse_prior_ids() -> None:
    case = approved_active_case()
    prepare(case)

    with pytest.raises(SubmitPermitPreparationError):
        prepare(case, current_safety=safety(source_fingerprint="b" * 64))


def test_prepare_submit_permit_rejects_changed_approval_observation_or_policy() -> None:
    case = approved_active_case()
    mutations = (
        {
            "approval_observation": case.evaluator.observation.model_copy(
                update={"observation_id": uid(822)}
            )
        },
        {
            "approval_policy": case.evaluator.policy.model_copy(
                update={"policy_version": "changed-approval-policy"}
            )
        },
    )

    for mutation in mutations:
        with pytest.raises(SubmitPermitPreparationError):
            prepare(case, **mutation)


def test_prepare_submit_permit_requires_safety_observed_no_later_than_prepare() -> None:
    case = approved_active_case()
    before = safety(observed_at=PREPARED_AT)
    after = safety(observed_at=PREPARED_AT + timedelta(microseconds=1))

    permit = prepare(case, current_safety=before)
    assert global_safety_binding_digest(before) == global_safety_binding_digest(after)
    with pytest.raises(SubmitPermitPreparationError):
        prepare(
            case,
            current_safety=after,
            permit_id=uid(823),
            event_id=uid(824),
        )
    assert permit.safety_binding_digest == global_safety_binding_digest(before)


def test_prepare_submit_permit_rejects_uninitialized_or_halted_stream() -> None:
    active = approved_active_case()
    uninitialized = InMemoryEventLedger()
    uninitialized.append(
        active.approval_event,
        active.ledger.load_outbox()[0],
    )
    with pytest.raises(SubmitPermitPreparationError):
        prepare(active, repository=uninitialized)

    halted = approved_active_case()
    record_global_halt_observation(
        repository=halted.ledger,
        stream_id=HALT_STREAM_ID,
        observation=halted.evaluator.observation,
        policy=halted.evaluator.policy,
        safety=safety(state=CanonicalKillSwitchState.ACTIVE),
        transition_id=uid(825),
        event_id=uid(826),
        decided_at=PREPARED_AT,
    )
    with pytest.raises(SubmitPermitPreparationError):
        prepare(halted)


@pytest.mark.parametrize("recovered", (False, True), ids=("initialization", "recovery"))
@pytest.mark.parametrize(
    ("transition_delta", "accepted"),
    (
        (timedelta(0), True),
        (timedelta(microseconds=1), False),
    ),
    ids=("equal-prepared-at", "one-microsecond-after"),
)
def test_prepare_submit_permit_requires_active_transition_no_later_than_prepared_at(
    recovered: bool,
    transition_delta: timedelta,
    accepted: bool,
) -> None:
    case = active_case_with_transition_at(
        PREPARED_AT + transition_delta,
        recovered=recovered,
    )

    if accepted:
        assert prepare(case).prepared_at == PREPARED_AT
    else:
        with pytest.raises(SubmitPermitPreparationError):
            prepare(case)


def test_prepare_submit_permit_rejects_expired_or_malformed_decision_authority() -> None:
    case = approved_active_case()
    for expired_at in (
        NOW + timedelta(minutes=5),
        NOW + timedelta(minutes=5, microseconds=1),
    ):
        with pytest.raises(SubmitPermitPreparationError):
            prepare(case, prepared_at=expired_at)

    malformed = case.approval_event.model_copy(
        update={"expires_at": case.approval_event.expires_at + timedelta(seconds=1)}
    )
    malformed_ref = matching_reference(malformed)  # type: ignore[arg-type]
    proxy = RepositoryProxy(
        case.ledger,
        load_transform=lambda events: tuple(
            malformed if item.event_id == malformed.event_id else item for item in events
        ),
    )
    with pytest.raises(SubmitPermitPreparationError):
        prepare(case, repository=proxy, approval_reference=malformed_ref)


@pytest.mark.parametrize("approval_fault", ("missing", "duplicate", "conflicting"))
def test_prepare_submit_permit_rejects_missing_duplicated_or_conflicting_approval(
    approval_fault: str,
) -> None:
    case = approved_active_case()

    def transform(
        events: tuple[EventEnvelope[object], ...]
    ) -> tuple[EventEnvelope[object], ...]:
        approval = next(
            item
            for item in events
            if item.event_id == case.approval_reference.event_id
        )
        if approval_fault == "missing":
            return tuple(item for item in events if item.event_id != approval.event_id)
        if approval_fault == "duplicate":
            return (*events, approval)
        return (*events, approval.model_copy(update={"source": "conflicting-source"}))

    with pytest.raises(SubmitPermitPreparationError):
        prepare(case, repository=RepositoryProxy(case.ledger, load_transform=transform))


def test_prepare_submit_permit_rejects_forged_rejected_and_wrong_ledger_approval() -> None:
    case = approved_active_case()
    forged = DurableOrderApprovalRef.model_construct(
        **{
            **case.approval_reference.model_dump(mode="python"),
            "event_digest": "f" * 64,
        }
    )
    with pytest.raises(SubmitPermitPreparationError):
        prepare(case, approval_reference=forged)

    rejected_observation = case.evaluator.observation.model_copy(
        update={"engine_ready": False}
    )
    rejected_case = EvaluatorCase(
        case.evaluator.intent,
        case.evaluator.policy_decision,
        rejected_observation,
        case.evaluator.policy,
    )
    rejected_event = runtime_risk_event(rejected_case, event_id=uid(827), stream_id=uid(828))
    rejected_ledger = InMemoryEventLedger()
    rejected_ledger.append(
        rejected_event,
        OutboxIntent(event_id=rejected_event.event_id, topic="runtime-risk.decisions"),
    )
    record_global_halt_observation(
        repository=rejected_ledger,
        stream_id=HALT_STREAM_ID,
        observation=rejected_observation,
        policy=rejected_case.policy,
        safety=safety(observed_at=NOW),
        transition_id=uid(829),
        event_id=uid(830),
        decided_at=NOW,
    )
    rejected_prepare_case = PrepareCase(
        rejected_ledger,
        rejected_case,
        matching_reference(rejected_event),
        rejected_event,
        safety(observed_at=NOW),
    )
    with pytest.raises(SubmitPermitPreparationError):
        prepare(rejected_prepare_case)

    other = approved_active_case()
    other_approval = runtime_risk_event(
        other.evaluator,
        event_id=uid(840),
        stream_id=uid(841),
    )
    wrong_ledger = InMemoryEventLedger()
    assert record_runtime_risk_decision(
        repository=wrong_ledger, event=other_approval
    ) is not None
    record_global_halt_observation(
        repository=wrong_ledger,
        stream_id=HALT_STREAM_ID,
        observation=other.evaluator.observation,
        policy=other.evaluator.policy,
        safety=safety(observed_at=NOW),
        transition_id=uid(842),
        event_id=uid(843),
        decided_at=NOW,
    )
    with pytest.raises(SubmitPermitPreparationError):
        prepare(case, repository=wrong_ledger)


@pytest.mark.parametrize(
    "failure",
    (
        RepositoryProxy,
    ),
)
def test_prepare_submit_permit_bounds_append_outbox_and_receipt_failures(
    failure: type[RepositoryProxy],
) -> None:
    case = approved_active_case()
    repositories = (
        failure(case.ledger, append_error=RuntimeError("private append detail")),
        failure(case.ledger, append_error=EventConflictError("private outbox detail")),
        failure(case.ledger, wrong_receipt=True),
    )
    for repository in repositories:
        with pytest.raises(SubmitPermitPreparationError) as caught:
            prepare(case, repository=repository)
        assert "private" not in str(caught.value)


@pytest.mark.parametrize("read_back_fault", ("missing", "duplicate", "wrong-bytes"))
def test_prepare_submit_permit_rejects_non_exact_prepared_read_back(
    read_back_fault: str,
) -> None:
    case = approved_active_case()

    def transform(
        events: tuple[EventEnvelope[object], ...]
    ) -> tuple[EventEnvelope[object], ...]:
        prepared = tuple(item for item in events if item.event_id == PREPARED_EVENT_ID)
        if not prepared:
            return events
        selected = prepared[0]
        without = tuple(item for item in events if item.event_id != PREPARED_EVENT_ID)
        if read_back_fault == "missing":
            return without
        if read_back_fault == "duplicate":
            return (*events, selected)
        return (*without, selected.model_copy(update={"source": "wrong-read-back"}))

    with pytest.raises(SubmitPermitPreparationError):
        prepare(case, repository=RepositoryProxy(case.ledger, load_transform=transform))


def test_prepare_submit_permit_exact_retry_is_idempotent_but_conflicts_reject() -> None:
    case = approved_active_case()
    first = prepare(case)
    first_event = prepared_envelope(case.ledger)

    assert prepare(case) == first
    assert prepared_envelope(case.ledger) == first_event
    with pytest.raises(SubmitPermitPreparationError):
        prepare(case, prepared_at=PREPARED_AT + timedelta(microseconds=1))
    with pytest.raises(SubmitPermitPreparationError):
        prepare(case, event_id=uid(831))


def test_prepare_submit_permit_changed_halt_generation_cannot_reuse_prior_ids() -> None:
    case = approved_active_case()
    prepare(case)
    record_global_halt_observation(
        repository=case.ledger,
        stream_id=HALT_STREAM_ID,
        observation=case.evaluator.observation,
        policy=case.evaluator.policy,
        safety=safety(state=CanonicalKillSwitchState.ACTIVE),
        transition_id=uid(832),
        event_id=uid(833),
        decided_at=PREPARED_AT + timedelta(seconds=1),
    )

    with pytest.raises(SubmitPermitPreparationError):
        prepare(
            case,
            prepared_at=PREPARED_AT + timedelta(seconds=1),
            current_safety=safety(observed_at=PREPARED_AT + timedelta(seconds=1)),
        )


def test_prepare_submit_permit_rejects_consumed_permit_id_without_mutation() -> None:
    case = approved_active_case()
    prepared = prepare(case)
    consumed_at = PREPARED_AT + timedelta(seconds=1)
    consumed_payload = SubmitPermitConsumed(
        permit_id=prepared.permit_id,
        prepared_event_digest=prepared.prepared_event_digest,
        halt_stream_id=prepared.halt_stream_id,
        halt_generation=prepared.halt_generation,
        halt_transition_digest=prepared.halt_transition_digest,
        consumed_at=consumed_at,
        schema_version="submit-permit-consumed-v1",
    )
    consumed = EventEnvelope[SubmitPermitConsumed](
        event_id=uid(860),
        event_type="SubmitPermitConsumed",
        schema_version="submit-permit-consumed-event-v1",
        source="runtime-risk",
        stream_id=HALT_STREAM_ID,
        sequence=3,
        observed_at=consumed_at,
        ingested_at=consumed_at,
        produced_at=consumed_at,
        effective_at=consumed_at,
        expires_at=consumed_at + timedelta(minutes=5),
        correlation_id=PERMIT_ID,
        causation_id=PERMIT_ID,
        trace_id=PERMIT_ID,
        payload=consumed_payload,
    )
    assert type(consumed.payload) is SubmitPermitConsumed
    case.ledger.append(
        consumed,
        OutboxIntent(event_id=consumed.event_id, topic="submit-permit-consumed.audit"),
    )
    prior_events = case.ledger.load_events()
    prior_outbox = case.ledger.load_outbox()
    prior_replay = replay_global_halt_authority(
        events=prior_events,
        stream_id=HALT_STREAM_ID,
    )
    assert prior_replay.consumed_permit_ids == (PERMIT_ID,)

    with pytest.raises(SubmitPermitPreparationError):
        prepare(
            case,
            event_id=uid(861),
            prepared_at=PREPARED_AT + timedelta(seconds=2),
            current_safety=safety(
                observed_at=PREPARED_AT + timedelta(seconds=2)
            ),
        )

    assert case.ledger.load_events() == prior_events
    assert case.ledger.load_outbox() == prior_outbox
    assert replay_global_halt_authority(
        events=case.ledger.load_events(),
        stream_id=HALT_STREAM_ID,
    ) == prior_replay


def test_prepare_submit_permit_does_not_mask_reference_programming_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = approved_active_case()

    def fail_reference(*args: object, **kwargs: object) -> PreparedSubmitPermit:
        raise RuntimeError("reference programming defect")

    monkeypatch.setattr(submit_authority_module, "_prepared_reference", fail_reference)
    with pytest.raises(RuntimeError, match="reference programming defect"):
        prepare(case)
