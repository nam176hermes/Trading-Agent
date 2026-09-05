"""Pure canonical event payload for one planned alpha registry transition."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class AlphaRegistryTransitionRecordedV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, revalidate_instances="always")

    schema_version: Literal["alpha-registry-transition-recorded-v1"] = "alpha-registry-transition-recorded-v1"
    epoch_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9._-]{0,127}$")]
    alpha_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9._-]{0,63}$")]
    alpha_version: Annotated[str, Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")]
    registry_sequence: Annotated[int, Field(ge=1)]
    predecessor_sha256: Sha256Hex | None
    registry_event_sha256: Sha256Hex
    registry_event_text: Annotated[str, Field(min_length=2, max_length=262_144)]
    evidence_sha256: Sha256Hex

    @model_validator(mode="after")
    def _registry_event_is_canonical_and_bound(self) -> "AlphaRegistryTransitionRecordedV1":
        try:
            value = json.loads(self.registry_event_text)
        except json.JSONDecodeError as error:
            raise ValueError("registry event text is invalid JSON") from error
        raw = json.dumps(
            value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        if raw.decode() != self.registry_event_text:
            raise ValueError("registry event text is not canonical JSON")
        if hashlib.sha256(raw).hexdigest() != self.registry_event_sha256:
            raise ValueError("registry event text does not match its digest")
        if not isinstance(value, dict) or value.get("sequence") != self.registry_sequence:
            raise ValueError("registry event sequence does not match payload")
        if value.get("predecessor_sha256") != self.predecessor_sha256:
            raise ValueError("registry event predecessor does not match payload")
        record = value.get("record")
        if not isinstance(record, dict) or (
            record.get("alpha_id") != self.alpha_id
            or record.get("version") != self.alpha_version
        ):
            raise ValueError("registry event identity does not match payload")
        return self


__all__ = ["AlphaRegistryTransitionRecordedV1"]
