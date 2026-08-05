"""Canonical JSON and SHA-256 helpers for engine protocol payloads."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    BeforeValidator,
    Field,
    PlainSerializer,
    WithJsonSchema,
)


_CANONICAL_UTC_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$",
    re.ASCII,
)
_CANONICAL_UTC_JSON_SCHEMA = {
    "type": "string",
    "format": "date-time",
    "pattern": r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$",
}


def _canonical_utc(value: Any) -> datetime:
    if isinstance(value, str):
        if _CANONICAL_UTC_PATTERN.fullmatch(value) is None:
            raise ValueError("timestamp must be canonical RFC 3339 UTC with a Z suffix")
        try:
            value = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
        except ValueError as exc:
            raise ValueError("timestamp must be a valid canonical UTC instant") from exc
    if not isinstance(value, datetime):
        raise ValueError("timestamp must be a UTC datetime")
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be a timezone-aware UTC datetime")
    if value.tzinfo is not UTC:
        raise ValueError("timestamp must use the canonical UTC timezone")
    return value


def _serialize_utc(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


CanonicalUtcDateTime = Annotated[
    datetime,
    BeforeValidator(_canonical_utc),
    PlainSerializer(_serialize_utc, return_type=str, when_used="json"),
    WithJsonSchema(_CANONICAL_UTC_JSON_SCHEMA, mode="validation"),
    WithJsonSchema(_CANONICAL_UTC_JSON_SCHEMA, mode="serialization"),
]
Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
SourceCommit = Annotated[
    str,
    Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$"),
]
ProducerIdentity = Annotated[
    str,
    Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$",
    ),
]


def _canonical_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _canonical_value(value.model_dump(mode="json"))
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        raise ValueError("float values are not canonical engine JSON")
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("canonical engine JSON object keys must be strings")
        return {
            key: _canonical_value(item)
            for key, item in sorted(value.items(), key=lambda pair: pair[0])
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_canonical_value(item) for item in value]
    raise ValueError(f"unsupported canonical engine JSON value: {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize supported values to deterministic UTF-8 JSON bytes."""

    canonical = _canonical_value(value)
    return json.dumps(
        canonical,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_json(value: Any) -> str:
    """Serialize supported values to deterministic JSON text."""

    return canonical_json_bytes(value).decode("utf-8")


def payload_digest(value: Any) -> str:
    """Return the lowercase SHA-256 digest of canonical payload bytes."""

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()
