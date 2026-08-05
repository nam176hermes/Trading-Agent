"""Canonical SNAPSHOT payload contract."""

from __future__ import annotations

import json
from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, model_validator

from packages.engine_contracts import ArtifactReference, CanonicalUtcDateTime

from .enums import JobType

class SnapshotPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    scope: Literal["default"]
    requested_as_of: None

class EngineBacktestInput(BaseModel):
    """Import-only worker contract; it is not an enqueueable paper job type."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    engine_configuration: ArtifactReference
    instrument_catalog: ArtifactReference
    strategy_configuration: ArtifactReference
    market_data: ArtifactReference
    start_time: CanonicalUtcDateTime
    end_time: CanonicalUtcDateTime

    @model_validator(mode="after")
    def _validate_window(self) -> "EngineBacktestInput":
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self

class EngineBacktestPayload(BaseModel):
    """Worker-side shape without granting BACKTEST enqueue authority."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    engine_backtest: EngineBacktestInput

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

__all__ = [
    "EngineBacktestInput", "EngineBacktestPayload", "JobPayload", "SnapshotPayload",
    "canonical_payload_json", "parse_payload", "payload_fingerprint",
]
