from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from packages.domain import EventEnvelope, OrderEvent, OrderState, OrderStatus
from packages.execution_sandbox import (
    SandboxCommandKind,
    SandboxConnectionState,
    SandboxOrderReconciliation,
    SandboxReconciliationReason,
    SandboxReconciliationRequest,
    SandboxReconciliationResult,
    SandboxReconciliationStatus,
    SandboxSnapshot,
)

from .test_client_lifecycle import (
    client_for,
    command,
    duplicate,
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
        reports = {
            "exact_ack": (original(20, submitted), original(21, accepted)),
            "partial_full_fill": (original(20, submitted), original(21, accepted)),
            "fill_before_ack": (original(20, submitted), original(21, accepted)),
            "cancel": (original(20, submitted), original(21, accepted)),
            "modify": (
                original(20, submitted),
                original(21, accepted),
                duplicate(22, 21, at=NOW + timedelta(seconds=1)),
            ),
        }
        client = client_for(
            scenario(
                commands=(command(100, SandboxCommandKind.SUBMIT, tuple(plan.report_id.int for plan in reports[case])),),
                reports=reports[case],
            ),
            safety_verifier,
        )
        client.submit(valid_submit_request(prepared_case))
        observed = client.drain_reports()
        return SandboxReconciliationRequest(
            snapshot=client.snapshot(), observed_reports=observed
        )

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
