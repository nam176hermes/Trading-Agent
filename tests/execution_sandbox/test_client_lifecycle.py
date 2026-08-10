from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest

from packages.domain import EventEnvelope, FillEvent, FillReportStatus, OrderEvent, OrderQuantity, OrderStatus
from packages.event_ledger import InMemoryEventLedger, OutboxIntent, serialize_event
from packages.execution_sandbox import (
    SandboxCancelRequest,
    SandboxCommandKind,
    SandboxCommandPlan,
    SandboxConnectionState,
    SandboxExecutionClient,
    SandboxExecutionError,
    SandboxModifyRequest,
    SandboxReportPlan,
    SandboxResponseDisposition,
    SandboxScenario,
    SandboxSubmitRequest,
)


NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


@pytest.fixture(name="safety_verifier")
def fixture_safety_verifier() -> object:
    """Task 2 accepts the trusted verifier dependency but does not consume permits."""

    return object()


def uid(value: int) -> UUID:
    return UUID(int=value)


def order_envelope(
    submitted_envelope: EventEnvelope[OrderEvent],
    *,
    event_id: int,
    envelope_sequence: int,
    order_sequence: int,
    status: OrderStatus,
) -> EventEnvelope[OrderEvent]:
    payload = OrderEvent.create(
        event_id=uid(10_000 + event_id),
        order_id=uid(1),
        sequence=order_sequence,
        target_status=status,
        occurred_at=NOW,
        reason="VENUE_REJECT" if status is OrderStatus.REJECTED else None,
    )
    return EventEnvelope[OrderEvent](
        **{
            **submitted_envelope.model_dump(mode="python"),
            "event_id": uid(event_id),
            "sequence": envelope_sequence,
            "payload": payload,
        }
    )


def fill_report(
    fill_envelope: EventEnvelope[FillEvent], *, event_id: int, sequence: int, partial: bool = False
) -> EventEnvelope[FillEvent]:
    payload = fill_envelope.payload
    if partial:
        payload = FillEvent(
            **{
                **{name: getattr(fill_envelope.payload, name) for name in FillEvent.model_fields},
                "execution_id": uid(20_000 + event_id),
                "report_sequence": 1,
                "venue_trade_id": f"partial-{event_id}",
                "status": FillReportStatus.PARTIALLY_FILLED,
                "quantity": OrderQuantity(Decimal("0.500"), 3),
                "cumulative_fill_quantity": OrderQuantity(Decimal("0.500"), 3),
                "leaves_quantity": OrderQuantity(Decimal("0.500"), 3),
            }
        )
    elif event_id != fill_envelope.event_id.int:
        payload = FillEvent(
            **{
                **{name: getattr(fill_envelope.payload, name) for name in FillEvent.model_fields},
                "execution_id": uid(20_000 + event_id),
                "report_sequence": 2,
                "venue_trade_id": f"full-{event_id}",
            }
        )
    return EventEnvelope[FillEvent](
        **{
            **{name: getattr(fill_envelope, name) for name in EventEnvelope.model_fields},
            "event_id": uid(event_id),
            "sequence": sequence,
            "payload": payload,
        }
    )


def original(report_id: int, event: EventEnvelope[object], *, at: datetime = NOW) -> SandboxReportPlan:
    return SandboxReportPlan(report_id=uid(report_id), deliver_at=at, event=event)


def duplicate(report_id: int, original_id: int, *, at: datetime) -> SandboxReportPlan:
    return SandboxReportPlan(
        report_id=uid(report_id), deliver_at=at, duplicate_of_report_id=uid(original_id)
    )


def command(
    command_id: int,
    kind: SandboxCommandKind,
    report_ids: tuple[int, ...],
) -> SandboxCommandPlan:
    return SandboxCommandPlan(
        command_id=uid(command_id),
        kind=kind,
        response_disposition=SandboxResponseDisposition.ACKNOWLEDGED,
        order_id=uid(1),
        report_ids=tuple(uid(report_id) for report_id in report_ids),
    )


def scenario(*, commands: tuple[SandboxCommandPlan, ...], reports: tuple[SandboxReportPlan, ...]) -> SandboxScenario:
    return SandboxScenario(command_plans=commands, report_plans=reports)


def client_for(scenario_value: SandboxScenario, safety_verifier: Any, repository: Any | None = None) -> SandboxExecutionClient:
    return SandboxExecutionClient(
        repository=InMemoryEventLedger() if repository is None else repository,
        safety_verifier=safety_verifier,
        scenario=scenario_value,
        initial_time=NOW,
    )


def valid_submit_request(prepared_case: Any, *, command_id: int = 100) -> SandboxSubmitRequest:
    return SandboxSubmitRequest(
        command_id=uid(command_id),
        order_id=uid(1),
        order_intent=prepared_case.intent,
        permit=prepared_case.permit,
        current_observation=prepared_case.observation,
        current_policy=prepared_case.policy,
        current_safety=prepared_case.safety,
        consumed_event_id=uid(9_001),
        submitted_at=NOW,
    )


def test_delayed_acceptance_advances_venue_not_observed_until_drain(
    submitted_envelope: EventEnvelope[OrderEvent], prepared_case: Any, safety_verifier: Any
) -> None:
    submitted = order_envelope(submitted_envelope, event_id=10, envelope_sequence=1, order_sequence=1, status=OrderStatus.SUBMITTED)
    accepted = order_envelope(submitted_envelope, event_id=11, envelope_sequence=2, order_sequence=2, status=OrderStatus.ACCEPTED)
    client = client_for(
        scenario(
            commands=(command(100, SandboxCommandKind.SUBMIT, (20, 21)),),
            reports=(original(20, submitted), original(21, accepted, at=NOW + timedelta(seconds=2))),
        ),
        safety_verifier,
    )

    client.submit(valid_submit_request(prepared_case))
    assert client.snapshot().orders[0].venue_state.status is OrderStatus.ACCEPTED
    assert client.snapshot().orders[0].observed_state.status is OrderStatus.INITIALIZED

    assert client.advance_time(to=NOW + timedelta(seconds=2)).current_time == NOW + timedelta(seconds=2)
    client.drain_reports()
    assert client.snapshot().orders[0].observed_state.status is OrderStatus.ACCEPTED


def test_duplicate_delivery_reuses_exact_envelope_and_is_idempotent(
    submitted_envelope: EventEnvelope[OrderEvent], prepared_case: Any, safety_verifier: Any
) -> None:
    submitted = order_envelope(submitted_envelope, event_id=10, envelope_sequence=1, order_sequence=1, status=OrderStatus.SUBMITTED)
    accepted = order_envelope(submitted_envelope, event_id=11, envelope_sequence=2, order_sequence=2, status=OrderStatus.ACCEPTED)
    client = client_for(
        scenario(
            commands=(command(100, SandboxCommandKind.SUBMIT, (20, 21, 22)),),
            reports=(
                original(20, submitted),
                original(21, accepted),
                duplicate(22, 21, at=NOW + timedelta(seconds=1)),
            ),
        ),
        safety_verifier,
    )

    client.submit(valid_submit_request(prepared_case))
    first = client.drain_reports()
    client.advance_time(to=NOW + timedelta(seconds=1))
    second = client.drain_reports()

    assert serialize_event(second[0]) == serialize_event(first[-1])
    assert client.snapshot().orders[0].observed_state.last_sequence == 2


def test_fill_before_ack_uses_existing_reducer_edge(
    submitted_envelope: EventEnvelope[OrderEvent], fill_envelope: EventEnvelope[FillEvent], prepared_case: Any, safety_verifier: Any
) -> None:
    submitted = order_envelope(submitted_envelope, event_id=10, envelope_sequence=1, order_sequence=1, status=OrderStatus.SUBMITTED)
    partial = order_envelope(submitted_envelope, event_id=11, envelope_sequence=2, order_sequence=2, status=OrderStatus.PARTIALLY_FILLED)
    client = client_for(
        scenario(
            commands=(command(100, SandboxCommandKind.SUBMIT, (20, 21, 22)),),
            reports=(original(20, submitted), original(21, partial), original(22, fill_report(fill_envelope, event_id=12, sequence=3))),
        ),
        safety_verifier,
    )

    client.submit(valid_submit_request(prepared_case))
    client.drain_reports()
    assert client.snapshot().orders[0].observed_state.status is OrderStatus.PARTIALLY_FILLED


@pytest.mark.parametrize(
    ("final_status", "reason"),
    ((OrderStatus.REJECTED, "VENUE_REJECT"), (OrderStatus.FILLED, None)),
)
def test_reject_and_full_fill_follow_declared_lifecycle(
    final_status: OrderStatus,
    reason: str | None,
    submitted_envelope: EventEnvelope[OrderEvent],
    prepared_case: Any,
    safety_verifier: Any,
) -> None:
    del reason
    submitted = order_envelope(submitted_envelope, event_id=10, envelope_sequence=1, order_sequence=1, status=OrderStatus.SUBMITTED)
    final = order_envelope(submitted_envelope, event_id=11, envelope_sequence=2, order_sequence=2, status=final_status)
    client = client_for(
        scenario(commands=(command(100, SandboxCommandKind.SUBMIT, (20, 21)),), reports=(original(20, submitted), original(21, final))),
        safety_verifier,
    )

    client.submit(valid_submit_request(prepared_case))
    client.drain_reports()
    assert client.snapshot().orders[0].observed_state.status is final_status


def test_partial_then_full_fill_progresses_lifecycle_and_delivers_fill_reports(
    submitted_envelope: EventEnvelope[OrderEvent], fill_envelope: EventEnvelope[FillEvent], prepared_case: Any, safety_verifier: Any
) -> None:
    submitted = order_envelope(submitted_envelope, event_id=10, envelope_sequence=1, order_sequence=1, status=OrderStatus.SUBMITTED)
    partial = order_envelope(submitted_envelope, event_id=11, envelope_sequence=2, order_sequence=2, status=OrderStatus.PARTIALLY_FILLED)
    full = order_envelope(submitted_envelope, event_id=12, envelope_sequence=4, order_sequence=3, status=OrderStatus.FILLED)
    client = client_for(
        scenario(
            commands=(command(100, SandboxCommandKind.SUBMIT, (20, 21, 22, 23, 24)),),
            reports=(
                original(20, submitted), original(21, partial), original(22, fill_report(fill_envelope, event_id=13, sequence=3, partial=True)),
                original(23, full), original(24, fill_report(fill_envelope, event_id=14, sequence=5)),
            ),
        ),
        safety_verifier,
    )

    client.submit(valid_submit_request(prepared_case))
    delivered = client.drain_reports()
    assert [event.event_type for event in delivered] == ["OrderEvent", "OrderEvent", "FillEvent", "OrderEvent", "FillEvent"]
    assert client.snapshot().orders[0].observed_state.status is OrderStatus.FILLED


def test_forbidden_transition_retains_pre_call_snapshot(
    submitted_envelope: EventEnvelope[OrderEvent], prepared_case: Any, safety_verifier: Any
) -> None:
    accepted = order_envelope(submitted_envelope, event_id=10, envelope_sequence=1, order_sequence=1, status=OrderStatus.ACCEPTED)
    client = client_for(
        scenario(commands=(command(100, SandboxCommandKind.SUBMIT, (20,)),), reports=(original(20, accepted),)),
        safety_verifier,
    )
    before = client.snapshot()

    with pytest.raises(SandboxExecutionError):
        client.submit(valid_submit_request(prepared_case))

    assert client.snapshot() == before


def test_altered_ledger_duplicate_conflict_retains_pre_drain_snapshot(
    submitted_envelope: EventEnvelope[OrderEvent], prepared_case: Any, safety_verifier: Any
) -> None:
    report = order_envelope(submitted_envelope, event_id=10, envelope_sequence=1, order_sequence=1, status=OrderStatus.SUBMITTED)
    repository = InMemoryEventLedger()
    altered = EventEnvelope[OrderEvent](**{**report.model_dump(mode="python"), "source": "other-source"})
    repository.append(altered, OutboxIntent(event_id=altered.event_id, topic="execution-sandbox.report", payload_json=serialize_event(altered)))
    client = client_for(
        scenario(commands=(command(100, SandboxCommandKind.SUBMIT, (20,)),), reports=(original(20, report),)),
        safety_verifier,
        repository,
    )
    client.submit(valid_submit_request(prepared_case))
    before = client.snapshot()

    with pytest.raises(SandboxExecutionError):
        client.drain_reports()

    assert client.snapshot() == before


def test_equal_delivery_timestamps_preserve_report_plan_fifo(
    submitted_envelope: EventEnvelope[OrderEvent], prepared_case: Any, safety_verifier: Any
) -> None:
    submitted = order_envelope(submitted_envelope, event_id=10, envelope_sequence=1, order_sequence=1, status=OrderStatus.SUBMITTED)
    accepted = order_envelope(submitted_envelope, event_id=11, envelope_sequence=2, order_sequence=2, status=OrderStatus.ACCEPTED)
    client = client_for(
        scenario(commands=(command(100, SandboxCommandKind.SUBMIT, (20, 21)),), reports=(original(20, submitted), original(21, accepted))),
        safety_verifier,
    )

    client.submit(valid_submit_request(prepared_case))
    assert [event.event_id for event in client.drain_reports()] == [uid(10), uid(11)]


def test_backwards_clock_is_rejected_without_mutation(submitted_envelope: EventEnvelope[OrderEvent], safety_verifier: Any) -> None:
    client = client_for(
        scenario(commands=(command(100, SandboxCommandKind.SUBMIT, (20,)),), reports=(original(20, order_envelope(submitted_envelope, event_id=10, envelope_sequence=1, order_sequence=1, status=OrderStatus.SUBMITTED)),)),
        safety_verifier,
    )
    before = client.snapshot()

    with pytest.raises(SandboxExecutionError):
        client.advance_time(to=NOW - timedelta(microseconds=1))

    assert client.snapshot() == before


def test_bad_ledger_stream_sequence_retains_pre_drain_snapshot(
    submitted_envelope: EventEnvelope[OrderEvent], prepared_case: Any, safety_verifier: Any
) -> None:
    report = order_envelope(submitted_envelope, event_id=10, envelope_sequence=2, order_sequence=1, status=OrderStatus.SUBMITTED)
    client = client_for(
        scenario(commands=(command(100, SandboxCommandKind.SUBMIT, (20,)),), reports=(original(20, report),)),
        safety_verifier,
    )
    client.submit(valid_submit_request(prepared_case))
    before = client.snapshot()

    with pytest.raises(SandboxExecutionError):
        client.drain_reports()

    assert client.snapshot() == before


def test_later_ledger_failure_reduces_only_confirmed_reports_in_append_order(
    submitted_envelope: EventEnvelope[OrderEvent], prepared_case: Any, safety_verifier: Any
) -> None:
    submitted = order_envelope(submitted_envelope, event_id=10, envelope_sequence=1, order_sequence=1, status=OrderStatus.SUBMITTED)
    accepted = order_envelope(submitted_envelope, event_id=11, envelope_sequence=2, order_sequence=2, status=OrderStatus.ACCEPTED)
    partial = order_envelope(submitted_envelope, event_id=12, envelope_sequence=3, order_sequence=3, status=OrderStatus.PARTIALLY_FILLED)
    call_order: list[tuple[str, UUID]] = []

    class FailingLaterLedger:
        def __init__(self) -> None:
            self._delegate = InMemoryEventLedger()

        def append(self, event: EventEnvelope[object], outbox: OutboxIntent) -> Any:
            call_order.append(("append", event.event_id))
            if event.event_id == uid(11):
                raise ValueError("scripted conflicting append")
            return self._delegate.append(event, outbox)

        def load_events(self) -> tuple[EventEnvelope[object], ...]:
            return self._delegate.load_events()

    client = client_for(
        scenario(
            commands=(command(100, SandboxCommandKind.SUBMIT, (20, 21, 22)),),
            reports=(original(20, submitted), original(21, accepted), original(22, partial)),
        ),
        safety_verifier,
        FailingLaterLedger(),
    )
    client.submit(valid_submit_request(prepared_case))
    before = client.snapshot()
    reduce = client._reduce

    def record_observed_reduction(state: Any, event: OrderEvent) -> Any:
        call_order.append(("reduce", event.event_id))
        return reduce(state, event)

    client._reduce = record_observed_reduction  # type: ignore[method-assign]

    with pytest.raises(SandboxExecutionError):
        client.drain_reports()

    assert call_order == [
        ("append", uid(10)),
        ("reduce", uid(10_010)),
        ("append", uid(11)),
    ]
    assert client.snapshot() == before


@pytest.mark.parametrize(
    ("kind", "first_status"),
    ((SandboxCommandKind.CANCEL, OrderStatus.PENDING_CANCEL), (SandboxCommandKind.MODIFY, OrderStatus.PENDING_UPDATE)),
)
def test_cancel_and_modify_fill_races_follow_the_declared_command_plan_order(
    kind: SandboxCommandKind,
    first_status: OrderStatus,
    submitted_envelope: EventEnvelope[OrderEvent],
    prepared_case: Any,
    safety_verifier: Any,
) -> None:
    submitted = order_envelope(submitted_envelope, event_id=10, envelope_sequence=1, order_sequence=1, status=OrderStatus.SUBMITTED)
    accepted = order_envelope(submitted_envelope, event_id=11, envelope_sequence=2, order_sequence=2, status=OrderStatus.ACCEPTED)
    pending = order_envelope(submitted_envelope, event_id=12, envelope_sequence=3, order_sequence=3, status=first_status)
    filled = order_envelope(submitted_envelope, event_id=13, envelope_sequence=4, order_sequence=4, status=OrderStatus.FILLED)
    client = client_for(
        scenario(
            commands=(command(100, SandboxCommandKind.SUBMIT, (20, 21)), command(101, kind, (22, 23))),
            reports=(original(20, submitted), original(21, accepted), original(22, pending), original(23, filled)),
        ),
        safety_verifier,
    )
    client.submit(valid_submit_request(prepared_case))
    client.drain_reports()

    if kind is SandboxCommandKind.CANCEL:
        client.cancel(SandboxCancelRequest(command_id=uid(101), order_id=uid(1), requested_at=NOW))
    else:
        client.modify(SandboxModifyRequest(command_id=uid(101), order_id=uid(1), replacement_order_intent=prepared_case.intent, requested_at=NOW))
    client.drain_reports()

    assert client.snapshot().orders[0].observed_state.status is OrderStatus.FILLED


def test_disconnect_and_reconnect_are_scenario_gated_and_do_not_drain(
    submitted_envelope: EventEnvelope[OrderEvent], prepared_case: Any, safety_verifier: Any
) -> None:
    dormant = order_envelope(submitted_envelope, event_id=10, envelope_sequence=1, order_sequence=1, status=OrderStatus.SUBMITTED)
    delayed = order_envelope(submitted_envelope, event_id=11, envelope_sequence=2, order_sequence=2, status=OrderStatus.ACCEPTED)
    client = client_for(
        scenario(
            commands=(command(100, SandboxCommandKind.DISCONNECT, (20,)), command(101, SandboxCommandKind.RECONNECT, (21,))),
            reports=(original(20, dormant), original(21, delayed)),
        ),
        safety_verifier,
    )
    result = client.disconnect(command_id=uid(100), at=NOW)
    assert result.command_id == uid(100)
    before = client.snapshot()

    with pytest.raises(SandboxExecutionError):
        client.drain_reports()
    with pytest.raises(SandboxExecutionError):
        client.submit(valid_submit_request(prepared_case))
    with pytest.raises(SandboxExecutionError):
        client.modify(SandboxModifyRequest(command_id=uid(999), order_id=uid(1), replacement_order_intent=prepared_case.intent, requested_at=NOW))
    with pytest.raises(SandboxExecutionError):
        client.cancel(SandboxCancelRequest(command_id=uid(999), order_id=uid(1), requested_at=NOW))
    assert client.snapshot() == before

    result = client.reconnect(command_id=uid(101), at=NOW)
    assert result.command_id == uid(101)
    assert client.snapshot().connection_state is SandboxConnectionState.CONNECTED
    assert client.drain_reports() == ()
