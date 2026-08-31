"""Closed, engine-neutral authority record for NT1231-U04-G1."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
from pathlib import Path
from typing import cast


_EXPECTED_G1_SHA256 = "2ea31eaca9cf19715fe2a73abc8c3d11c7731466e6e84e50e65db4979be46f8c"


class CandidateGenerationError(ValueError):
    """The candidate generation bytes are not the accepted closed record."""


@dataclass(frozen=True, slots=True)
class EngineIdentity:
    family: str
    name: str
    release_tag: str
    release_tag_object: str
    source_sha256: str
    upstream_commit: str
    version: str


@dataclass(frozen=True, slots=True)
class RepositorySource:
    build_commit: str
    build_tree: str
    evidence_commit: str
    evidence_tree: str


@dataclass(frozen=True, slots=True)
class AuthorityDigests:
    u02_provenance_policy_sha256: str
    u02_source_archive_sha256: str
    u03_toolchain_inputs_sha256: str
    x4_authority_receipt_sha256: str


@dataclass(frozen=True, slots=True)
class ArtifactIdentity:
    artifact_manifest_sha256: str
    native_object_count: int
    wheel_sha256: str
    wheel_size: int


@dataclass(frozen=True, slots=True)
class ClosureIdentity:
    attestation_sha256: str
    directory_count: int
    file_inventory_sha256: str
    manifest_sha256: str
    native_inventory_sha256: str
    regular_file_count: int
    schema_version: int
    symlink_count: int
    writable_regular_file_count: int


@dataclass(frozen=True, slots=True)
class RollbackIdentity:
    closure_sha256: str
    schema_version: int
    upstream_commit: str
    version: str


@dataclass(frozen=True, slots=True)
class AuthorityLimits:
    candidate_active: bool
    candidate_promoted: bool
    live_authorized: bool
    network_trading_authorized: bool
    production_authorized: bool


@dataclass(frozen=True, slots=True)
class CandidateGeneration:
    schema: str
    generation_id: str
    engine_identity: EngineIdentity
    repository_source: RepositorySource
    authority_digests: AuthorityDigests
    artifact: ArtifactIdentity
    closure: ClosureIdentity
    rollback: RollbackIdentity
    authority_limits: AuthorityLimits
    record_sha256: str


def _canonical(value: object) -> bytes:
    return (json.dumps(value, allow_nan=False, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def _loads_exact(raw: bytes) -> dict[str, object]:
    def no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise CandidateGenerationError("candidate generation contains a duplicate key")
            result[key] = value
        return result

    def reject_float(_value: str) -> object:
        raise CandidateGenerationError("candidate generation float input is forbidden")

    try:
        value = json.loads(
            raw,
            object_pairs_hook=no_duplicates,
            parse_float=reject_float,
            parse_constant=reject_float,
        )
    except CandidateGenerationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CandidateGenerationError("candidate generation bytes are invalid JSON") from exc
    if not isinstance(value, dict) or _canonical(value) != raw:
        raise CandidateGenerationError("candidate generation bytes are not canonical")
    return cast(dict[str, object], value)


def _object(value: dict[str, object], key: str) -> dict[str, object]:
    nested = value.get(key)
    if not isinstance(nested, dict):
        raise CandidateGenerationError("candidate generation shape is invalid")
    return cast(dict[str, object], nested)


def _str(value: dict[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str):
        raise CandidateGenerationError("candidate generation shape is invalid")
    return item


def _int(value: dict[str, object], key: str) -> int:
    item = value.get(key)
    if type(item) is not int:
        raise CandidateGenerationError("candidate generation shape is invalid")
    return cast(int, item)


def _bool(value: dict[str, object], key: str) -> bool:
    item = value.get(key)
    if type(item) is not bool:
        raise CandidateGenerationError("candidate generation shape is invalid")
    return cast(bool, item)


def load_candidate_generation(path: Path) -> CandidateGeneration:
    """Load the one accepted G1 generation from exact canonical bytes."""

    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise CandidateGenerationError("candidate generation record is unavailable") from exc
    value = _loads_exact(raw)
    record_sha256 = hashlib.sha256(raw).hexdigest()
    if not hmac.compare_digest(record_sha256, _EXPECTED_G1_SHA256):
        raise CandidateGenerationError("candidate generation record SHA-256 is not accepted")

    engine = _object(value, "engine_identity")
    source = _object(value, "repository_source")
    digests = _object(value, "authority_digests")
    artifact = _object(value, "artifact")
    closure = _object(value, "closure")
    rollback = _object(value, "rollback")
    limits = _object(value, "authority_limits")
    return CandidateGeneration(
        schema=_str(value, "schema"),
        generation_id=_str(value, "generation_id"),
        engine_identity=EngineIdentity(
            family=_str(engine, "family"),
            name=_str(engine, "name"),
            release_tag=_str(engine, "release_tag"),
            release_tag_object=_str(engine, "release_tag_object"),
            source_sha256=_str(engine, "source_sha256"),
            upstream_commit=_str(engine, "upstream_commit"),
            version=_str(engine, "version"),
        ),
        repository_source=RepositorySource(
            build_commit=_str(source, "build_commit"),
            build_tree=_str(source, "build_tree"),
            evidence_commit=_str(source, "evidence_commit"),
            evidence_tree=_str(source, "evidence_tree"),
        ),
        authority_digests=AuthorityDigests(
            u02_provenance_policy_sha256=_str(digests, "u02_provenance_policy_sha256"),
            u02_source_archive_sha256=_str(digests, "u02_source_archive_sha256"),
            u03_toolchain_inputs_sha256=_str(digests, "u03_toolchain_inputs_sha256"),
            x4_authority_receipt_sha256=_str(digests, "x4_authority_receipt_sha256"),
        ),
        artifact=ArtifactIdentity(
            artifact_manifest_sha256=_str(artifact, "artifact_manifest_sha256"),
            native_object_count=_int(artifact, "native_object_count"),
            wheel_sha256=_str(artifact, "wheel_sha256"),
            wheel_size=_int(artifact, "wheel_size"),
        ),
        closure=ClosureIdentity(
            attestation_sha256=_str(closure, "attestation_sha256"),
            directory_count=_int(closure, "directory_count"),
            file_inventory_sha256=_str(closure, "file_inventory_sha256"),
            manifest_sha256=_str(closure, "manifest_sha256"),
            native_inventory_sha256=_str(closure, "native_inventory_sha256"),
            regular_file_count=_int(closure, "regular_file_count"),
            schema_version=_int(closure, "schema_version"),
            symlink_count=_int(closure, "symlink_count"),
            writable_regular_file_count=_int(closure, "writable_regular_file_count"),
        ),
        rollback=RollbackIdentity(
            closure_sha256=_str(rollback, "closure_sha256"),
            schema_version=_int(rollback, "schema_version"),
            upstream_commit=_str(rollback, "upstream_commit"),
            version=_str(rollback, "version"),
        ),
        authority_limits=AuthorityLimits(
            candidate_active=_bool(limits, "candidate_active"),
            candidate_promoted=_bool(limits, "candidate_promoted"),
            live_authorized=_bool(limits, "live_authorized"),
            network_trading_authorized=_bool(limits, "network_trading_authorized"),
            production_authorized=_bool(limits, "production_authorized"),
        ),
        record_sha256=record_sha256,
    )
