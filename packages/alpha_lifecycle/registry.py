"""Append-only, content-addressed AlphaRegistry foundation."""

from __future__ import annotations

from enum import Enum
import hashlib
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import CanonicalUtcDateTime, Sha256Hex, canonical_json_bytes


class AlphaRegistryError(ValueError):
    """An alpha lifecycle record would fork or weaken immutable lineage."""


class AlphaLifecycleStatus(str, Enum):
    IDEA = "IDEA"
    CANDIDATE = "CANDIDATE"
    RESEARCHED = "RESEARCHED"
    OOS_PASS = "OOS_PASS"
    QUALIFIED = "QUALIFIED"
    PAPER_OBSERVED = "PAPER_OBSERVED"
    REJECTED = "REJECTED"
    RETIRED = "RETIRED"


class QualificationDecision(str, Enum):
    NOT_EVALUATED = "NOT_EVALUATED"
    PASS = "PASS"
    FAIL = "FAIL"


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, revalidate_instances="always")


class AlphaRecordV1(_Frozen):
    schema_version: Literal["alpha-record-v1"] = "alpha-record-v1"
    alpha_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9._-]{0,63}$")]
    version: Annotated[str, Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")]
    source_sha: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    implementation_identity: Annotated[str, Field(min_length=3, max_length=256)]
    dataset_snapshot_sha256: Sha256Hex
    feature_set: tuple[Annotated[str, Field(min_length=1, max_length=64)], ...] = Field(
        min_length=1, max_length=256
    )
    parameter_set_sha256: Sha256Hex
    training_start_at: CanonicalUtcDateTime
    training_end_at: CanonicalUtcDateTime
    validation_start_at: CanonicalUtcDateTime
    validation_end_at: CanonicalUtcDateTime
    oos_start_at: CanonicalUtcDateTime
    oos_end_at: CanonicalUtcDateTime
    universe: tuple[Annotated[str, Field(min_length=1, max_length=64)], ...] = Field(
        min_length=1, max_length=128
    )
    cost_model_sha256: Sha256Hex
    baseline_id: Annotated[str, Field(pattern=r"^B[0-9]_[A-Z_]+$")]
    baseline_version: Annotated[str, Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")]
    metrics_sha256: Sha256Hex | None
    robustness_sha256: Sha256Hex | None
    qualification_decision: QualificationDecision
    qualification_reason: Annotated[str, Field(min_length=1, max_length=512)]
    artifact_digests: tuple[Sha256Hex, ...] = Field(min_length=1, max_length=256)
    lineage: tuple[Annotated[str, Field(min_length=1, max_length=128)], ...] = Field(
        min_length=1, max_length=128
    )
    superseded_version: Annotated[
        str, Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    ] | None
    lifecycle_status: AlphaLifecycleStatus

    @model_validator(mode="after")
    def _canonical(self) -> "AlphaRecordV1":
        if not (
            self.training_start_at <= self.training_end_at < self.validation_start_at
            <= self.validation_end_at < self.oos_start_at <= self.oos_end_at
        ):
            raise ValueError("alpha research periods must be ordered and disjoint")
        for name in ("feature_set", "universe", "artifact_digests", "lineage"):
            values = getattr(self, name)
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{name} must be sorted and unique")
        if self.lifecycle_status in {
            AlphaLifecycleStatus.QUALIFIED,
            AlphaLifecycleStatus.PAPER_OBSERVED,
        } and (
            self.qualification_decision is not QualificationDecision.PASS
            or self.metrics_sha256 is None
            or self.robustness_sha256 is None
        ):
            raise ValueError("qualified alpha records require PASS metrics and robustness")
        if (
            self.lifecycle_status is AlphaLifecycleStatus.REJECTED
            and self.qualification_decision is not QualificationDecision.FAIL
        ):
            raise ValueError("rejected alpha records require a FAIL decision")
        return self


class AlphaRegistryEventV1(_Frozen):
    schema_version: Literal["alpha-registry-event-v1"] = "alpha-registry-event-v1"
    sequence: Annotated[int, Field(ge=1)]
    predecessor_sha256: Sha256Hex | None
    record: AlphaRecordV1
    event_sha256: Sha256Hex
    artifact: ArtifactRefV1

    @model_validator(mode="after")
    def _bound(self) -> "AlphaRegistryEventV1":
        payload = {
            "predecessor_sha256": self.predecessor_sha256,
            "record": self.record,
            "schema_version": self.schema_version,
            "sequence": self.sequence,
        }
        digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        if digest != self.event_sha256 or self.artifact.content_sha256 != digest:
            raise ValueError("registry event artifact is not hash-bound")
        return self


_TRANSITIONS = {
    AlphaLifecycleStatus.IDEA: {AlphaLifecycleStatus.CANDIDATE, AlphaLifecycleStatus.REJECTED, AlphaLifecycleStatus.RETIRED},
    AlphaLifecycleStatus.CANDIDATE: {AlphaLifecycleStatus.RESEARCHED, AlphaLifecycleStatus.REJECTED, AlphaLifecycleStatus.RETIRED},
    AlphaLifecycleStatus.RESEARCHED: {AlphaLifecycleStatus.OOS_PASS, AlphaLifecycleStatus.REJECTED, AlphaLifecycleStatus.RETIRED},
    AlphaLifecycleStatus.OOS_PASS: {AlphaLifecycleStatus.QUALIFIED, AlphaLifecycleStatus.REJECTED, AlphaLifecycleStatus.RETIRED},
    AlphaLifecycleStatus.QUALIFIED: {AlphaLifecycleStatus.PAPER_OBSERVED, AlphaLifecycleStatus.RETIRED},
    AlphaLifecycleStatus.PAPER_OBSERVED: {AlphaLifecycleStatus.RETIRED},
    AlphaLifecycleStatus.REJECTED: {AlphaLifecycleStatus.RETIRED},
    AlphaLifecycleStatus.RETIRED: set(),
}
_IDENTITY_FIELDS = (
    "alpha_id",
    "version",
    "source_sha",
    "implementation_identity",
    "dataset_snapshot_sha256",
    "feature_set",
    "parameter_set_sha256",
    "training_start_at",
    "training_end_at",
    "validation_start_at",
    "validation_end_at",
    "oos_start_at",
    "oos_end_at",
    "universe",
    "cost_model_sha256",
    "baseline_id",
    "baseline_version",
    "lineage",
    "superseded_version",
)


class AlphaRegistry:
    def __init__(
        self,
        store: LocalArtifactStore,
        history: tuple[AlphaRegistryEventV1, ...] = (),
    ) -> None:
        self._store = store
        self._heads: dict[tuple[str, str], AlphaRegistryEventV1] = {}
        self._events: list[AlphaRegistryEventV1] = []
        for supplied in history:
            value = AlphaRegistryEventV1.model_validate(supplied)
            payload = {
                "predecessor_sha256": value.predecessor_sha256,
                "record": value.record,
                "schema_version": value.schema_version,
                "sequence": value.sequence,
            }
            if self._store.read_bytes(value.artifact) != canonical_json_bytes(payload):
                raise AlphaRegistryError("registry history artifact bytes are invalid")
            if self.append(
                value.record, predecessor_sha256=value.predecessor_sha256
            ) != value:
                raise AlphaRegistryError("registry history replay is not exact")

    def head(self, alpha_id: str, version: str) -> AlphaRegistryEventV1 | None:
        return self._heads.get((alpha_id, version))

    def export_history(self) -> tuple[AlphaRegistryEventV1, ...]:
        return tuple(self._events)

    def append(
        self,
        record: AlphaRecordV1,
        *,
        predecessor_sha256: str | None,
    ) -> AlphaRegistryEventV1:
        value = AlphaRecordV1.model_validate(record)
        key = (value.alpha_id, value.version)
        head = self._heads.get(key)
        if head is None:
            if predecessor_sha256 is not None or value.lifecycle_status is not AlphaLifecycleStatus.IDEA:
                raise AlphaRegistryError("new alpha versions must start at IDEA without a predecessor")
            sequence = 1
        else:
            if predecessor_sha256 != head.event_sha256:
                raise AlphaRegistryError("registry predecessor does not match the current head")
            if value.lifecycle_status not in _TRANSITIONS[head.record.lifecycle_status]:
                raise AlphaRegistryError("alpha lifecycle transition is not allowed")
            if any(getattr(value, name) != getattr(head.record, name) for name in _IDENTITY_FIELDS):
                raise AlphaRegistryError("alpha version identity cannot drift across lifecycle events")
            sequence = head.sequence + 1
        payload = {
            "predecessor_sha256": predecessor_sha256,
            "record": value,
            "schema_version": "alpha-registry-event-v1",
            "sequence": sequence,
        }
        artifact = self._store.put_bytes(
            canonical_json_bytes(payload), media_type="application/json"
        )
        event = AlphaRegistryEventV1(
            sequence=sequence,
            predecessor_sha256=predecessor_sha256,
            record=value,
            event_sha256=artifact.content_sha256,
            artifact=artifact,
        )
        self._heads[key] = event
        self._events.append(event)
        return event


__all__ = [
    "AlphaLifecycleStatus",
    "AlphaRecordV1",
    "AlphaRegistry",
    "AlphaRegistryError",
    "AlphaRegistryEventV1",
    "QualificationDecision",
]
