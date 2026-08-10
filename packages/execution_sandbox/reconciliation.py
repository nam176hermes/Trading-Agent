"""Canonical ingress helpers for deterministic execution reconciliation."""

from __future__ import annotations

from uuid import UUID

from packages.domain.events import EventEnvelope
from packages.domain.orders import FillEvent, OrderEvent
from packages.event_ledger.replay import deserialize_event, serialize_event

from .models import SandboxReconciliationError, SandboxReconciliationRequest


def _canonical_envelope(value: object) -> EventEnvelope[object]:
    if not isinstance(value, EventEnvelope):
        raise SandboxReconciliationError("invalid observed envelope")
    canonical = deserialize_event(serialize_event(value))
    if type(canonical.payload) not in (OrderEvent, FillEvent):
        raise SandboxReconciliationError("invalid observed envelope")
    return canonical


def _canonical_request(value: object) -> SandboxReconciliationRequest:
    if type(value) is not SandboxReconciliationRequest:
        raise SandboxReconciliationError("invalid reconciliation request")
    return SandboxReconciliationRequest.model_validate(value.model_dump(mode="python"))


def _observed_by_event_id(
    reports: tuple[EventEnvelope[object], ...],
) -> dict[UUID, EventEnvelope[object]]:
    observed: dict[UUID, EventEnvelope[object]] = {}
    for report in reports:
        canonical = _canonical_envelope(report)
        prior = observed.get(canonical.event_id)
        if prior is not None and serialize_event(prior) != serialize_event(canonical):
            raise SandboxReconciliationError("conflicting observed event")
        observed[canonical.event_id] = canonical
    return observed
