"""P1-only schema-8 validation over the immutable legacy spawn provider."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import NoReturn

from packages.engine_contracts import (
    EngineCommandEnvelope,
    RunBacktest,
    RunBacktestSimulation,
    ValidatePaperCompatibility,
)

from . import engine_spawn as _legacy
from .engine_profiles import EngineProfilePolicy, P1_REAL_BACKTEST_POLICY
from .engine_spawn_interface import EngineSpawnError


_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_PRODUCT_LINEAGE_TARGET = PurePosixPath("/engine/p1-product-lineage.json")


def _blocked(message: str) -> NoReturn:
    raise EngineSpawnError("ENGINE_CLOSURE_INVALID", message)


@dataclass(frozen=True, slots=True)
class P1EngineClosureAttestation:
    """Exact schema-8 P1 closure authority before legacy spawn adaptation."""

    manifest_schema_version: int
    profile: str
    source_commit: str
    closure_sha256: str
    mounts: tuple[_legacy.ReadOnlyClosureMount, ...]
    entrypoint: PurePosixPath
    argv_prefix: tuple[str, ...]
    timeout_seconds: int
    result_validator_id: str
    sandbox: _legacy.OsSandboxProof
    semantic_profile: str
    closure_manifest: _legacy.ReadOnlyClosureMount
    native_entry_guard: _legacy.NativeEntryGuardAttestation
    dependency_import_policy: str
    runtime_family: str
    engine_version: str
    engine_upstream_commit: str
    event_schema: str
    runtime_inventory_sha256: str
    product_lineage: _legacy.ReadOnlyClosureMount


def _validate_native_entry_guard(
    attestation: P1EngineClosureAttestation,
) -> None:
    guard = attestation.native_entry_guard
    if type(guard) is not _legacy.NativeEntryGuardAttestation:
        _blocked("P1 native entry guard contract is invalid")
    digest_fields = (
        guard.binary_sha256,
        guard.source_sha256,
        guard.cargo_manifest_sha256,
        guard.cargo_lock_sha256,
        guard.rust_toolchain_policy_sha256,
        guard.llvm_toolchain_policy_sha256,
    )
    if (
        guard.target != _legacy._NATIVE_GUARD_TARGET
        or guard.guarded_executable != _legacy._NATIVE_GUARDED_EXECUTABLE
        or attestation.entrypoint != guard.target
        or attestation.argv_prefix != P1_REAL_BACKTEST_POLICY.argv_prefix
        or guard.mode != 0o500
        or guard.source != _legacy._NATIVE_GUARD_SOURCE
        or guard.cargo_manifest != _legacy._NATIVE_GUARD_CARGO_MANIFEST
        or guard.cargo_lock != _legacy._NATIVE_GUARD_CARGO_LOCK
        or isinstance(guard.binary_size, bool)
        or not isinstance(guard.binary_size, int)
        or guard.binary_size <= 0
        or any(
            not isinstance(digest, str) or _SHA256.fullmatch(digest) is None
            for digest in digest_fields
        )
        or not isinstance(guard.cargo_identity, str)
        or _legacy._NATIVE_CARGO_IDENTITY.fullmatch(guard.cargo_identity) is None
        or not isinstance(guard.rustc_identity, str)
        or _legacy._NATIVE_RUSTC_IDENTITY.fullmatch(guard.rustc_identity) is None
        or guard.target_triple != "x86_64-unknown-linux-gnu"
    ):
        _blocked("P1 native entry guard contract is invalid")
    matching_guard = [
        mount for mount in attestation.mounts if mount.target == guard.target
    ]
    matching_python = [
        mount
        for mount in attestation.mounts
        if mount.target == guard.guarded_executable
    ]
    if (
        len(matching_guard) != 1
        or matching_guard[0].mode != guard.mode
        or matching_guard[0].size != guard.binary_size
        or matching_guard[0].sha256 != guard.binary_sha256
        or len(matching_python) != 1
        or matching_python[0].mode != 0o500
        or not matching_python[0].mode & 0o111
        or guard.guarded_executable == guard.target
    ):
        _blocked("P1 native entry guard executable binding is invalid")


def _adapt_p1_closure(value: object) -> _legacy.CompleteEngineClosureAttestation:
    if type(value) is not P1EngineClosureAttestation:
        raise EngineSpawnError(
            "ENGINE_CLOSURE_UNAVAILABLE",
            "typed P1 engine closure attestation is required",
        )
    attestation = value
    profile = P1_REAL_BACKTEST_POLICY
    if (
        attestation.manifest_schema_version != profile.manifest_schema_version
        or attestation.profile != profile.profile
        or attestation.closure_sha256 != profile.closure_sha256
        or attestation.semantic_profile != profile.semantic_profile
        or attestation.entrypoint != PurePosixPath(profile.entrypoint)
        or attestation.argv_prefix != profile.argv_prefix
        or attestation.timeout_seconds != profile.timeout_seconds
        or attestation.result_validator_id != profile.result_validator_id
        or attestation.dependency_import_policy != profile.dependency_import_policy
        or attestation.runtime_family != profile.runtime_family
        or attestation.engine_version != profile.engine_version
        or attestation.engine_upstream_commit != profile.engine_upstream_commit
        or attestation.event_schema != profile.event_schema
        or attestation.runtime_inventory_sha256
        != profile.runtime_inventory_sha256
        or type(attestation.mounts) is not tuple
        or not attestation.mounts
        or any(
            type(mount) is not _legacy.ReadOnlyClosureMount
            for mount in attestation.mounts
        )
        or type(attestation.closure_manifest) is not _legacy.ReadOnlyClosureMount
        or type(attestation.product_lineage) is not _legacy.ReadOnlyClosureMount
    ):
        _blocked("complete P1 engine closure profile is invalid")
    lineage = attestation.product_lineage
    if (
        lineage.target != _PRODUCT_LINEAGE_TARGET
        or lineage.mode != 0o400
        or any(mount.target == _PRODUCT_LINEAGE_TARGET for mount in attestation.mounts)
    ):
        _blocked("P1 product lineage attestation is invalid")
    _validate_native_entry_guard(attestation)
    adapted = _legacy.CompleteEngineClosureAttestation(
        manifest_schema_version=4,
        profile="zero-order",
        source_commit=attestation.source_commit,
        closure_sha256=attestation.closure_sha256,
        mounts=(*attestation.mounts, lineage),
        entrypoint=attestation.entrypoint,
        argv_prefix=attestation.argv_prefix,
        timeout_seconds=attestation.timeout_seconds,
        result_validator_id=attestation.result_validator_id,
        sandbox=attestation.sandbox,
        closure_manifest=attestation.closure_manifest,
    )
    _legacy._validate_closure(adapted, expected_manifest_schema_version=4)
    expected_lineage = (
        json.dumps(
            {
                "closure_sha256": attestation.closure_sha256,
                "engine_version": attestation.engine_version,
                "event_schema": attestation.event_schema,
                "profile": attestation.profile,
                "profile_manifest_schema_version": attestation.manifest_schema_version,
                "runtime_family": attestation.runtime_family,
                "runtime_inventory_sha256": attestation.runtime_inventory_sha256,
            },
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")
    if _legacy._verified_closure_file(lineage) != expected_lineage:
        _blocked("P1 product lineage authority is invalid")
    return adapted


class P1EngineSpawnProvider:
    """Schema-8 P1 authority using the unchanged legacy spawn implementation."""

    def __init__(
        self,
        *,
        transport_root: Path,
        attest_closure: Callable[[], P1EngineClosureAttestation],
        expected_manifest_schema_version: int,
        profile_policy: EngineProfilePolicy,
        attest_inputs: Callable[
            [RunBacktest | RunBacktestSimulation | ValidatePaperCompatibility],
            tuple[_legacy.HashBoundEngineInput, ...],
        ]
        | None = None,
        monotonic_ns: Callable[[], int],
    ) -> None:
        if expected_manifest_schema_version != 8:
            raise ValueError("P1 closure manifest schema must be exactly 8")
        if profile_policy is not P1_REAL_BACKTEST_POLICY:
            raise ValueError("exact code-owned P1 engine profile policy is required")
        self._provider = _legacy.EngineSpawnProvider(
            transport_root=transport_root,
            attest_closure=lambda: _adapt_p1_closure(attest_closure()),
            expected_manifest_schema_version=4,
            attest_inputs=attest_inputs,
            monotonic_ns=monotonic_ns,
        )

    def prepare(self, envelope: EngineCommandEnvelope) -> _legacy.PreparedEngineSpawn:
        if (
            type(envelope) is not EngineCommandEnvelope
            or type(envelope.payload) is not RunBacktest
        ):
            raise EngineSpawnError(
                "ENGINE_REQUEST_INVALID",
                "P1 engine command does not match the code-owned profile",
            )
        return self._provider.prepare(envelope)


__all__ = [
    "P1EngineClosureAttestation",
    "P1EngineSpawnProvider",
]
