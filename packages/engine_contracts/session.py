"""Stable identity for a long-lived engine session port."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from .serialization import Sha256Hex


class EngineSessionIdentityV1(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )

    schema_version: Literal["engine-session-identity-v1"] = (
        "engine-session-identity-v1"
    )
    runtime_family: Annotated[str, Field(min_length=1, max_length=64)]
    engine_version: Annotated[str, Field(min_length=1, max_length=64)]
    engine_upstream_commit: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    closure_digest: Sha256Hex
    request_protocol: Annotated[str, Field(min_length=1, max_length=128)]
    event_schema: Annotated[str, Field(min_length=1, max_length=128)]
    paper_schema: Annotated[str, Field(min_length=1, max_length=128)]


__all__ = ["EngineSessionIdentityV1"]
