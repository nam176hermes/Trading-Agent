"""Fail-closed promotion types and observational deployment evidence.

This module validates already-collected identity observations.  It deliberately
contains no operating-system reader, collector, publisher, or runtime authority.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from enum import Enum
import json
from pathlib import PurePosixPath
import re
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)


GitObjectId = Annotated[
    StrictStr,
    Field(min_length=40, max_length=40, pattern=r"^[0-9a-f]{40}$"),
]
Sha256Hex = Annotated[
    StrictStr,
    Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"),
]


def _safe_absolute_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value.startswith("/")
        or value.startswith("//")
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts[1:])
    ):
        raise ValueError("path must be a normalized absolute POSIX path")
    return value


SafeAbsolutePath = Annotated[
    StrictStr,
    Field(
        min_length=2,
        max_length=1024,
        pattern=r"^/[A-Za-z0-9._@+=,/-]+$",
    ),
    AfterValidator(_safe_absolute_path),
]
ServiceId = Annotated[
    StrictStr,
    Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9-]*$"),
]
SystemdUnitName = Annotated[
    StrictStr,
    Field(
        min_length=9,
        max_length=255,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.@:-]*\.service$",
    ),
]


_SECRET_KEY_PARTS = frozenset({"credential", "dsn", "password", "secret", "token"})
_SECRET_KEY_NAMES = frozenset(
    {
        "access_key",
        "api_key",
        "database_url",
        "env",
        "environment",
        "private_key",
    }
)
_RFC3339_UTC_PATTERN = (
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?(?:Z|\+00:00)$"
)
_RFC3339_UTC = re.compile(_RFC3339_UTC_PATTERN)


def _contains_secret_like_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")
    return normalized in _SECRET_KEY_NAMES or bool(
        _SECRET_KEY_PARTS.intersection(normalized.split("_"))
    )


def _reject_secret_like_keys(value: object, seen: set[int] | None = None) -> None:
    if seen is None:
        seen = set()
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in seen:
            return
        seen.add(identity)
        for key, child in value.items():
            if isinstance(key, str) and _contains_secret_like_key(key):
                raise ValueError("deployment evidence contains a secret-like key")
            _reject_secret_like_keys(child, seen)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        identity = id(value)
        if identity in seen:
            return
        seen.add(identity)
        for child in value:
            _reject_secret_like_keys(child, seen)


class PromotionDecision(str, Enum):
    """The complete set of recorded production-promotion decisions."""

    NO_GO = "NO_GO"
    GO_PAPER_PRODUCTION = "GO_PAPER_PRODUCTION"
    GO_LIVE_LIMITED = "GO_LIVE_LIMITED"


class EvidenceState(str, Enum):
    """Observed state of one identity link in the deployment chain."""

    VERIFIED = "VERIFIED"
    DRIFTED = "DRIFTED"
    UNAVAILABLE = "UNAVAILABLE"


class StrictEvidenceModel(BaseModel):
    """Closed and immutable base model for publish-safe observations."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
    )

    @model_validator(mode="before")
    @classmethod
    def reject_secret_like_keys(cls, value: Any) -> Any:
        _reject_secret_like_keys(value)
        return value


class SourceIdentity(StrictEvidenceModel):
    repository_root: SafeAbsolutePath
    commit: GitObjectId
    tree: GitObjectId


class ReleaseIdentity(StrictEvidenceModel):
    release_root: SafeAbsolutePath
    manifest_path: SafeAbsolutePath
    manifest_sha256: Sha256Hex
    source_commit: GitObjectId
    source_tree: GitObjectId


class UnitIdentity(StrictEvidenceModel):
    unit_name: SystemdUnitName
    fragment_path: SafeAbsolutePath
    effective_unit_sha256: Sha256Hex
    release_manifest_sha256: Sha256Hex
    command_fingerprint: Sha256Hex


class ProcessIdentity(StrictEvidenceModel):
    """Reuse-safe Linux process identity; a bare PID is never sufficient."""

    pid: Annotated[StrictInt, Field(gt=0)]
    start_ticks: Annotated[StrictInt, Field(gt=0)]
    command_fingerprint: Sha256Hex


class ServiceDeploymentEvidence(StrictEvidenceModel):
    service_id: ServiceId
    release_to_unit: EvidenceState
    unit: UnitIdentity | None
    unit_to_process: EvidenceState
    process: ProcessIdentity | None

    @model_validator(mode="after")
    def validate_unit_to_process_link(self) -> "ServiceDeploymentEvidence":
        if self.release_to_unit is EvidenceState.UNAVAILABLE:
            if (
                self.unit is not None
                or self.unit_to_process is not EvidenceState.UNAVAILABLE
                or self.process is not None
            ):
                raise ValueError("UNAVAILABLE unit link cannot imply an identity")
            return self
        if self.unit is None:
            raise ValueError("available unit link requires a unit identity")

        if self.unit_to_process is EvidenceState.UNAVAILABLE:
            if self.process is not None:
                raise ValueError("UNAVAILABLE process link cannot imply an identity")
            return self
        if self.process is None:
            raise ValueError("available process link requires a process identity")

        matches = self.unit.command_fingerprint == self.process.command_fingerprint
        if self.unit_to_process is EvidenceState.VERIFIED and not matches:
            raise ValueError("VERIFIED unit-to-process link does not match")
        if self.unit_to_process is EvidenceState.DRIFTED and matches:
            raise ValueError("DRIFTED unit-to-process link has no observed mismatch")
        return self


class DeploymentEvidence(StrictEvidenceModel):
    """One observational source-to-release-to-unit-to-process snapshot."""

    schema_version: Literal[1]
    observed_at: Annotated[
        StrictStr,
        Field(
            pattern=_RFC3339_UTC_PATTERN,
            json_schema_extra={"format": "date-time"},
        ),
    ]
    source: SourceIdentity
    source_to_release: EvidenceState
    release: ReleaseIdentity | None
    services: tuple[ServiceDeploymentEvidence, ...]

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_exact_schema_version(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("schema_version must be the integer 1")
        return value

    @field_validator("observed_at", mode="before")
    @classmethod
    def require_exact_rfc3339_utc(cls, value: object) -> object:
        if not isinstance(value, str) or _RFC3339_UTC.fullmatch(value) is None:
            raise ValueError("observed_at must be exact RFC 3339 UTC")
        return value

    @field_validator("observed_at")
    @classmethod
    def require_valid_rfc3339_timestamp(cls, value: str) -> str:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            datetime.fromisoformat(normalized)
        except ValueError:
            raise ValueError("observed_at must be exact RFC 3339 UTC") from None
        return value

    @model_validator(mode="after")
    def validate_identity_chain(self) -> "DeploymentEvidence":
        service_ids = [service.service_id for service in self.services]
        if len(service_ids) != len(set(service_ids)):
            raise ValueError("duplicate service_id")

        if self.source_to_release is EvidenceState.UNAVAILABLE:
            if self.release is not None or self.services:
                raise ValueError("UNAVAILABLE release link cannot imply an identity")
            return self
        if self.release is None:
            raise ValueError("available release link requires a release identity")
        if not self.services:
            raise ValueError("available release link requires service evidence")

        source_matches = (
            self.source.commit == self.release.source_commit
            and self.source.tree == self.release.source_tree
        )
        if self.source_to_release is EvidenceState.VERIFIED and not source_matches:
            raise ValueError("VERIFIED source-to-release link does not match")
        if self.source_to_release is EvidenceState.DRIFTED and source_matches:
            raise ValueError("DRIFTED source-to-release link has no observed mismatch")

        for service in self.services:
            if service.release_to_unit is EvidenceState.UNAVAILABLE:
                continue
            if service.unit is None:  # guarded by ServiceDeploymentEvidence
                raise ValueError("available unit link requires a unit identity")
            unit_matches = (
                service.unit.release_manifest_sha256 == self.release.manifest_sha256
            )
            if service.release_to_unit is EvidenceState.VERIFIED and not unit_matches:
                raise ValueError("VERIFIED release-to-unit link does not match")
            if service.release_to_unit is EvidenceState.DRIFTED and unit_matches:
                raise ValueError("DRIFTED release-to-unit link has no observed mismatch")
        return self

    @property
    def is_fully_verified(self) -> bool:
        release = self.release
        if (
            self.source_to_release is not EvidenceState.VERIFIED
            or release is None
            or not self.services
            or self.source.commit != release.source_commit
            or self.source.tree != release.source_tree
        ):
            return False
        return all(
            service.release_to_unit is EvidenceState.VERIFIED
            and service.unit_to_process is EvidenceState.VERIFIED
            and service.unit is not None
            and service.process is not None
            and service.unit.release_manifest_sha256 == release.manifest_sha256
            and service.unit.command_fingerprint
            == service.process.command_fingerprint
            for service in self.services
        )


def canonical_json(model: DeploymentEvidence) -> str:
    """Revalidate and serialize one normative deployment-evidence document."""

    if not isinstance(model, DeploymentEvidence):
        raise TypeError("canonical JSON requires DeploymentEvidence")
    validated = DeploymentEvidence.model_validate(model.model_dump(mode="python"))
    return json.dumps(
        validated.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def deployment_evidence_json_schema() -> dict[str, object]:
    """Render the structural schema plus its normative semantic contract."""

    schema = DeploymentEvidence.model_json_schema()
    schema["description"] = (
        "Structural deployment-evidence schema. Schema-only validation is not "
        "authoritative; consumers must apply x-semantic-validation."
    )
    schema["x-semantic-validation"] = {
        "constraints": [
            "normalized_absolute_paths",
            "exact_rfc3339_utc",
            "unique_service_ids",
            "identity_link_consistency",
            "secret_like_key_rejection",
        ],
        "promotion_authority": False,
        "required": True,
        "schema_only_authoritative": False,
        "validator": "packages.deployment_evidence.DeploymentEvidence",
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **schema,
    }


def resolve_promotion_decision(
    decision: PromotionDecision | str | None = None,
    evidence: DeploymentEvidence | Mapping[str, object] | None = None,
) -> PromotionDecision:
    """Resolve a recorded decision without granting any runtime authority.

    Invalid, incomplete, unavailable, drifted, or bypass-constructed input
    fails closed.  A completely verified observation also remains ``NO_GO``:
    this module has no promotion authority and cannot authorize deployment or
    trading.
    """

    try:
        candidate = (
            PromotionDecision.NO_GO
            if decision is None
            else PromotionDecision(decision)
        )
    except (TypeError, ValueError):
        return PromotionDecision.NO_GO
    if candidate is PromotionDecision.NO_GO:
        return candidate
    try:
        raw_evidence = (
            evidence.model_dump(mode="python")
            if isinstance(evidence, DeploymentEvidence)
            else evidence
        )
        validated = DeploymentEvidence.model_validate(raw_evidence)
    except (TypeError, ValueError, ValidationError):
        return PromotionDecision.NO_GO
    if not validated.is_fully_verified:
        return PromotionDecision.NO_GO
    return PromotionDecision.NO_GO
