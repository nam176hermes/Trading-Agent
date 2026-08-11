"""Strict immutable recovery evidence for the deterministic execution sandbox."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from typing import Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, field_validator, model_validator

from packages.domain.clock import require_utc
from packages.domain.events import EventEnvelope
from packages.domain.orders import TERMINAL_ORDER_STATUSES, CanonicalIdentifier
from packages.domain.runtime_halt import (
    ConsumedSubmitAuthority,
    PreparedSubmitPermit,
    SubmitPermitConsumed,
    SubmitPermitPrepared,
)
from packages.domain.runtime_risk import Sha256
from packages.event_ledger.replay import (
    ReplayError,
    deserialize_event,
    event_digest,
    serialize_event,
)
from packages.runtime_risk import canonical_model_digest, canonical_model_json

from .models import (
    SandboxModel,
    SandboxOrderSnapshot,
    SandboxReconciliationResult,
    SandboxReconciliationStatus,
    SandboxSnapshot,
)


ModelT = TypeVar("ModelT", bound=BaseModel)


def _canonical_model(
    value: object, expected: type[ModelT], field_name: str
) -> ModelT:
    """Rebuild one exact nested model without trusting an existing instance."""

    if type(value) is not expected:
        raise ValueError(f"{field_name} must be a concrete {expected.__name__}")
    try:
        values = {name: getattr(value, name) for name in expected.model_fields}
        return expected.model_validate(values)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} is not canonical") from exc


def _require_concrete_uuid(value: object, field_name: str) -> UUID:
    if type(value) is not UUID:
        raise ValueError(f"{field_name} must be a concrete UUID")
    return value


def _require_authority_boundary_types(
    prepared: PreparedSubmitPermit,
    consumed: ConsumedSubmitAuthority,
) -> None:
    for field_name in ("permit_id", "halt_stream_id"):
        _require_concrete_uuid(
            getattr(prepared, field_name),
            f"prepared_permit {field_name}",
        )
        _require_concrete_uuid(
            getattr(consumed, field_name),
            f"consumed_authority {field_name}",
        )
    if type(consumed.consumed_at) is not datetime:
        raise ValueError("consumed_authority consumed_at must be a concrete datetime")


def _require_snapshot_boundary_types(value: object) -> None:
    if type(value) is not SandboxSnapshot:
        return
    if type(value.current_time) is not datetime:
        raise ValueError("snapshot current_time must be a concrete datetime")
    if type(value.orders) is not tuple:
        return
    for order in value.orders:
        if type(order) is not SandboxOrderSnapshot:
            continue
        identities = (
            ("order_id", order.order_id),
            ("order_intent.intent_id", order.order_intent.intent_id),
            ("venue_state.order_id", order.venue_state.order_id),
            ("observed_state.order_id", order.observed_state.order_id),
        )
        for identity_name, identity in identities:
            _require_concrete_uuid(
                identity,
                f"snapshot order {identity_name}",
            )


class SandboxSubmitCustody(SandboxModel):
    """Recovery evidence only; grants neither submit nor retry authority."""

    command_id: UUID
    order_id: UUID
    client_order_id: CanonicalIdentifier
    prepared_permit: PreparedSubmitPermit
    consumed_authority: ConsumedSubmitAuthority

    @field_validator("command_id", mode="before")
    @classmethod
    def _concrete_command_id(cls, value: object) -> UUID:
        return _require_concrete_uuid(value, "command_id")

    @field_validator("order_id", mode="before")
    @classmethod
    def _concrete_order_id(cls, value: object) -> UUID:
        return _require_concrete_uuid(value, "order_id")

    @model_validator(mode="after")
    def _canonical_authority_lineage(self) -> "SandboxSubmitCustody":
        prepared = _canonical_model(
            self.prepared_permit,
            PreparedSubmitPermit,
            "prepared_permit",
        )
        consumed = _canonical_model(
            self.consumed_authority,
            ConsumedSubmitAuthority,
            "consumed_authority",
        )
        _require_authority_boundary_types(prepared, consumed)
        for field_name in (
            "permit_id",
            "prepared_event_digest",
            "halt_stream_id",
            "halt_generation",
            "halt_transition_digest",
        ):
            if getattr(prepared, field_name) != getattr(consumed, field_name):
                raise ValueError(
                    f"prepared_permit and consumed_authority {field_name} must match"
                )
        object.__setattr__(self, "prepared_permit", prepared)
        object.__setattr__(self, "consumed_authority", consumed)
        return self


class SandboxRecoveryCheckpoint(SandboxModel):
    """Canonical process-local recovery evidence with no execution authority."""

    checkpoint_id: UUID
    scenario_digest: Sha256
    snapshot: SandboxSnapshot
    executed_command_ids: tuple[UUID, ...]
    submit_custodies: tuple[SandboxSubmitCustody, ...]
    created_at: datetime
    schema_version: Literal["sandbox-recovery-checkpoint-v1"]

    @field_validator("checkpoint_id", mode="before")
    @classmethod
    def _concrete_checkpoint_id(cls, value: object) -> UUID:
        return _require_concrete_uuid(value, "checkpoint_id")

    @field_validator("executed_command_ids", mode="before")
    @classmethod
    def _concrete_executed_command_ids(cls, value: object) -> object:
        if type(value) is not tuple:
            raise ValueError("executed_command_ids must be a concrete tuple")
        for command_id in value:
            _require_concrete_uuid(command_id, "executed_command_ids")
        return value

    @field_validator("created_at")
    @classmethod
    def _utc_created_at(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _canonical_checkpoint(self) -> "SandboxRecoveryCheckpoint":
        _require_snapshot_boundary_types(self.snapshot)
        snapshot = _canonical_model(self.snapshot, SandboxSnapshot, "snapshot")
        _require_snapshot_boundary_types(snapshot)
        custodies = tuple(
            _canonical_model(custody, SandboxSubmitCustody, "submit_custodies")
            for custody in self.submit_custodies
        )
        if len(self.executed_command_ids) != len(set(self.executed_command_ids)):
            raise ValueError("executed_command_ids must not contain duplicates")

        custody_command_ids = tuple(custody.command_id for custody in custodies)
        custody_order_ids = tuple(custody.order_id for custody in custodies)
        custody_client_order_ids = tuple(
            custody.client_order_id for custody in custodies
        )
        if len(custody_command_ids) != len(set(custody_command_ids)):
            raise ValueError("custody command_id must be unique")
        if len(custody_order_ids) != len(set(custody_order_ids)):
            raise ValueError("custody order_id must be unique")
        if len(custody_client_order_ids) != len(set(custody_client_order_ids)):
            raise ValueError("custody client_order_id must be unique")

        executed_command_ids = set(self.executed_command_ids)
        for custody in custodies:
            if custody.command_id not in executed_command_ids:
                raise ValueError(
                    "custody command_id must occur in executed_command_ids"
                )
            matching_orders = tuple(
                order for order in snapshot.orders if order.order_id == custody.order_id
            )
            if len(matching_orders) != 1:
                raise ValueError("custody must bind exactly one snapshot order")
            snapshot_order = matching_orders[0]
            if (
                custody.client_order_id != snapshot_order.client_order_id
                or custody.client_order_id
                != snapshot_order.order_intent.client_order_id
            ):
                raise ValueError(
                    "custody client_order_id must match the snapshot order intent"
                )
            if (
                canonical_model_digest(snapshot_order.order_intent)
                != custody.prepared_permit.intent_digest
            ):
                raise ValueError(
                    "snapshot order intent_digest must match prepared_permit"
                )
            if custody.consumed_authority.consumed_at > snapshot.current_time:
                raise ValueError(
                    "consumed_authority consumed_at cannot follow snapshot current_time"
                )

        object.__setattr__(self, "snapshot", snapshot)
        object.__setattr__(self, "submit_custodies", custodies)
        return self

    @property
    def digest(self) -> str:
        return canonical_model_digest(self)


class SandboxRecoveryMalformedInput(ValueError):
    """Recovery evidence could not be safely canonicalized."""


class SandboxRecoveryDisposition(str, Enum):
    """Closed evidence-only recovery outcomes with no submit authority."""

    SAFE_TO_RESTORE = "SAFE_TO_RESTORE"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    ALREADY_SETTLED = "ALREADY_SETTLED"
    STATE_CONFLICT = "STATE_CONFLICT"


class SandboxRecoveryReason(str, Enum):
    """Deterministically ordered explanations for one recovery decision."""

    PREPARED_EVENT_MISSING = "PREPARED_EVENT_MISSING"
    CONSUMED_EVENT_MISSING = "CONSUMED_EVENT_MISSING"
    AUTHORITY_EVIDENCE_CONFLICT = "AUTHORITY_EVIDENCE_CONFLICT"
    RECONCILIATION_SNAPSHOT_CONFLICT = "RECONCILIATION_SNAPSHOT_CONFLICT"
    PENDING_REPORT_INVENTORY_CONFLICT = "PENDING_REPORT_INVENTORY_CONFLICT"
    RECONCILIATION_MISMATCH = "RECONCILIATION_MISMATCH"
    RECONCILIATION_EVIDENCE_INCOMPLETE = "RECONCILIATION_EVIDENCE_INCOMPLETE"
    DELIVERY_PENDING = "DELIVERY_PENDING"
    RECOVERY_EVIDENCE_COMPLETE = "RECOVERY_EVIDENCE_COMPLETE"
    ORDERS_ALREADY_SETTLED = "ORDERS_ALREADY_SETTLED"


class SandboxRecoveryDecision(SandboxModel):
    """Digestible evidence outcome; it contains no executable recovery action."""

    disposition: SandboxRecoveryDisposition
    checkpoint_id: UUID
    checkpoint_digest: Sha256
    reconciliation_digest: Sha256
    reason_codes: tuple[SandboxRecoveryReason, ...]
    affected_order_ids: tuple[UUID, ...] = ()

    @field_validator("checkpoint_id", mode="before")
    @classmethod
    def _concrete_checkpoint_id(cls, value: object) -> UUID:
        return _require_concrete_uuid(value, "checkpoint_id")

    @field_validator("reason_codes", mode="before")
    @classmethod
    def _concrete_reason_codes(cls, value: object) -> object:
        if type(value) is not tuple:
            raise ValueError("reason_codes must be a concrete tuple")
        return value

    @field_validator("affected_order_ids", mode="before")
    @classmethod
    def _concrete_affected_order_ids(cls, value: object) -> object:
        if type(value) is not tuple:
            raise ValueError("affected_order_ids must be a concrete tuple")
        for order_id in value:
            _require_concrete_uuid(order_id, "affected_order_ids")
        return value

    @model_validator(mode="after")
    def _canonical_result(self) -> "SandboxRecoveryDecision":
        reason_indices = tuple(
            list(SandboxRecoveryReason).index(reason) for reason in self.reason_codes
        )
        if (
            not reason_indices
            or len(reason_indices) != len(set(reason_indices))
            or reason_indices != tuple(sorted(reason_indices))
        ):
            raise ValueError("reason_codes must be unique and follow enum order")
        if (
            any(type(order_id) is not UUID for order_id in self.affected_order_ids)
            or len(self.affected_order_ids) != len(set(self.affected_order_ids))
            or self.affected_order_ids
            != tuple(sorted(self.affected_order_ids, key=lambda order_id: order_id.int))
        ):
            raise ValueError("affected_order_ids must be unique UUID ordered values")
        return self

    @property
    def digest(self) -> str:
        return canonical_model_digest(self)


def _require_safe_recovery_value(
    value: object,
    field_name: str,
    *,
    active_ids: set[int] | None = None,
) -> None:
    """Reject attacker-controlled primitive/container subclasses before use."""

    if isinstance(value, UUID):
        if type(value) is not UUID:
            raise SandboxRecoveryMalformedInput(
                f"{field_name} must contain concrete UUID values"
            )
        return
    if isinstance(value, datetime):
        if type(value) is not datetime:
            raise SandboxRecoveryMalformedInput(
                f"{field_name} must contain concrete datetime values"
            )
        return
    if isinstance(value, str):
        if type(value) is not str and not isinstance(value, Enum):
            raise SandboxRecoveryMalformedInput(
                f"{field_name} must contain concrete string values"
            )
        return
    if isinstance(value, tuple):
        if type(value) is not tuple:
            raise SandboxRecoveryMalformedInput(
                f"{field_name} must contain concrete tuple values"
            )
        active = set() if active_ids is None else active_ids
        identity = id(value)
        if identity in active:
            raise SandboxRecoveryMalformedInput(f"{field_name} is recursive")
        active.add(identity)
        try:
            for index, item in enumerate(tuple.__iter__(value)):
                _require_safe_recovery_value(
                    item,
                    f"{field_name}.{index}",
                    active_ids=active,
                )
        finally:
            active.remove(identity)
        return
    if isinstance(value, BaseModel):
        active = set() if active_ids is None else active_ids
        identity = id(value)
        if identity in active:
            raise SandboxRecoveryMalformedInput(f"{field_name} is recursive")
        active.add(identity)
        try:
            for nested_name in type(value).model_fields:
                try:
                    nested = object.__getattribute__(value, nested_name)
                except AttributeError as exc:
                    raise SandboxRecoveryMalformedInput(
                        f"{field_name} is incomplete"
                    ) from exc
                _require_safe_recovery_value(
                    nested,
                    f"{field_name}.{nested_name}",
                    active_ids=active,
                )
        finally:
            active.remove(identity)


def _canonical_checkpoint_input(value: object) -> SandboxRecoveryCheckpoint:
    if type(value) is not SandboxRecoveryCheckpoint:
        raise SandboxRecoveryMalformedInput(
            "checkpoint must be a concrete SandboxRecoveryCheckpoint"
        )
    try:
        _require_safe_recovery_value(value, "checkpoint")
        canonical = _canonical_model(
            value,
            SandboxRecoveryCheckpoint,
            "checkpoint",
        )
        _require_safe_recovery_value(canonical, "checkpoint")
        return canonical
    except SandboxRecoveryMalformedInput:
        raise
    except (AttributeError, TypeError, ValueError) as exc:
        raise SandboxRecoveryMalformedInput("checkpoint is not canonical") from exc


def _canonical_reconciliation_input(
    value: object,
) -> SandboxReconciliationResult:
    if type(value) is not SandboxReconciliationResult:
        raise SandboxRecoveryMalformedInput(
            "reconciliation must be a concrete SandboxReconciliationResult"
        )
    try:
        _require_safe_recovery_value(value, "reconciliation")
        canonical = _canonical_model(
            value,
            SandboxReconciliationResult,
            "reconciliation",
        )
        _require_safe_recovery_value(canonical, "reconciliation")
        return canonical
    except SandboxRecoveryMalformedInput:
        raise
    except (AttributeError, TypeError, ValueError) as exc:
        raise SandboxRecoveryMalformedInput(
            "reconciliation is not canonical"
        ) from exc


class _CanonicalAuthorityEvent:
    __slots__ = ("event", "canonical_text", "digest")

    def __init__(
        self,
        event: EventEnvelope[object],
        canonical_text: str,
    ) -> None:
        self.event = event
        self.canonical_text = canonical_text
        self.digest = event_digest(canonical_text)


def _canonical_authority_events(
    values: object,
) -> tuple[
    dict[UUID, _CanonicalAuthorityEvent],
    tuple[_CanonicalAuthorityEvent, ...],
]:
    if type(values) is not tuple:
        raise SandboxRecoveryMalformedInput("authority_events must be a concrete tuple")
    by_id: dict[UUID, _CanonicalAuthorityEvent] = {}
    ordered: list[_CanonicalAuthorityEvent] = []
    try:
        iterator = tuple.__iter__(values)
        for index, supplied in enumerate(iterator):
            if not isinstance(supplied, EventEnvelope):
                raise ValueError("authority evidence must be an EventEnvelope")
            _require_safe_recovery_value(supplied, f"authority_events.{index}")
            canonical_text = serialize_event(supplied)
            canonical = deserialize_event(canonical_text)
            _require_safe_recovery_value(canonical, f"authority_events.{index}")
            if serialize_event(canonical) != canonical_text:
                raise ValueError("authority event did not round-trip canonically")
            item = _CanonicalAuthorityEvent(canonical, canonical_text)
            previous = by_id.get(canonical.event_id)
            if previous is not None:
                if previous.canonical_text != canonical_text:
                    raise SandboxRecoveryMalformedInput(
                        "event identity has conflicting canonical evidence"
                    )
                continue
            by_id[canonical.event_id] = item
            ordered.append(item)
    except SandboxRecoveryMalformedInput:
        raise
    except (AttributeError, ReplayError, TypeError, ValueError) as exc:
        raise SandboxRecoveryMalformedInput(
            "authority_events contain noncanonical evidence"
        ) from exc
    return by_id, tuple(ordered)


def _prepared_payload_from_reference(
    reference: PreparedSubmitPermit,
) -> SubmitPermitPrepared:
    return SubmitPermitPrepared(
        **{
            **{
                name: getattr(reference, name)
                for name in SubmitPermitPrepared.model_fields
            },
            "schema_version": "submit-permit-prepared-v1",
        }
    )


def _consumed_payload_from_reference(
    reference: ConsumedSubmitAuthority,
) -> SubmitPermitConsumed:
    return SubmitPermitConsumed(
        **{
            **{
                name: getattr(reference, name)
                for name in SubmitPermitConsumed.model_fields
            },
            "schema_version": "submit-permit-consumed-v1",
        }
    )


def _prepared_event_matches(
    item: _CanonicalAuthorityEvent,
    reference: PreparedSubmitPermit,
) -> bool:
    event = item.event
    payload = event.payload
    if type(payload) is not SubmitPermitPrepared:
        return False
    expected = _prepared_payload_from_reference(reference)
    return (
        item.digest == reference.prepared_event_digest
        and canonical_model_json(payload) == canonical_model_json(expected)
        and event.event_type == "SubmitPermitPrepared"
        and event.schema_version == "submit-permit-prepared-event-v1"
        and event.source == "runtime-risk"
        and event.stream_id == payload.halt_stream_id
        and event.observed_at == payload.prepared_at
        and event.ingested_at == payload.prepared_at
        and event.produced_at == payload.prepared_at
        and event.effective_at == payload.prepared_at
        and event.expires_at == payload.expires_at
        and event.correlation_id == payload.permit_id
        and event.causation_id == payload.approval_event_id
        and event.trace_id == payload.permit_id
    )


def _consumed_event_matches(
    item: _CanonicalAuthorityEvent,
    reference: ConsumedSubmitAuthority,
) -> bool:
    event = item.event
    payload = event.payload
    if type(payload) is not SubmitPermitConsumed:
        return False
    expected = _consumed_payload_from_reference(reference)
    return (
        item.digest == reference.consumed_event_digest
        and canonical_model_json(payload) == canonical_model_json(expected)
        and event.event_type == "SubmitPermitConsumed"
        and event.schema_version == "submit-permit-consumed-event-v1"
        and event.source == "runtime-risk"
        and event.stream_id == payload.halt_stream_id
        and event.observed_at == payload.consumed_at
        and event.ingested_at == payload.consumed_at
        and event.produced_at == payload.consumed_at
        and event.effective_at == payload.consumed_at
        and event.expires_at == payload.consumed_at + timedelta(minutes=5)
        and event.correlation_id == payload.permit_id
        and event.causation_id == payload.permit_id
        and event.trace_id == payload.permit_id
    )


def _authority_reasons(
    checkpoint: SandboxRecoveryCheckpoint,
    by_id: dict[UUID, _CanonicalAuthorityEvent],
    events: tuple[_CanonicalAuthorityEvent, ...],
) -> tuple[set[SandboxRecoveryReason], set[UUID]]:
    reasons: set[SandboxRecoveryReason] = set()
    affected: set[UUID] = set()
    for custody in checkpoint.submit_custodies:
        prepared_reference = custody.prepared_permit
        consumed_reference = custody.consumed_authority
        prepared_candidates = tuple(
            item
            for item in events
            if type(item.event.payload) is SubmitPermitPrepared
            and item.event.payload.permit_id == prepared_reference.permit_id
        )
        consumed_candidates = tuple(
            item
            for item in events
            if type(item.event.payload) is SubmitPermitConsumed
            and item.event.payload.permit_id == consumed_reference.permit_id
        )

        prepared = by_id.get(prepared_reference.prepared_event_id)
        if prepared is None:
            reasons.add(
                SandboxRecoveryReason.AUTHORITY_EVIDENCE_CONFLICT
                if prepared_candidates
                else SandboxRecoveryReason.PREPARED_EVENT_MISSING
            )
            affected.add(custody.order_id)
        elif (
            len(prepared_candidates) != 1
            or prepared_candidates[0] is not prepared
            or not _prepared_event_matches(prepared, prepared_reference)
        ):
            reasons.add(SandboxRecoveryReason.AUTHORITY_EVIDENCE_CONFLICT)
            affected.add(custody.order_id)

        consumed = by_id.get(consumed_reference.consumed_event_id)
        if consumed is None:
            reasons.add(
                SandboxRecoveryReason.AUTHORITY_EVIDENCE_CONFLICT
                if consumed_candidates
                else SandboxRecoveryReason.CONSUMED_EVENT_MISSING
            )
            affected.add(custody.order_id)
        elif (
            len(consumed_candidates) != 1
            or consumed_candidates[0] is not consumed
            or not _consumed_event_matches(consumed, consumed_reference)
        ):
            reasons.add(SandboxRecoveryReason.AUTHORITY_EVIDENCE_CONFLICT)
            affected.add(custody.order_id)
    return reasons, affected


def _canonical_state_matches(left: BaseModel, right: BaseModel) -> bool:
    return canonical_model_json(left) == canonical_model_json(right)


def _reconciliation_reasons(
    checkpoint: SandboxRecoveryCheckpoint,
    reconciliation: SandboxReconciliationResult,
) -> tuple[set[SandboxRecoveryReason], set[UUID]]:
    reasons: set[SandboxRecoveryReason] = set()
    affected: set[UUID] = set()
    snapshot = checkpoint.snapshot
    if reconciliation.snapshot_time != snapshot.current_time:
        reasons.add(SandboxRecoveryReason.RECONCILIATION_SNAPSHOT_CONFLICT)

    snapshot_by_order = {order.order_id: order for order in snapshot.orders}
    reconciliation_by_order = {
        order.order_id: order for order in reconciliation.orders
    }
    if set(snapshot_by_order) != set(reconciliation_by_order):
        reasons.add(SandboxRecoveryReason.RECONCILIATION_SNAPSHOT_CONFLICT)
        affected.update(set(snapshot_by_order) ^ set(reconciliation_by_order))
    for order_id in set(snapshot_by_order) & set(reconciliation_by_order):
        snapshot_order = snapshot_by_order[order_id]
        result_order = reconciliation_by_order[order_id]
        if (
            not _canonical_state_matches(
                result_order.observed_state,
                snapshot_order.observed_state,
            )
            or not _canonical_state_matches(
                result_order.expected_venue_state,
                snapshot_order.venue_state,
            )
        ):
            reasons.add(SandboxRecoveryReason.RECONCILIATION_SNAPSHOT_CONFLICT)
            affected.add(order_id)

    known_by_report_id = {
        report.report_id: report.event for report in snapshot.known_reports
    }
    ordered_pending = tuple(
        plan.report_id
        for _, plan in sorted(
            enumerate(snapshot.queued_reports),
            key=lambda item: (item[1].deliver_at, item[0]),
        )
    )
    if reconciliation.pending_report_ids != ordered_pending:
        reasons.add(SandboxRecoveryReason.PENDING_REPORT_INVENTORY_CONFLICT)
    for plan in snapshot.queued_reports:
        known_event = known_by_report_id[plan.report_id]
        expected_event = plan.event
        if expected_event is None:
            expected_event = known_by_report_id.get(plan.duplicate_of_report_id)
        if (
            expected_event is None
            or plan.duplicate_of_report_id == plan.report_id
            or serialize_event(expected_event) != serialize_event(known_event)
        ):
            reasons.add(SandboxRecoveryReason.PENDING_REPORT_INVENTORY_CONFLICT)
            affected.add(known_event.payload.order_id)
    queued_ids = set(ordered_pending)
    incomplete = False
    for order_id in set(snapshot_by_order) & set(reconciliation_by_order):
        result_order = reconciliation_by_order[order_id]
        expected_pending = tuple(
            report_id
            for report_id in ordered_pending
            if known_by_report_id[report_id].payload.order_id == order_id
        )
        if result_order.pending_report_ids != expected_pending:
            reasons.add(SandboxRecoveryReason.PENDING_REPORT_INVENTORY_CONFLICT)
            affected.add(order_id)
        expected_observed = tuple(
            report.report_id
            for report in snapshot.known_reports
            if report.report_id not in queued_ids
            and report.event.payload.order_id == order_id
        )
        if result_order.observed_report_ids != expected_observed:
            supplied_observed = set(result_order.observed_report_ids)
            if (
                len(result_order.observed_report_ids) < len(expected_observed)
                and supplied_observed <= set(expected_observed)
                and result_order.observed_report_ids
                == tuple(
                    report_id
                    for report_id in expected_observed
                    if report_id in supplied_observed
                )
            ):
                incomplete = True
            else:
                reasons.add(SandboxRecoveryReason.RECONCILIATION_SNAPSHOT_CONFLICT)
            affected.add(order_id)

    if reconciliation.status is SandboxReconciliationStatus.MISMATCH:
        reasons.add(SandboxRecoveryReason.RECONCILIATION_MISMATCH)
        affected.update(snapshot_by_order)
    if incomplete:
        reasons.add(SandboxRecoveryReason.RECONCILIATION_EVIDENCE_INCOMPLETE)
    return reasons, affected


def _ordered_recovery_reasons(
    reasons: set[SandboxRecoveryReason],
) -> tuple[SandboxRecoveryReason, ...]:
    return tuple(reason for reason in SandboxRecoveryReason if reason in reasons)


def plan_sandbox_recovery(
    *,
    checkpoint: SandboxRecoveryCheckpoint,
    authority_events: tuple[EventEnvelope[object], ...],
    reconciliation: SandboxReconciliationResult,
) -> SandboxRecoveryDecision:
    """Purely classify recovery evidence; never authorize command resubmission."""

    canonical_checkpoint = _canonical_checkpoint_input(checkpoint)
    canonical_reconciliation = _canonical_reconciliation_input(reconciliation)
    by_id, canonical_events = _canonical_authority_events(authority_events)
    authority_reasons, affected = _authority_reasons(
        canonical_checkpoint,
        by_id,
        canonical_events,
    )
    reconciliation_reasons, reconciliation_affected = _reconciliation_reasons(
        canonical_checkpoint,
        canonical_reconciliation,
    )
    affected.update(reconciliation_affected)
    reasons = authority_reasons | reconciliation_reasons

    conflict_reasons = {
        SandboxRecoveryReason.AUTHORITY_EVIDENCE_CONFLICT,
        SandboxRecoveryReason.RECONCILIATION_SNAPSHOT_CONFLICT,
        SandboxRecoveryReason.PENDING_REPORT_INVENTORY_CONFLICT,
    }
    if reasons & conflict_reasons:
        disposition = SandboxRecoveryDisposition.STATE_CONFLICT
    elif reasons:
        disposition = SandboxRecoveryDisposition.RECONCILIATION_REQUIRED
    elif (
        canonical_reconciliation.status is SandboxReconciliationStatus.RECONCILED
        and not canonical_reconciliation.pending_report_ids
        and bool(canonical_checkpoint.snapshot.orders)
        and all(
            order.observed_state.status in TERMINAL_ORDER_STATUSES
            and order.venue_state.status in TERMINAL_ORDER_STATUSES
            for order in canonical_checkpoint.snapshot.orders
        )
    ):
        disposition = SandboxRecoveryDisposition.ALREADY_SETTLED
        reasons.add(SandboxRecoveryReason.ORDERS_ALREADY_SETTLED)
    else:
        disposition = SandboxRecoveryDisposition.SAFE_TO_RESTORE
        reasons.add(
            SandboxRecoveryReason.DELIVERY_PENDING
            if canonical_reconciliation.status
            is SandboxReconciliationStatus.DELIVERY_PENDING
            else SandboxRecoveryReason.RECOVERY_EVIDENCE_COMPLETE
        )

    return SandboxRecoveryDecision(
        disposition=disposition,
        checkpoint_id=canonical_checkpoint.checkpoint_id,
        checkpoint_digest=canonical_checkpoint.digest,
        reconciliation_digest=canonical_reconciliation.digest,
        reason_codes=_ordered_recovery_reasons(reasons),
        affected_order_ids=tuple(sorted(affected, key=lambda order_id: order_id.int)),
    )


__all__ = [
    "SandboxRecoveryCheckpoint",
    "SandboxRecoveryDecision",
    "SandboxRecoveryDisposition",
    "SandboxRecoveryMalformedInput",
    "SandboxRecoveryReason",
    "SandboxSubmitCustody",
    "plan_sandbox_recovery",
]
