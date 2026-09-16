"""Shared schema-8 closure value consumed by attestation and spawn validation."""

from dataclasses import dataclass
from pathlib import PurePosixPath

from .engine_spawn import NativeEntryGuardAttestation, OsSandboxProof, ReadOnlyClosureMount


@dataclass(frozen=True, slots=True)
class P1EngineClosureAttestation:
    """Exact schema-8 P1 closure authority before legacy spawn adaptation."""

    manifest_schema_version: int
    profile: str
    source_commit: str
    closure_sha256: str
    mounts: tuple[ReadOnlyClosureMount, ...]
    entrypoint: PurePosixPath
    argv_prefix: tuple[str, ...]
    timeout_seconds: int
    result_validator_id: str
    sandbox: OsSandboxProof
    semantic_profile: str
    closure_manifest: ReadOnlyClosureMount
    native_entry_guard: NativeEntryGuardAttestation
    dependency_import_policy: str
    runtime_family: str
    engine_version: str
    engine_upstream_commit: str
    event_schema: str
    runtime_inventory_sha256: str
    product_lineage: ReadOnlyClosureMount
