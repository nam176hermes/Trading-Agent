from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from hypothesis import given, strategies as st

from packages.domain import (
    Currency, EvidenceLocator, EvidenceLocatorKind, EvidenceReference, EvidenceSource,
    EventEnvelope, FillEvent, InstrumentId, Money,
    OrderEvent, OrderIntent, OrderSide, OrderStatus, OrderType, PortfolioSnapshot,
    PositionSnapshot, Price, ProductType, Quantity, ResearchPacket, RiskDecision,
    RiskOutcome, RiskReasonCode, RiskStateSnapshot, SignalDirection, SignalProposal, TargetPortfolio,
    TargetPosition, TimeInForce,
)

from packages.event_ledger import (
    ConflictingEventError, ReducerPolicy, ReplayError, ReplayIssueCode, SequenceError, StoredEvent,
    reduce_events,
)



NOW = datetime(2026, 7, 20, 12, tzinfo=UTC)
INSTRUMENT = InstrumentId("BTC-USD", ProductType.CRYPTO_SPOT, "ALPACA")


def uid(number: int) -> UUID:
    return UUID(int=number)


def evidence(number: int = 10) -> EvidenceReference:
    return EvidenceReference(
        evidence_id=uid(number),
        source=EvidenceSource.RESEARCH,
        locator=EvidenceLocator(
            kind=EvidenceLocatorKind.DATASET,
            authority="research",
            path=("evidence", "1"),
        ),
        observed_at=NOW,
        schema_version="1",
    )


def research_packet() -> ResearchPacket:
    return ResearchPacket(packet_id=uid(11), cutoff_at=NOW, evidence=(evidence(),), model_version="research-1", schema_version="1")


def signal() -> SignalProposal:
    packet = research_packet()
    return SignalProposal(signal_id=uid(12), research_packet_id=packet.packet_id, instrument=INSTRUMENT, direction=SignalDirection.LONG, score=Decimal("0.123456789012345678901234567890"), confidence=Decimal("0.8"), research_packet_cutoff_at=packet.cutoff_at, cutoff_at=NOW, expires_at=NOW + timedelta(minutes=5), evidence=(evidence(),), model_version="model-1", strategy_version="strategy-1", schema_version="1")


def target(number: int = 13) -> TargetPortfolio:
    return TargetPortfolio(target_id=uid(number), positions=(TargetPosition(instrument=INSTRUMENT, target_weight=Decimal("0.25")),), source_signal_ids=(uid(12),), effective_at=NOW, schema_version="1")


def state() -> RiskStateSnapshot:
    portfolio = PortfolioSnapshot(snapshot_id=uid(14), positions=(PositionSnapshot(instrument=INSTRUMENT, quantity=Quantity(Decimal("1"), 0), observed_at=NOW),), observed_at=NOW, schema_version="1")
    return RiskStateSnapshot(state_id=uid(15), portfolio=portfolio, open_order_ids=(uid(16),), kill_switch_engaged=False, observed_at=NOW, schema_version="1")


def risk() -> RiskDecision:
    approved = target(13)
    return RiskDecision(
        decision_id=uid(17),
        original_target=approved,
        approved_target=approved,
        outcome=RiskOutcome.APPROVED,
        reason_codes=(RiskReasonCode.WITHIN_LIMITS,),
        policy_version="risk-1",
        state_snapshot=state(),
        decided_at=NOW,
        schema_version="1",
    )


def order_intent() -> OrderIntent:
    return OrderIntent(intent_id=uid(19), risk_decision_id=uid(17), instrument=INSTRUMENT, side=OrderSide.BUY, order_type=OrderType.LIMIT, time_in_force=TimeInForce.DAY, quantity=Quantity(Decimal("1.25"), 2), limit_price=Price(Decimal("100.00000000000000000001"), Currency.USD), requested_at=NOW, schema_version="1")


def order_event() -> OrderEvent:
    intent = order_intent()
    return OrderEvent(order_id=uid(20), intent_id=intent.intent_id, instrument=intent.instrument, side=intent.side, order_type=intent.order_type, time_in_force=intent.time_in_force, status=OrderStatus.ACCEPTED, quantity=intent.quantity, limit_price=intent.limit_price, occurred_at=NOW, schema_version="1")


def fill() -> FillEvent:
    return FillEvent(fill_id=uid(21), order_id=uid(20), instrument=INSTRUMENT, side=OrderSide.BUY, quantity=Quantity(Decimal("1.25"), 2), price=Price(Decimal("100.00000000000000000001"), Currency.USD), fees=Money(Decimal("0.01"), Currency.USD), filled_at=NOW, schema_version="1")


def envelope(payload: object, *, event_number: int, stream_number: int = 100, sequence: int = 1) -> EventEnvelope[object]:
    return EventEnvelope[object](event_id=uid(event_number), event_type=type(payload).__name__, schema_version="1", source="test", stream_id=uid(stream_number), sequence=sequence, observed_at=NOW, ingested_at=NOW + timedelta(seconds=1), produced_at=NOW + timedelta(seconds=2), effective_at=NOW + timedelta(seconds=2), expires_at=NOW + timedelta(minutes=5), correlation_id=uid(30), causation_id=uid(31), trace_id=uid(32), payload=payload)


def test_identical_duplicates_are_ignored_but_conflicts_fail_closed() -> None:
    first = envelope(signal(), event_number=1)
    result = reduce_events((first, first))
    assert result.state.event_count == 1

    conflicting = first.model_copy(update={"source": "other"})
    with pytest.raises(ConflictingEventError):
        reduce_events((first, conflicting))


@pytest.mark.parametrize("events", [
    lambda: (envelope(signal(), event_number=1, sequence=1), envelope(fill(), event_number=2, sequence=3)),
    lambda: (envelope(signal(), event_number=1, sequence=1), envelope(fill(), event_number=2, sequence=1)),
])
def test_sequence_gap_and_duplicate_sequence_fail_closed(events: object) -> None:
    with pytest.raises(SequenceError):
        reduce_events(events())  # type: ignore[operator]


def test_degraded_policy_is_typed_deterministic_and_skips_bad_events() -> None:
    good = envelope(signal(), event_number=1, sequence=1)
    gap = envelope(fill(), event_number=2, sequence=3)
    result = reduce_events((good, gap), policy=ReducerPolicy.DEGRADED)
    assert result.status.value == "DEGRADED"
    assert result.state.event_count == 1
    assert tuple(issue.code for issue in result.issues) == (ReplayIssueCode.SEQUENCE_GAP,)
    assert result.issues == reduce_events((good, gap), policy=ReducerPolicy.DEGRADED).issues


@pytest.mark.parametrize("policy", ("DEGRADED", True, 1, object()))
def test_reduce_events_rejects_non_enum_policy_values(policy: object) -> None:
    with pytest.raises(ReplayError):
        reduce_events((envelope(signal(), event_number=1),), policy=policy)  # type: ignore[arg-type]


def test_valid_cross_stream_interleavings_have_same_canonical_state() -> None:
    stream_a = (envelope(signal(), event_number=1, stream_number=100, sequence=1), envelope(fill(), event_number=2, stream_number=100, sequence=2))
    stream_b = (envelope(signal(), event_number=3, stream_number=200, sequence=1), envelope(fill(), event_number=4, stream_number=200, sequence=2))
    hashes: set[str] = set()
    for positions_a in itertools.combinations(range(4), 2):
        positions_b = tuple(index for index in range(4) if index not in positions_a)
        order = [None] * 4
        for index, event in zip(positions_a, stream_a, strict=True):
            order[index] = event
        for index, event in zip(positions_b, stream_b, strict=True):
            order[index] = event
        hashes.add(reduce_events(order).state_hash)
    assert len(hashes) == 1


def _permutation_event_set() -> tuple[EventEnvelope[object], ...]:
    return (
        envelope(signal(), event_number=1, stream_number=100, sequence=1),
        envelope(fill(), event_number=2, stream_number=100, sequence=2),
        envelope(signal(), event_number=3, stream_number=200, sequence=1),
        envelope(fill(), event_number=4, stream_number=200, sequence=2),
    )


@given(order=st.permutations((0, 1, 2, 3)))
def test_every_permutation_of_same_event_set_has_same_state_hash(
    order: list[int],
) -> None:
    events = _permutation_event_set()
    canonical = reduce_events(events)

    permuted = reduce_events(tuple(events[index] for index in order))

    assert permuted == canonical
    assert permuted.state_hash == canonical.state_hash


@given(
    data=st.data(),
    duplicates=st.lists(st.integers(min_value=0, max_value=3), max_size=4),
)
def test_permutations_with_identical_duplicates_are_set_deterministic(
    data, duplicates: list[int]
) -> None:
    events = _permutation_event_set()
    indices = (0, 1, 2, 3, *duplicates)
    order = data.draw(st.permutations(tuple(range(len(indices)))))
    supplied = tuple(events[indices[position]] for position in order)

    assert reduce_events(supplied) == reduce_events(events)


@given(order=st.permutations((0, 1)))
def test_gap_detection_depends_on_sequence_set_not_caller_order(
    order: list[int],
) -> None:
    events = (
        envelope(signal(), event_number=1, sequence=1),
        envelope(fill(), event_number=3, sequence=3),
    )
    supplied = tuple(events[index] for index in order)

    degraded = reduce_events(supplied, policy=ReducerPolicy.DEGRADED)
    canonical = reduce_events(events, policy=ReducerPolicy.DEGRADED)

    assert degraded == canonical
    assert tuple(issue.expected_sequence for issue in degraded.issues) == (2,)
    with pytest.raises(SequenceError, match="expected 2, got 3"):
        reduce_events(supplied)


def test_reducer_consumes_supplied_iterable_exactly_once() -> None:
    class SinglePass:
        def __init__(self) -> None:
            self.iterations = 0

        def __iter__(self):
            self.iterations += 1
            if self.iterations != 1:
                raise AssertionError("event source was consumed more than once")
            yield envelope(signal(), event_number=1, sequence=1)
            yield envelope(fill(), event_number=2, sequence=2)

    supplied = SinglePass()
    result = reduce_events(supplied)
    assert supplied.iterations == 1
    assert result.state.event_count == 2


def test_stored_event_fingerprint_is_canonical_content() -> None:
    record = StoredEvent.from_envelope(envelope(signal(), event_number=1))
    assert len(record.digest) == 64
    assert '"score":"0.12345678901234567890123456789"' in record.canonical_json
