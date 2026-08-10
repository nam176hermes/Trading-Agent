from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from packages.domain import EventEnvelope, FillEvent, OrderEvent, OrderState, OrderStatus
from packages.execution_sandbox import (
    SandboxCancelRequest,
    SandboxCommandKind,
    SandboxCommandPlan,
    SandboxConnectionState,
    SandboxLostResponse,
    SandboxModifyRequest,
    SandboxKnownReport,
    SandboxOrderReconciliation,
    SandboxReconciliationReason,
    SandboxReconciliationRequest,
    SandboxReconciliationResult,
    SandboxReconciliationStatus,
    SandboxSnapshot,
    SandboxResponseDisposition,
    reconcile_execution_state,
)

from .test_client_lifecycle import (
    client_for,
    command,
    duplicate,
    fill_report,
    order_envelope,
    original,
    scenario,
    valid_submit_request,
)


NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


def uid(value: int) -> UUID:
    return UUID(int=value)


@pytest.fixture(name="safety_verifier")
def fixture_safety_verifier(prepared_case: Any) -> object:
    class ExactSafetyVerifier:
        def verify(self, *, observation: object) -> object:
            return observation

    verifier = ExactSafetyVerifier()
    verifier.repository = prepared_case.ledger
    return verifier


@pytest.fixture(name="reconciliation_request_factory")
def fixture_reconciliation_request_factory(
    prepared_case: Any,
    safety_verifier: object,
    submitted_envelope: EventEnvelope[OrderEvent],
) -> Callable[[str], SandboxReconciliationRequest]:
    """Build the five later replay cases from 06A's in-memory helpers only."""

    def build(case: str) -> SandboxReconciliationRequest:
        submitted = order_envelope(
            submitted_envelope,
            event_id=10,
            envelope_sequence=1,
            order_sequence=1,
            status=OrderStatus.SUBMITTED,
        )
        accepted = order_envelope(
            submitted_envelope,
            event_id=11,
            envelope_sequence=2,
            order_sequence=2,
            status=OrderStatus.ACCEPTED,
        )
        delayed = case in {"delayed_ack", "disconnected_queue"}
        reports = (
            original(20, submitted),
            original(
                21,
                accepted,
                at=NOW + timedelta(seconds=2) if delayed else NOW,
            ),
        )
        response = (
            SandboxResponseDisposition.LOST_RESPONSE
            if case == "lost_response"
            else SandboxResponseDisposition.ACKNOWLEDGED
        )
        commands = (
            SandboxCommandPlan(
                command_id=uid(100),
                kind=SandboxCommandKind.SUBMIT,
                response_disposition=response,
                order_id=uid(1),
                report_ids=(uid(20), uid(21)),
            ),
        )
        if case == "disconnected_queue":
            commands += (
                SandboxCommandPlan(
                    command_id=uid(101),
                    kind=SandboxCommandKind.DISCONNECT,
                    response_disposition=SandboxResponseDisposition.ACKNOWLEDGED,
                    order_id=uid(1),
                    report_ids=(),
                ),
            )
        client = client_for(
            scenario(
                commands=commands,
                reports=reports,
            ),
            safety_verifier,
        )
        if case == "lost_response":
            with pytest.raises(SandboxLostResponse):
                client.submit(valid_submit_request(prepared_case))
            observed = ()
        else:
            client.submit(valid_submit_request(prepared_case))
            observed = client.drain_reports()
        if case == "disconnected_queue":
            client.disconnect(command_id=uid(101), at=NOW + timedelta(seconds=2))
        snapshot = client.snapshot()
        if case == "missing_observed_ack":
            observed = observed[:1]
        if case == "forged_observed_state":
            order = snapshot.orders[0]
            snapshot = snapshot.model_copy(
                update={
                    "orders": (
                        order.model_copy(update={"observed_state": OrderState(order_id=order.order_id)}),
                    )
                }
            )
        if case == "unknown_order":
            payload = OrderEvent.create(
                event_id=uid(10_901),
                order_id=uid(900),
                sequence=1,
                target_status=OrderStatus.SUBMITTED,
                occurred_at=NOW,
            )
            observed = observed + (
                submitted.model_copy(update={"event_id": uid(901), "payload": payload}),
            )
        return SandboxReconciliationRequest(snapshot=snapshot, observed_reports=observed)

    return build


def test_contract_enums_have_the_locked_order() -> None:
    assert tuple(status.value for status in SandboxReconciliationStatus) == (
        "RECONCILED",
        "DELIVERY_PENDING",
        "MISMATCH",
    )
    assert tuple(reason.value for reason in SandboxReconciliationReason) == (
        "UNKNOWN_ORDER_REPORT",
        "OBSERVED_ORDER_REPLAY_FAILED",
        "OBSERVED_STATE_MISMATCH",
        "PENDING_ORDER_REPLAY_FAILED",
        "VENUE_STATE_MISMATCH",
        "FILL_EVIDENCE_MISMATCH",
        "UNEXPECTED_OBSERVED_REPORT",
    )


def test_contract_uses_the_locked_reconciliation_public_names() -> None:
    assert "expected_venue_state" in SandboxOrderReconciliation.model_fields
    assert "expected_state" not in SandboxOrderReconciliation.model_fields


def test_contract_models_are_frozen_and_forbid_extra() -> None:
    request = SandboxReconciliationRequest(
        snapshot=SandboxSnapshot(
            connection_state=SandboxConnectionState.CONNECTED,
            current_time=NOW,
        ),
        observed_reports=(),
    )
    with pytest.raises(ValueError):
        SandboxReconciliationRequest(
            snapshot=request.snapshot, observed_reports=(), unexpected=True
        )
    with pytest.raises(ValueError):
        request.snapshot = request.snapshot


def test_request_rejects_forged_snapshot_and_non_execution_envelope(
    prepared_case: Any,
    submitted_envelope: EventEnvelope[OrderEvent],
) -> None:
    forged_snapshot = SandboxSnapshot.model_construct(
        connection_state=SandboxConnectionState.CONNECTED,
        current_time=NOW,
        orders=object(),
    )
    with pytest.raises(ValueError, match="snapshot"):
        SandboxReconciliationRequest(snapshot=forged_snapshot, observed_reports=())

    with pytest.raises(ValueError, match="observed"):
        SandboxReconciliationRequest(
            snapshot=SandboxSnapshot(
                connection_state=SandboxConnectionState.CONNECTED,
                current_time=NOW,
            ),
            observed_reports=(
                submitted_envelope.model_copy(
                    update={"event_type": "OrderIntent", "payload": prepared_case.intent}
                ),
            ),
        )


def test_same_event_id_with_different_canonical_bytes_is_rejected(
    reconciliation_request_factory: Callable[[str], SandboxReconciliationRequest],
) -> None:
    request = reconciliation_request_factory("exact_ack")
    submitted_envelope = request.observed_reports[0]
    altered = submitted_envelope.model_copy(update={"source": "other"})
    with pytest.raises(ValueError, match="conflicting observed event"):
        SandboxReconciliationRequest(
            snapshot=request.snapshot,
            observed_reports=(submitted_envelope, altered),
        )


def test_result_canonical_digest_is_stable_after_reconstruction() -> None:
    result = SandboxReconciliationResult(
        status=SandboxReconciliationStatus.RECONCILED,
        snapshot_time=NOW,
        orders=(),
        pending_report_ids=(),
        unattributed_event_ids=(),
        unattributed_reason_codes=(),
    )
    rebuilt = SandboxReconciliationResult.model_validate(result.model_dump(mode="python"))
    assert rebuilt.digest == result.digest


@pytest.mark.parametrize(
    ("status", "pending_report_ids", "unattributed_reason_codes"),
    (
        (SandboxReconciliationStatus.RECONCILED, (uid(1),), ()),
        (SandboxReconciliationStatus.DELIVERY_PENDING, (), ()),
        (
            SandboxReconciliationStatus.DELIVERY_PENDING,
            (),
            (SandboxReconciliationReason.UNKNOWN_ORDER_REPORT,),
        ),
    ),
)
def test_invalid_input_result_status_must_match_findings_and_pending_content(
    status: SandboxReconciliationStatus,
    pending_report_ids: tuple[UUID, ...],
    unattributed_reason_codes: tuple[SandboxReconciliationReason, ...],
) -> None:
    with pytest.raises(ValueError, match="status"):
        SandboxReconciliationResult(
            status=status,
            snapshot_time=NOW,
            orders=(),
            pending_report_ids=pending_report_ids,
            unattributed_event_ids=(),
            unattributed_reason_codes=unattributed_reason_codes,
        )


@pytest.mark.parametrize(
    ("scenario_kind", "expected_status"),
    [
        ("settled_ack", SandboxReconciliationStatus.RECONCILED),
        ("delayed_ack", SandboxReconciliationStatus.DELIVERY_PENDING),
        ("lost_response", SandboxReconciliationStatus.DELIVERY_PENDING),
        ("disconnected_queue", SandboxReconciliationStatus.DELIVERY_PENDING),
        ("missing_observed_ack", SandboxReconciliationStatus.MISMATCH),
    ],
)
def test_reconcile_execution_state_classifies_delivery_state(
    scenario_kind: str,
    expected_status: SandboxReconciliationStatus,
    reconciliation_request_factory: Callable[[str], SandboxReconciliationRequest],
) -> None:
    """A wrong delivery classification must change the public reconciliation status."""

    result = reconcile_execution_state(reconciliation_request_factory(scenario_kind))
    assert result.status is expected_status


def test_reconcile_execution_state_marks_forged_observed_state_mismatch(
    reconciliation_request_factory: Callable[[str], SandboxReconciliationRequest],
) -> None:
    """Skipping the observed-state comparison must expose the forged snapshot state."""

    result = reconcile_execution_state(reconciliation_request_factory("forged_observed_state"))
    assert result.status is SandboxReconciliationStatus.MISMATCH
    assert result.orders[0].reason_codes == (
        SandboxReconciliationReason.OBSERVED_STATE_MISMATCH,
    )


def test_reconcile_execution_state_keeps_unknown_report_unattributed(
    reconciliation_request_factory: Callable[[str], SandboxReconciliationRequest],
) -> None:
    """Assigning foreign evidence to a sandbox order must remain impossible."""

    result = reconcile_execution_state(reconciliation_request_factory("unknown_order"))
    assert result.status is SandboxReconciliationStatus.MISMATCH
    assert result.unattributed_event_ids == (uid(901),)
    assert result.unattributed_reason_codes == (
        SandboxReconciliationReason.UNKNOWN_ORDER_REPORT,
    )


def test_reconcile_execution_state_replays_observed_events_independent_of_input_order(
    reconciliation_request_factory: Callable[[str], SandboxReconciliationRequest],
) -> None:
    """Treating caller tuple order as lifecycle authority must change the result."""

    request = reconciliation_request_factory("settled_ack")
    permuted = SandboxReconciliationRequest(
        snapshot=request.snapshot,
        observed_reports=tuple(reversed(request.observed_reports)),
    )
    assert reconcile_execution_state(permuted) == reconcile_execution_state(request)


def test_reconcile_execution_state_marks_invalid_observed_lifecycle(
    reconciliation_request_factory: Callable[[str], SandboxReconciliationRequest],
) -> None:
    """Ignoring a reducer failure must hide an invalid observed lifecycle."""

    request = reconciliation_request_factory("settled_ack")
    result = reconcile_execution_state(
        SandboxReconciliationRequest(
            snapshot=request.snapshot,
            observed_reports=(request.observed_reports[1],),
        )
    )
    assert SandboxReconciliationReason.OBSERVED_ORDER_REPLAY_FAILED in result.orders[0].reason_codes


def test_reconcile_execution_state_marks_invalid_pending_lifecycle(
    reconciliation_request_factory: Callable[[str], SandboxReconciliationRequest],
    submitted_envelope: EventEnvelope[OrderEvent],
) -> None:
    """Ignoring a queued reducer failure must hide an invalid venue lifecycle."""

    request = reconciliation_request_factory("delayed_ack")
    invalid_pending = order_envelope(
        submitted_envelope,
        event_id=11,
        envelope_sequence=2,
        order_sequence=2,
        status=OrderStatus.SUBMITTED,
    )
    snapshot = SandboxSnapshot(
        connection_state=request.snapshot.connection_state,
        current_time=request.snapshot.current_time,
        orders=request.snapshot.orders,
        known_reports=(
            request.snapshot.known_reports[0],
            SandboxKnownReport(report_id=uid(21), event=invalid_pending),
        ),
        queued_reports=request.snapshot.queued_reports,
    )
    result = reconcile_execution_state(
        SandboxReconciliationRequest(
            snapshot=snapshot,
            observed_reports=request.observed_reports,
        )
    )
    assert SandboxReconciliationReason.PENDING_ORDER_REPLAY_FAILED in result.orders[0].reason_codes


def test_reconcile_execution_state_keeps_unexpected_report_unattributed(
    reconciliation_request_factory: Callable[[str], SandboxReconciliationRequest],
) -> None:
    """Accepting an event absent from delivered inventory must be a mismatch."""

    request = reconciliation_request_factory("settled_ack")
    unexpected = request.observed_reports[0].model_copy(update={"event_id": uid(902)})
    result = reconcile_execution_state(
        SandboxReconciliationRequest(
            snapshot=request.snapshot,
            observed_reports=request.observed_reports + (unexpected,),
        )
    )
    assert result.unattributed_event_ids == (uid(902),)
    assert result.unattributed_reason_codes == (
        SandboxReconciliationReason.UNEXPECTED_OBSERVED_REPORT,
    )


def test_reconcile_execution_state_does_not_mutate_input_evidence(
    reconciliation_request_factory: Callable[[str], SandboxReconciliationRequest],
) -> None:
    """Mutating snapshot or evidence while reconciling would break repeatable results."""

    request = reconciliation_request_factory("delayed_ack")
    snapshot_before = request.snapshot
    reports_before = request.observed_reports

    reconcile_execution_state(request)

    assert request.snapshot == snapshot_before
    assert request.observed_reports == reports_before


def test_reconcile_execution_state_replays_fill_before_ack_without_order_state_mutation(
    prepared_case: Any,
    safety_verifier: object,
    submitted_envelope: EventEnvelope[OrderEvent],
    fill_envelope: EventEnvelope[FillEvent],
) -> None:
    """Treating a fill envelope as an order transition would corrupt an ACK race."""

    submitted = order_envelope(
        submitted_envelope,
        event_id=10,
        envelope_sequence=2,
        order_sequence=1,
        status=OrderStatus.SUBMITTED,
    )
    accepted = order_envelope(
        submitted_envelope,
        event_id=11,
        envelope_sequence=3,
        order_sequence=2,
        status=OrderStatus.ACCEPTED,
    )
    client = client_for(
        scenario(
            commands=(command(100, SandboxCommandKind.SUBMIT, (22, 20, 21)),),
            reports=(
                original(22, fill_report(fill_envelope, event_id=12, sequence=1)),
                original(20, submitted),
                original(21, accepted),
            ),
        ),
        safety_verifier,
    )
    client.submit(valid_submit_request(prepared_case))
    observed_reports = client.drain_reports()
    request = SandboxReconciliationRequest(
        snapshot=client.snapshot(), observed_reports=observed_reports
    )
    snapshot_before = request.snapshot
    reports_before = request.observed_reports

    result = reconcile_execution_state(request)

    assert result.status is SandboxReconciliationStatus.RECONCILED
    assert result.orders[0].observed_state.status is OrderStatus.ACCEPTED
    assert result.orders[0].expected_venue_state.status is OrderStatus.ACCEPTED
    assert result.orders[0].reason_codes == ()
    assert result.orders[0].observed_report_ids == (uid(22), uid(20), uid(21))
    assert request.snapshot == snapshot_before
    assert request.observed_reports == reports_before


@pytest.mark.parametrize(
    ("command_kind", "pending_status"),
    [
        (SandboxCommandKind.CANCEL, OrderStatus.PENDING_CANCEL),
        (SandboxCommandKind.MODIFY, OrderStatus.PENDING_UPDATE),
    ],
    ids=("cancel_fill", "modify_fill"),
)
def test_reconcile_execution_state_replays_command_fill_edges(
    command_kind: SandboxCommandKind,
    pending_status: OrderStatus,
    prepared_case: Any,
    safety_verifier: object,
    submitted_envelope: EventEnvelope[OrderEvent],
    fill_envelope: EventEnvelope[FillEvent],
) -> None:
    """Ignoring pending cancel/update state would hide a valid terminal fill race."""

    submitted = order_envelope(
        submitted_envelope,
        event_id=10,
        envelope_sequence=1,
        order_sequence=1,
        status=OrderStatus.SUBMITTED,
    )
    accepted = order_envelope(
        submitted_envelope,
        event_id=11,
        envelope_sequence=2,
        order_sequence=2,
        status=OrderStatus.ACCEPTED,
    )
    pending = order_envelope(
        submitted_envelope,
        event_id=12,
        envelope_sequence=3,
        order_sequence=3,
        status=pending_status,
    )
    filled = order_envelope(
        submitted_envelope,
        event_id=13,
        envelope_sequence=4,
        order_sequence=4,
        status=OrderStatus.FILLED,
    )
    client = client_for(
        scenario(
            commands=(
                command(100, SandboxCommandKind.SUBMIT, (20, 21)),
                command(101, command_kind, (22, 23, 24)),
            ),
            reports=(
                original(20, submitted),
                original(21, accepted),
                original(22, pending),
                original(23, filled),
                original(24, fill_report(fill_envelope, event_id=14, sequence=5)),
            ),
        ),
        safety_verifier,
    )
    client.submit(valid_submit_request(prepared_case))
    observed_reports = client.drain_reports()
    if command_kind is SandboxCommandKind.CANCEL:
        client.cancel(
            SandboxCancelRequest(
                command_id=uid(101),
                order_id=uid(1),
                requested_at=NOW + timedelta(seconds=1),
            )
        )
    else:
        client.modify(
            SandboxModifyRequest(
                command_id=uid(101),
                order_id=uid(1),
                replacement_order_intent=prepared_case.intent,
                requested_at=NOW + timedelta(seconds=1),
            )
        )
    observed_reports += client.drain_reports()
    request = SandboxReconciliationRequest(
        snapshot=client.snapshot(), observed_reports=observed_reports
    )
    snapshot_before = request.snapshot
    reports_before = request.observed_reports

    result = reconcile_execution_state(request)

    assert result.status is SandboxReconciliationStatus.RECONCILED
    assert result.orders[0].observed_state.status is OrderStatus.FILLED
    assert result.orders[0].expected_venue_state.status is OrderStatus.FILLED
    assert result.orders[0].reason_codes == ()
    assert result.orders[0].pending_report_ids == ()
    assert request.snapshot == snapshot_before
    assert request.observed_reports == reports_before
