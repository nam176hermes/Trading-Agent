"""Canonical SNAPSHOT payload contract."""

from __future__ import annotations

import json
from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict

from .enums import JobType

class SnapshotPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    scope: Literal["default"]
    requested_as_of: None

JobPayload: TypeAlias = SnapshotPayload

def parse_payload(job_type: JobType | str, payload: Any) -> JobPayload:
    try:
        selected_type = JobType(job_type)
    except (TypeError, ValueError) as exc:
        raise ValueError("job type is not allowlisted") from exc
    if selected_type is not JobType.SNAPSHOT:
        raise ValueError("job type is not allowlisted")
    return SnapshotPayload.model_validate(payload)

def canonical_payload_json(payload: JobPayload) -> str:
    return json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)

def payload_fingerprint(payload: JobPayload) -> str:
    import hashlib
    return hashlib.sha256(canonical_payload_json(payload).encode("utf-8")).hexdigest()

__all__ = ["JobPayload", "SnapshotPayload", "canonical_payload_json", "parse_payload", "payload_fingerprint"]
