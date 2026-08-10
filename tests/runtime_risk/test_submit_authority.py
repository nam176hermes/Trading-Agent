from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Callable
from uuid import UUID

import pytest

import packages.runtime_risk.submit_authority as submit_authority_module
from packages.domain import EventEnvelope, Money
from packages.domain.runtime_halt import (
    ConsumedSubmitAuthority,
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
    FilesystemGlobalSafetyAuthority,
    SubmitPermitConsumptionError,
    SubmitPermitPreparationError,
    canonical_model_digest,
    consume_submit_permit,
    global_safety_binding_digest,
    observe_global_safety,
    prepare_submit_permit,
    record_global_halt_observation,
    record_runtime_risk_decision,
    recover_global_halt,
    replay_global_halt_authority,
)
from packages.safety_evidence import CanonicalKillSwitchState, safety_source_fingerprint

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


class ExactSafetyVerifier:
    def verify(self, *, observation: GlobalSafetyObservation) -> GlobalSafetyObservation:
        return observation


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
        safety_verifier=ExactSafetyVerifier(),
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
        safety_verifier=ExactSafetyVerifier(),
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
            safety_verifier=ExactSafetyVerifier(),
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


def approved_case_with_historical_active_facts(*, recovered: bool) -> PrepareCase:
    ledger = InMemoryEventLedger()
    current = evaluator_case()
    approval_event = runtime_risk_event(
        current,
        event_id=uid(865),
        stream_id=uid(866),
    )
    approval_reference = record_runtime_risk_decision(
        repository=ledger,
        event=approval_event,
    )
    assert approval_reference is not None
    historical_portfolio = current.observation.portfolio.model_copy(
        update={"snapshot_id": uid(867)}
    )
    historical_observation = current.observation.model_copy(
        update={
            "observation_id": uid(868),
            "state_version": current.observation.state_version + 1,
            "portfolio": historical_portfolio,
            "daily_pnl": Money(
                Decimal("1"),
                current.observation.daily_pnl.currency,
            ),
        }
    )
    historical_policy = current.policy.model_copy(
        update={
            "policy_id": uid(869),
            "policy_version": "historical-policy",
        }
    )
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
        observation=historical_observation,
        policy=historical_policy,
        safety=initial_safety,
        safety_verifier=ExactSafetyVerifier(),
        transition_id=uid(870),
        event_id=uid(871),
        decided_at=NOW,
    )
    if recovered:
        safe = safety(observed_at=NOW)
        authorization = GlobalHaltRecoveryAuthorization(
            authorization_id=uid(872),
            authorization_digest="b" * 64,
            halted_generation=state.generation,
            halted_transition_digest=state.transition_digest,
            runtime_policy_digest=canonical_model_digest(historical_policy),
            runtime_observation_digest=canonical_model_digest(historical_observation),
            portfolio_digest=canonical_model_digest(historical_portfolio),
            safety_binding_digest=global_safety_binding_digest(safe),
            issued_at=NOW,
            expires_at=NOW + timedelta(minutes=1),
            operator_authority_digest="c" * 64,
            schema_version="global-halt-recovery-authorization-v1",
        )
        state = recover_global_halt(
            repository=ledger,
            stream_id=HALT_STREAM_ID,
            observation=historical_observation,
            policy=historical_policy,
            safety=safe,
            safety_verifier=ExactSafetyVerifier(),
            authorization=authorization,
            verifier=ExactRecoveryVerifier(),
            transition_id=uid(873),
            event_id=uid(874),
            decided_at=NOW,
        )
    assert state.status is GlobalHaltStatus.ACTIVE
    return PrepareCase(
        ledger=ledger,
        evaluator=current,
        approval_reference=approval_reference,
        approval_event=approval_event,
        initial_safety=initial_safety,
    )


@pytest.mark.parametrize("recovered", (False, True), ids=("initialized", "recovered"))
def test_prepare_and_consume_allow_current_facts_to_differ_from_transition_history(
    recovered: bool,
) -> None:
    case = approved_case_with_historical_active_facts(recovered=recovered)
    historical = replay_global_halt_authority(
        events=case.ledger.load_events(),
        stream_id=HALT_STREAM_ID,
    ).state
    assert historical is not None
    permit = prepare(case)
    assert historical.runtime_policy_digest != permit.runtime_policy_digest
    assert historical.runtime_observation_digest != permit.runtime_observation_digest
    assert historical.portfolio_digest != permit.portfolio_digest

    authority = consume(case, permit)
    assert authority.permit_id == permit.permit_id


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
        "current_safety": safety(observed_at=NOW),
        "safety_verifier": ExactSafetyVerifier(),
        "permit_id": PERMIT_ID,
        "event_id": PREPARED_EVENT_ID,
        "prepared_at": PREPARED_AT,
    }
    arguments.update(changes)
    return prepare_submit_permit(**arguments)  # type: ignore[arg-type]


def _activate_private_safety_root(root: Path) -> None:
    root.mkdir(exist_ok=True)
    sentinel = root / ".kill_switch"
    sentinel.write_text(
        "2026-08-10T12:00:00Z: final-wave safety test\n",
        encoding="utf-8",
    )
    sentinel.chmod(0o600)


@pytest.mark.parametrize("evidence", ("alternate-root", "fabricated"))
def test_prepare_submit_permit_rejects_untrusted_inactive_safety(
    tmp_path: Path,
    evidence: str,
) -> None:
    case = approved_active_case()
    canonical_root = tmp_path / "canonical"
    alternate_root = tmp_path / "alternate"
    _activate_private_safety_root(canonical_root)
    alternate_root.mkdir()
    if evidence == "alternate-root":
        supplied = observe_global_safety(
            source_root=alternate_root,
            observed_at=NOW,
        )
    else:
        supplied = GlobalSafetyObservation(
            source_fingerprint=safety_source_fingerprint(canonical_root),
            kill_switch_state=CanonicalKillSwitchState.INACTIVE,
            observed_at=NOW,
            schema_version="global-safety-observation-v1",
        )

    with pytest.raises(SubmitPermitPreparationError):
        prepare(
            case,
            current_safety=supplied,
            safety_verifier=FilesystemGlobalSafetyAuthority(canonical_root),
        )


@pytest.mark.parametrize("evidence", ("alternate-root", "fabricated"))
def test_consume_submit_permit_rejects_safety_that_rotated_after_prepare(
    tmp_path: Path,
    evidence: str,
) -> None:
    case = approved_active_case()
    canonical_root = tmp_path / "canonical"
    alternate_root = tmp_path / "alternate"
    canonical_root.mkdir()
    alternate_root.mkdir()
    preparation_root = alternate_root if evidence == "alternate-root" else canonical_root
    preparation_read = observe_global_safety(
        source_root=preparation_root,
        observed_at=NOW,
    )
    permit = prepare(
        case,
        current_safety=preparation_read,
        safety_verifier=FilesystemGlobalSafetyAuthority(preparation_root),
    )
    _activate_private_safety_root(canonical_root)
    consume_read = GlobalSafetyObservation(
        source_fingerprint=preparation_read.source_fingerprint,
        kill_switch_state=CanonicalKillSwitchState.INACTIVE,
        observed_at=PREPARED_AT + timedelta(seconds=1),
        schema_version="global-safety-observation-v1",
    )

    with pytest.raises(SubmitPermitConsumptionError):
        consume(
            case,
            permit,
            current_safety=consume_read,
            safety_verifier=FilesystemGlobalSafetyAuthority(canonical_root),
        )


def prepared_envelope(ledger: InMemoryEventLedger) -> EventEnvelope[object]:
    return next(
        event
        for event in ledger.load_events()
        if event.event_id == PREPARED_EVENT_ID
    )


def consume(
    case: PrepareCase,
    permit: PreparedSubmitPermit,
    *,
    repository: EventLedgerRepository | None = None,
    **changes: object,
) -> ConsumedSubmitAuthority:
    arguments: dict[str, object] = {
        "repository": repository or case.ledger,
        "permit": permit,
        "current_observation": case.evaluator.observation,
        "current_policy": case.evaluator.policy,
        "current_safety": safety(observed_at=PREPARED_AT + timedelta(seconds=1)),
        "safety_verifier": ExactSafetyVerifier(),
        "consumed_event_id": uid(900),
        "consumed_at": PREPARED_AT + timedelta(seconds=1),
    }
    arguments.update(changes)
    return consume_submit_permit(**arguments)  # type: ignore[arg-type]


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


def test_prepare_submit_permit_requires_safety_observed_strictly_before_prepare() -> None:
    case = approved_active_case()
    before = safety(observed_at=PREPARED_AT - timedelta(microseconds=1))
    equal = safety(observed_at=PREPARED_AT)
    after = safety(observed_at=PREPARED_AT + timedelta(microseconds=1))

    permit = prepare(case, current_safety=before)
    assert global_safety_binding_digest(before) == global_safety_binding_digest(equal)
    assert global_safety_binding_digest(before) == global_safety_binding_digest(after)
    for index, invalid in enumerate((equal, after), start=823):
        with pytest.raises(SubmitPermitPreparationError):
            prepare(
                case,
                current_safety=invalid,
                permit_id=uid(index),
                event_id=uid(index + 10),
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
        safety_verifier=ExactSafetyVerifier(),
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
        safety_verifier=ExactSafetyVerifier(),
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
        safety_verifier=ExactSafetyVerifier(),
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


@pytest.mark.parametrize(
    ("delta", "accepted"),
    (
        (timedelta(0), True),
        (timedelta(microseconds=-1), False),
    ),
    ids=("equal-stream-head", "one-microsecond-before-stream-head"),
)
def test_prepare_submit_permit_requires_nondecreasing_stream_time(
    delta: timedelta,
    accepted: bool,
) -> None:
    case = approved_active_case()
    prepare(case)
    before = case.ledger.load_events()
    arguments = {
        "permit_id": uid(875),
        "event_id": uid(876),
        "prepared_at": PREPARED_AT + delta,
        "current_safety": safety(observed_at=NOW),
    }
    if accepted:
        assert prepare(case, **arguments).prepared_at == PREPARED_AT
    else:
        with pytest.raises(SubmitPermitPreparationError):
            prepare(case, **arguments)
        assert case.ledger.load_events() == before


def test_consume_submit_permit_rejects_time_behind_intervening_permit() -> None:
    case = approved_active_case()
    permit = prepare(case)
    later_at = PREPARED_AT + timedelta(seconds=2)
    unrelated_payload = SubmitPermitPrepared(
        **{
            **{
                name: getattr(permit, name)
                for name in SubmitPermitPrepared.model_fields
            },
            "permit_id": uid(877),
            "prepared_at": later_at,
            "expires_at": later_at + timedelta(seconds=5),
            "schema_version": "submit-permit-prepared-v1",
        }
    )
    unrelated = EventEnvelope[SubmitPermitPrepared](
        event_id=uid(878),
        event_type="SubmitPermitPrepared",
        schema_version="submit-permit-prepared-event-v1",
        source="runtime-risk",
        stream_id=HALT_STREAM_ID,
        sequence=3,
        observed_at=later_at,
        ingested_at=later_at,
        produced_at=later_at,
        effective_at=later_at,
        expires_at=later_at + timedelta(seconds=5),
        correlation_id=unrelated_payload.permit_id,
        causation_id=unrelated_payload.approval_event_id,
        trace_id=unrelated_payload.permit_id,
        payload=unrelated_payload,
    )
    case.ledger.append(
        unrelated,
        OutboxIntent(event_id=unrelated.event_id, topic="test.unrelated-permit"),
    )
    before = case.ledger.load_events()

    with pytest.raises(SubmitPermitConsumptionError):
        consume(case, permit)
    assert case.ledger.load_events() == before


def test_prepare_submit_permit_changed_halt_generation_cannot_reuse_prior_ids() -> None:
    case = approved_active_case()
    prepare(case)
    record_global_halt_observation(
        repository=case.ledger,
        stream_id=HALT_STREAM_ID,
        observation=case.evaluator.observation,
        policy=case.evaluator.policy,
        safety=safety(state=CanonicalKillSwitchState.ACTIVE),
        safety_verifier=ExactSafetyVerifier(),
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


def test_consume_submit_permit_persists_exact_flat_content_addressed_authority() -> None:
    case = approved_active_case()
    permit = prepare(case)

    authority = consume(case, permit)

    event = next(
        item for item in case.ledger.load_events() if item.event_id == uid(900)
    )
    assert authority == ConsumedSubmitAuthority(
        permit_id=permit.permit_id,
        prepared_event_digest=permit.prepared_event_digest,
        halt_stream_id=permit.halt_stream_id,
        halt_generation=permit.halt_generation,
        halt_transition_digest=permit.halt_transition_digest,
        consumed_at=PREPARED_AT + timedelta(seconds=1),
        consumed_event_id=uid(900),
        consumed_event_digest=event_digest(serialize_event(event)),
        schema_version="consumed-submit-authority-v1",
    )
    assert type(event.payload) is SubmitPermitConsumed
    assert "consumed_event_id" not in type(event.payload).model_fields
    assert "consumed_event_digest" not in type(event.payload).model_fields
    assert event.sequence == 3
    assert event.causation_id == permit.permit_id
    assert next(
        item for item in case.ledger.load_outbox() if item.event_id == uid(900)
    ) == OutboxIntent(
        event_id=uid(900),
        topic="runtime-risk.submit-permits-consumed",
        payload_json=json.dumps(
            {"permit_id": str(permit.permit_id)},
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


@pytest.mark.parametrize(
    ("offset", "accepted"),
    (
        (timedelta(0), True),
        (timedelta(seconds=5), True),
        (timedelta(seconds=5, microseconds=1), False),
    ),
    ids=("prepared-at", "expires-at", "one-microsecond-after"),
)
def test_consume_submit_permit_enforces_inclusive_five_second_window(
    offset: timedelta,
    accepted: bool,
) -> None:
    case = approved_active_case()
    permit = prepare(case)
    consumed_at = PREPARED_AT + offset

    if accepted:
        authority = consume(
            case,
            permit,
            current_safety=safety(observed_at=consumed_at),
            consumed_at=consumed_at,
        )
        assert authority.consumed_at == consumed_at
    else:
        with pytest.raises(SubmitPermitConsumptionError):
            consume(
                case,
                permit,
                current_safety=safety(observed_at=permit.expires_at),
                consumed_at=consumed_at,
            )


def test_consume_submit_permit_requires_new_bounded_same_binding_safety_read() -> None:
    case = approved_active_case()
    preparation_read = safety(
        observed_at=PREPARED_AT - timedelta(microseconds=1)
    )
    permit = prepare(case, current_safety=preparation_read)

    for index, consumed_at in enumerate(
        (PREPARED_AT, PREPARED_AT + timedelta(seconds=1)),
        start=906,
    ):
        with pytest.raises(SubmitPermitConsumptionError):
            consume(
                case,
                permit,
                current_safety=preparation_read,
                consumed_event_id=uid(index),
                consumed_at=consumed_at,
            )
    assert consume(
        case,
        permit,
        current_safety=safety(observed_at=PREPARED_AT),
        consumed_event_id=uid(908),
        consumed_at=PREPARED_AT,
    ).permit_id == permit.permit_id


@pytest.mark.parametrize(
    "current_safety",
    (
        safety(
            observed_at=PREPARED_AT + timedelta(seconds=1, microseconds=1)
        ),
        safety(
            source_fingerprint="b" * 64,
            observed_at=PREPARED_AT + timedelta(seconds=1),
        ),
        safety(
            state=CanonicalKillSwitchState.ACTIVE,
            observed_at=PREPARED_AT + timedelta(seconds=1),
        ),
    ),
    ids=("after-consume", "changed-source", "changed-state"),
)
def test_consume_submit_permit_rejects_invalid_current_safety(
    current_safety: GlobalSafetyObservation,
) -> None:
    case = approved_active_case()
    permit = prepare(case)

    with pytest.raises(SubmitPermitConsumptionError):
        consume(case, permit, current_safety=current_safety)


@pytest.mark.parametrize(
    ("field", "mutation"),
    (
        (
            "current_observation",
            lambda case: case.evaluator.observation.model_copy(
                update={"observation_id": uid(901)}
            ),
        ),
        (
            "current_policy",
            lambda case: case.evaluator.policy.model_copy(
                update={"policy_version": "changed-at-consume"}
            ),
        ),
        (
            "current_observation",
            lambda case: case.evaluator.observation.model_copy(
                update={
                    "portfolio": case.evaluator.observation.portfolio.model_copy(
                        update={"snapshot_id": uid(902)}
                    )
                }
            ),
        ),
    ),
    ids=("observation", "policy", "portfolio"),
)
def test_consume_submit_permit_rejects_changed_current_authority(
    field: str,
    mutation: Callable[[PrepareCase], object],
) -> None:
    case = approved_active_case()
    permit = prepare(case)

    with pytest.raises(SubmitPermitConsumptionError):
        consume(case, permit, **{field: mutation(case)})


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("halt_stream_id", uid(903)),
        ("halt_generation", 99),
        ("halt_transition_event_id", uid(904)),
        ("halt_transition_digest", "f" * 64),
    ),
    ids=("stream", "generation", "transition-id", "transition-digest"),
)
def test_consume_submit_permit_rejects_wrong_halt_binding(
    field: str,
    value: object,
) -> None:
    case = approved_active_case()
    permit = prepare(case)
    forged = PreparedSubmitPermit.model_construct(
        **{**permit.model_dump(mode="python"), field: value}
    )

    with pytest.raises(SubmitPermitConsumptionError):
        consume(case, forged)


def test_consume_submit_permit_is_strictly_one_shot() -> None:
    case = approved_active_case()
    permit = prepare(case)
    consume(case, permit)
    prior_events = case.ledger.load_events()
    prior_outbox = case.ledger.load_outbox()

    with pytest.raises(SubmitPermitConsumptionError):
        consume(case, permit, consumed_event_id=uid(905))

    assert case.ledger.load_events() == prior_events
    assert case.ledger.load_outbox() == prior_outbox
