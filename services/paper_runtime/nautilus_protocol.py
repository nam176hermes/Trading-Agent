"""Shared canonical paper response conversions."""

from __future__ import annotations

from typing import Protocol
import json

from packages.engine_contracts import EngineEvent, EventAttribute, canonical_json_bytes
from packages.nautilus_runtime_contracts.events import P1Event
from packages.nautilus_runtime_contracts.result import P1_EVENT_FAMILIES

from .nautilus_checkpoint import NautilusCheckpointRecord


class NautilusRecoveryRecorder(Protocol):
    def record(
        self,
        command_raw: bytes,
        checkpoint: NautilusCheckpointRecord,
        *,
        engine_version: str,
        closure_digest: str,
        source_commit: str,
        config_digest: str,
    ) -> None: ...


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise ValueError("paper response contains a duplicate key")
        result[key] = value
    return result


def paper_response_object(raw: bytes) -> dict[str, object]:
    value = json.loads(raw, object_pairs_hook=_pairs)
    if type(value) is not dict or canonical_json_bytes(value) != raw:
        raise ValueError("paper response is not canonical JSON")
    return value


def paper_event_payload(event: P1Event) -> EngineEvent:
    document = event.model_dump(mode="json")
    attributes: list[EventAttribute] = []
    for name, value in document.items():
        if name == "event_type" or value is None:
            continue
        if type(value) is list:
            value = canonical_json_bytes(value).decode("utf-8")
        attributes.append(EventAttribute(name=name, value=value))
    return EngineEvent(
        event_type=event.event_type,
        family=P1_EVENT_FAMILIES[event.event_type],
        attributes=tuple(attributes),
    )
