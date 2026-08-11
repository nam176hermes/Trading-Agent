from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from packages.domain import EventEnvelope, OrderEvent, OrderState, OrderStatus, reduce_order
from packages.domain.runtime_halt import (
    ConsumedSubmitAuthority,
    SubmitPermitConsumed,
    SubmitPermitPrepared,
)
from packages.event_ledger import deserialize_event, serialize_event
from packages.event_ledger.replay import event_digest
from packages.execution_sandbox import (
    SandboxConnectionState,
    SandboxKnownReport,
    SandboxOrderReconciliation,
    SandboxOrderSnapshot,
    SandboxReconciliationReason,
    SandboxReconciliationResult,
    SandboxReconciliationStatus,
    SandboxRecoveryCheckpoint,
    SandboxRecoveryDecision,
    SandboxRecoveryDisposition,
    SandboxRecoveryMalformedInput,
    SandboxRecoveryReason,
    SandboxReportPlan,
    SandboxSnapshot,
    SandboxSubmitCustody,
    plan_sandbox_recovery,
)
from packages.runtime_risk import canonical_model_digest


NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


def uid(value: int) -> UUID:
    return UUID(int=value)


class AdversarialUUID(UUID):
    def __eq__(self, other: object) -> bool:
        raise AssertionError("UUID equality must not run")

    def __hash__(self) -> int:
        raise AssertionError("UUID hashing must not run")


class AdversarialDatetime(datetime):
    def __eq__(self, other: object) -> bool:
        raise AssertionError("datetime equality must not run")

    def __lt__(self, other: object) -> bool:
        raise AssertionError("datetime ordering must not run")


class AdversarialString(str):
    compared = False

    def __eq__(self, other: object) -> bool:
        type(self).compared = True
        raise AssertionError("string equality must not run")

    def __hash__(self) -> int:
        type(self).compared = True
        raise AssertionError("string hashing must not run")


class AdversarialTuple(tuple):
    iterated = False

    def __iter__(self):  # type: ignore[override]
        type(self).iterated = True
        raise AssertionError("tuple iteration must not run")


@dataclass(frozen=True)
class RecoveryCase:
    checkpoint: SandboxRecoveryCheckpoint
    authority_events: tuple[EventEnvelope[object], ...]
    reconciliation: SandboxReconciliationResult


def _model_values(value: Any) -> dict[str, object]:
    return {name: getattr(value, name) for name in type(value).model_fields}


def _order_state(order_id: UUID, status: OrderStatus) -> OrderState:
    state = OrderState(order_id=order_id)
    if status is OrderStatus.INITIALIZED:
        return state
    path = {
        OrderStatus.SUBMITTED: (OrderStatus.SUBMITTED,),
        OrderStatus.ACCEPTED: (OrderStatus.SUBMITTED, OrderStatus.ACCEPTED),
        OrderStatus.FILLED: (OrderStatus.SUBMITTED, OrderStatus.FILLED),
        OrderStatus.CANCELED: (OrderStatus.SUBMITTED, OrderStatus.CANCELED),
    }[status]
    for sequence, target in enumerate(path, start=1):
        event = OrderEvent.create(
            event_id=uid(70_000 + sequence),
            order_id=order_id,
            sequence=sequence,
            target_status=target,
            occurred_at=NOW,
            reason="TEST_TERMINAL" if target is OrderStatus.CANCELED else None,
        )
        state = reduce_order(state, event)
    return state


def _consumed_event_and_reference(
    prepared_case: Any,
) -> tuple[EventEnvelope[object], ConsumedSubmitAuthority]:
    consumed_at = NOW + timedelta(seconds=2)
    payload = SubmitPermitConsumed(
        permit_id=prepared_case.permit.permit_id,
        prepared_event_digest=prepared_case.permit.prepared_event_digest,
        halt_stream_id=prepared_case.permit.halt_stream_id,
        halt_generation=prepared_case.permit.halt_generation,
        halt_transition_digest=prepared_case.permit.halt_transition_digest,
        consumed_at=consumed_at,
        schema_version="submit-permit-consumed-v1",
    )
    event = EventEnvelope[SubmitPermitConsumed](
        event_id=uid(30),
        event_type="SubmitPermitConsumed",
        schema_version="submit-permit-consumed-event-v1",
        source="runtime-risk",
        stream_id=payload.halt_stream_id,
        sequence=3,
        observed_at=consumed_at,
        ingested_at=consumed_at,
        produced_at=consumed_at,
        effective_at=consumed_at,
        expires_at=consumed_at + timedelta(minutes=5),
        correlation_id=payload.permit_id,
        causation_id=payload.permit_id,
        trace_id=payload.permit_id,
        payload=payload,
    )
    canonical = deserialize_event(serialize_event(event))
    reference = ConsumedSubmitAuthority(
        **{
            **payload.model_dump(mode="python"),
            "consumed_event_id": canonical.event_id,
            "consumed_event_digest": event_digest(serialize_event(canonical)),
            "schema_version": "consumed-submit-authority-v1",
        }
    )
    return canonical, reference


def _reconciliation_for(snapshot: SandboxSnapshot) -> SandboxReconciliationResult:
    queued_ids = {plan.report_id for plan in snapshot.queued_reports}
    ordered_pending = tuple(
        plan.report_id
        for _, plan in sorted(
            enumerate(snapshot.queued_reports),
            key=lambda item: (item[1].deliver_at, item[0]),
        )
    )
    orders = tuple(
        SandboxOrderReconciliation(
            order_id=order.order_id,
            observed_state=order.observed_state,
            expected_venue_state=order.venue_state,
            observed_report_ids=tuple(
                report.report_id
                for report in snapshot.known_reports
                if report.report_id not in queued_ids
                and report.event.payload.order_id == order.order_id
            ),
            pending_report_ids=tuple(
                report_id
                for report_id in ordered_pending
                if next(
                    report.event.payload.order_id
                    for report in snapshot.known_reports
                    if report.report_id == report_id
                )
                == order.order_id
            ),
        )
        for order in snapshot.orders
    )
    return SandboxReconciliationResult(
        status=(
            SandboxReconciliationStatus.DELIVERY_PENDING
            if ordered_pending
            else SandboxReconciliationStatus.RECONCILED
        ),
        snapshot_time=snapshot.current_time,
        orders=orders,
        pending_report_ids=ordered_pending,
        unattributed_event_ids=(),
        unattributed_reason_codes=(),
    )


def recovery_case(
    prepared_case: Any,
    submitted_envelope: EventEnvelope[OrderEvent],
    *,
    status: OrderStatus = OrderStatus.ACCEPTED,
    pending: bool = False,
    delivered: bool = False,
) -> RecoveryCase:
    prepared_event = next(
        event
        for event in prepared_case.ledger.load_events()
        if event.event_id == prepared_case.permit.prepared_event_id
    )
    consumed_event, consumed = _consumed_event_and_reference(prepared_case)
    state = _order_state(prepared_case.intent.intent_id, status)
    known_reports: tuple[SandboxKnownReport, ...] = ()
    queued_reports: tuple[SandboxReportPlan, ...] = ()
    if pending or delivered:
        report = deserialize_event(serialize_event(submitted_envelope))
        known_reports = (SandboxKnownReport(report_id=uid(50), event=report),)
        if pending:
            queued_reports = (
                SandboxReportPlan(
                    report_id=uid(50),
                    deliver_at=NOW + timedelta(seconds=4),
                    event=report,
                ),
            )
    snapshot = SandboxSnapshot(
        connection_state=SandboxConnectionState.CONNECTED,
        current_time=NOW + timedelta(seconds=2),
        orders=(
            SandboxOrderSnapshot(
                order_id=prepared_case.intent.intent_id,
                client_order_id=prepared_case.intent.client_order_id,
                order_intent=prepared_case.intent,
                venue_state=state,
                observed_state=state,
            ),
        ),
        known_reports=known_reports,
        queued_reports=queued_reports,
    )
    custody = SandboxSubmitCustody(
        command_id=uid(100),
        order_id=prepared_case.intent.intent_id,
        client_order_id=prepared_case.intent.client_order_id,
        prepared_permit=prepared_case.permit,
        consumed_authority=consumed,
    )
    checkpoint = SandboxRecoveryCheckpoint(
        checkpoint_id=uid(200),
        scenario_digest="a" * 64,
        snapshot=snapshot,
        executed_command_ids=(uid(100),),
        submit_custodies=(custody,),
        created_at=NOW + timedelta(seconds=3),
        schema_version="sandbox-recovery-checkpoint-v1",
    )
    return RecoveryCase(
        checkpoint=checkpoint,
        authority_events=(prepared_event, consumed_event),
        reconciliation=_reconciliation_for(snapshot),
    )


def _replace_event(
    event: EventEnvelope[object],
    *,
    payload: object | None = None,
    **changes: object,
) -> EventEnvelope[object]:
    selected_payload = event.payload if payload is None else payload
    values = {
        **{name: getattr(event, name) for name in EventEnvelope.model_fields},
        "payload": selected_payload,
        **changes,
    }
    return EventEnvelope[type(selected_payload)](**values)


def _replace_prepared_payload(
    event: EventEnvelope[object], field_name: str
) -> EventEnvelope[object]:
    payload = event.payload
    assert type(payload) is SubmitPermitPrepared
    replacements: dict[str, object] = {
        "permit_id": uid(901),
        "approval_event_id": uid(902),
        "approval_reference_digest": "1" * 64,
        "intent_digest": "2" * 64,
        "policy_risk_decision_digest": "3" * 64,
        "runtime_risk_decision_digest": "4" * 64,
        "runtime_policy_digest": "5" * 64,
        "runtime_observation_digest": "6" * 64,
        "portfolio_digest": "7" * 64,
        "safety_binding_digest": "8" * 64,
        "halt_stream_id": uid(903),
        "halt_generation": payload.halt_generation + 1,
        "halt_transition_event_id": uid(904),
        "halt_transition_digest": "9" * 64,
        "prepared_at": payload.prepared_at + timedelta(microseconds=1),
        "expires_at": payload.expires_at + timedelta(microseconds=1),
    }
    updates = {field_name: replacements[field_name]}
    if field_name == "prepared_at":
        updates["expires_at"] = payload.expires_at + timedelta(microseconds=1)
    elif field_name == "expires_at":
        updates["prepared_at"] = payload.prepared_at + timedelta(microseconds=1)
    changed = SubmitPermitPrepared(**{**_model_values(payload), **updates})
    return _replace_event(event, payload=changed)


def _replace_consumed_payload(
    event: EventEnvelope[object], field_name: str
) -> EventEnvelope[object]:
    payload = event.payload
    assert type(payload) is SubmitPermitConsumed
    replacements: dict[str, object] = {
        "permit_id": uid(911),
        "prepared_event_digest": "a" * 64,
        "halt_stream_id": uid(912),
        "halt_generation": payload.halt_generation + 1,
        "halt_transition_digest": "b" * 64,
        "consumed_at": payload.consumed_at + timedelta(microseconds=1),
    }
    changed = SubmitPermitConsumed(
        **{**_model_values(payload), field_name: replacements[field_name]}
    )
    return _replace_event(event, payload=changed)


def _replace_reconciliation(
    value: SandboxReconciliationResult, **changes: object
) -> SandboxReconciliationResult:
    return SandboxReconciliationResult(**{**_model_values(value), **changes})


def _replace_order_reconciliation(
    value: SandboxOrderReconciliation, **changes: object
) -> SandboxOrderReconciliation:
    return SandboxOrderReconciliation(**{**_model_values(value), **changes})


def test_recovery_disposition_is_closed_and_has_no_submit_or_retry_member() -> None:
    assert tuple(item.value for item in SandboxRecoveryDisposition) == (
        "SAFE_TO_RESTORE",
        "RECONCILIATION_REQUIRED",
        "ALREADY_SETTLED",
        "STATE_CONFLICT",
    )
    assert not any(
        "SUBMIT" in item.value or "RETRY" in item.value
        for item in SandboxRecoveryDisposition
    )


def test_reconciled_terminal_order_is_already_settled(
    prepared_case: Any, submitted_envelope: EventEnvelope[OrderEvent]
) -> None:
    case = recovery_case(prepared_case, submitted_envelope, status=OrderStatus.FILLED)

    decision = plan_sandbox_recovery(
        checkpoint=case.checkpoint,
        authority_events=case.authority_events,
        reconciliation=case.reconciliation,
    )

    assert decision.disposition is SandboxRecoveryDisposition.ALREADY_SETTLED
    assert decision.reason_codes == (SandboxRecoveryReason.ORDERS_ALREADY_SETTLED,)


def test_reconciled_nonterminal_order_is_safe_to_restore_process_state(
    prepared_case: Any, submitted_envelope: EventEnvelope[OrderEvent]
) -> None:
    case = recovery_case(prepared_case, submitted_envelope)

    decision = plan_sandbox_recovery(
        checkpoint=case.checkpoint,
        authority_events=case.authority_events,
        reconciliation=case.reconciliation,
    )

    assert decision.disposition is SandboxRecoveryDisposition.SAFE_TO_RESTORE
    assert decision.reason_codes == (SandboxRecoveryReason.RECOVERY_EVIDENCE_COMPLETE,)


def test_delivery_pending_is_safe_to_restore_without_resubmit_authority(
    prepared_case: Any, submitted_envelope: EventEnvelope[OrderEvent]
) -> None:
    case = recovery_case(prepared_case, submitted_envelope, pending=True)

    decision = plan_sandbox_recovery(
        checkpoint=case.checkpoint,
        authority_events=case.authority_events,
        reconciliation=case.reconciliation,
    )

    assert decision.disposition is SandboxRecoveryDisposition.SAFE_TO_RESTORE
    assert decision.reason_codes == (SandboxRecoveryReason.DELIVERY_PENDING,)


@pytest.mark.parametrize(
    ("missing", "expected_reasons"),
    (
        ("prepared", (SandboxRecoveryReason.PREPARED_EVENT_MISSING,)),
        ("consumed", (SandboxRecoveryReason.CONSUMED_EVENT_MISSING,)),
        (
            "both",
            (
                SandboxRecoveryReason.PREPARED_EVENT_MISSING,
                SandboxRecoveryReason.CONSUMED_EVENT_MISSING,
            ),
        ),
    ),
)
def test_missing_authority_evidence_requires_reconciliation(
    missing: str,
    expected_reasons: tuple[SandboxRecoveryReason, ...],
    prepared_case: Any,
    submitted_envelope: EventEnvelope[OrderEvent],
) -> None:
    case = recovery_case(prepared_case, submitted_envelope)
    events = tuple(
        event
        for event in case.authority_events
        if not (
            (missing in ("prepared", "both") and type(event.payload) is SubmitPermitPrepared)
            or (missing in ("consumed", "both") and type(event.payload) is SubmitPermitConsumed)
        )
    )

    decision = plan_sandbox_recovery(
        checkpoint=case.checkpoint,
        authority_events=events,
        reconciliation=case.reconciliation,
    )

    assert decision.disposition is SandboxRecoveryDisposition.RECONCILIATION_REQUIRED
    assert decision.reason_codes == expected_reasons


@pytest.mark.parametrize("authority_kind", ("prepared", "consumed"))
def test_matching_authority_payload_under_different_event_id_is_state_conflict(
    authority_kind: str,
    prepared_case: Any,
    submitted_envelope: EventEnvelope[OrderEvent],
) -> None:
    case = recovery_case(prepared_case, submitted_envelope)
    target_type = SubmitPermitPrepared if authority_kind == "prepared" else SubmitPermitConsumed
    events = tuple(
        _replace_event(event, event_id=uid(999))
        if type(event.payload) is target_type
        else event
        for event in case.authority_events
    )

    decision = plan_sandbox_recovery(
        checkpoint=case.checkpoint,
        authority_events=events,
        reconciliation=case.reconciliation,
    )

    assert decision.disposition is SandboxRecoveryDisposition.STATE_CONFLICT
    assert SandboxRecoveryReason.AUTHORITY_EVIDENCE_CONFLICT in decision.reason_codes


@pytest.mark.parametrize("authority_kind", ("prepared", "consumed"))
def test_checkpoint_authority_digest_conflict_is_state_conflict(
    authority_kind: str,
    prepared_case: Any,
    submitted_envelope: EventEnvelope[OrderEvent],
) -> None:
    case = recovery_case(prepared_case, submitted_envelope)
    custody = case.checkpoint.submit_custodies[0]
    prepared = custody.prepared_permit
    consumed = custody.consumed_authority
    if authority_kind == "prepared":
        prepared = type(prepared)(
            **{**_model_values(prepared), "prepared_event_digest": "c" * 64}
        )
        consumed = type(consumed)(
            **{**_model_values(consumed), "prepared_event_digest": "c" * 64}
        )
    else:
        consumed = type(consumed)(
            **{**_model_values(consumed), "consumed_event_digest": "d" * 64}
        )
    changed_custody = SandboxSubmitCustody(
        command_id=custody.command_id,
        order_id=custody.order_id,
        client_order_id=custody.client_order_id,
        prepared_permit=prepared,
        consumed_authority=consumed,
    )
    checkpoint = SandboxRecoveryCheckpoint(
        **{
            **_model_values(case.checkpoint),
            "submit_custodies": (changed_custody,),
        }
    )

    decision = plan_sandbox_recovery(
        checkpoint=checkpoint,
        authority_events=case.authority_events,
        reconciliation=case.reconciliation,
    )

    assert decision.disposition is SandboxRecoveryDisposition.STATE_CONFLICT
    assert SandboxRecoveryReason.AUTHORITY_EVIDENCE_CONFLICT in decision.reason_codes


@pytest.mark.parametrize(
    "field_name",
    (
        "permit_id",
        "approval_event_id",
        "approval_reference_digest",
        "intent_digest",
        "policy_risk_decision_digest",
        "runtime_risk_decision_digest",
        "runtime_policy_digest",
        "runtime_observation_digest",
        "portfolio_digest",
        "safety_binding_digest",
        "halt_stream_id",
        "halt_generation",
        "halt_transition_event_id",
        "halt_transition_digest",
        "prepared_at",
        "expires_at",
    ),
)
def test_each_prepared_payload_authority_conflict_is_state_conflict(
    field_name: str,
    prepared_case: Any,
    submitted_envelope: EventEnvelope[OrderEvent],
) -> None:
    case = recovery_case(prepared_case, submitted_envelope)
    events = tuple(
        _replace_prepared_payload(event, field_name)
        if type(event.payload) is SubmitPermitPrepared
        else event
        for event in case.authority_events
    )

    decision = plan_sandbox_recovery(
        checkpoint=case.checkpoint,
        authority_events=events,
        reconciliation=case.reconciliation,
    )

    assert decision.disposition is SandboxRecoveryDisposition.STATE_CONFLICT
    assert SandboxRecoveryReason.AUTHORITY_EVIDENCE_CONFLICT in decision.reason_codes


@pytest.mark.parametrize(
    "field_name",
    (
        "permit_id",
        "prepared_event_digest",
        "halt_stream_id",
        "halt_generation",
        "halt_transition_digest",
        "consumed_at",
    ),
)
def test_each_consumed_payload_authority_conflict_is_state_conflict(
    field_name: str,
    prepared_case: Any,
    submitted_envelope: EventEnvelope[OrderEvent],
) -> None:
    case = recovery_case(prepared_case, submitted_envelope)
    events = tuple(
        _replace_consumed_payload(event, field_name)
        if type(event.payload) is SubmitPermitConsumed
        else event
        for event in case.authority_events
    )

    decision = plan_sandbox_recovery(
        checkpoint=case.checkpoint,
        authority_events=events,
        reconciliation=case.reconciliation,
    )

    assert decision.disposition is SandboxRecoveryDisposition.STATE_CONFLICT
    assert SandboxRecoveryReason.AUTHORITY_EVIDENCE_CONFLICT in decision.reason_codes


@pytest.mark.parametrize("authority_kind", ("prepared", "consumed"))
def test_authority_envelope_binding_conflict_is_state_conflict(
    authority_kind: str,
    prepared_case: Any,
    submitted_envelope: EventEnvelope[OrderEvent],
) -> None:
    case = recovery_case(prepared_case, submitted_envelope)
    target_type = SubmitPermitPrepared if authority_kind == "prepared" else SubmitPermitConsumed
    events = tuple(
        _replace_event(event, source="changed-source")
        if type(event.payload) is target_type
        else event
        for event in case.authority_events
    )

    decision = plan_sandbox_recovery(
        checkpoint=case.checkpoint,
        authority_events=events,
        reconciliation=case.reconciliation,
    )

    assert decision.disposition is SandboxRecoveryDisposition.STATE_CONFLICT
    assert SandboxRecoveryReason.AUTHORITY_EVIDENCE_CONFLICT in decision.reason_codes


@pytest.mark.parametrize(
    "conflict",
    ("snapshot_time", "missing_order", "extra_order", "observed_state", "venue_state"),
)
def test_reconciliation_snapshot_binding_conflict_is_state_conflict(
    conflict: str,
    prepared_case: Any,
    submitted_envelope: EventEnvelope[OrderEvent],
) -> None:
    case = recovery_case(prepared_case, submitted_envelope)
    reconciliation = case.reconciliation
    if conflict == "snapshot_time":
        reconciliation = _replace_reconciliation(
            reconciliation,
            snapshot_time=reconciliation.snapshot_time + timedelta(microseconds=1),
        )
    elif conflict == "missing_order":
        reconciliation = _replace_reconciliation(reconciliation, orders=())
    elif conflict == "extra_order":
        extra = SandboxOrderReconciliation(
            order_id=uid(999),
            observed_state=OrderState(order_id=uid(999)),
            expected_venue_state=OrderState(order_id=uid(999)),
            observed_report_ids=(),
            pending_report_ids=(),
        )
        reconciliation = _replace_reconciliation(
            reconciliation, orders=reconciliation.orders + (extra,)
        )
    else:
        order = reconciliation.orders[0]
        changed_state = OrderState(order_id=order.order_id)
        reconciliation = _replace_reconciliation(
            reconciliation,
            orders=(
                _replace_order_reconciliation(
                    order,
                    **{
                        "observed_state" if conflict == "observed_state" else "expected_venue_state": changed_state
                    },
                ),
            ),
        )

    decision = plan_sandbox_recovery(
        checkpoint=case.checkpoint,
        authority_events=case.authority_events,
        reconciliation=reconciliation,
    )

    assert decision.disposition is SandboxRecoveryDisposition.STATE_CONFLICT
    assert SandboxRecoveryReason.RECONCILIATION_SNAPSHOT_CONFLICT in decision.reason_codes


def test_pending_report_inventory_and_order_conflict_is_state_conflict(
    prepared_case: Any, submitted_envelope: EventEnvelope[OrderEvent]
) -> None:
    case = recovery_case(prepared_case, submitted_envelope, pending=True)
    order = case.reconciliation.orders[0]
    reconciliation = _replace_reconciliation(
        case.reconciliation,
        orders=(
            _replace_order_reconciliation(order, pending_report_ids=(uid(51),)),
        ),
        pending_report_ids=(uid(51),),
    )

    decision = plan_sandbox_recovery(
        checkpoint=case.checkpoint,
        authority_events=case.authority_events,
        reconciliation=reconciliation,
    )

    assert decision.disposition is SandboxRecoveryDisposition.STATE_CONFLICT
    assert SandboxRecoveryReason.PENDING_REPORT_INVENTORY_CONFLICT in decision.reason_codes


def test_original_queued_report_content_conflict_is_state_conflict(
    prepared_case: Any, submitted_envelope: EventEnvelope[OrderEvent]
) -> None:
    case = recovery_case(prepared_case, submitted_envelope, pending=True)
    snapshot = case.checkpoint.snapshot
    original_plan = snapshot.queued_reports[0]
    assert original_plan.event is not None
    conflicting_plan = SandboxReportPlan(
        report_id=original_plan.report_id,
        deliver_at=original_plan.deliver_at,
        event=_replace_event(original_plan.event, source="conflicting-queued-source"),
    )
    conflicting_snapshot = SandboxSnapshot(
        **{**_model_values(snapshot), "queued_reports": (conflicting_plan,)}
    )
    checkpoint = SandboxRecoveryCheckpoint(
        **{**_model_values(case.checkpoint), "snapshot": conflicting_snapshot}
    )
    reconciliation = _reconciliation_for(conflicting_snapshot)

    decision = plan_sandbox_recovery(
        checkpoint=checkpoint,
        authority_events=case.authority_events,
        reconciliation=reconciliation,
    )

    assert decision.disposition is SandboxRecoveryDisposition.STATE_CONFLICT
    assert decision.reason_codes == (
        SandboxRecoveryReason.PENDING_REPORT_INVENTORY_CONFLICT,
    )


def test_duplicate_queued_report_must_match_referenced_original_content(
    prepared_case: Any, submitted_envelope: EventEnvelope[OrderEvent]
) -> None:
    case = recovery_case(prepared_case, submitted_envelope, pending=True)
    snapshot = case.checkpoint.snapshot
    original = snapshot.known_reports[0]
    conflicting_duplicate = SandboxKnownReport(
        report_id=uid(51),
        event=_replace_event(original.event, source="conflicting-duplicate-source"),
    )
    duplicate_plan = SandboxReportPlan(
        report_id=conflicting_duplicate.report_id,
        deliver_at=snapshot.queued_reports[0].deliver_at,
        duplicate_of_report_id=original.report_id,
    )
    conflicting_snapshot = SandboxSnapshot(
        **{
            **_model_values(snapshot),
            "known_reports": (original, conflicting_duplicate),
            "queued_reports": (duplicate_plan,),
        }
    )
    checkpoint = SandboxRecoveryCheckpoint(
        **{**_model_values(case.checkpoint), "snapshot": conflicting_snapshot}
    )
    reconciliation = _reconciliation_for(conflicting_snapshot)

    decision = plan_sandbox_recovery(
        checkpoint=checkpoint,
        authority_events=case.authority_events,
        reconciliation=reconciliation,
    )

    assert decision.disposition is SandboxRecoveryDisposition.STATE_CONFLICT
    assert decision.reason_codes == (
        SandboxRecoveryReason.PENDING_REPORT_INVENTORY_CONFLICT,
    )


def test_mismatch_reconciliation_requires_more_economic_evidence(
    prepared_case: Any, submitted_envelope: EventEnvelope[OrderEvent]
) -> None:
    case = recovery_case(prepared_case, submitted_envelope)
    order = _replace_order_reconciliation(
        case.reconciliation.orders[0],
        reason_codes=(SandboxReconciliationReason.OBSERVED_STATE_MISMATCH,),
    )
    reconciliation = _replace_reconciliation(
        case.reconciliation,
        status=SandboxReconciliationStatus.MISMATCH,
        orders=(order,),
    )

    decision = plan_sandbox_recovery(
        checkpoint=case.checkpoint,
        authority_events=case.authority_events,
        reconciliation=reconciliation,
    )

    assert decision.disposition is SandboxRecoveryDisposition.RECONCILIATION_REQUIRED
    assert decision.reason_codes == (SandboxRecoveryReason.RECONCILIATION_MISMATCH,)


def test_unattributed_reconciliation_evidence_requires_more_evidence(
    prepared_case: Any, submitted_envelope: EventEnvelope[OrderEvent]
) -> None:
    case = recovery_case(prepared_case, submitted_envelope)
    reconciliation = _replace_reconciliation(
        case.reconciliation,
        status=SandboxReconciliationStatus.MISMATCH,
        unattributed_event_ids=(uid(999),),
        unattributed_reason_codes=(SandboxReconciliationReason.UNKNOWN_ORDER_REPORT,),
    )

    decision = plan_sandbox_recovery(
        checkpoint=case.checkpoint,
        authority_events=case.authority_events,
        reconciliation=reconciliation,
    )

    assert decision.disposition is SandboxRecoveryDisposition.RECONCILIATION_REQUIRED
    assert decision.reason_codes == (SandboxRecoveryReason.RECONCILIATION_MISMATCH,)


def test_incomplete_delivered_report_inventory_requires_reconciliation(
    prepared_case: Any, submitted_envelope: EventEnvelope[OrderEvent]
) -> None:
    case = recovery_case(prepared_case, submitted_envelope, delivered=True)
    order = _replace_order_reconciliation(
        case.reconciliation.orders[0], observed_report_ids=()
    )
    reconciliation = _replace_reconciliation(case.reconciliation, orders=(order,))

    decision = plan_sandbox_recovery(
        checkpoint=case.checkpoint,
        authority_events=case.authority_events,
        reconciliation=reconciliation,
    )

    assert decision.disposition is SandboxRecoveryDisposition.RECONCILIATION_REQUIRED
    assert decision.reason_codes == (
        SandboxRecoveryReason.RECONCILIATION_EVIDENCE_INCOMPLETE,
    )


def test_nonprefix_subset_of_delivered_reports_is_incomplete_not_conflicting(
    prepared_case: Any, submitted_envelope: EventEnvelope[OrderEvent]
) -> None:
    case = recovery_case(prepared_case, submitted_envelope, delivered=True)
    first = case.checkpoint.snapshot.known_reports[0]
    snapshot = SandboxSnapshot(
        **{
            **_model_values(case.checkpoint.snapshot),
            "known_reports": (
                first,
                SandboxKnownReport(report_id=uid(51), event=first.event),
            ),
        }
    )
    checkpoint = SandboxRecoveryCheckpoint(
        **{**_model_values(case.checkpoint), "snapshot": snapshot}
    )
    reconciliation = _reconciliation_for(snapshot)
    reconciliation = _replace_reconciliation(
        reconciliation,
        orders=(
            _replace_order_reconciliation(
                reconciliation.orders[0], observed_report_ids=(uid(51),)
            ),
        ),
    )

    decision = plan_sandbox_recovery(
        checkpoint=checkpoint,
        authority_events=case.authority_events,
        reconciliation=reconciliation,
    )

    assert decision.disposition is SandboxRecoveryDisposition.RECONCILIATION_REQUIRED
    assert decision.reason_codes == (
        SandboxRecoveryReason.RECONCILIATION_EVIDENCE_INCOMPLETE,
    )


@pytest.mark.parametrize("forged", ("checkpoint", "reconciliation"))
def test_forged_recovery_models_raise_narrow_malformed_input(
    forged: str,
    prepared_case: Any,
    submitted_envelope: EventEnvelope[OrderEvent],
) -> None:
    case = recovery_case(prepared_case, submitted_envelope)
    checkpoint = case.checkpoint
    reconciliation = case.reconciliation
    if forged == "checkpoint":
        checkpoint = checkpoint.model_copy()
        object.__setattr__(checkpoint, "scenario_digest", "not-a-digest")
    else:
        reconciliation = reconciliation.model_copy()
        object.__setattr__(reconciliation, "snapshot_time", datetime(2026, 8, 10))

    with pytest.raises(SandboxRecoveryMalformedInput):
        plan_sandbox_recovery(
            checkpoint=checkpoint,
            authority_events=case.authority_events,
            reconciliation=reconciliation,
        )


def test_noncanonical_authority_envelope_raises_narrow_malformed_input(
    prepared_case: Any, submitted_envelope: EventEnvelope[OrderEvent]
) -> None:
    case = recovery_case(prepared_case, submitted_envelope)
    forged = case.authority_events[0].model_copy(update={"source": ""})

    with pytest.raises(SandboxRecoveryMalformedInput):
        plan_sandbox_recovery(
            checkpoint=case.checkpoint,
            authority_events=(forged, case.authority_events[1]),
            reconciliation=case.reconciliation,
        )


def test_hostile_event_type_string_is_rejected_before_equality_dispatch(
    prepared_case: Any, submitted_envelope: EventEnvelope[OrderEvent]
) -> None:
    case = recovery_case(prepared_case, submitted_envelope)
    forged = case.authority_events[0].model_copy()
    object.__setattr__(
        forged,
        "event_type",
        AdversarialString(forged.event_type),
    )
    AdversarialString.compared = False

    with pytest.raises(SandboxRecoveryMalformedInput):
        plan_sandbox_recovery(
            checkpoint=case.checkpoint,
            authority_events=(forged, case.authority_events[1]),
            reconciliation=case.reconciliation,
        )
    assert not AdversarialString.compared


def test_conflicting_same_event_id_raises_narrow_malformed_input(
    prepared_case: Any, submitted_envelope: EventEnvelope[OrderEvent]
) -> None:
    case = recovery_case(prepared_case, submitted_envelope)
    prepared = case.authority_events[0]
    conflicting = _replace_event(prepared, source="conflicting-source")

    with pytest.raises(SandboxRecoveryMalformedInput, match="event identity"):
        plan_sandbox_recovery(
            checkpoint=case.checkpoint,
            authority_events=(prepared, conflicting, case.authority_events[1]),
            reconciliation=case.reconciliation,
        )


@pytest.mark.parametrize("container", (list, AdversarialTuple), ids=("list", "tuple-subclass"))
def test_authority_event_container_must_be_exact_tuple_without_iterator_dispatch(
    container: type,
    prepared_case: Any,
    submitted_envelope: EventEnvelope[OrderEvent],
) -> None:
    case = recovery_case(prepared_case, submitted_envelope)
    supplied = container(case.authority_events)
    AdversarialTuple.iterated = False

    with pytest.raises(SandboxRecoveryMalformedInput, match="authority_events"):
        plan_sandbox_recovery(
            checkpoint=case.checkpoint,
            authority_events=supplied,
            reconciliation=case.reconciliation,
        )
    if container is AdversarialTuple:
        assert not AdversarialTuple.iterated


@pytest.mark.parametrize("attack", ("uuid", "datetime", "result-tuple"))
def test_adversarial_identity_time_and_tuple_subclasses_are_rejected_before_operations(
    attack: str,
    prepared_case: Any,
    submitted_envelope: EventEnvelope[OrderEvent],
) -> None:
    case = recovery_case(prepared_case, submitted_envelope)
    checkpoint = case.checkpoint
    reconciliation = case.reconciliation
    if attack == "uuid":
        checkpoint = checkpoint.model_copy()
        object.__setattr__(checkpoint, "checkpoint_id", AdversarialUUID(int=200))
    elif attack == "datetime":
        snapshot = checkpoint.snapshot.model_copy()
        object.__setattr__(
            snapshot,
            "current_time",
            AdversarialDatetime(2026, 8, 10, 12, 0, 2, tzinfo=UTC),
        )
        checkpoint = checkpoint.model_copy(update={"snapshot": snapshot})
    else:
        reconciliation = reconciliation.model_copy(
            update={"orders": AdversarialTuple(reconciliation.orders)}
        )
        AdversarialTuple.iterated = False

    with pytest.raises(SandboxRecoveryMalformedInput):
        plan_sandbox_recovery(
            checkpoint=checkpoint,
            authority_events=case.authority_events,
            reconciliation=reconciliation,
        )
    if attack == "result-tuple":
        assert not AdversarialTuple.iterated


def test_event_permutation_and_exact_duplicates_are_idempotent(
    prepared_case: Any, submitted_envelope: EventEnvelope[OrderEvent]
) -> None:
    case = recovery_case(prepared_case, submitted_envelope)
    expected = plan_sandbox_recovery(
        checkpoint=case.checkpoint,
        authority_events=case.authority_events,
        reconciliation=case.reconciliation,
    )

    permuted = plan_sandbox_recovery(
        checkpoint=case.checkpoint,
        authority_events=tuple(reversed(case.authority_events)),
        reconciliation=case.reconciliation,
    )
    duplicated = plan_sandbox_recovery(
        checkpoint=case.checkpoint,
        authority_events=case.authority_events + case.authority_events,
        reconciliation=case.reconciliation,
    )

    assert permuted == duplicated == expected
    assert permuted.digest == duplicated.digest == expected.digest


def test_decision_digest_is_deterministic_and_changes_with_disposition(
    prepared_case: Any, submitted_envelope: EventEnvelope[OrderEvent]
) -> None:
    case = recovery_case(prepared_case, submitted_envelope)
    safe = plan_sandbox_recovery(
        checkpoint=case.checkpoint,
        authority_events=case.authority_events,
        reconciliation=case.reconciliation,
    )
    repeated = plan_sandbox_recovery(
        checkpoint=case.checkpoint,
        authority_events=case.authority_events,
        reconciliation=case.reconciliation,
    )
    missing = plan_sandbox_recovery(
        checkpoint=case.checkpoint,
        authority_events=case.authority_events[:1],
        reconciliation=case.reconciliation,
    )

    assert safe.digest == repeated.digest == canonical_model_digest(safe)
    assert missing.digest != safe.digest


def test_decision_binds_exact_input_digests_and_does_not_mutate_evidence(
    prepared_case: Any, submitted_envelope: EventEnvelope[OrderEvent]
) -> None:
    case = recovery_case(prepared_case, submitted_envelope, pending=True)
    checkpoint_before = case.checkpoint.model_dump(mode="python")
    reconciliation_before = case.reconciliation.model_dump(mode="python")
    events_before = tuple(serialize_event(event) for event in case.authority_events)

    decision = plan_sandbox_recovery(
        checkpoint=case.checkpoint,
        authority_events=case.authority_events,
        reconciliation=case.reconciliation,
    )

    assert type(decision) is SandboxRecoveryDecision
    assert decision.checkpoint_id == case.checkpoint.checkpoint_id
    assert decision.checkpoint_digest == case.checkpoint.digest
    assert decision.reconciliation_digest == case.reconciliation.digest
    assert case.checkpoint.model_dump(mode="python") == checkpoint_before
    assert case.reconciliation.model_dump(mode="python") == reconciliation_before
    assert tuple(serialize_event(event) for event in case.authority_events) == events_before


@pytest.mark.parametrize("attack", ("checkpoint-uuid", "reason-tuple"))
def test_decision_contract_rejects_adversarial_identity_and_tuple_subclasses(
    attack: str,
    prepared_case: Any,
    submitted_envelope: EventEnvelope[OrderEvent],
) -> None:
    case = recovery_case(prepared_case, submitted_envelope)
    valid = plan_sandbox_recovery(
        checkpoint=case.checkpoint,
        authority_events=case.authority_events,
        reconciliation=case.reconciliation,
    )
    values = _model_values(valid)
    if attack == "checkpoint-uuid":
        values["checkpoint_id"] = AdversarialUUID(int=200)
    else:
        values["reason_codes"] = AdversarialTuple(valid.reason_codes)
        AdversarialTuple.iterated = False

    with pytest.raises(ValueError):
        SandboxRecoveryDecision(**values)
    if attack == "reason-tuple":
        assert not AdversarialTuple.iterated
