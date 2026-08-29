"""Canonical all-or-completion-last JSONL output."""

from __future__ import annotations

import hashlib
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
    raw_sha256 = stream.raw_sha256
    semantic_sha256 = stream.semantic_sha256
    if (
        type(events) is not tuple
        or type(envelopes) is not tuple
        or type(raw) is not bytes
        or type(raw_sha256) is not str
        or type(semantic_sha256) is not str
    ):
        raise ValueError("P1 event stream is not write-ready")
    validate_projected_stream(
        events,
        envelopes,
        stream.request_message_id,
        stream.request_authority,
    )
    if (
        hashlib.sha256(raw).hexdigest() != raw_sha256
        or events[-1].get("semantic_digest") != semantic_sha256
    ):
        raise ValueError("P1 event stream digest is invalid")
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
