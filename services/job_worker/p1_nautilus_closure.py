"""Schema-8 P1 product closure attestation, separate from legacy schema 1-6."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path, PurePosixPath
from typing import NoReturn

from packages.nautilus_upgrade_authority import (
    CandidateGenerationError,
    load_candidate_generation,
)

from . import nautilus_closure as _legacy
from .engine_profiles import P1_REAL_BACKTEST_POLICY
from .engine_spawn import (
    NativeEntryGuardAttestation,
    ReadOnlyClosureMount,
)
from .engine_spawn_interface import EngineSpawnError
from .nautilus_closure import NautilusClosureConfig
from .p1_engine_spawn import P1EngineClosureAttestation


_ROOT = Path(__file__).resolve().parents[2]
_POLICY_PATH = _ROOT / "engines/nautilus/p1-runtime-closure-policy.json"
_MANIFEST_NAME = "closure-manifest.json"
_LINEAGE_NAME = "p1-product-lineage.json"
_LINEAGE_TARGET = PurePosixPath("/engine/p1-product-lineage.json")
_MANIFEST_TARGET = PurePosixPath("/engine/closure-manifest.json")
_GUARD_TARGET = PurePosixPath("/engine/bin/nautilus-entry-guard")
_PYTHON_TARGET = PurePosixPath("/usr/bin/python3.12")
_MAX_MANIFEST_BYTES = 8 * 1024 * 1024
_SOURCE_COMMIT = re.compile(r"[0-9a-f]{40}", re.ASCII)
_DIGEST = re.compile(r"[0-9a-f]{64}", re.ASCII)
_POLICY_FIELDS = {
    "argv_prefix",
    "artifact_manifest_sha256",
    "authority_limits",
    "candidate_closure_sha256",
    "candidate_generation_id",
    "candidate_generation_sha256",
    "command_type",
    "dependency_import_policy",
    "engine_name",
    "engine_upstream_commit",
    "engine_version",
    "engine_wheel",
    "entrypoint",
    "event_schema",
    "native_entry_guard",
    "p1_baseline_receipt_sha256",
    "p1_baseline_scope",
    "p1_baseline_status",
    "product_lineage_target",
    "profile",
    "profile_manifest_schema_version",
    "python_identity",
    "request_protocol_version",
    "required_artifact_names",
    "result_validator_id",
    "runtime_family",
    "runtime_inventory",
    "runtime_inventory_sha256",
    "sandbox_profile_sha256",
    "schema",
    "semantic_profile",
    "timeout_seconds",
}
_MANIFEST_FIELDS = {
    "argv_prefix",
    "artifact_manifest_sha256",
    "candidate_closure_sha256",
    "candidate_generation_id",
    "candidate_generation_sha256",
    "command_type",
    "dependency_import_policy",
    "engine_name",
    "engine_upstream_commit",
    "engine_version",
    "entrypoint",
    "event_schema",
    "files",
    "native_entry_guard",
    "p1_baseline_receipt_sha256",
    "p1_baseline_scope",
    "p1_baseline_status",
    "profile",
    "python_identity",
    "request_protocol_version",
    "required_artifact_names",
    "result_validator_id",
    "runtime_family",
    "runtime_inventory_sha256",
    "sandbox_profile_sha256",
    "schema_version",
    "semantic_profile",
    "source_commit",
    "timeout_seconds",
}
_GUARD_POLICY_FIELDS = {
    "binary_sha256",
    "binary_size",
    "build_environment",
    "cargo_identity",
    "cargo_lock",
    "cargo_lock_sha256",
    "cargo_manifest",
    "cargo_manifest_sha256",
    "llvm_toolchain_policy_sha256",
    "mode",
    "rust_toolchain_policy_sha256",
    "rustc_identity",
    "source",
    "source_sha256",
    "target",
    "target_triple",
}
_GUARD_MANIFEST_FIELDS = {
    "binary_sha256",
    "binary_size",
    *(_GUARD_POLICY_FIELDS - {"build_environment"}),
}
_INVENTORY_FIELDS = {"mode", "sha256", "source", "target"}
_WHEEL_FIELDS = {"mode", "sha256", "size", "target"}
_LINEAGE_FIELDS = {
    "closure_sha256",
    "engine_version",
    "event_schema",
    "profile",
    "profile_manifest_schema_version",
    "runtime_family",
    "runtime_inventory_sha256",
}


def _blocked(message: str) -> NoReturn:
    raise EngineSpawnError("ENGINE_CLOSURE_INVALID", message)


def _json_object(raw: bytes, label: str) -> dict[str, object]:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in items:
            if key in value:
                _blocked(f"{label} contains a duplicate key")
            value[key] = item
        return value

    def reject_number(_value: str) -> object:
        _blocked(f"{label} contains a noncanonical number")
        raise AssertionError("unreachable")

    try:
        value = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_float=reject_number,
            parse_constant=reject_number,
        )
    except EngineSpawnError:
        raise
    except (RecursionError, UnicodeDecodeError, ValueError) as exc:
        raise EngineSpawnError(
            "ENGINE_CLOSURE_INVALID", f"{label} is not valid JSON"
        ) from exc
    if type(value) is not dict:
        _blocked(f"{label} must be an object")
    return value


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _manifest_snapshot(
    path: Path,
) -> tuple[dict[str, object], ReadOnlyClosureMount]:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
        before = os.fstat(descriptor)
        if before.st_size > _MAX_MANIFEST_BYTES:
            _blocked("P1 closure manifest exceeds the maximum size")
        mode = stat.S_IMODE(before.st_mode)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or mode != 0o400
        ):
            _blocked("P1 closure manifest is not an immutable regular file")
        chunks: list[bytes] = []
        offset = 0
        while offset < before.st_size:
            chunk = os.pread(
                descriptor, min(1024 * 1024, before.st_size - offset), offset
            )
            if not chunk:
                break
            chunks.append(chunk)
            offset += len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
        if (
            len(raw) != before.st_size
            or (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
            or after.st_size != before.st_size
            or after.st_ctime_ns != before.st_ctime_ns
            or after.st_mtime_ns != before.st_mtime_ns
            or stat.S_IMODE(after.st_mode) != mode
        ):
            _blocked("P1 closure manifest changed while being read")
        digest = hashlib.sha256(raw).hexdigest()
        return _json_object(raw, "P1 closure manifest"), ReadOnlyClosureMount(
            source=path,
            target=_MANIFEST_TARGET,
            identity=(before.st_dev, before.st_ino),
            size=before.st_size,
            mode=mode,
            sha256=digest,
        )
    except EngineSpawnError:
        raise
    except OSError as exc:
        raise EngineSpawnError(
            "ENGINE_CLOSURE_UNAVAILABLE",
            "P1 closure manifest cannot be snapshotted",
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _load_policy() -> dict[str, object]:
    policy = _json_object(_POLICY_PATH.read_bytes(), "P1 closure policy")
    profile = P1_REAL_BACKTEST_POLICY
    if (
        set(policy) != _POLICY_FIELDS
        or policy.get("schema") != "trading-agent-p1-runtime-closure-policy/v1"
        or policy.get("profile") != profile.profile
        or policy.get("semantic_profile") != profile.semantic_profile
        or policy.get("command_type") != profile.command_type
        or policy.get("profile_manifest_schema_version")
        != profile.manifest_schema_version
        or policy.get("runtime_family") != profile.runtime_family
        or policy.get("engine_version") != profile.engine_version
        or policy.get("engine_upstream_commit")
        != profile.engine_upstream_commit
        or policy.get("entrypoint") != profile.entrypoint
        or policy.get("argv_prefix") != list(profile.argv_prefix)
        or policy.get("result_validator_id") != profile.result_validator_id
        or policy.get("timeout_seconds") != profile.timeout_seconds
        or policy.get("required_artifact_names")
        != list(profile.required_artifact_names)
        or policy.get("request_protocol_version")
        != profile.request_protocol_version
        or policy.get("event_schema") != profile.event_schema
        or policy.get("dependency_import_policy")
        != profile.dependency_import_policy
        or policy.get("runtime_inventory_sha256")
        != profile.runtime_inventory_sha256
        or policy.get("sandbox_profile_sha256")
        != profile.sandbox_profile_sha256
        or policy.get("p1_baseline_receipt_sha256")
        != profile.p1_baseline_receipt_sha256
        or policy.get("p1_baseline_status") != profile.p1_baseline_status
        or policy.get("p1_baseline_scope") != profile.p1_baseline_scope
        or policy.get("product_lineage_target") != str(_LINEAGE_TARGET)
        or policy.get("python_identity") != "CPython 3.12.3"
        or policy.get("engine_name") != "nautilus_trader"
        or policy.get("authority_limits")
        != {
            "live_authorized": False,
            "network_trading_authorized": False,
            "production_authorized": False,
        }
    ):
        _blocked("P1 closure policy is invalid")
    try:
        generation = load_candidate_generation(
            _ROOT
            / "docs/implementation/p1-real-nautilus/upgrade/candidate-generations/NT1231-U04-G1.json"
        )
    except CandidateGenerationError as exc:
        raise EngineSpawnError(
            "ENGINE_CLOSURE_INVALID", "accepted G1 authority is invalid"
        ) from exc
    if (
        generation.generation_id != policy["candidate_generation_id"]
        or generation.record_sha256 != policy["candidate_generation_sha256"]
        or generation.artifact.artifact_manifest_sha256
        != policy["artifact_manifest_sha256"]
    ):
        _blocked("P1 artifact manifest policy is invalid")
    wheel = policy.get("engine_wheel")
    if (
        type(wheel) is not dict
        or set(wheel) != _WHEEL_FIELDS
        or wheel.get("mode") != "0400"
        or wheel.get("target")
        != "/engine/wheels/nautilus_trader-1.231.0-cp312-cp312-manylinux_2_39_x86_64.whl"
        or wheel.get("sha256")
        != "ecc461d0f634c25db17e0fb79136c3bf0d513edd323d4f9adaaf84346e68b2fb"
        or wheel.get("size") != 183626605
    ):
        _blocked("P1 engine wheel policy is invalid")
    baseline_path = (
        _ROOT
        / "docs/implementation/p1-real-nautilus/upgrade/p1-engine-baseline-receipt.json"
    )
    baseline_raw = baseline_path.read_bytes()
    baseline = _json_object(baseline_raw, "P1 baseline receipt")
    if (
        hashlib.sha256(baseline_raw).hexdigest()
        != policy["p1_baseline_receipt_sha256"]
        or baseline.get("status") != policy["p1_baseline_status"]
        or baseline.get("scope") != policy["p1_baseline_scope"]
        or baseline.get("candidate_generation_id")
        != policy["candidate_generation_id"]
        or baseline.get("candidate_generation_sha256")
        != policy["candidate_generation_sha256"]
        or baseline.get("candidate_closure_sha256")
        != policy["candidate_closure_sha256"]
        or baseline.get("operator_decision") != "PROMOTE_1_231_FOR_P1"
        or baseline.get("authority_limits")
        != {
            "candidate_active": False,
            "candidate_promoted": False,
            "live_authorized": False,
            "network_trading_authorized": False,
            "production_authorized": False,
        }
    ):
        _blocked("P1 baseline receipt authority is invalid")
    inventory = policy.get("runtime_inventory")
    if type(inventory) is not list or not inventory:
        _blocked("P1 runtime inventory is invalid")
    sources: set[str] = set()
    targets: set[str] = set()
    for record in inventory:
        if type(record) is not dict or set(record) != _INVENTORY_FIELDS:
            _blocked("P1 runtime inventory record is invalid")
        source = record.get("source")
        target = record.get("target")
        digest = record.get("sha256")
        if (
            type(source) is not str
            or not source.startswith("engines/nautilus/runtime_v1/")
            or type(target) is not str
            or target != "/engine/runtime_v1/" + Path(source).name
            or record.get("mode") != "0400"
            or type(digest) is not str
            or _DIGEST.fullmatch(digest) is None
            or source in sources
            or target in targets
            or hashlib.sha256((_ROOT / source).read_bytes()).hexdigest() != digest
        ):
            _blocked("P1 runtime inventory record is invalid")
        sources.add(source)
        targets.add(target)
    if hashlib.sha256(_canonical_json(inventory)).hexdigest() != profile.runtime_inventory_sha256:
        _blocked("P1 runtime inventory digest is invalid")
    guard = policy.get("native_entry_guard")
    if type(guard) is not dict or set(guard) != _GUARD_POLICY_FIELDS:
        _blocked("P1 native guard policy is invalid")
    if (
        guard.get("source")
        != "engines/nautilus/native_entry_guard/src/main.rs"
        or guard.get("cargo_manifest")
        != "engines/nautilus/native_entry_guard/Cargo.toml"
        or guard.get("cargo_lock") != "engines/nautilus/native_entry_guard/Cargo.lock"
        or guard.get("target") != str(_GUARD_TARGET)
        or guard.get("target_triple") != "x86_64-unknown-linux-gnu"
        or guard.get("mode") != "0500"
        or guard.get("cargo_identity")
        != "cargo 1.95.0 (f2d3ce0bd 2026-03-21)"
        or guard.get("rustc_identity")
        != "rustc 1.95.0 (59807616e 2026-04-14)"
        or guard.get("build_environment")
        != {
            "NAUTILUS_GUARD_ENTRYPOINT": str(_GUARD_TARGET),
            "NAUTILUS_GUARD_LAUNCHER": "/engine/runtime_v1/main.py",
            "NAUTILUS_GUARD_PROFILE": profile.profile,
            "NAUTILUS_GUARD_PYTHON": str(_PYTHON_TARGET),
            "NAUTILUS_GUARD_REQUEST": "/inputs/request.json",
            "NAUTILUS_GUARD_SIDECAR": "/inputs/request.sha256",
        }
        or guard.get("rust_toolchain_policy_sha256")
        != hashlib.sha256(
            (_ROOT / "engines/nautilus/toolchain-inputs.json").read_bytes()
        ).hexdigest()
        or guard.get("llvm_toolchain_policy_sha256")
        != hashlib.sha256(
            (_ROOT / "engines/nautilus/llvm-toolchain-policy.json").read_bytes()
        ).hexdigest()
    ):
        _blocked("P1 native guard policy is invalid")
    for path_field, digest_field in (
        ("source", "source_sha256"),
        ("cargo_manifest", "cargo_manifest_sha256"),
        ("cargo_lock", "cargo_lock_sha256"),
    ):
        path = guard.get(path_field)
        digest = guard.get(digest_field)
        if (
            type(path) is not str
            or type(digest) is not str
            or _DIGEST.fullmatch(digest) is None
            or hashlib.sha256((_ROOT / path).read_bytes()).hexdigest() != digest
        ):
            _blocked("P1 native guard source policy is invalid")
    return policy


def p1_closure_authority_sha256(
    manifest: dict[str, object], mounts: tuple[ReadOnlyClosureMount, ...]
) -> str:
    """Hash the closed product authority projection, excluding derived lineage."""

    projection = {
        key: manifest[key]
        for key in sorted(_MANIFEST_FIELDS - {"files"})
    }
    projection["files"] = [
        {
            "mode": f"{mount.mode:04o}",
            "sha256": mount.sha256,
            "size": mount.size,
            "target": str(mount.target),
        }
        for mount in sorted(mounts, key=lambda item: str(item.target))
    ]
    return hashlib.sha256(_canonical_json(projection)).hexdigest()


def derive_p1_product_lineage(
    closure_sha256: str, runtime_inventory_sha256: str
) -> dict[str, object]:
    profile = P1_REAL_BACKTEST_POLICY
    if (
        _DIGEST.fullmatch(closure_sha256) is None
        or runtime_inventory_sha256 != profile.runtime_inventory_sha256
    ):
        raise ValueError("exact P1 closure and runtime inventory digests are required")
    return {
        "closure_sha256": closure_sha256,
        "engine_version": profile.engine_version,
        "event_schema": profile.event_schema,
        "profile": profile.profile,
        "profile_manifest_schema_version": profile.manifest_schema_version,
        "runtime_family": profile.runtime_family,
        "runtime_inventory_sha256": runtime_inventory_sha256,
    }


def _lineage_mount(runtime_root: Path, expected: dict[str, object]) -> ReadOnlyClosureMount:
    path = runtime_root / _LINEAGE_NAME
    observed = _legacy._sealed_file(path, "P1 product lineage")
    raw = path.read_bytes()
    if stat.S_IMODE(observed.st_mode) != 0o400 or raw != _canonical_json(expected) + b"\n":
        _blocked("P1 product lineage is invalid")
    return ReadOnlyClosureMount(
        source=path,
        target=_LINEAGE_TARGET,
        identity=(observed.st_dev, observed.st_ino),
        size=observed.st_size,
        mode=0o400,
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def _native_guard(
    value: object,
    *,
    policy: dict[str, object],
    mounts: tuple[ReadOnlyClosureMount, ...],
) -> NativeEntryGuardAttestation:
    expected = policy["native_entry_guard"]
    if (
        type(value) is not dict
        or set(value) != _GUARD_MANIFEST_FIELDS
        or type(expected) is not dict
    ):
        _blocked("P1 native guard manifest is invalid")
    for field in _GUARD_POLICY_FIELDS - {"build_environment"}:
        if value.get(field) != expected.get(field):
            _blocked("P1 native guard provenance is invalid")
    binary_sha256 = value.get("binary_sha256")
    binary_size = value.get("binary_size")
    if (
        type(binary_sha256) is not str
        or _DIGEST.fullmatch(binary_sha256) is None
        or type(binary_size) is not int
        or binary_size <= 0
        or binary_sha256 != expected.get("binary_sha256")
        or binary_size != expected.get("binary_size")
    ):
        _blocked("P1 native guard binary identity is invalid")
    matching_guard = [mount for mount in mounts if mount.target == _GUARD_TARGET]
    matching_python = [mount for mount in mounts if mount.target == _PYTHON_TARGET]
    if (
        len(matching_guard) != 1
        or matching_guard[0].mode != 0o500
        or matching_guard[0].sha256 != binary_sha256
        or matching_guard[0].size != binary_size
        or len(matching_python) != 1
        or matching_python[0].mode != 0o500
    ):
        _blocked("P1 native guard executable binding is invalid")
    return NativeEntryGuardAttestation(
        target=_GUARD_TARGET,
        guarded_executable=_PYTHON_TARGET,
        binary_sha256=binary_sha256,
        binary_size=binary_size,
        mode=0o500,
        source=str(value["source"]),
        source_sha256=str(value["source_sha256"]),
        cargo_manifest=str(value["cargo_manifest"]),
        cargo_manifest_sha256=str(value["cargo_manifest_sha256"]),
        cargo_lock=str(value["cargo_lock"]),
        cargo_lock_sha256=str(value["cargo_lock_sha256"]),
        cargo_identity=str(value["cargo_identity"]),
        rustc_identity=str(value["rustc_identity"]),
        rust_toolchain_policy_sha256=str(value["rust_toolchain_policy_sha256"]),
        llvm_toolchain_policy_sha256=str(value["llvm_toolchain_policy_sha256"]),
        target_triple=str(value["target_triple"]),
    )


def attest_p1_nautilus_closure(
    config: NautilusClosureConfig,
) -> P1EngineClosureAttestation:
    """Attest one exact schema-8 P1 product closure and derived lineage."""

    if type(config) is not NautilusClosureConfig:
        raise TypeError("NautilusClosureConfig is required")
    policy = _load_policy()
    _legacy._ensure_external_private_directory(config.runtime_root, "P1 runtime root")
    _legacy._ensure_external_private_directory(
        config.artifact_directory, "P1 artifact directory"
    )
    manifest_path = config.runtime_root / _MANIFEST_NAME
    manifest, closure_manifest = _manifest_snapshot(manifest_path)
    profile = P1_REAL_BACKTEST_POLICY
    policy_bound_fields = {
        "argv_prefix": list(profile.argv_prefix),
        "artifact_manifest_sha256": policy["artifact_manifest_sha256"],
        "candidate_closure_sha256": policy["candidate_closure_sha256"],
        "candidate_generation_id": policy["candidate_generation_id"],
        "candidate_generation_sha256": policy["candidate_generation_sha256"],
        "command_type": profile.command_type,
        "dependency_import_policy": profile.dependency_import_policy,
        "engine_name": policy["engine_name"],
        "engine_upstream_commit": profile.engine_upstream_commit,
        "engine_version": profile.engine_version,
        "entrypoint": profile.entrypoint,
        "event_schema": profile.event_schema,
        "p1_baseline_receipt_sha256": profile.p1_baseline_receipt_sha256,
        "p1_baseline_scope": profile.p1_baseline_scope,
        "p1_baseline_status": profile.p1_baseline_status,
        "profile": profile.profile,
        "python_identity": policy["python_identity"],
        "request_protocol_version": profile.request_protocol_version,
        "required_artifact_names": list(profile.required_artifact_names),
        "result_validator_id": profile.result_validator_id,
        "runtime_family": profile.runtime_family,
        "runtime_inventory_sha256": profile.runtime_inventory_sha256,
        "sandbox_profile_sha256": profile.sandbox_profile_sha256,
        "schema_version": profile.manifest_schema_version,
        "semantic_profile": profile.semantic_profile,
        "timeout_seconds": profile.timeout_seconds,
    }
    if (
        set(manifest) != _MANIFEST_FIELDS
        or any(manifest.get(key) != value for key, value in policy_bound_fields.items())
        or type(manifest.get("source_commit")) is not str
        or _SOURCE_COMMIT.fullmatch(str(manifest["source_commit"])) is None
        or manifest["source_commit"] == profile.engine_upstream_commit
    ):
        _blocked("P1 closure manifest identity is invalid")
    artifact_manifest = config.artifact_directory / "artifact-manifest.json"
    _legacy._sealed_file(artifact_manifest, "P1 artifact manifest")
    if _legacy._sha256_path(artifact_manifest) != policy["artifact_manifest_sha256"]:
        _blocked("P1 artifact manifest digest drifted")
    mounts = _legacy._manifest_files(config.runtime_root, manifest["files"])
    if any(mount.target == _LINEAGE_TARGET for mount in mounts):
        _blocked("P1 product lineage must remain outside the closure inventory")
    inventory = policy["runtime_inventory"]
    assert isinstance(inventory, list)
    expected_runtime = {
        (record["target"], record["mode"], record["sha256"])
        for record in inventory
        if isinstance(record, dict)
    }
    observed_runtime = {
        (str(mount.target), f"{mount.mode:04o}", mount.sha256)
        for mount in mounts
        if mount.target.is_relative_to(PurePosixPath("/engine/runtime_v1"))
    }
    if observed_runtime != expected_runtime:
        _blocked("P1 runtime inventory does not match the code-owned policy")
    wheel = policy["engine_wheel"]
    assert isinstance(wheel, dict)
    matching_wheels = [
        mount for mount in mounts if str(mount.target) == wheel["target"]
    ]
    if (
        len(matching_wheels) != 1
        or f"{matching_wheels[0].mode:04o}" != wheel["mode"]
        or matching_wheels[0].sha256 != wheel["sha256"]
        or matching_wheels[0].size != wheel["size"]
    ):
        _blocked("P1 engine wheel does not match the promoted generation")
    entrypoint = PurePosixPath(str(manifest["entrypoint"]))
    if entrypoint != _GUARD_TARGET:
        _blocked("P1 closure entrypoint is invalid")
    native_guard = _native_guard(
        manifest["native_entry_guard"], policy=policy, mounts=mounts
    )
    sandbox = _legacy._sandbox_proof(config.sandbox_executable)
    if sandbox.profile_sha256 != profile.sandbox_profile_sha256:
        _blocked("P1 sandbox profile authority is invalid")
    closure_sha256 = p1_closure_authority_sha256(manifest, mounts)
    lineage = derive_p1_product_lineage(
        closure_sha256, profile.runtime_inventory_sha256
    )
    product_lineage = _lineage_mount(config.runtime_root, lineage)
    return P1EngineClosureAttestation(
        manifest_schema_version=profile.manifest_schema_version,
        profile=profile.profile,
        source_commit=str(manifest["source_commit"]),
        closure_sha256=closure_sha256,
        mounts=mounts,
        entrypoint=entrypoint,
        argv_prefix=profile.argv_prefix,
        timeout_seconds=profile.timeout_seconds,
        result_validator_id=profile.result_validator_id,
        sandbox=sandbox,
        semantic_profile=profile.semantic_profile,
        closure_manifest=closure_manifest,
        native_entry_guard=native_guard,
        dependency_import_policy=profile.dependency_import_policy,
        runtime_family=profile.runtime_family,
        engine_version=profile.engine_version,
        engine_upstream_commit=profile.engine_upstream_commit,
        event_schema=profile.event_schema,
        runtime_inventory_sha256=profile.runtime_inventory_sha256,
        product_lineage=product_lineage,
    )


__all__ = [
    "NautilusClosureConfig",
    "attest_p1_nautilus_closure",
    "derive_p1_product_lineage",
    "p1_closure_authority_sha256",
]
