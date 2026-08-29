from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath

import pytest

import services.job_worker.p1_nautilus_closure as closure_module
from services.job_worker.engine_profiles import P1_REAL_BACKTEST_POLICY
from services.job_worker.engine_spawn import (
    EngineSpawnError,
    OsSandboxProof,
    ReadOnlyClosureMount,
)
from services.job_worker.p1_nautilus_closure import (
    NautilusClosureConfig,
    attest_p1_nautilus_closure,
    derive_p1_product_lineage,
    p1_closure_authority_sha256,
)


ROOT = Path(__file__).resolve().parents[2]
POLICY = json.loads(
    (ROOT / "engines/nautilus/p1-runtime-closure-policy.json").read_bytes()
)
SOURCE_COMMIT = "0123456789abcdef0123456789abcdef01234567"
GUARD_BYTES = b"p1-native-guard-fixture"


def _record(root: Path, source: Path, target: str, mode: int) -> dict[str, object]:
    raw = source.read_bytes()
    observed = source.stat(follow_symlinks=False)
    return {
        "path": source.relative_to(root).as_posix(),
        "target": target,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size": len(raw),
        "mode": f"{mode:04o}",
    }


def _mount(root: Path, record: dict[str, object]) -> ReadOnlyClosureMount:
    source = root / str(record["path"])
    observed = source.stat(follow_symlinks=False)
    return ReadOnlyClosureMount(
        source=source,
        target=PurePosixPath(str(record["target"])),
        identity=(observed.st_dev, observed.st_ino),
        size=int(record["size"]),
        mode=int(str(record["mode"]), 8),
        sha256=str(record["sha256"]),
    )


def _write(root: Path, relative: str, raw: bytes, mode: int) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    path.chmod(mode)
    return path


def _seal(root: Path) -> None:
    for directory, child_directories, files in os.walk(root, topdown=False):
        current = Path(directory)
        for name in files:
            path = current / name
            path.chmod(0o500 if name in {"python3.12", "nautilus-entry-guard"} else 0o400)
        for name in child_directories:
            (current / name).chmod(0o500)
        current.chmod(0o500)


def _unseal(root: Path) -> None:
    for directory, child_directories, files in os.walk(root):
        current = Path(directory)
        current.chmod(0o700)
        for name in child_directories:
            (current / name).chmod(0o700)
        for name in files:
            (current / name).chmod(0o600)


def _manifest(policy: dict[str, object], files: list[dict[str, object]]) -> dict[str, object]:
    guard_policy = dict(policy["native_entry_guard"])
    guard_policy.pop("build_environment")
    return {
        "schema_version": 8,
        "profile": policy["profile"],
        "semantic_profile": policy["semantic_profile"],
        "command_type": policy["command_type"],
        "runtime_family": policy["runtime_family"],
        "engine_name": policy["engine_name"],
        "engine_version": policy["engine_version"],
        "engine_upstream_commit": policy["engine_upstream_commit"],
        "python_identity": policy["python_identity"],
        "source_commit": SOURCE_COMMIT,
        "artifact_manifest_sha256": policy["artifact_manifest_sha256"],
        "candidate_generation_id": policy["candidate_generation_id"],
        "candidate_generation_sha256": policy["candidate_generation_sha256"],
        "candidate_closure_sha256": policy["candidate_closure_sha256"],
        "p1_baseline_receipt_sha256": policy["p1_baseline_receipt_sha256"],
        "p1_baseline_status": policy["p1_baseline_status"],
        "p1_baseline_scope": policy["p1_baseline_scope"],
        "entrypoint": policy["entrypoint"],
        "argv_prefix": policy["argv_prefix"],
        "timeout_seconds": policy["timeout_seconds"],
        "result_validator_id": policy["result_validator_id"],
        "request_protocol_version": policy["request_protocol_version"],
        "event_schema": policy["event_schema"],
        "required_artifact_names": policy["required_artifact_names"],
        "dependency_import_policy": policy["dependency_import_policy"],
        "runtime_inventory_sha256": policy["runtime_inventory_sha256"],
        "sandbox_profile_sha256": policy["sandbox_profile_sha256"],
        "native_entry_guard": {
            **guard_policy,
            "binary_sha256": hashlib.sha256(GUARD_BYTES).hexdigest(),
            "binary_size": len(GUARD_BYTES),
        },
        "files": files,
    }


@pytest.fixture
def p1_closure(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, NautilusClosureConfig, dict[str, object]]:
    base = Path(tempfile.mkdtemp(prefix="p1-schema8-closure-", dir="/tmp"))
    runtime = base / "runtime"
    artifacts = base / "artifacts"
    runtime.mkdir(mode=0o700)
    artifacts.mkdir(mode=0o700)
    artifact_manifest = artifacts / "artifact-manifest.json"
    artifact_manifest.write_bytes(b'{"fixture":"p1-artifact"}\n')
    artifact_manifest.chmod(0o400)
    policy = json.loads(json.dumps(POLICY))
    policy["artifact_manifest_sha256"] = hashlib.sha256(
        artifact_manifest.read_bytes()
    ).hexdigest()
    wheel_bytes = b"nautilus-1.231-wheel-fixture"
    policy["engine_wheel"] = {
        "mode": "0400",
        "sha256": hashlib.sha256(wheel_bytes).hexdigest(),
        "size": len(wheel_bytes),
        "target": policy["engine_wheel"]["target"],
    }

    files: list[dict[str, object]] = []
    for record in policy["runtime_inventory"]:
        assert isinstance(record, dict)
        source = ROOT / str(record["source"])
        destination = _write(
            runtime,
            "files" + str(record["target"]),
            source.read_bytes(),
            0o400,
        )
        files.append(_record(runtime, destination, str(record["target"]), 0o400))
    python = _write(runtime, "files/usr/bin/python3.12", b"python", 0o500)
    wheel = _write(
        runtime,
        "files" + str(policy["engine_wheel"]["target"]),
        wheel_bytes,
        0o400,
    )
    guard = _write(
        runtime,
        "files/engine/bin/nautilus-entry-guard",
        GUARD_BYTES,
        0o500,
    )
    files.extend(
        (
            _record(runtime, python, "/usr/bin/python3.12", 0o500),
            _record(runtime, wheel, str(policy["engine_wheel"]["target"]), 0o400),
            _record(runtime, guard, "/engine/bin/nautilus-entry-guard", 0o500),
        )
    )
    manifest = _manifest(policy, files)
    manifest_path = runtime / "closure-manifest.json"
    manifest_path.write_bytes(_canonical(manifest))
    manifest_path.chmod(0o400)
    mounts = tuple(_mount(runtime, record) for record in files)
    closure_sha256 = p1_closure_authority_sha256(manifest, mounts)
    lineage = derive_p1_product_lineage(
        closure_sha256, P1_REAL_BACKTEST_POLICY.runtime_inventory_sha256
    )
    lineage_path = runtime / "p1-product-lineage.json"
    lineage_path.write_bytes(_canonical(lineage))
    lineage_path.chmod(0o400)
    _seal(runtime)
    artifacts.chmod(0o500)

    sandbox = base / "sandbox"
    sandbox.write_bytes(b"sandbox")
    sandbox.chmod(0o500)
    proof = OsSandboxProof(
        executable=sandbox,
        identity=(1, 1),
        executable_sha256="a" * 64,
        profile_sha256=(
            "742d3d2cf313a0dc5832fd88d277da1d00e07c6e4abcc4ca51bf0ebcd7c3936e"
        ),
        version="bubblewrap 0.9.0",
        capabilities=("--perms", "--ro-bind-data"),
    )
    monkeypatch.setattr(closure_module, "_load_policy", lambda: policy)
    monkeypatch.setattr(closure_module._legacy, "_sandbox_proof", lambda path: proof)
    config = NautilusClosureConfig(runtime, artifacts, sandbox)
    try:
        yield base, config, manifest
    finally:
        _unseal(base)
        shutil.rmtree(base)


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, allow_nan=False, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("ascii")


def _replace_manifest(config: NautilusClosureConfig, manifest: dict[str, object]) -> None:
    _unseal(config.runtime_root)
    path = config.runtime_root / "closure-manifest.json"
    path.write_bytes(_canonical(manifest))
    _seal(config.runtime_root)


def test_schema8_attestor_binds_exact_policy_and_excludes_derived_lineage(
    p1_closure: tuple[Path, NautilusClosureConfig, dict[str, object]],
) -> None:
    _base, config, _manifest_value = p1_closure
    attestation = attest_p1_nautilus_closure(config)
    assert attestation.manifest_schema_version == 8
    assert attestation.profile == P1_REAL_BACKTEST_POLICY.profile
    assert attestation.runtime_family == "cython-v1"
    assert attestation.engine_version == "1.231.0"
    assert attestation.product_lineage is not None
    assert attestation.product_lineage.target == PurePosixPath(
        "/engine/p1-product-lineage.json"
    )
    assert all(
        mount.target != attestation.product_lineage.target
        for mount in attestation.mounts
    )
    assert json.loads(attestation.product_lineage.source.read_bytes())[
        "closure_sha256"
    ] == attestation.closure_sha256


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("schema_version", 7),
        ("schema_version", 9),
        ("profile", "qualification-only"),
        ("semantic_profile", "wrong"),
        ("result_validator_id", "wrong"),
        ("event_schema", "wrong"),
        ("candidate_generation_sha256", "0" * 64),
        ("candidate_closure_sha256", "0" * 64),
        ("p1_baseline_receipt_sha256", "0" * 64),
        ("p1_baseline_status", "HOLD"),
        ("p1_baseline_scope", "GLOBAL"),
        ("dependency_import_policy", "ambient"),
        ("entrypoint", "/engine/runtime_v1/main.py"),
        ("request_protocol_version", "1.0.1"),
        ("sandbox_profile_sha256", "0" * 64),
        ("argv_prefix", ["/usr/bin/python3.12", "--version"]),
    ),
)
def test_schema8_attestor_rejects_profile_generation_or_baseline_substitution(
    p1_closure: tuple[Path, NautilusClosureConfig, dict[str, object]],
    field: str,
    value: object,
) -> None:
    _base, config, manifest = p1_closure
    _replace_manifest(config, {**manifest, field: value})
    with pytest.raises(EngineSpawnError, match="manifest identity"):
        attest_p1_nautilus_closure(config)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("binary_sha256", "0" * 64),
        ("binary_size", len(GUARD_BYTES) + 1),
        ("source_sha256", "0" * 64),
        ("target", "/engine/bin/alternate-guard"),
        ("cargo_identity", "ambient cargo"),
    ),
)
def test_schema8_attestor_rejects_native_guard_identity_or_provenance_drift(
    p1_closure: tuple[Path, NautilusClosureConfig, dict[str, object]],
    field: str,
    value: object,
) -> None:
    _base, config, manifest = p1_closure
    changed = json.loads(json.dumps(manifest))
    changed["native_entry_guard"][field] = value
    _replace_manifest(config, changed)
    with pytest.raises(EngineSpawnError, match="native guard"):
        attest_p1_nautilus_closure(config)


def test_derived_lineage_tamper_or_self_reference_is_rejected(
    p1_closure: tuple[Path, NautilusClosureConfig, dict[str, object]],
) -> None:
    _base, config, manifest = p1_closure
    _unseal(config.runtime_root)
    lineage = config.runtime_root / "p1-product-lineage.json"
    document = json.loads(lineage.read_bytes())
    document["closure_sha256"] = hashlib.sha256(_canonical(manifest)).hexdigest()
    lineage.write_bytes(_canonical(document))
    _seal(config.runtime_root)
    with pytest.raises(EngineSpawnError, match="lineage"):
        attest_p1_nautilus_closure(config)


def test_lineage_cannot_enter_the_hashed_file_inventory(
    p1_closure: tuple[Path, NautilusClosureConfig, dict[str, object]],
) -> None:
    _base, config, manifest = p1_closure
    files = list(manifest["files"])
    first = dict(files[0])
    first["target"] = "/engine/p1-product-lineage.json"
    files[0] = first
    _replace_manifest(config, {**manifest, "files": files})
    with pytest.raises(EngineSpawnError, match="lineage|inventory"):
        attest_p1_nautilus_closure(config)


def test_schema8_manifest_duplicate_key_is_rejected_before_sandbox_probe(
    p1_closure: tuple[Path, NautilusClosureConfig, dict[str, object]],
) -> None:
    _base, config, _manifest_value = p1_closure
    _unseal(config.runtime_root)
    path = config.runtime_root / "closure-manifest.json"
    path.write_bytes(b'{"schema_version":8,"schema_version":8}\n')
    _seal(config.runtime_root)
    with pytest.raises(EngineSpawnError, match="duplicate"):
        attest_p1_nautilus_closure(config)


def test_schema8_attestor_pins_one_manifest_snapshot_across_a_path_replacement(
    p1_closure: tuple[Path, NautilusClosureConfig, dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _base, config, manifest = p1_closure
    replacement_manifest = {
        **manifest,
        "source_commit": "fedcba9876543210fedcba9876543210fedcba98",
    }
    original_manifest_files = closure_module._legacy._manifest_files

    def swap_after_inventory(
        runtime_root: Path, value: object
    ) -> tuple[ReadOnlyClosureMount, ...]:
        mounts = original_manifest_files(runtime_root, value)
        replacement = runtime_root / "closure-manifest.next"
        runtime_root.chmod(0o700)
        replacement.write_bytes(_canonical(replacement_manifest))
        replacement.chmod(0o400)
        os.replace(replacement, runtime_root / "closure-manifest.json")
        runtime_root.chmod(0o500)
        return mounts

    monkeypatch.setattr(
        closure_module._legacy, "_manifest_files", swap_after_inventory
    )
    attestation = attest_p1_nautilus_closure(config)
    assert attestation.source_commit == manifest["source_commit"]
    assert attestation.closure_manifest is not None
    assert attestation.closure_manifest.sha256 == hashlib.sha256(
        _canonical(manifest)
    ).hexdigest()
    assert attestation.closure_manifest.sha256 != hashlib.sha256(
        (config.runtime_root / "closure-manifest.json").read_bytes()
    ).hexdigest()
    replaced = (config.runtime_root / "closure-manifest.json").stat()
    replacement_identity = (replaced.st_dev, replaced.st_ino)
    assert attestation.closure_manifest.identity != replacement_identity
