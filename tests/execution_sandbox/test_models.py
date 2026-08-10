from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from packages.domain import EventEnvelope, OrderIntent, OrderState
from packages.execution_sandbox import (
    SandboxCancelRequest,
    SandboxCommandKind,
    SandboxCommandPlan,
    SandboxConnectionState,
    SandboxExecutionError,
    SandboxLostResponse,
    SandboxModifyRequest,
    SandboxOrderSnapshot,
    SandboxSnapshot,
    SandboxReportPlan,
    SandboxResponseDisposition,
    SandboxScenario,
    SandboxSubmitRequest,
)

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


def uid(value: int) -> UUID:
    return UUID(int=value)


def original_report(*, report_id: UUID, event: EventEnvelope[object]) -> SandboxReportPlan:
    return SandboxReportPlan(report_id=report_id, deliver_at=NOW, event=event)


def duplicate_report(*, report_id: UUID, original_report_id: UUID) -> SandboxReportPlan:
    return SandboxReportPlan(
        report_id=report_id,
        deliver_at=NOW,
        duplicate_of_report_id=original_report_id,
    )


def submit_plan(command_id: UUID, report_ids: tuple[UUID, ...], *, order_id: UUID = uid(1)) -> SandboxCommandPlan:
    return SandboxCommandPlan(
        command_id=command_id,
        kind=SandboxCommandKind.SUBMIT,
        response_disposition=SandboxResponseDisposition.ACKNOWLEDGED,
        order_id=order_id,
        report_ids=report_ids,
    )


def valid_submit_values(prepared_case: Any) -> dict[str, object]:
    return {
        "command_id": uid(30),
        "order_id": uid(1),
        "order_intent": prepared_case.intent,
        "permit": prepared_case.permit,
        "current_observation": prepared_case.observation,
        "current_policy": prepared_case.policy,
        "current_safety": prepared_case.safety,
        "consumed_event_id": uid(31),
        "submitted_at": NOW,
    }


def test_scenario_requires_unique_command_and_report_ids(submitted_envelope: EventEnvelope[object]) -> None:
    report = original_report(report_id=uid(10), event=submitted_envelope)
    with pytest.raises(ValueError, match="duplicate command_id"):
        SandboxScenario(
            command_plans=(submit_plan(uid(1), (uid(10),)), submit_plan(uid(1), (uid(10),))),
            report_plans=(report,),
        )


def test_duplicate_report_references_one_prior_original_without_new_event(submitted_envelope: EventEnvelope[object]) -> None:
    original = original_report(report_id=uid(11), event=submitted_envelope)
    duplicate = duplicate_report(report_id=uid(12), original_report_id=uid(11))
    scenario = SandboxScenario(
        command_plans=(submit_plan(uid(2), (uid(11), uid(12))),),
        report_plans=(original, duplicate),
    )
    assert scenario.report_plans[1].duplicate_of_report_id == uid(11)


def test_submit_request_rejects_naive_time(prepared_case: Any) -> None:
    with pytest.raises(ValueError):
        SandboxSubmitRequest(**{**valid_submit_values(prepared_case), "submitted_at": datetime(2026, 8, 10)})


def test_enums_have_closed_exact_values() -> None:
    assert tuple(SandboxConnectionState) == (SandboxConnectionState.CONNECTED, SandboxConnectionState.DISCONNECTED)
    assert tuple(SandboxCommandKind) == (
        SandboxCommandKind.SUBMIT, SandboxCommandKind.MODIFY, SandboxCommandKind.CANCEL,
        SandboxCommandKind.DISCONNECT, SandboxCommandKind.RECONNECT,
    )
    assert tuple(SandboxResponseDisposition) == (
        SandboxResponseDisposition.ACKNOWLEDGED, SandboxResponseDisposition.LOST_RESPONSE,
    )


@pytest.mark.parametrize(
    "values",
    [
        {"event": None, "duplicate_of_report_id": None},
        {"event": object(), "duplicate_of_report_id": uid(1)},
    ],
)
def test_report_plan_requires_exactly_one_original_or_duplicate(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        SandboxReportPlan(report_id=uid(41), deliver_at=NOW, **values)


def test_duplicate_must_reference_prior_original(submitted_envelope: EventEnvelope[object]) -> None:
    duplicate = duplicate_report(report_id=uid(51), original_report_id=uid(52))
    original = original_report(report_id=uid(52), event=submitted_envelope)
    with pytest.raises(ValueError, match="prior original"):
        SandboxScenario(command_plans=(submit_plan(uid(53), (uid(51), uid(52))),), report_plans=(duplicate, original))


def test_command_requires_existing_nonrepeating_report_ids(submitted_envelope: EventEnvelope[object]) -> None:
    report = original_report(report_id=uid(60), event=submitted_envelope)
    with pytest.raises(ValueError, match="occur exactly once"):
        SandboxScenario(command_plans=(submit_plan(uid(61), (uid(60), uid(60))),), report_plans=(report,))
    with pytest.raises(ValueError, match="does not exist"):
        SandboxScenario(command_plans=(submit_plan(uid(62), (uid(63),)),), report_plans=(report,))


def test_report_plan_requires_concrete_order_or_fill_event(prepared_case: Any) -> None:
    wrong = EventEnvelope[OrderIntent](
        event_id=uid(71),
        event_type="OrderIntent",
        schema_version="sandbox-order-intent-v1",
        source="execution-sandbox",
        stream_id=uid(72),
        sequence=1,
        observed_at=NOW,
        ingested_at=NOW,
        produced_at=NOW,
        effective_at=NOW,
        expires_at=NOW.replace(minute=1),
        correlation_id=uid(73),
        causation_id=uid(74),
        trace_id=uid(75),
        payload=prepared_case.intent,
    )
    with pytest.raises((ValidationError, ValueError)):
        SandboxReportPlan(report_id=uid(70), deliver_at=NOW, event=wrong)


def test_reports_match_their_declared_command_order_id(fill_envelope: EventEnvelope[object]) -> None:
    report = original_report(report_id=uid(80), event=fill_envelope)
    with pytest.raises(ValueError, match="order_id"):
        SandboxScenario(command_plans=(submit_plan(uid(81), (uid(80),), order_id=uid(999)),), report_plans=(report,))


def test_submit_request_requires_the_intent_identity(prepared_case: Any) -> None:
    with pytest.raises(ValueError, match="must equal"):
        SandboxSubmitRequest(**{**valid_submit_values(prepared_case), "order_id": uid(82)})


def test_models_are_frozen_and_forbid_extra_fields(prepared_case: Any, submitted_envelope: EventEnvelope[object]) -> None:
    request = SandboxSubmitRequest(**valid_submit_values(prepared_case))
    report = SandboxReportPlan(report_id=uid(83), deliver_at=NOW, event=submitted_envelope)
    with pytest.raises(ValidationError):
        SandboxSubmitRequest(**{**valid_submit_values(prepared_case), "unexpected": "value"})
    with pytest.raises(ValidationError):
        SandboxReportPlan(report_id=uid(89), deliver_at=NOW, event=report.event, unexpected="value")
    with pytest.raises(ValidationError):
        request.command_id = uid(99)  # type: ignore[misc]


def test_requests_require_canonical_nested_values(prepared_case: Any) -> None:
    forged = prepared_case.intent.model_copy()
    object.__setattr__(forged, "requested_at", datetime(2026, 8, 10))
    with pytest.raises(ValueError):
        SandboxSubmitRequest(**{**valid_submit_values(prepared_case), "order_intent": forged})
    with pytest.raises(ValueError):
        SandboxModifyRequest(command_id=uid(91), order_id=uid(1), replacement_order_intent=forged, requested_at=NOW)
    with pytest.raises(ValueError):
        SandboxCancelRequest(command_id=uid(92), order_id=uid(1), requested_at=datetime(2026, 8, 10))


def test_report_canonicalization_rejects_forged_nested_event(submitted_envelope: EventEnvelope[object]) -> None:
    forged = submitted_envelope.model_copy()
    object.__setattr__(forged, "payload", object())
    with pytest.raises(ValueError):
        SandboxReportPlan(report_id=uid(93), deliver_at=NOW, event=forged)


def test_snapshot_uses_frozen_tuples_not_mutable_collections() -> None:
    snapshot = SandboxSnapshot(
        connection_state=SandboxConnectionState.CONNECTED,
        current_time=NOW,
        orders=(),
        queued_reports=(),
    )
    assert snapshot.orders == ()
    assert snapshot.queued_reports == ()
    with pytest.raises(ValidationError):
        SandboxSnapshot(
            connection_state=SandboxConnectionState.CONNECTED,
            current_time=NOW,
            orders=[],
            queued_reports=[],
        )


def test_order_snapshot_requires_intent_identity_to_match_order_id(prepared_case: Any) -> None:
    order_id = uid(94)
    with pytest.raises(ValueError, match="order_intent"):
        SandboxOrderSnapshot(
            order_id=order_id,
            client_order_id=prepared_case.intent.client_order_id,
            order_intent=prepared_case.intent,
            venue_state=OrderState(order_id=order_id),
            observed_state=OrderState(order_id=order_id),
        )


def test_lost_response_is_a_bounded_sandbox_error() -> None:
    assert issubclass(SandboxLostResponse, SandboxExecutionError)
    with pytest.raises(SandboxExecutionError):
        raise SandboxLostResponse("sandbox response was intentionally lost")
