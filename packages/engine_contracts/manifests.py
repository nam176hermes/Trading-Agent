"""Immutable content-addressed engine run manifest contracts."""

from __future__ import annotations

from enum import Enum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from .commands import CommandName
from .serialization import (
    CanonicalUtcDateTime,
    ProducerIdentity,
    Sha256Hex,
    SourceCommit,
)
from .versions import SchemaVersion


class TransportArtifact(str, Enum):
    """Closed filenames used by the immutable file transport."""

    REQUEST = "request.json"
    REQUEST_DIGEST = "request.sha256"
    EVENTS = "events.jsonl"
    RESULT = "result.json"
    MANIFEST = "manifest.json"
    STDOUT = "stdout.log"
    STDERR = "stderr.log"


class ManifestModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )


class ManifestArtifact(ManifestModel):
    """Digest and size for one bounded run artifact."""

    name: TransportArtifact
    sha256: Sha256Hex
    size_bytes: Annotated[StrictInt, Field(ge=0, le=1_073_741_824)]


class EngineRunManifest(ManifestModel):
    """Final, canonical inventory of a completed engine run."""

    schema_version: SchemaVersion
    engine_run_id: UUID
    command_type: CommandName
    started_at: CanonicalUtcDateTime
    completed_at: CanonicalUtcDateTime
    producer_identity: ProducerIdentity
    source_commit: SourceCommit
    config_digest: Sha256Hex
    artifacts: tuple[ManifestArtifact, ...] = Field(max_length=7)

    @model_validator(mode="after")
    def _validate_manifest(self) -> "EngineRunManifest":
        if self.completed_at < self.started_at:
            raise ValueError("completed_at must not be before started_at")
        names: set[TransportArtifact] = set()
        for artifact in self.artifacts:
            if artifact.name in names:
                raise ValueError(f"duplicate manifest artifact: {artifact.name.value}")
            names.add(artifact.name)
        return self
