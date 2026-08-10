"""Canonical JSON identity for strict runtime-risk contracts."""

from __future__ import annotations

from collections.abc import Mapping
import json
from hashlib import sha256

from pydantic import BaseModel, ValidationError


_MAX_CANONICAL_NESTING = 128


def _rebuild_python_value(
    value: object,
    *,
    active_ids: set[int] | None = None,
    nesting: int = 0,
) -> object:
    """Revalidate models and containers within a bounded nesting depth.

    The root model is at nesting zero. At most 128 traversable child levels are
    accepted so malformed graphs fail deterministically before Python's stack
    limit, while primitive leaves beyond the final container remain valid.
    """

    traversable = isinstance(
        value,
        (BaseModel, Mapping, tuple, list, set, frozenset),
    )
    if not traversable:
        return value
    if nesting > _MAX_CANONICAL_NESTING:
        raise ValueError("model or container nesting exceeds canonical limit")
    active = set() if active_ids is None else active_ids
    identity = id(value)
    if identity in active:
        raise ValueError("recursive model or container graph")
    active.add(identity)
    try:
        if isinstance(value, BaseModel):
            fields = {
                name: _rebuild_python_value(
                    getattr(value, name),
                    active_ids=active,
                    nesting=nesting + 1,
                )
                for name in type(value).model_fields
            }
            return type(value).model_validate(fields)
        if isinstance(value, Mapping):
            return {
                _rebuild_python_value(
                    key,
                    active_ids=active,
                    nesting=nesting + 1,
                ): _rebuild_python_value(
                    item,
                    active_ids=active,
                    nesting=nesting + 1,
                )
                for key, item in value.items()
            }
        if isinstance(value, tuple):
            return tuple(
                _rebuild_python_value(
                    item,
                    active_ids=active,
                    nesting=nesting + 1,
                )
                for item in value
            )
        if isinstance(value, list):
            return [
                _rebuild_python_value(
                    item,
                    active_ids=active,
                    nesting=nesting + 1,
                )
                for item in value
            ]
        if isinstance(value, set):
            return {
                _rebuild_python_value(
                    item,
                    active_ids=active,
                    nesting=nesting + 1,
                )
                for item in value
            }
        return frozenset(
            _rebuild_python_value(
                item,
                active_ids=active,
                nesting=nesting + 1,
            )
            for item in value
        )
    finally:
        active.remove(identity)


def canonical_model_json(value: BaseModel) -> str:
    """Return a stable, validated JSON representation of a Pydantic model."""

    if not isinstance(value, BaseModel):
        raise ValueError("value must be a Pydantic model")
    try:
        canonical = _rebuild_python_value(value)
        if not isinstance(canonical, BaseModel):
            raise TypeError("canonical value must be a Pydantic model")
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
