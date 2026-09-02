"""Canonical SHA-256 functions for operator contracts."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping

from pydantic import BaseModel

from packages.engine_contracts.serialization import canonical_json_bytes

from .contracts import OperatorActorV1, SubmitOperatorCommandV1


def _without(payload: Mapping[str, object], field: str) -> dict[str, object]:
    return {key: value for key, value in payload.items() if key != field}


def _digest(payload: object) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def request_sha256(actor: OperatorActorV1, request: SubmitOperatorCommandV1) -> str:
    return _digest(
        {
            "actor": actor,
            "command": request.command,
            "expected_state_sha256": request.expected_state_sha256,
        }
    )


def state_sha256(payload: Mapping[str, object]) -> str:
    return _digest(_without(payload, "state_sha256"))


def evidence_sha256(payload: Mapping[str, object]) -> str:
    return _digest(_without(payload, "evidence_sha256"))


def journal_sha256(payload: BaseModel, digest_field: str) -> str:
    if digest_field not in type(payload).model_fields:
        raise ValueError(f"unknown journal digest field: {digest_field}")
    return _digest(_without(payload.model_dump(mode="json"), digest_field))


def idempotency_key_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def reason_sha256(value: str) -> str:
    return hashlib.sha256(value.strip().encode("utf-8")).hexdigest()
