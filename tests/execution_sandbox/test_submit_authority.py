from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest

from packages.domain import EventEnvelope, OrderEvent, OrderStatus, Price
from packages.domain.runtime_halt import SubmitPermitConsumed
from packages.event_ledger import serialize_event
from packages.execution_sandbox import (
    SandboxCancelRequest,
    SandboxCommandKind,
    SandboxCommandPlan,
    SandboxConnectionState,
    SandboxExecutionClient,
    SandboxExecutionError,
    SandboxLostResponse,
    SandboxModifyRequest,
    SandboxReportPlan,
    SandboxResponseDisposition,
    SandboxScenario,
    SandboxSubmitRequest,
)
from packages.runtime_risk import record_global_halt_observation
from packages.safety_evidence import CanonicalKillSwitchState

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
HALT_STREAM_ID = UUID(int=900)


class ExactSafetyVerifier:
    def verify(self, *, observation: object) -> object:
        return observation


def uid(value: int) -> UUID:
    return UUID(int=value)


def _order_report(
    source: EventEnvelope[OrderEvent], *, report_id: int, event_id: int, sequence: int, status: OrderStatus, at=NOW
) -> SandboxReportPlan:
    payload = OrderEvent.create(
        event_id=uid(10_000 + event_id),
        order_id=uid(1),
        sequence=sequence,
        target_status=status,
        occurred_at=NOW,
    )
    event = EventEnvelope[OrderEvent](
        **{
            **source.model_dump(mode="python"),
            "event_id": uid(event_id),
            "sequence": sequence,
            "payload": payload,
        }
    )
    return SandboxReportPlan(report_id=uid(report_id), deliver_at=at, event=event)


def _command(command_id: int, kind: SandboxCommandKind, report_ids: tuple[int, ...], *, response=SandboxResponseDisposition.ACKNOWLEDGED) -> SandboxCommandPlan:
    return SandboxCommandPlan(
        command_id=uid(command_id),
        kind=kind,
        response_disposition=response,
        order_id=uid(1),
        report_ids=tuple(uid(report_id) for report_id in report_ids),
    )


def _scenario(
    commands: tuple[SandboxCommandPlan, ...], reports: tuple[SandboxReportPlan, ...]
) -> SandboxScenario:
    return SandboxScenario(command_plans=commands, report_plans=reports)


def _submit_request(case: Any, *, command_id: int = 100, consumed_event_id: int = 9_001, submitted_at=NOW + timedelta(seconds=1)) -> SandboxSubmitRequest:
    return SandboxSubmitRequest(
        command_id=uid(command_id),
        order_id=case.intent.intent_id,
        order_intent=case.intent,
        permit=case.permit,
        current_observation=case.observation,
        current_policy=case.policy,
        current_safety=case.safety,
        consumed_event_id=uid(consumed_event_id),
        submitted_at=submitted_at,
    )


def _client(case: Any, scenario: SandboxScenario) -> SandboxExecutionClient:
    return SandboxExecutionClient(
        repository=case.ledger,
        safety_verifier=ExactSafetyVerifier(),
        scenario=scenario,
        initial_time=NOW,
    )


def _accepted_submit_scenario(
    submitted_envelope: EventEnvelope[OrderEvent], *, response=SandboxResponseDisposition.ACKNOWLEDGED
) -> SandboxScenario:
    return _scenario(
        (_command(100, SandboxCommandKind.SUBMIT, (20, 21), response=response),),
        (
            _order_report(submitted_envelope, report_id=20, event_id=100, sequence=1, status=OrderStatus.SUBMITTED),
            _order_report(submitted_envelope, report_id=21, event_id=101, sequence=2, status=OrderStatus.ACCEPTED),
        ),
    )


def test_submit_consumes_exact_permit_before_creating_venue_order(
    prepared_case: Any, submitted_envelope: EventEnvelope[OrderEvent]
) -> None:
    client = _client(prepared_case, _accepted_submit_scenario(submitted_envelope))

    result = client.submit(_submit_request(prepared_case))

    assert result.consumed_authority is not None
    assert result.consumed_authority.permit_id == prepared_case.permit.permit_id
    assert any(type(event.payload) is SubmitPermitConsumed for event in prepared_case.ledger.load_events())
    assert client.snapshot().orders[0].order_id == prepared_case.intent.intent_id


def test_failed_consumption_creates_no_order_report_or_response(
    prepared_case: Any, submitted_envelope: EventEnvelope[OrderEvent]
) -> None:
    client = _client(prepared_case, _accepted_submit_scenario(submitted_envelope))

    with pytest.raises(SandboxExecutionError):
        client.submit(_submit_request(prepared_case, submitted_at=NOW + timedelta(seconds=7)))

    assert client.snapshot().orders == ()
    assert client.snapshot().queued_reports == ()


def test_lost_response_never_resends_or_reuses_permit(
    prepared_case: Any, submitted_envelope: EventEnvelope[OrderEvent]
) -> None:
    client = _client(
        prepared_case,
        _accepted_submit_scenario(
            submitted_envelope, response=SandboxResponseDisposition.LOST_RESPONSE
        ),
    )
    request = _submit_request(prepared_case)

    with pytest.raises(SandboxLostResponse):
        client.submit(request)
    with pytest.raises(SandboxExecutionError):
        client.submit(request)

    assert client.snapshot().orders[0].client_order_id == prepared_case.intent.client_order_id
    assert len([event for event in prepared_case.ledger.load_events() if type(event.payload) is SubmitPermitConsumed]) == 1


def test_altered_permit_is_rejected_before_any_venue_effect(
    prepared_case: Any, submitted_envelope: EventEnvelope[OrderEvent]
) -> None:
    client = _client(prepared_case, _accepted_submit_scenario(submitted_envelope))
    request = _submit_request(
        prepared_case,
    ).model_copy(update={"permit": prepared_case.permit.model_copy(update={"permit_id": uid(500)})})

    with pytest.raises(SandboxExecutionError):
        client.submit(request)

    assert client.snapshot().orders == ()
    assert client.snapshot().queued_reports == ()


def test_stale_safety_is_rejected_before_any_venue_effect(
    prepared_case: Any, submitted_envelope: EventEnvelope[OrderEvent]
) -> None:
    client = _client(prepared_case, _accepted_submit_scenario(submitted_envelope))
    request = _submit_request(prepared_case).model_copy(
        update={"current_safety": prepared_case.safety.model_copy(update={"observed_at": NOW})}
    )

    with pytest.raises(SandboxExecutionError):
        client.submit(request)

    assert client.snapshot().orders == ()


def test_halted_authority_is_rejected_before_any_venue_effect(
    prepared_case: Any, submitted_envelope: EventEnvelope[OrderEvent]
) -> None:
    halted_safety = prepared_case.safety.model_copy(
        update={"kill_switch_state": CanonicalKillSwitchState.ACTIVE}
    )
    record_global_halt_observation(
        repository=prepared_case.ledger,
        stream_id=HALT_STREAM_ID,
        observation=prepared_case.observation,
        policy=prepared_case.policy,
        safety=halted_safety,
        safety_verifier=ExactSafetyVerifier(),
        transition_id=uid(510),
        event_id=uid(511),
        decided_at=NOW + timedelta(seconds=1),
    )
    client = _client(prepared_case, _accepted_submit_scenario(submitted_envelope))

    with pytest.raises(SandboxExecutionError):
        client.submit(_submit_request(prepared_case))

    assert client.snapshot().orders == ()


def test_duplicate_client_order_id_is_rejected_without_second_consumption(
    prepared_case: Any, submitted_envelope: EventEnvelope[OrderEvent]
) -> None:
    scenario = _scenario(
        (
            _command(100, SandboxCommandKind.SUBMIT, (20, 21)),
            _command(101, SandboxCommandKind.SUBMIT, (22, 23)),
        ),
        (
            _order_report(submitted_envelope, report_id=20, event_id=100, sequence=1, status=OrderStatus.SUBMITTED),
            _order_report(submitted_envelope, report_id=21, event_id=101, sequence=2, status=OrderStatus.ACCEPTED),
            _order_report(submitted_envelope, report_id=22, event_id=102, sequence=1, status=OrderStatus.SUBMITTED),
            _order_report(submitted_envelope, report_id=23, event_id=103, sequence=2, status=OrderStatus.ACCEPTED),
        ),
    )
    client = _client(prepared_case, scenario)
    client.submit(_submit_request(prepared_case))

    with pytest.raises(SandboxExecutionError):
        client.submit(_submit_request(prepared_case, command_id=101, consumed_event_id=9_002))

    assert len([event for event in prepared_case.ledger.load_events() if type(event.payload) is SubmitPermitConsumed]) == 1


def test_lost_response_after_acceptance_keeps_reports_for_explicit_delivery(
    prepared_case: Any, submitted_envelope: EventEnvelope[OrderEvent]
) -> None:
    client = _client(
        prepared_case,
        _accepted_submit_scenario(
            submitted_envelope, response=SandboxResponseDisposition.LOST_RESPONSE
        ),
    )

    with pytest.raises(SandboxLostResponse):
        client.submit(_submit_request(prepared_case))

    assert client.snapshot().orders[0].venue_state.status is OrderStatus.ACCEPTED
    assert [event.event_type for event in client.drain_reports()] == ["OrderEvent", "OrderEvent"]


def test_delayed_submit_report_is_delivered_only_after_reconnect(
    prepared_case: Any, submitted_envelope: EventEnvelope[OrderEvent]
) -> None:
    scenario = _scenario(
        (
            _command(100, SandboxCommandKind.SUBMIT, (20,)),
            _command(101, SandboxCommandKind.DISCONNECT, (21,)),
            _command(102, SandboxCommandKind.RECONNECT, (22,)),
        ),
        (
            _order_report(submitted_envelope, report_id=20, event_id=100, sequence=1, status=OrderStatus.SUBMITTED, at=NOW + timedelta(seconds=2)),
            _order_report(submitted_envelope, report_id=21, event_id=101, sequence=2, status=OrderStatus.ACCEPTED),
            _order_report(submitted_envelope, report_id=22, event_id=102, sequence=3, status=OrderStatus.ACCEPTED),
        ),
    )
    client = _client(prepared_case, scenario)
    client.submit(_submit_request(prepared_case))
    client.disconnect(command_id=uid(101), at=NOW + timedelta(seconds=1))
    client.advance_time(to=NOW + timedelta(seconds=2))

    with pytest.raises(SandboxExecutionError):
        client.drain_reports()

    client.reconnect(command_id=uid(102), at=NOW + timedelta(seconds=2))
    assert [event.event_id for event in client.drain_reports()] == [uid(100)]


def test_modify_uses_replacement_intent_only_after_pending_update_is_accepted(
    prepared_case: Any, submitted_envelope: EventEnvelope[OrderEvent]
) -> None:
    scenario = _scenario(
        (
            _command(100, SandboxCommandKind.SUBMIT, (20, 21)),
            _command(101, SandboxCommandKind.MODIFY, (22, 23)),
        ),
        (
            _order_report(submitted_envelope, report_id=20, event_id=100, sequence=1, status=OrderStatus.SUBMITTED),
            _order_report(submitted_envelope, report_id=21, event_id=101, sequence=2, status=OrderStatus.ACCEPTED),
            _order_report(submitted_envelope, report_id=22, event_id=102, sequence=3, status=OrderStatus.PENDING_UPDATE),
            _order_report(submitted_envelope, report_id=23, event_id=103, sequence=4, status=OrderStatus.ACCEPTED),
        ),
    )
    client = _client(prepared_case, scenario)
    client.submit(_submit_request(prepared_case))
    client.drain_reports()
    replacement = prepared_case.intent.model_copy(
        update={"limit_price": Price(Decimal("101"), prepared_case.intent.limit_price.currency)}
    )

    client.modify(
        SandboxModifyRequest(
            command_id=uid(101),
            order_id=uid(1),
            replacement_order_intent=replacement,
            requested_at=NOW,
        )
    )

    assert client.snapshot().orders[0].order_intent == replacement
    client.drain_reports()
    assert client.snapshot().orders[0].observed_state.status is OrderStatus.ACCEPTED


@pytest.mark.parametrize(
    ("kind", "first_status"),
    ((SandboxCommandKind.CANCEL, OrderStatus.PENDING_CANCEL), (SandboxCommandKind.MODIFY, OrderStatus.PENDING_UPDATE)),
)
def test_cancel_and_modify_fill_races_keep_the_existing_accepted_intent(
    kind: SandboxCommandKind,
    first_status: OrderStatus,
    prepared_case: Any,
    submitted_envelope: EventEnvelope[OrderEvent],
) -> None:
    scenario = _scenario(
        (
            _command(100, SandboxCommandKind.SUBMIT, (20, 21)),
            _command(101, kind, (22, 23)),
        ),
        (
            _order_report(submitted_envelope, report_id=20, event_id=100, sequence=1, status=OrderStatus.SUBMITTED),
            _order_report(submitted_envelope, report_id=21, event_id=101, sequence=2, status=OrderStatus.ACCEPTED),
            _order_report(submitted_envelope, report_id=22, event_id=102, sequence=3, status=first_status),
            _order_report(submitted_envelope, report_id=23, event_id=103, sequence=4, status=OrderStatus.FILLED),
        ),
    )
    client = _client(prepared_case, scenario)
    client.submit(_submit_request(prepared_case))
    client.drain_reports()
    replacement = prepared_case.intent.model_copy(
        update={"limit_price": Price(Decimal("101"), prepared_case.intent.limit_price.currency)}
    )

    if kind is SandboxCommandKind.CANCEL:
        client.cancel(SandboxCancelRequest(command_id=uid(101), order_id=uid(1), requested_at=NOW))
    else:
        client.modify(
            SandboxModifyRequest(
                command_id=uid(101),
                order_id=uid(1),
                replacement_order_intent=replacement,
                requested_at=NOW,
            )
        )
    client.drain_reports()

    assert client.snapshot().orders[0].order_intent == prepared_case.intent
    assert client.snapshot().orders[0].observed_state.status is OrderStatus.FILLED
