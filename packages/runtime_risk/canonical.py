"""Canonical JSON identity for strict runtime-risk contracts."""

from __future__ import annotations

from collections.abc import Mapping
import json
from hashlib import sha256

from pydantic import BaseModel, ValidationError


def _rebuild_python_value(value: object) -> object:
    """Turn nested Pydantic models into mappings without coercing primitives."""

    if isinstance(value, BaseModel):
        return {
            name: _rebuild_python_value(getattr(value, name))
            for name in type(value).model_fields
        }
    if isinstance(value, Mapping):
        return {key: _rebuild_python_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_rebuild_python_value(item) for item in value)
    if isinstance(value, list):
        return [_rebuild_python_value(item) for item in value]
    if isinstance(value, set):
        return {_rebuild_python_value(item) for item in value}
    if isinstance(value, frozenset):
        return frozenset(_rebuild_python_value(item) for item in value)
    return value


def canonical_model_json(value: BaseModel) -> str:
    """Return a stable, validated JSON representation of a Pydantic model."""

    if not isinstance(value, BaseModel):
        raise ValueError("value must be a Pydantic model")
    try:
        canonical = type(value).model_validate(_rebuild_python_value(value))
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
