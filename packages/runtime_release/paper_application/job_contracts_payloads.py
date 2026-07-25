"""Canonical SNAPSHOT payload contract."""

from __future__ import annotations

import json
from typing import Annotated, Any, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter

from .enums import JobType

AssetSymbol = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32, pattern=r"^[A-Z0-9][A-Z0-9._:/-]*$")]

class SnapshotPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    assets: tuple[AssetSymbol, ...] = Field(min_length=1, max_length=256)
    include_debate: bool = True

JobPayload: TypeAlias = SnapshotPayload
_PAYLOAD_ADAPTER = TypeAdapter(JobPayload)

def parse_payload(job_type: JobType, payload: Any) -> JobPayload:
    if job_type is not JobType.SNAPSHOT:
        raise ValueError("job type is not allowlisted")
    return SnapshotPayload.model_validate(payload)

def canonical_payload_json(payload: JobPayload) -> str:
    return json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)

def payload_fingerprint(payload: JobPayload) -> str:
    import hashlib
    return hashlib.sha256(canonical_payload_json(payload).encode("utf-8")).hexdigest()

__all__ = ["AssetSymbol", "JobPayload", "SnapshotPayload", "canonical_payload_json", "parse_payload", "payload_fingerprint"]
