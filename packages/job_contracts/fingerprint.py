"""Bounded canonical JSON and payload fingerprints."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel


MAX_CANONICAL_PAYLOAD_BYTES = 8 * 1024


class PayloadTooLarge(ValueError):
    """Raised when canonical payload data exceeds the contract boundary."""


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("payload must contain only canonical JSON values") from exc


def _ensure_size(canonical: str) -> str:
    size = len(canonical.encode("utf-8"))
    if size > MAX_CANONICAL_PAYLOAD_BYTES:
        raise PayloadTooLarge(
            f"canonical payload exceeds {MAX_CANONICAL_PAYLOAD_BYTES} bytes"
        )
    return canonical


def validate_canonical_input_size(value: Any) -> None:
    """Reject oversized request data before schema validation."""

    _ensure_size(_canonical_json(value))


def canonical_payload_json(model: BaseModel) -> str:
    """Return the stable compact representation used for persistence and hashing."""

    return _ensure_size(_canonical_json(model.model_dump(mode="json")))


def payload_fingerprint(model: BaseModel) -> str:
    """Return a lowercase SHA-256 hex digest of a validated payload."""

    canonical = canonical_payload_json(model)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
