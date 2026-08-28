"""Semantic projection kept separate from immutable raw event identity."""

from __future__ import annotations

import hashlib

from packages.engine_contracts import canonical_json_bytes

from .events import P1Event


_CUSTODY_ONLY_FIELDS = {"native_fill_id", "native_order_id"}


def semantic_projection(events: tuple[P1Event, ...]) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            key: value
            for key, value in event.model_dump(mode="json").items()
            if key not in _CUSTODY_ONLY_FIELDS
        }
        for event in events
    )


def semantic_digest(events: tuple[P1Event, ...]) -> str:
    return hashlib.sha256(canonical_json_bytes(semantic_projection(events))).hexdigest()
