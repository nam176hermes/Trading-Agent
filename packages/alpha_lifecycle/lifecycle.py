"""Pure reconstruction of proposed alpha registry transitions."""

from __future__ import annotations

import hashlib

from packages.alpha_lifecycle.contracts.lifecycle import PrePublicationEvidence
from packages.alpha_lifecycle.registry import (
    AlphaLifecycleStatus, AlphaRecordV1, AlphaRegistryError, AlphaRegistryEventV1,
    _IDENTITY_FIELDS, _TRANSITIONS,
)
from packages.data_contracts import ArtifactRefV1
from packages.domain.alpha_events import AlphaRegistryTransitionRecordedV1
from packages.engine_contracts.serialization import canonical_json_bytes


_STAGE_TARGETS = {
    "REGISTER": {AlphaLifecycleStatus.IDEA, AlphaLifecycleStatus.CANDIDATE},
    "RESEARCH_DECISION": {
        AlphaLifecycleStatus.RESEARCHED, AlphaLifecycleStatus.OOS_PASS,
        AlphaLifecycleStatus.REJECTED,
    },
    "EXIT_DECISION": {AlphaLifecycleStatus.QUALIFIED, AlphaLifecycleStatus.REJECTED},
}


def plan_transition(
    record: AlphaRecordV1,
    head: AlphaRegistryEventV1 | None,
    evidence: PrePublicationEvidence,
) -> AlphaRegistryEventV1:
    record = AlphaRecordV1.model_validate(record)
    evidence = PrePublicationEvidence.model_validate(evidence)
    if record.lifecycle_status not in _STAGE_TARGETS[evidence.stage]:
        raise AlphaRegistryError("lifecycle transition does not match publication stage")
    if head is None:
        if record.lifecycle_status is not AlphaLifecycleStatus.IDEA:
            raise AlphaRegistryError("new alpha versions must start at IDEA")
        sequence, predecessor = 1, None
    else:
        head = AlphaRegistryEventV1.model_validate(head)
        if record.lifecycle_status not in _TRANSITIONS[head.record.lifecycle_status]:
            raise AlphaRegistryError("alpha lifecycle transition is not allowed")
        if any(getattr(record, name) != getattr(head.record, name) for name in _IDENTITY_FIELDS):
            raise AlphaRegistryError("alpha version identity cannot drift across transitions")
        sequence, predecessor = head.sequence + 1, head.event_sha256
    payload = {
        "predecessor_sha256": predecessor, "record": record,
        "schema_version": "alpha-registry-event-v1", "sequence": sequence,
    }
    raw = canonical_json_bytes(payload)
    digest = hashlib.sha256(raw).hexdigest()
    return AlphaRegistryEventV1(
        sequence=sequence, predecessor_sha256=predecessor, record=record,
        event_sha256=digest,
        artifact=ArtifactRefV1(
            content_sha256=digest, size_bytes=len(raw), media_type="application/json",
            locator=f"{digest}.blob",
        ),
    )


def to_domain_payload(
    event: AlphaRegistryEventV1,
    evidence: PrePublicationEvidence,
    *,
    epoch_id: str,
) -> AlphaRegistryTransitionRecordedV1:
    """Embed the exact registry bytes needed for independent SQL validation."""

    event = AlphaRegistryEventV1.model_validate(event)
    evidence = PrePublicationEvidence.model_validate(evidence)
    raw = canonical_json_bytes({
        "predecessor_sha256": event.predecessor_sha256,
        "record": event.record,
        "schema_version": event.schema_version,
        "sequence": event.sequence,
    })
    return AlphaRegistryTransitionRecordedV1(
        epoch_id=epoch_id,
        alpha_id=event.record.alpha_id,
        alpha_version=event.record.version,
        registry_sequence=event.sequence,
        predecessor_sha256=event.predecessor_sha256,
        registry_event_sha256=event.event_sha256,
        registry_event_text=raw.decode(),
        evidence_sha256=hashlib.sha256(canonical_json_bytes(evidence)).hexdigest(),
    )


__all__ = ["plan_transition", "to_domain_payload"]
