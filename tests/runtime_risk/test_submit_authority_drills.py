from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable
from uuid import UUID

import pytest
from pydantic import BaseModel

from packages.domain import EventEnvelope
from packages.domain.runtime_halt import (
    GlobalHaltStatus,
    PreparedSubmitPermit,
    SubmitPermitConsumed,
    SubmitPermitPrepared,
)
from packages.event_ledger import (
    AppendOutcome,
    EventConflictError,
    InMemoryEventLedger,
    OutboxIntent,
)
from packages.runtime_risk import (
    GlobalHaltAuthorityError,
    SubmitPermitConsumptionError,
    audit_submit_authority_stream,
    consume_submit_permit,
    record_global_halt_observation,
)
from packages.safety_evidence import CanonicalKillSwitchState

from tests.runtime_risk.test_evaluator import uid
from tests.runtime_risk.test_submit_authority import (
    HALT_STREAM_ID,
    PREPARED_AT,
    PrepareCase,
    approved_active_case,
    prepare,
    safety,
)


CONSUMED_AT = PREPARED_AT + timedelta(seconds=1)


def consumed_event(
    permit: PreparedSubmitPermit,
    *,
    event_id: UUID,
    sequence: int,
    consumed_at: datetime = CONSUMED_AT,
) -> EventEnvelope[SubmitPermitConsumed]:
    payload = SubmitPermitConsumed(
        permit_id=permit.permit_id,
        prepared_event_digest=permit.prepared_event_digest,
        halt_stream_id=permit.halt_stream_id,
        halt_generation=permit.halt_generation,
        halt_transition_digest=permit.halt_transition_digest,
        consumed_at=consumed_at,
        schema_version="submit-permit-consumed-v1",
    )
    return EventEnvelope[SubmitPermitConsumed](
        event_id=event_id,
        event_type="SubmitPermitConsumed",
        schema_version="submit-permit-consumed-event-v1",
        source="runtime-risk",
        stream_id=permit.halt_stream_id,
        sequence=sequence,
        observed_at=consumed_at,
        ingested_at=consumed_at,
        produced_at=consumed_at,
        effective_at=consumed_at,
        expires_at=consumed_at + timedelta(minutes=5),
        correlation_id=permit.permit_id,
        causation_id=permit.permit_id,
        trace_id=permit.permit_id,
        payload=payload,
    )


def unrelated_prepared_event(
    permit: PreparedSubmitPermit,
    *,
    permit_id: UUID,
    event_id: UUID,
    sequence: int,
) -> EventEnvelope[SubmitPermitPrepared]:
    payload = SubmitPermitPrepared(
        **{
            **{
                name: getattr(permit, name)
                for name in SubmitPermitPrepared.model_fields
            },
            "permit_id": permit_id,
            "schema_version": "submit-permit-prepared-v1",
        }
    )
    return EventEnvelope[SubmitPermitPrepared](
        event_id=event_id,
        event_type="SubmitPermitPrepared",
        schema_version="submit-permit-prepared-event-v1",
        source="runtime-risk",
        stream_id=permit.halt_stream_id,
        sequence=sequence,
        observed_at=payload.prepared_at,
        ingested_at=payload.prepared_at,
        produced_at=payload.prepared_at,
        effective_at=payload.prepared_at,
        expires_at=payload.expires_at,
        correlation_id=permit_id,
        causation_id=payload.approval_event_id,
        trace_id=permit_id,
        payload=payload,
    )


class RaceRepository:
    def __init__(
        self,
        ledger: InMemoryEventLedger,
        inject: Callable[[InMemoryEventLedger], None],
        *,
        second_conflict: bool = False,
    ) -> None:
        self.ledger = ledger
        self.inject = inject
        self.second_conflict = second_conflict
        self.consume_append_attempts = 0
        self._injected = False

    def append(
        self, event: EventEnvelope[object], outbox: OutboxIntent
    ) -> AppendOutcome:
        if event.event_type != "SubmitPermitConsumed":
            return self.ledger.append(event, outbox)
        self.consume_append_attempts += 1
        if not self._injected:
            self._injected = True
            self.inject(self.ledger)
        if self.consume_append_attempts == 1 or self.second_conflict:
            raise EventConflictError("private sequence conflict")
        return self.ledger.append(event, outbox)

    def load_events(self) -> tuple[EventEnvelope[object], ...]:
        return self.ledger.load_events()

    def claim_inbox(self, consumer: str, event_id: UUID) -> bool:
        return self.ledger.claim_inbox(consumer, event_id)

    def acknowledge_outbox(self, event_id: UUID) -> bool:
        return self.ledger.acknowledge_outbox(event_id)

    def save_snapshot(self, snapshot: object) -> None:
        raise NotImplementedError

    def load_snapshot(self, state_hash: str) -> None:
        raise NotImplementedError


def _append(ledger: InMemoryEventLedger, event: EventEnvelope[object]) -> None:
    ledger.append(
        event,
        OutboxIntent(event_id=event.event_id, topic="test.reviewed-authority"),
    )


def prepared_authority_fixture(
    intervening: str,
) -> tuple[RaceRepository, PreparedSubmitPermit, PrepareCase]:
    case = approved_active_case()
    permit = prepare(case)
    unrelated_id = uid(930)
    unrelated_event_id = uid(931)
    unrelated = unrelated_prepared_event(
        permit,
        permit_id=unrelated_id,
        event_id=unrelated_event_id,
        sequence=3,
    )
    if intervening == "unrelated-permit-consumed":
        _append(case.ledger, unrelated)

    def inject(ledger: InMemoryEventLedger) -> None:
        next_sequence = max(
            event.sequence
            for event in ledger.load_events()
            if event.stream_id == HALT_STREAM_ID
        ) + 1
        if intervening == "halt-transition":
            record_global_halt_observation(
                repository=ledger,
                stream_id=HALT_STREAM_ID,
                observation=case.evaluator.observation,
                policy=case.evaluator.policy,
                safety=safety(
                    state=CanonicalKillSwitchState.ACTIVE,
                    observed_at=CONSUMED_AT,
                ),
                transition_id=uid(932),
                event_id=uid(933),
                decided_at=CONSUMED_AT,
            )
        elif intervening == "recovery-transition":
            halted = record_global_halt_observation(
                repository=ledger,
                stream_id=HALT_STREAM_ID,
                observation=case.evaluator.observation,
                policy=case.evaluator.policy,
                safety=safety(
                    state=CanonicalKillSwitchState.ACTIVE,
                    observed_at=CONSUMED_AT,
                ),
                transition_id=uid(934),
                event_id=uid(935),
                decided_at=CONSUMED_AT,
            )
            assert halted.status is GlobalHaltStatus.HALTED
        elif intervening == "same-permit-consumed":
            _append(
                ledger,
                consumed_event(
                    permit,
                    event_id=uid(936),
                    sequence=next_sequence,
                ),
            )
        elif intervening == "unrelated-permit-prepared":
            _append(ledger, unrelated.model_copy(update={"sequence": next_sequence}))
        else:
            unrelated_permit = next(
                item
                for item in audit_submit_authority_stream(
                    repository=ledger,
                    stream_id=HALT_STREAM_ID,
                ).prepared
                if item.permit_id == unrelated_id
            )
            _append(
                ledger,
                consumed_event(
                    unrelated_permit,
                    event_id=uid(937),
                    sequence=next_sequence,
                ),
            )

    return RaceRepository(case.ledger, inject), permit, case


@pytest.mark.parametrize(
    "intervening",
    [
        "halt-transition",
        "recovery-transition",
        "same-permit-consumed",
        "unrelated-permit-prepared",
        "unrelated-permit-consumed",
    ],
)
def test_consume_classifies_intervening_authority(intervening: str) -> None:
    repository, permit, current = prepared_authority_fixture(intervening)
    if intervening in {"unrelated-permit-prepared", "unrelated-permit-consumed"}:
        authority = consume_submit_permit(
            repository=repository,
            permit=permit,
            current_observation=current.evaluator.observation,
            current_policy=current.evaluator.policy,
            current_safety=safety(observed_at=CONSUMED_AT),
            consumed_event_id=UUID(int=900),
            consumed_at=CONSUMED_AT,
        )
        assert authority.permit_id == permit.permit_id
        assert repository.consume_append_attempts == 2
    else:
        with pytest.raises(SubmitPermitConsumptionError):
            consume_submit_permit(
                repository=repository,
                permit=permit,
                current_observation=current.evaluator.observation,
                current_policy=current.evaluator.policy,
                current_safety=safety(observed_at=CONSUMED_AT),
                consumed_event_id=UUID(int=900),
                consumed_at=CONSUMED_AT,
            )


def test_consume_bounds_second_sequence_conflict_without_partial_authority() -> None:
    repository, permit, current = prepared_authority_fixture(
        "unrelated-permit-prepared"
    )
    repository.second_conflict = True

    with pytest.raises(SubmitPermitConsumptionError):
        consume_submit_permit(
            repository=repository,
            permit=permit,
            current_observation=current.evaluator.observation,
            current_policy=current.evaluator.policy,
            current_safety=safety(observed_at=CONSUMED_AT),
            consumed_event_id=uid(938),
            consumed_at=CONSUMED_AT,
        )

    assert repository.consume_append_attempts == 2
    assert uid(938) not in {item.event_id for item in repository.load_events()}


class ConcurrentDuplicateRepository:
    def __init__(self, ledger: InMemoryEventLedger) -> None:
        self.ledger = ledger

    def append(
        self, event: EventEnvelope[object], outbox: OutboxIntent
    ) -> AppendOutcome:
        self.ledger.append(event, outbox)
        return self.ledger.append(event, outbox)

    def load_events(self) -> tuple[EventEnvelope[object], ...]:
        return self.ledger.load_events()


def test_consume_rejects_concurrent_exact_duplicate_instead_of_sharing_authority() -> None:
    case = approved_active_case()
    permit = prepare(case)

    with pytest.raises(SubmitPermitConsumptionError):
        consume_submit_permit(
            repository=ConcurrentDuplicateRepository(case.ledger),  # type: ignore[arg-type]
            permit=permit,
            current_observation=case.evaluator.observation,
            current_policy=case.evaluator.policy,
            current_safety=safety(observed_at=CONSUMED_AT),
            consumed_event_id=uid(943),
            consumed_at=CONSUMED_AT,
        )


class LookalikeAppendOutcome(BaseModel):
    event_id: UUID
    inserted: bool


class WrongTypeConcurrentDuplicateRepository:
    def __init__(self, ledger: InMemoryEventLedger) -> None:
        self.ledger = ledger
        self.actual_duplicate: AppendOutcome | None = None

    def append(
        self, event: EventEnvelope[object], outbox: OutboxIntent
    ) -> LookalikeAppendOutcome:
        self.ledger.append(event, outbox)
        self.actual_duplicate = self.ledger.append(event, outbox)
        return LookalikeAppendOutcome(event_id=event.event_id, inserted=True)

    def load_events(self) -> tuple[EventEnvelope[object], ...]:
        return self.ledger.load_events()


def test_consume_rejects_wrong_type_receipt_for_real_concurrent_duplicate() -> None:
    case = approved_active_case()
    permit = prepare(case)
    repository = WrongTypeConcurrentDuplicateRepository(case.ledger)

    with pytest.raises(SubmitPermitConsumptionError):
        consume_submit_permit(
            repository=repository,  # type: ignore[arg-type]
            permit=permit,
            current_observation=case.evaluator.observation,
            current_policy=case.evaluator.policy,
            current_safety=safety(observed_at=CONSUMED_AT),
            consumed_event_id=uid(944),
            consumed_at=CONSUMED_AT,
        )

    assert repository.actual_duplicate == AppendOutcome(
        event_id=uid(944),
        inserted=False,
    )
    replay = audit_submit_authority_stream(
        repository=case.ledger,
        stream_id=HALT_STREAM_ID,
    )
    assert replay.consumed_permit_ids == (permit.permit_id,)
    assert replay.prepared == ()


class FaultRepository:
    def __init__(
        self,
        ledger: InMemoryEventLedger,
        *,
        load_transform: Callable[[tuple[EventEnvelope[object], ...]], object]
        | None = None,
        append_error: Exception | None = None,
        wrong_receipt: bool = False,
        read_back_fault: str | None = None,
    ) -> None:
        self.ledger = ledger
        self.load_transform = load_transform
        self.append_error = append_error
        self.wrong_receipt = wrong_receipt
        self.read_back_fault = read_back_fault
        self.append_calls = 0
        self.load_calls = 0

    def append(
        self, event: EventEnvelope[object], outbox: OutboxIntent
    ) -> AppendOutcome:
        self.append_calls += 1
        if self.append_error is not None:
            raise self.append_error
        outcome = self.ledger.append(event, outbox)
        if self.wrong_receipt:
            return AppendOutcome(event_id=uid(939), inserted=True)
        return outcome

    def load_events(self) -> tuple[EventEnvelope[object], ...]:
        self.load_calls += 1
        events: object = self.ledger.load_events()
        if self.read_back_fault is not None and self.append_calls:
            target = uid(940)
            if self.read_back_fault == "missing":
                events = tuple(item for item in events if item.event_id != target)
            elif self.read_back_fault == "duplicate":
                match = next(item for item in events if item.event_id == target)
                events = (*events, match)
            else:
                events = tuple(
                    item.model_copy(update={"source": "forged"})
                    if item.event_id == target
                    else item
                    for item in events
                )
        return events if self.load_transform is None else self.load_transform(events)  # type: ignore[arg-type,return-value]


@pytest.mark.parametrize(
    "repository",
    (
        lambda ledger: FaultRepository(
            ledger, append_error=OSError("private persistence detail")
        ),
        lambda ledger: FaultRepository(ledger, wrong_receipt=True),
    ),
    ids=("append-error", "wrong-receipt"),
)
def test_consume_persistence_failure_returns_no_partial_authority(
    repository: Callable[[InMemoryEventLedger], FaultRepository],
) -> None:
    case = approved_active_case()
    permit = prepare(case)
    fault = repository(case.ledger)
    prior = case.ledger.load_events()

    with pytest.raises(SubmitPermitConsumptionError) as caught:
        consume_submit_permit(
            repository=fault,
            permit=permit,
            current_observation=case.evaluator.observation,
            current_policy=case.evaluator.policy,
            current_safety=safety(observed_at=CONSUMED_AT),
            consumed_event_id=uid(940),
            consumed_at=CONSUMED_AT,
        )

    assert "private" not in str(caught.value)
    if fault.append_error is not None:
        assert case.ledger.load_events() == prior


@pytest.mark.parametrize("fault", ("missing", "duplicate", "forged"))
def test_consume_requires_exact_consumed_event_read_back(fault: str) -> None:
    case = approved_active_case()
    permit = prepare(case)
    repository = FaultRepository(case.ledger, read_back_fault=fault)

    with pytest.raises(SubmitPermitConsumptionError):
        consume_submit_permit(
            repository=repository,
            permit=permit,
            current_observation=case.evaluator.observation,
            current_policy=case.evaluator.policy,
            current_safety=safety(observed_at=CONSUMED_AT),
            consumed_event_id=uid(940),
            consumed_at=CONSUMED_AT,
        )


@pytest.mark.parametrize("fault", ("missing", "forged"))
def test_consume_rejects_missing_or_forged_prepared_event(fault: str) -> None:
    case = approved_active_case()
    permit = prepare(case)

    def transform(events: tuple[EventEnvelope[object], ...]) -> object:
        if fault == "missing":
            return tuple(item for item in events if item.event_id != permit.prepared_event_id)
        return tuple(
            item.model_copy(update={"source": "forged"})
            if item.event_id == permit.prepared_event_id
            else item
            for item in events
        )

    repository = FaultRepository(case.ledger, load_transform=transform)
    with pytest.raises(SubmitPermitConsumptionError):
        consume_submit_permit(
            repository=repository,
            permit=permit,
            current_observation=case.evaluator.observation,
            current_policy=case.evaluator.policy,
            current_safety=safety(observed_at=CONSUMED_AT),
            consumed_event_id=uid(941),
            consumed_at=CONSUMED_AT,
        )


def test_restart_audit_loads_once_writes_nothing_and_returns_full_replay() -> None:
    case = approved_active_case()
    permit = prepare(case)
    consume_submit_permit(
        repository=case.ledger,
        permit=permit,
        current_observation=case.evaluator.observation,
        current_policy=case.evaluator.policy,
        current_safety=safety(observed_at=CONSUMED_AT),
        consumed_event_id=uid(942),
        consumed_at=CONSUMED_AT,
    )
    repository = FaultRepository(case.ledger)

    replay = audit_submit_authority_stream(
        repository=repository,
        stream_id=HALT_STREAM_ID,
    )

    assert replay.state is not None
    assert replay.consumed_permit_ids == (permit.permit_id,)
    assert replay.prepared == ()
    assert repository.load_calls == 1
    assert repository.append_calls == 0


@pytest.mark.parametrize("malformed", ("list", "gap", "reordered", "foreign"))
def test_restart_audit_rejects_malformed_repository_history(malformed: str) -> None:
    case = approved_active_case()
    prepare(case)

    def transform(events: tuple[EventEnvelope[object], ...]) -> object:
        selected = tuple(item for item in events if item.stream_id == HALT_STREAM_ID)
        others = tuple(item for item in events if item.stream_id != HALT_STREAM_ID)
        if malformed == "list":
            return list(events)
        if malformed == "gap":
            return (*others, selected[0], selected[1].model_copy(update={"sequence": 3}))
        if malformed == "reordered":
            return (*others, selected[1], selected[0])
        return (*events, object())

    repository = FaultRepository(case.ledger, load_transform=transform)
    with pytest.raises(GlobalHaltAuthorityError):
        audit_submit_authority_stream(
            repository=repository,
            stream_id=HALT_STREAM_ID,
        )
    assert repository.load_calls == 1
    assert repository.append_calls == 0
