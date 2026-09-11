"""Closed private transport for the P3 database publication capability."""

from __future__ import annotations

import json
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BeforeValidator, Field, model_validator

from packages.alpha_lifecycle.contracts.base import StrictModel, Text
from packages.alpha_lifecycle.contracts.lifecycle import PublicationRequest
from packages.alpha_lifecycle.registry import AlphaRecordV1
from packages.data_contracts import ArtifactRefV1
from packages.domain.alpha_events import AlphaRegistryTransitionRecordedV1
from packages.domain.events import EventEnvelope
from packages.engine_contracts.serialization import canonical_json_bytes


_MAX_TRANSPORT_BYTES = 1_048_576
_MAX_OUTBOX_BYTES = 65_536


def _canonical_json_text(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("canonical JSON must be text")
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError("canonical JSON text is invalid") from error
    if canonical_json_bytes(decoded).decode() != value:
        raise ValueError("JSON text is not canonical")
    return value


CanonicalJsonText = Annotated[str, BeforeValidator(_canonical_json_text)]


def _tuple(value: object) -> tuple[object, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("entries must be a JSON array")
    return tuple(value)


class DomainAppendEntry(StrictModel):
    event_id: UUID
    stream_id: UUID
    sequence: Annotated[int, Field(ge=1)]
    event_type: Literal["AlphaRegistryTransitionRecordedV1"]
    canonical_event_text: CanonicalJsonText
    topic: Literal["p3.alpha-registry"]
    outbox_payload_text: CanonicalJsonText

    @model_validator(mode="after")
    def _matches_registered_envelope(self) -> "DomainAppendEntry":
        envelope = EventEnvelope[AlphaRegistryTransitionRecordedV1].model_validate_json(
            self.canonical_event_text
        )
        registry_value = json.loads(envelope.payload.registry_event_text)
        if (
            not isinstance(registry_value, dict)
            or set(registry_value) != {
                "predecessor_sha256", "record", "schema_version", "sequence"
            }
            or registry_value["schema_version"] != "alpha-registry-event-v1"
        ):
            raise ValueError("registry event has an unknown shape")
        AlphaRecordV1.model_validate_json(canonical_json_bytes(registry_value["record"]))
        if (
            envelope.event_id != self.event_id
            or envelope.stream_id != self.stream_id
            or envelope.sequence != self.sequence
            or envelope.event_type != self.event_type
            or envelope.payload.registry_sequence != self.sequence
        ):
            raise ValueError("append metadata does not match the registered envelope")
        if len(self.outbox_payload_text.encode("utf-8")) > _MAX_OUTBOX_BYTES:
            raise ValueError("outbox payload exceeds 64 KiB")
        return self


class PublicationTransport(StrictModel):
    job_id: Text
    attempt_id: Text
    worker_id: Text
    lease_token: Text
    request: PublicationRequest
    output_inventory_ref: ArtifactRefV1 | None = None
    entries: Annotated[
        tuple[DomainAppendEntry, ...], BeforeValidator(_tuple), Field(min_length=1, max_length=8)
    ]

    @model_validator(mode="after")
    def _matches_request(self) -> "PublicationTransport":
        if self.job_id != self.request.job_id:
            raise ValueError("transport job does not match publication request")
        if len(self.entries) != len(self.request.proposed_event_refs):
            raise ValueError("publication artifact count differs from entries")
        for entry, ref in zip(self.entries, self.request.proposed_event_refs, strict=True):
            payload = EventEnvelope[AlphaRegistryTransitionRecordedV1].model_validate_json(
                entry.canonical_event_text
            ).payload
            if (
                payload.evidence_sha256 != self.request.evidence_ref.content_sha256
                or ref.content_sha256 != payload.registry_event_sha256
                or ref.size_bytes != len(payload.registry_event_text.encode("utf-8"))
                or ref.media_type != "application/json"
            ):
                raise ValueError("publication artifact does not match its ordered entry")
        if len(canonical_json_bytes(self)) > _MAX_TRANSPORT_BYTES:
            raise ValueError("publication transport exceeds 1 MiB")
        return self

    def canonical_bytes(self) -> bytes:
        # Only the historical disposable 0020 fixture uses the six-field transport.
        return canonical_json_bytes(self.model_dump(mode="json",exclude={"output_inventory_ref"} if self.output_inventory_ref is None else set()))

    @classmethod
    def from_canonical_bytes(cls, raw: bytes) -> "PublicationTransport":
        if not isinstance(raw, bytes) or len(raw) > _MAX_TRANSPORT_BYTES:
            raise ValueError("publication transport exceeds 1 MiB")
        value = cls.model_validate_json(raw)
        if value.canonical_bytes() != raw:
            raise ValueError("publication transport is not canonical JSON")
        return value


class PublicationProposal(StrictModel):
    """Unprivileged child output; lease authority is added only by the worker."""

    request: PublicationRequest
    entries: Annotated[
        tuple[DomainAppendEntry, ...], BeforeValidator(_tuple), Field(min_length=1, max_length=8)
    ]


__all__ = ["CanonicalJsonText", "DomainAppendEntry", "PublicationProposal", "PublicationTransport"]
