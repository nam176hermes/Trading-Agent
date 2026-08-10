"""Canonical JSON identity for strict runtime-risk contracts."""

from __future__ import annotations

import json
from hashlib import sha256

from pydantic import BaseModel, ValidationError


def canonical_model_json(value: BaseModel) -> str:
    """Return a stable, validated JSON representation of a Pydantic model."""

    if not isinstance(value, BaseModel):
        raise ValueError("value must be a Pydantic model")
    try:
        fields = {name: getattr(value, name) for name in type(value).model_fields}
        canonical = type(value).model_validate(fields)
        document = canonical.model_dump(mode="json")
    except (AttributeError, TypeError, ValidationError, ValueError) as exc:
        raise ValueError("model cannot be canonically represented") from exc
    return json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def canonical_model_digest(value: BaseModel) -> str:
    """Return the lowercase SHA-256 digest of canonical model JSON."""

    return sha256(canonical_model_json(value).encode("utf-8")).hexdigest()
