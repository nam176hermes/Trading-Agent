"""Canonical all-or-completion-last JSONL output."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable

from .generated_protocol import canonical_json_bytes


def encode_jsonl(envelopes: Iterable[dict[str, object]]) -> bytes:
    batch = tuple(envelopes)
    if not batch or len(batch) > 4096:
        raise ValueError("P1 event stream size is invalid")
    return b"".join(canonical_json_bytes(item) + b"\n" for item in batch)


def write_jsonl(
    stream: object, *, fd: int = 1, writer: Callable[[int, bytes], int] = os.write
) -> int:
    """Validate the complete buffer, then write it with completion last."""

    from .event_projector import validate_projected_stream

    events = stream.events
    envelopes = stream.envelopes
    raw = stream.jsonl
    if (
        type(events) is not tuple
        or type(envelopes) is not tuple
        or type(raw) is not bytes
    ):
        raise ValueError("P1 event stream is not write-ready")
    validate_projected_stream(events, envelopes)
    if encode_jsonl(envelopes) != raw:
        raise ValueError("P1 event stream is not write-ready")
    offset = 0
    while offset < len(raw):
        written = writer(fd, raw[offset:])
        if type(written) is not int or written <= 0 or written > len(raw) - offset:
            raise OSError("event stream writer made no valid progress")
        offset += written
    return offset


__all__ = ["encode_jsonl", "write_jsonl"]
