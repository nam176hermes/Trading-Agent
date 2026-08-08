from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path, PurePosixPath

import pytest

import services.job_worker.nautilus_closure as nautilus_closure_module
from services.job_worker.engine_spawn import (
    EngineSpawnError,
    OsSandboxProof,
    ReadOnlyClosureMount,
)
from services.job_worker.nautilus_closure import (
    NautilusClosureConfig,
    attest_nautilus_backtest_closure,
)


SOURCE_COMMIT = "280ae1762df51a492a4ce71506a40b5c8706def5"
REPOSITORY_SOURCE_COMMIT = "4648ecaf6169b0886daf47fe27467b0292153cbb"
NATIVE_GUARD_VALUE = b"reviewed-native-entry-guard-v1"
DEPENDENCY_IMPORT_POLICY = (
    "native-guarded-stdlib-first-sealed-wheel-path-v1"
)


def _native_guard_record() -> dict[str, object]:
    return {
        "binary_sha256": hashlib.sha256(NATIVE_GUARD_VALUE).hexdigest(),
        "binary_size": len(NATIVE_GUARD_VALUE),
        "cargo_identity": "cargo 1.95.0 (fixture)",
        "cargo_lock": "engines/nautilus/native_entry_guard/Cargo.lock",
        "cargo_lock_sha256": "1" * 64,
        "cargo_manifest": "engines/nautilus/native_entry_guard/Cargo.toml",
        "cargo_manifest_sha256": "2" * 64,
        "llvm_toolchain_policy_sha256": "3" * 64,
        "mode": "0500",
        "rust_toolchain_policy_sha256": "4" * 64,
        "rustc_identity": "rustc 1.95.0 (fixture)",
        "source": "engines/nautilus/native_entry_guard/src/main.rs",
        "source_sha256": "5" * 64,
        "target": "/engine/bin/nautilus-entry-guard",
        "target_triple": "x86_64-unknown-linux-gnu",
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_file(root: Path, path: Path, value: bytes, mode: int) -> dict[str, object]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)
    path.chmod(mode)
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": _sha256(path),
        "size": len(value),
        "mode": f"{mode:04o}",
    }


def _seal(root: Path) -> None:
    for directory, child_directories, files in os.walk(root, topdown=False):
        current = Path(directory)
        for name in files:
            (current / name).chmod(
                0o500
                if name in {"python3.12", "nautilus-entry-guard"}
                else 0o400
            )
        for name in child_directories:
            (current / name).chmod(0o500)
    root.chmod(0o500)


@pytest.fixture
def closure_config() -> NautilusClosureConfig:
    if not Path("/usr/bin/bwrap").is_file():
        pytest.skip("Bubblewrap is required for the Nautilus closure test")

    base = Path(tempfile.mkdtemp(prefix="nautilus-closure-test-", dir="/home/thenam176/.cache"))
    try:
        sandbox = base / "bwrap"
        shutil.copyfile("/usr/bin/bwrap", sandbox)
        sandbox.chmod(0o500)
        yield _build_closure_config(base, sandbox, profile="zero-order")
    finally:
        for directory, child_directories, files in os.walk(base, topdown=False):
            current = Path(directory)
            for name in files:
                (current / name).chmod(0o600)
            for name in child_directories:
                (current / name).chmod(0o700)
            current.chmod(0o700)
        shutil.rmtree(base)


def _build_closure_config(
    tmp_path: Path,
    sandbox: Path,
    *,
    profile: str,
    semantic_profile: str | None = None,
    source_commit: str = SOURCE_COMMIT,
    engine_upstream_commit: str | None = None,
    native_guard: bool = False,
    dependency_import_policy: object | None = None,
):

    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(mode=0o700)
    artifact_manifest = artifacts / "artifact-manifest.json"
    artifact_manifest.write_text(
        json.dumps(
            {
                "engine_name": "nautilus_trader",
                "engine_version": "1.227.0",
                "python_identity": "CPython 3.12.3",
                "upstream_commit": SOURCE_COMMIT,
                "wheel": {"sha256": "a" * 64},
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    artifact_manifest.chmod(0o400)
    artifacts.chmod(0o500)

    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    files = [
        _write_file(runtime, runtime / "files/bin/python3.12", b"python", 0o500),
        _write_file(runtime, runtime / "files/launcher/nautilus_backtest.py", b"launcher", 0o400),
        _write_file(runtime, runtime / "files/lib/python3.12/encodings/__init__.py", b"", 0o400),
    ]
    targets = [
        "/engine/bin/python3.12",
        "/engine/launcher/nautilus_backtest.py",
        "/engine/lib/python3.12/encodings/__init__.py",
    ]
    native_guard_record: dict[str, object] | None = None
    if native_guard:
        targets[0] = "/usr/bin/python3.12"
        guard_file = _write_file(
            runtime,
            runtime / "files/engine/bin/nautilus-entry-guard",
            NATIVE_GUARD_VALUE,
            0o500,
        )
        files.append(guard_file)
        targets.append("/engine/bin/nautilus-entry-guard")
        native_guard_record = _native_guard_record()
    if engine_upstream_commit is not None:
        files.append(
            _write_file(
                runtime,
                runtime / "files/engine/launcher/target_portfolio_strategy.py",
                b"class TargetPortfolioStrategy: pass\nclass TargetPortfolioStrategyConfig: pass\n",
                0o400,
            )
        )
        targets.append("/engine/launcher/target_portfolio_strategy.py")
    records = [dict(item, target=target) for item, target in zip(files, targets, strict=True)]
    profiles = {
        "zero-order": (
            ["-I", "-S", "/engine/launcher/nautilus_backtest.py"],
            "nautilus-backtest-result-v1",
        ),
        "execution-simulation": (
            [
                "-I",
                "-S",
                "/engine/launcher/nautilus_backtest.py",
                "--profile",
                "execution-simulation",
            ],
            "nautilus-backtest-simulation-result-v1",
        ),
    }
    argv_prefix, validator_id = profiles[profile]
    if native_guard:
        argv_prefix = ["/usr/bin/python3.12", *argv_prefix]
    manifest = {
        "schema_version": (
            6
            if dependency_import_policy is not None
            else 5 if native_guard
            else 4
            if engine_upstream_commit is not None
            else 3 if semantic_profile is not None else 2
        ),
        "profile": profile,
        "engine_name": "nautilus_trader",
        "engine_version": "1.227.0",
        "python_identity": "CPython 3.12.3",
        "source_commit": source_commit,
        "artifact_manifest_sha256": _sha256(artifact_manifest),
        "entrypoint": (
            "/engine/bin/nautilus-entry-guard"
            if native_guard
            else "/engine/bin/python3.12"
        ),
        "argv_prefix": argv_prefix,
        "timeout_seconds": 120,
        "result_validator_id": validator_id,
        "files": records,
    }
    if semantic_profile is not None:
        manifest["semantic_profile"] = semantic_profile
    if engine_upstream_commit is not None:
        manifest["engine_upstream_commit"] = engine_upstream_commit
    if native_guard_record is not None:
        manifest["native_entry_guard"] = native_guard_record
    if dependency_import_policy is not None:
        manifest["dependency_import_policy"] = dependency_import_policy
    (runtime / "closure-manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )
    _seal(runtime)
    return NautilusClosureConfig(
        runtime_root=runtime,
        artifact_directory=artifacts,
        sandbox_executable=sandbox,
    )


def test_attestor_rejects_artifact_manifest_drift_before_spawn(
    closure_config: NautilusClosureConfig,
) -> None:
    artifacts = closure_config.artifact_directory
    artifacts.chmod(0o700)
    manifest = artifacts / "artifact-manifest.json"
    manifest.chmod(0o600)
    manifest.write_text("{}", encoding="utf-8")
    manifest.chmod(0o400)
    artifacts.chmod(0o500)

    with pytest.raises(EngineSpawnError, match="artifact"):
        attest_nautilus_backtest_closure(
            closure_config, expected_profile="zero-order"
        )


def test_attestor_binds_only_read_only_launcher_and_python_closure_files(
    closure_config: NautilusClosureConfig,
) -> None:
    closure = attest_nautilus_backtest_closure(
        closure_config, expected_profile="zero-order"
    )

    assert str(closure.entrypoint) == "/engine/bin/python3.12"
    assert closure.argv_prefix == (
        "-I",
        "-S",
        "/engine/launcher/nautilus_backtest.py",
    )
    assert closure.profile == "zero-order"
    assert {str(mount.target) for mount in closure.mounts} == {
        "/engine/bin/python3.12",
        "/engine/launcher/nautilus_backtest.py",
        "/engine/lib/python3.12/encodings/__init__.py",
    }
    assert all(mount.mode & 0o222 == 0 for mount in closure.mounts)
    assert all(mount.source.is_relative_to(closure_config.runtime_root) for mount in closure.mounts)


@pytest.mark.parametrize("native_guard", (False, True))
def test_attestor_separates_repository_and_engine_upstream_identities_before_sandbox(
    monkeypatch: pytest.MonkeyPatch,
    native_guard: bool,
) -> None:
    def reviewed_sandbox(path: Path) -> OsSandboxProof:
        return OsSandboxProof(
            executable=path,
            identity=(1, 1),
            executable_sha256="b" * 64,
            profile_sha256="c" * 64,
            version="bubblewrap 0.9.0",
            capabilities=("--perms", "--ro-bind-data"),
        )

    monkeypatch.setattr(nautilus_closure_module, "_sandbox_proof", reviewed_sandbox)
    base = Path(tempfile.mkdtemp(prefix="nautilus-split-identity-test-", dir="/tmp"))
    try:
        valid_root = base / "valid"
        valid_root.mkdir()
        valid_config = _build_closure_config(
            valid_root,
            valid_root / "not-invoked-bwrap",
            profile="execution-simulation",
            semantic_profile="nautilus-execution-simulation-v2",
            source_commit=REPOSITORY_SOURCE_COMMIT,
            engine_upstream_commit=SOURCE_COMMIT,
            native_guard=native_guard,
        )

        closure = attest_nautilus_backtest_closure(
            valid_config, expected_profile="execution-simulation"
        )

        assert closure.manifest_schema_version == (5 if native_guard else 4)
        assert closure.source_commit == REPOSITORY_SOURCE_COMMIT

        identical_root = base / "identical"
        identical_root.mkdir()
        identical_config = _build_closure_config(
            identical_root,
            identical_root / "not-invoked-bwrap",
            profile="execution-simulation",
            semantic_profile="nautilus-execution-simulation-v2",
            source_commit=SOURCE_COMMIT,
            engine_upstream_commit=SOURCE_COMMIT,
            native_guard=native_guard,
        )
        with pytest.raises(EngineSpawnError, match="closure manifest identity"):
            attest_nautilus_backtest_closure(
                identical_config, expected_profile="execution-simulation"
            )

        mismatch_root = base / "mismatch"
        mismatch_root.mkdir()
        mismatch_config = _build_closure_config(
            mismatch_root,
            mismatch_root / "not-invoked-bwrap",
            profile="execution-simulation",
            semantic_profile="nautilus-execution-simulation-v2",
            source_commit=REPOSITORY_SOURCE_COMMIT,
            engine_upstream_commit="f" * 40,
            native_guard=native_guard,
        )
        with pytest.raises(EngineSpawnError, match="artifact manifest identity"):
            attest_nautilus_backtest_closure(
                mismatch_config, expected_profile="execution-simulation"
            )
    finally:
        for directory, child_directories, files in os.walk(base, topdown=False):
            current = Path(directory)
            for name in files:
                (current / name).chmod(0o600)
            for name in child_directories:
                (current / name).chmod(0o700)
            current.chmod(0o700)
        shutil.rmtree(base)


def test_v4_closure_digest_preserves_engine_upstream_and_sidecar_binding() -> None:
    common = {
        "schema_version": 4,
        "argv_prefix": [
            "-I",
            "-S",
            "/engine/launcher/nautilus_backtest.py",
            "--profile",
            "execution-simulation",
        ],
        "result_validator_id": "nautilus-backtest-simulation-result-v1",
        "source_commit": REPOSITORY_SOURCE_COMMIT,
    }
    first_manifest = ReadOnlyClosureMount(
        source=Path("/sealed/closure-manifest.json"),
        target=PurePosixPath("/engine/closure-manifest.json"),
        identity=(1, 2),
        size=101,
        mode=0o400,
        sha256="1" * 64,
    )

    def closure_digest(
        engine_upstream_commit: str,
        sidecar: ReadOnlyClosureMount,
    ) -> str:
        return nautilus_closure_module._closure_digest(
            closure_manifest={
                **common,
                "engine_upstream_commit": engine_upstream_commit,
            },
            artifact_digest="a" * 64,
            profile="execution-simulation",
            semantic_profile="nautilus-execution-simulation-v2",
            mounts=(),
            entrypoint=PurePosixPath("/engine/bin/python3.12"),
            timeout=120,
            closure_manifest_sidecar=sidecar,
        )

    baseline = closure_digest(SOURCE_COMMIT, first_manifest)
    expected_document = {
        "artifact_manifest_sha256": "a" * 64,
        "argv_prefix": common["argv_prefix"],
        "closure_manifest": {
            "identity": [1, 2],
            "mode": "0400",
            "sha256": "1" * 64,
            "size": 101,
            "target": "/engine/closure-manifest.json",
        },
        "engine_upstream_commit": SOURCE_COMMIT,
        "entrypoint": "/engine/bin/python3.12",
        "files": [],
        "profile": "execution-simulation",
        "result_validator_id": "nautilus-backtest-simulation-result-v1",
        "semantic_profile": "nautilus-execution-simulation-v2",
        "source_commit": REPOSITORY_SOURCE_COMMIT,
        "timeout_seconds": 120,
    }

    assert baseline == hashlib.sha256(
        json.dumps(
            expected_document,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
    ).hexdigest()
    assert baseline != closure_digest("f" * 40, first_manifest)
    assert baseline != closure_digest(
        SOURCE_COMMIT,
        replace(first_manifest, identity=(3, 4), sha256="2" * 64),
    )
    with pytest.raises(EngineSpawnError, match="schema-v4.*sidecar"):
        nautilus_closure_module._closure_digest(
            closure_manifest={
                **common,
                "engine_upstream_commit": SOURCE_COMMIT,
            },
            artifact_digest="a" * 64,
            profile="execution-simulation",
            semantic_profile="nautilus-execution-simulation-v2",
            mounts=(),
            entrypoint=PurePosixPath("/engine/bin/python3.12"),
            timeout=120,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("identity", (3, 4)),
        ("mode", 0o500),
        ("size", 102),
        ("sha256", "2" * 64),
        ("target", PurePosixPath("/engine/other-manifest.json")),
    ),
)
def test_v4_closure_digest_binds_each_manifest_sidecar_field_independently(
    field: str,
    value: object,
) -> None:
    common = {
        "schema_version": 4,
        "argv_prefix": [
            "-I",
            "-S",
            "/engine/launcher/nautilus_backtest.py",
            "--profile",
            "execution-simulation",
        ],
        "result_validator_id": "nautilus-backtest-simulation-result-v1",
        "source_commit": REPOSITORY_SOURCE_COMMIT,
        "engine_upstream_commit": SOURCE_COMMIT,
    }
    sidecar = ReadOnlyClosureMount(
        source=Path("/sealed/closure-manifest.json"),
        target=PurePosixPath("/engine/closure-manifest.json"),
        identity=(1, 2),
        size=101,
        mode=0o400,
        sha256="1" * 64,
    )

    def closure_digest(value: ReadOnlyClosureMount) -> str:
        return nautilus_closure_module._closure_digest(
            closure_manifest=common,
            artifact_digest="a" * 64,
            profile="execution-simulation",
            semantic_profile="nautilus-execution-simulation-v2",
            mounts=(),
            entrypoint=PurePosixPath("/engine/bin/python3.12"),
            timeout=120,
            closure_manifest_sidecar=value,
        )

    assert closure_digest(sidecar) != closure_digest(
        replace(sidecar, **{field: value})
    )


def test_v5_closure_digest_binds_engine_upstream_identity() -> None:
    common = {
        "schema_version": 5,
        "argv_prefix": [
            "/usr/bin/python3.12",
            "-I",
            "-S",
            "/engine/launcher/nautilus_backtest.py",
            "--profile",
            "execution-simulation",
        ],
        "result_validator_id": "nautilus-backtest-simulation-result-v1",
        "source_commit": REPOSITORY_SOURCE_COMMIT,
        "native_entry_guard": _native_guard_record(),
    }
    first_manifest = ReadOnlyClosureMount(
        source=Path("/sealed/closure-manifest.json"),
        target=PurePosixPath("/engine/closure-manifest.json"),
        identity=(1, 2),
        size=101,
        mode=0o400,
        sha256="1" * 64,
    )
    first = nautilus_closure_module._closure_digest(
        closure_manifest={**common, "engine_upstream_commit": SOURCE_COMMIT},
        artifact_digest="a" * 64,
        profile="execution-simulation",
        semantic_profile="nautilus-execution-simulation-v2",
        mounts=(),
        entrypoint=PurePosixPath("/engine/bin/nautilus-entry-guard"),
        timeout=120,
        closure_manifest_sidecar=first_manifest,
    )
    second = nautilus_closure_module._closure_digest(
        closure_manifest={**common, "engine_upstream_commit": "f" * 40},
        artifact_digest="a" * 64,
        profile="execution-simulation",
        semantic_profile="nautilus-execution-simulation-v2",
        mounts=(),
        entrypoint=PurePosixPath("/engine/bin/nautilus-entry-guard"),
        timeout=120,
        closure_manifest_sidecar=first_manifest,
    )

    assert first != second


def test_v5_closure_digest_explicitly_binds_manifest_schema_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def capture_document(value: object) -> bytes:
        assert isinstance(value, dict)
        captured.update(value)
        return b"{}"

    monkeypatch.setattr(
        nautilus_closure_module,
        "_canonical_json_bytes",
        capture_document,
    )
    nautilus_closure_module._closure_digest(
        closure_manifest={
            "schema_version": 5,
            "argv_prefix": [
                "/usr/bin/python3.12",
                "-I",
                "-S",
                "/engine/launcher/nautilus_backtest.py",
                "--profile",
                "execution-simulation",
            ],
            "result_validator_id": "nautilus-backtest-simulation-result-v1",
            "source_commit": REPOSITORY_SOURCE_COMMIT,
            "engine_upstream_commit": SOURCE_COMMIT,
            "native_entry_guard": _native_guard_record(),
        },
        artifact_digest="a" * 64,
        profile="execution-simulation",
        semantic_profile="nautilus-execution-simulation-v2",
        mounts=(),
        entrypoint=PurePosixPath("/engine/bin/nautilus-entry-guard"),
        timeout=120,
        closure_manifest_sidecar=ReadOnlyClosureMount(
            source=Path("/sealed/closure-manifest.json"),
            target=PurePosixPath("/engine/closure-manifest.json"),
            identity=(1, 2),
            size=101,
            mode=0o400,
            sha256="1" * 64,
        ),
    )

    assert captured["manifest_schema_version"] == 5


def test_schema_6_closure_digest_binds_only_dependency_import_policy_change() -> None:
    common = {
        "schema_version": 6,
        "argv_prefix": [
            "/usr/bin/python3.12",
            "-I",
            "-S",
            "/engine/launcher/nautilus_backtest.py",
            "--profile",
            "execution-simulation",
        ],
        "result_validator_id": "nautilus-backtest-simulation-result-v1",
        "source_commit": REPOSITORY_SOURCE_COMMIT,
        "engine_upstream_commit": SOURCE_COMMIT,
        "native_entry_guard": _native_guard_record(),
    }
    sidecar = ReadOnlyClosureMount(
        source=Path("/sealed/closure-manifest.json"),
        target=PurePosixPath("/engine/closure-manifest.json"),
        identity=(1, 2),
        size=101,
        mode=0o400,
        sha256="1" * 64,
    )

    def closure_digest(dependency_import_policy: object) -> str:
        return nautilus_closure_module._closure_digest(
            closure_manifest={
                **common,
                "dependency_import_policy": dependency_import_policy,
            },
            artifact_digest="a" * 64,
            profile="execution-simulation",
            semantic_profile="nautilus-execution-simulation-v2",
            mounts=(),
            entrypoint=PurePosixPath("/engine/bin/nautilus-entry-guard"),
            timeout=120,
            closure_manifest_sidecar=sidecar,
        )

    assert closure_digest(DEPENDENCY_IMPORT_POLICY) != closure_digest(
        "ambient-site-packages-first"
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("source_sha256", "0" * 64),
        ("binary_sha256", "6" * 64),
        ("cargo_lock_sha256", "7" * 64),
        ("mode", "0400"),
    ),
)
def test_v5_closure_digest_binds_native_guard_provenance(
    field: str,
    value: object,
) -> None:
    native_guard = _native_guard_record()
    common = {
        "schema_version": 5,
        "argv_prefix": [
            "/usr/bin/python3.12",
            "-I",
            "-S",
            "/engine/launcher/nautilus_backtest.py",
            "--profile",
            "execution-simulation",
        ],
        "result_validator_id": "nautilus-backtest-simulation-result-v1",
        "source_commit": REPOSITORY_SOURCE_COMMIT,
        "engine_upstream_commit": SOURCE_COMMIT,
        "native_entry_guard": native_guard,
    }
    sidecar = ReadOnlyClosureMount(
        source=Path("/sealed/closure-manifest.json"),
        target=PurePosixPath("/engine/closure-manifest.json"),
        identity=(1, 2),
        size=101,
        mode=0o400,
        sha256="1" * 64,
    )

    def closure_digest(manifest: dict[str, object]) -> str:
        return nautilus_closure_module._closure_digest(
            closure_manifest=manifest,
            artifact_digest="a" * 64,
            profile="execution-simulation",
            semantic_profile="nautilus-execution-simulation-v2",
            mounts=(),
            entrypoint=PurePosixPath("/engine/bin/nautilus-entry-guard"),
            timeout=120,
            closure_manifest_sidecar=sidecar,
        )

    drifted_guard = {**native_guard, field: value}
    assert closure_digest(common) != closure_digest(
        {**common, "native_entry_guard": drifted_guard}
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("identity", (3, 4)),
        ("mode", 0o500),
        ("size", 102),
        ("sha256", "2" * 64),
        ("target", PurePosixPath("/engine/other-manifest.json")),
    ),
)
def test_v5_closure_digest_binds_each_manifest_sidecar_field_independently(
    field: str,
    value: object,
) -> None:
    common = {
        "schema_version": 5,
        "argv_prefix": [
            "/usr/bin/python3.12",
            "-I",
            "-S",
            "/engine/launcher/nautilus_backtest.py",
            "--profile",
            "execution-simulation",
        ],
        "result_validator_id": "nautilus-backtest-simulation-result-v1",
        "source_commit": REPOSITORY_SOURCE_COMMIT,
        "engine_upstream_commit": SOURCE_COMMIT,
        "native_entry_guard": _native_guard_record(),
    }
    first_manifest = ReadOnlyClosureMount(
        source=Path("/sealed/closure-manifest.json"),
        target=PurePosixPath("/engine/closure-manifest.json"),
        identity=(1, 2),
        size=101,
        mode=0o400,
        sha256="1" * 64,
    )

    def closure_digest(sidecar: ReadOnlyClosureMount) -> str:
        return nautilus_closure_module._closure_digest(
            closure_manifest=common,
            artifact_digest="a" * 64,
            profile="execution-simulation",
            semantic_profile="nautilus-execution-simulation-v2",
            mounts=(),
            entrypoint=PurePosixPath("/engine/bin/nautilus-entry-guard"),
            timeout=120,
            closure_manifest_sidecar=sidecar,
        )

    assert closure_digest(first_manifest) != closure_digest(
        replace(first_manifest, **{field: value})
    )


@pytest.mark.parametrize(
    ("schema_version", "semantic_profile"),
    ((1, None), (2, None), (3, "nautilus-execution-simulation-v2")),
)
def test_v1_v3_closure_digests_ignore_optional_v5_manifest_sidecar(
    schema_version: int,
    semantic_profile: str | None,
) -> None:
    common = {
        "schema_version": schema_version,
        "argv_prefix": ["-I", "-S", "/engine/launcher/nautilus_backtest.py"],
        "result_validator_id": "nautilus-backtest-result-v1",
        "source_commit": SOURCE_COMMIT,
    }
    first_manifest = ReadOnlyClosureMount(
        source=Path("/sealed/closure-manifest.json"),
        target=PurePosixPath("/engine/closure-manifest.json"),
        identity=(1, 2),
        size=101,
        mode=0o400,
        sha256="1" * 64,
    )
    second_manifest = replace(first_manifest, identity=(3, 4), sha256="2" * 64)

    digests = {
        nautilus_closure_module._closure_digest(
            closure_manifest=common,
            artifact_digest="a" * 64,
            profile="zero-order",
            semantic_profile=semantic_profile,
            mounts=(),
            entrypoint=PurePosixPath("/engine/bin/python3.12"),
            timeout=120,
            closure_manifest_sidecar=sidecar,
        )
        for sidecar in (first_manifest, second_manifest)
    }

    assert len(digests) == 1


def test_v5_attestor_binds_manifest_as_a_separate_fixed_sidecar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        nautilus_closure_module,
        "_sandbox_proof",
        lambda path: OsSandboxProof(
            executable=path,
            identity=(1, 1),
            executable_sha256="b" * 64,
            profile_sha256="c" * 64,
            version="bubblewrap 0.9.0",
            capabilities=("--perms", "--ro-bind-data"),
        ),
    )
    base = Path(tempfile.mkdtemp(prefix="nautilus-v5-manifest-sidecar-", dir="/tmp"))
    try:
        config = _build_closure_config(
            base,
            base / "not-invoked-bwrap",
            profile="execution-simulation",
            semantic_profile="nautilus-execution-simulation-v2",
            source_commit=REPOSITORY_SOURCE_COMMIT,
            engine_upstream_commit=SOURCE_COMMIT,
            native_guard=True,
        )

        closure = attest_nautilus_backtest_closure(
            config, expected_profile="execution-simulation"
        )

        sidecar = closure.closure_manifest
        assert sidecar is not None
        manifest_path = config.runtime_root / "closure-manifest.json"
        observed = manifest_path.stat(follow_symlinks=False)
        assert sidecar.source == manifest_path
        assert sidecar.target == PurePosixPath("/engine/closure-manifest.json")
        assert sidecar.identity == (observed.st_dev, observed.st_ino)
        assert sidecar.size == observed.st_size
        assert sidecar.mode == 0o400
        assert sidecar.sha256 == _sha256(manifest_path)
        assert sidecar not in closure.mounts
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert all(
            record["path"] != "closure-manifest.json"
            and record["target"] != "/engine/closure-manifest.json"
            for record in manifest["files"]
        )
    finally:
        for directory, child_directories, file_names in os.walk(base, topdown=False):
            current = Path(directory)
            for name in file_names:
                (current / name).chmod(0o600)
            for name in child_directories:
                (current / name).chmod(0o700)
            current.chmod(0o700)
        shutil.rmtree(base)


def _reviewed_sandbox(path: Path) -> OsSandboxProof:
    return OsSandboxProof(
        executable=path,
        identity=(1, 1),
        executable_sha256="b" * 64,
        profile_sha256="c" * 64,
        version="bubblewrap 0.9.0",
        capabilities=("--perms", "--ro-bind-data"),
    )


def _remove_test_tree(base: Path) -> None:
    for directory, child_directories, file_names in os.walk(base, topdown=False):
        current = Path(directory)
        for name in file_names:
            (current / name).chmod(0o600)
        for name in child_directories:
            (current / name).chmod(0o700)
        current.chmod(0o700)
    shutil.rmtree(base)


def test_v5_attestor_requires_native_guard_before_sealed_cpython(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        nautilus_closure_module, "_sandbox_proof", _reviewed_sandbox
    )
    base = Path(tempfile.mkdtemp(prefix="nautilus-v5-native-guard-", dir="/tmp"))
    try:
        config = _build_closure_config(
            base,
            base / "not-invoked-bwrap",
            profile="execution-simulation",
            semantic_profile="nautilus-execution-simulation-v2",
            source_commit=REPOSITORY_SOURCE_COMMIT,
            engine_upstream_commit=SOURCE_COMMIT,
            native_guard=True,
        )

        closure = attest_nautilus_backtest_closure(
            config, expected_profile="execution-simulation"
        )

        assert closure.manifest_schema_version == 5
        assert closure.dependency_import_policy is None
        assert closure.entrypoint == PurePosixPath(
            "/engine/bin/nautilus-entry-guard"
        )
        assert closure.argv_prefix == (
            "/usr/bin/python3.12",
            "-I",
            "-S",
            "/engine/launcher/nautilus_backtest.py",
            "--profile",
            "execution-simulation",
        )
        assert closure.entrypoint != PurePosixPath(closure.argv_prefix[0])
        guard = closure.native_entry_guard
        assert guard is not None
        assert guard.target == closure.entrypoint
        assert guard.guarded_executable == PurePosixPath("/usr/bin/python3.12")
        assert guard.mode == 0o500
        assert guard.binary_sha256 == next(
            mount.sha256
            for mount in closure.mounts
            if mount.target == closure.entrypoint
        )
    finally:
        _remove_test_tree(base)


def test_v4_attestor_preserves_direct_python_and_manifest_sidecar_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        nautilus_closure_module, "_sandbox_proof", _reviewed_sandbox
    )
    base = Path(tempfile.mkdtemp(prefix="nautilus-v4-direct-python-", dir="/tmp"))
    try:
        config = _build_closure_config(
            base,
            base / "not-invoked-bwrap",
            profile="execution-simulation",
            semantic_profile="nautilus-execution-simulation-v2",
            source_commit=REPOSITORY_SOURCE_COMMIT,
            engine_upstream_commit=SOURCE_COMMIT,
        )

        closure = attest_nautilus_backtest_closure(
            config, expected_profile="execution-simulation"
        )

        assert closure.manifest_schema_version == 4
        assert closure.dependency_import_policy is None
        assert closure.entrypoint == PurePosixPath("/engine/bin/python3.12")
        assert closure.argv_prefix == (
            "-I",
            "-S",
            "/engine/launcher/nautilus_backtest.py",
            "--profile",
            "execution-simulation",
        )
        assert closure.closure_manifest is not None
        assert closure.native_entry_guard is None
    finally:
        _remove_test_tree(base)


@pytest.mark.parametrize(
    "mutation",
    (
        "direct-python-entry",
        "missing-python-argv",
        "foreign-executable",
        "binary-hash",
        "binary-mode",
        "source-path",
        "source-hash",
        "missing-native-guard",
    ),
)
def test_v5_attestor_rejects_direct_python_or_native_guard_drift(
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    monkeypatch.setattr(
        nautilus_closure_module, "_sandbox_proof", _reviewed_sandbox
    )
    base = Path(tempfile.mkdtemp(prefix="nautilus-v5-native-drift-", dir="/tmp"))
    try:
        config = _build_closure_config(
            base,
            base / "not-invoked-bwrap",
            profile="execution-simulation",
            semantic_profile="nautilus-execution-simulation-v2",
            source_commit=REPOSITORY_SOURCE_COMMIT,
            engine_upstream_commit=SOURCE_COMMIT,
            native_guard=True,
        )
        manifest_path = config.runtime_root / "closure-manifest.json"
        config.runtime_root.chmod(0o700)
        manifest_path.chmod(0o600)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        guard = manifest["native_entry_guard"]
        if mutation == "direct-python-entry":
            manifest["entrypoint"] = "/usr/bin/python3.12"
        elif mutation == "missing-python-argv":
            manifest["argv_prefix"] = manifest["argv_prefix"][1:]
        elif mutation == "foreign-executable":
            manifest["argv_prefix"][0] = "/engine/bin/foreign-python"
        elif mutation == "binary-hash":
            guard["binary_sha256"] = "0" * 64
        elif mutation == "binary-mode":
            guard["mode"] = "0400"
        elif mutation == "source-path":
            guard["source"] = "engines/nautilus/native_entry_guard/src/foreign.rs"
        elif mutation == "source-hash":
            guard["source_sha256"] = "not-a-sha256"
        else:
            del manifest["native_entry_guard"]
        manifest_path.write_text(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        manifest_path.chmod(0o400)
        config.runtime_root.chmod(0o500)

        with pytest.raises(EngineSpawnError, match="native|entrypoint|identity|fields"):
            attest_nautilus_backtest_closure(
                config, expected_profile="execution-simulation"
            )
    finally:
        _remove_test_tree(base)


@pytest.mark.parametrize("schema_version", (1, 2, 3))
def test_v1_v3_attestor_preserves_legacy_direct_cpython_contracts(
    monkeypatch: pytest.MonkeyPatch,
    schema_version: int,
) -> None:
    monkeypatch.setattr(
        nautilus_closure_module,
        "_sandbox_proof",
        lambda path: OsSandboxProof(
            executable=path,
            identity=(1, 1),
            executable_sha256="b" * 64,
            profile_sha256="c" * 64,
            version="bubblewrap 0.9.0",
            capabilities=("--perms", "--ro-bind-data"),
        ),
    )
    base = Path(
        tempfile.mkdtemp(
            prefix=f"nautilus-v{schema_version}-manifest-compat-", dir="/tmp"
        )
    )
    try:
        profile = "execution-simulation" if schema_version == 3 else "zero-order"
        config = _build_closure_config(
            base,
            base / "not-invoked-bwrap",
            profile=profile,
            semantic_profile=(
                "nautilus-execution-simulation-v2"
                if schema_version == 3
                else None
            ),
        )
        if schema_version == 1:
            manifest_path = config.runtime_root / "closure-manifest.json"
            config.runtime_root.chmod(0o700)
            manifest_path.chmod(0o600)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["schema_version"] = 1
            del manifest["profile"]
            manifest_path.write_text(
                json.dumps(manifest, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            manifest_path.chmod(0o400)
            config.runtime_root.chmod(0o500)

        closure = attest_nautilus_backtest_closure(
            config, expected_profile=profile
        )

        assert closure.manifest_schema_version == schema_version
        assert closure.dependency_import_policy is None
        assert closure.closure_manifest is None
        assert closure.native_entry_guard is None
        assert closure.entrypoint == PurePosixPath("/engine/bin/python3.12")
    finally:
        for directory, child_directories, file_names in os.walk(base, topdown=False):
            current = Path(directory)
            for name in file_names:
                (current / name).chmod(0o600)
            for name in child_directories:
                (current / name).chmod(0o700)
            current.chmod(0o700)
        shutil.rmtree(base)


@pytest.mark.parametrize(
    "dependency_import_policy",
    (None, "ambient-site-packages-first", True),
)
def test_schema_6_attestor_rejects_missing_unknown_or_boolean_import_policy(
    monkeypatch: pytest.MonkeyPatch,
    dependency_import_policy: object,
) -> None:
    monkeypatch.setattr(
        nautilus_closure_module, "_sandbox_proof", _reviewed_sandbox
    )
    base = Path(tempfile.mkdtemp(prefix="nautilus-v6-import-policy-", dir="/tmp"))
    try:
        config = _build_closure_config(
            base,
            base / "not-invoked-bwrap",
            profile="execution-simulation",
            semantic_profile="nautilus-execution-simulation-v2",
            source_commit=REPOSITORY_SOURCE_COMMIT,
            engine_upstream_commit=SOURCE_COMMIT,
            native_guard=True,
            dependency_import_policy=DEPENDENCY_IMPORT_POLICY,
        )
        closure = attest_nautilus_backtest_closure(
            config, expected_profile="execution-simulation"
        )
        assert closure.manifest_schema_version == 6
        assert closure.dependency_import_policy == DEPENDENCY_IMPORT_POLICY

        manifest_path = config.runtime_root / "closure-manifest.json"
        config.runtime_root.chmod(0o700)
        manifest_path.chmod(0o600)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if dependency_import_policy is None:
            del manifest["dependency_import_policy"]
        else:
            manifest["dependency_import_policy"] = dependency_import_policy
        manifest_path.write_text(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        manifest_path.chmod(0o400)
        config.runtime_root.chmod(0o500)

        with pytest.raises(EngineSpawnError, match="ENGINE_CLOSURE_INVALID"):
            attest_nautilus_backtest_closure(
                config, expected_profile="execution-simulation"
            )
    finally:
        _remove_test_tree(base)


def test_attestor_rejects_unlisted_runtime_file(closure_config: NautilusClosureConfig) -> None:
    runtime = closure_config.runtime_root
    runtime.chmod(0o700)
    (runtime / "files").chmod(0o700)
    unexpected = runtime / "files/unexpected.py"
    unexpected.write_text("unexpected", encoding="utf-8")
    unexpected.chmod(0o400)
    (runtime / "files").chmod(0o500)
    runtime.chmod(0o500)

    with pytest.raises(EngineSpawnError, match="unlisted"):
        attest_nautilus_backtest_closure(
            closure_config, expected_profile="zero-order"
        )


@pytest.mark.parametrize("mutation", ["profile", "argv", "validator"])
def test_attestor_rejects_every_profile_identity_mismatch(
    closure_config: NautilusClosureConfig, mutation: str
) -> None:
    runtime = closure_config.runtime_root
    manifest_path = runtime / "closure-manifest.json"
    runtime.chmod(0o700)
    manifest_path.chmod(0o600)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if mutation == "profile":
        manifest["profile"] = "execution-simulation"
    elif mutation == "argv":
        manifest["argv_prefix"] = [
            "-I",
            "-S",
            "/engine/launcher/nautilus_backtest.py",
            "--profile",
            "execution-simulation",
        ]
    else:
        manifest["result_validator_id"] = "nautilus-backtest-simulation-result-v1"
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    manifest_path.chmod(0o400)
    runtime.chmod(0o500)

    with pytest.raises(EngineSpawnError, match="profile|identity"):
        attest_nautilus_backtest_closure(
            closure_config, expected_profile="zero-order"
        )


def test_attestor_accepts_only_an_explicit_execution_simulation_profile(
    tmp_path: Path,
) -> None:
    if not Path("/usr/bin/bwrap").is_file():
        pytest.skip("Bubblewrap is required for the Nautilus closure test")
    base = Path(
        tempfile.mkdtemp(
            prefix="nautilus-simulation-closure-test-",
            dir="/home/thenam176/.cache",
        )
    )
    try:
        sandbox = base / "bwrap"
        shutil.copyfile("/usr/bin/bwrap", sandbox)
        sandbox.chmod(0o500)
        config = _build_closure_config(
            base,
            sandbox,
            profile="execution-simulation",
            semantic_profile="nautilus-execution-simulation-v2",
        )

        closure = attest_nautilus_backtest_closure(
            config, expected_profile="execution-simulation"
        )

        assert closure.profile == "execution-simulation"
        assert closure.semantic_profile == "nautilus-execution-simulation-v2"
        assert closure.argv_prefix[-2:] == ("--profile", "execution-simulation")
        assert closure.result_validator_id == "nautilus-backtest-simulation-result-v1"
        with pytest.raises(EngineSpawnError, match="profile"):
            attest_nautilus_backtest_closure(config, expected_profile="zero-order")
    finally:
        for directory, child_directories, files in os.walk(base, topdown=False):
            current = Path(directory)
            for name in files:
                (current / name).chmod(0o600)
            for name in child_directories:
                (current / name).chmod(0o700)
            current.chmod(0o700)
        shutil.rmtree(base)


def test_attestor_rejects_legacy_execution_transport_manifest_for_v2_authority(
    tmp_path: Path,
) -> None:
    if not Path("/usr/bin/bwrap").is_file():
        pytest.skip("Bubblewrap is required for the Nautilus closure test")
    base = Path(
        tempfile.mkdtemp(prefix="nautilus-legacy-closure-test-", dir="/home/thenam176/.cache")
    )
    try:
        sandbox = base / "bwrap"
        shutil.copyfile("/usr/bin/bwrap", sandbox)
        sandbox.chmod(0o500)
        config = _build_closure_config(
            base, sandbox, profile="execution-simulation"
        )

        with pytest.raises(EngineSpawnError, match="semantic"):
            attest_nautilus_backtest_closure(
                config, expected_profile="execution-simulation"
            )
    finally:
        for directory, child_directories, files in os.walk(base, topdown=False):
            current = Path(directory)
            for name in files:
                (current / name).chmod(0o600)
            for name in child_directories:
                (current / name).chmod(0o700)
            current.chmod(0o700)
        shutil.rmtree(base)


@pytest.mark.parametrize("mutation", ["missing", "v1", "changed"])
def test_attestor_rejects_semantic_profile_manifest_drift(
    tmp_path: Path, mutation: str
) -> None:
    if not Path("/usr/bin/bwrap").is_file():
        pytest.skip("Bubblewrap is required for the Nautilus closure test")
    base = Path(
        tempfile.mkdtemp(prefix="nautilus-semantic-closure-test-", dir="/home/thenam176/.cache")
    )
    try:
        sandbox = base / "bwrap"
        shutil.copyfile("/usr/bin/bwrap", sandbox)
        sandbox.chmod(0o500)
        config = _build_closure_config(
            base,
            sandbox,
            profile="execution-simulation",
            semantic_profile="nautilus-execution-simulation-v2",
        )
        manifest_path = config.runtime_root / "closure-manifest.json"
        config.runtime_root.chmod(0o700)
        manifest_path.chmod(0o600)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if mutation == "missing":
            del manifest["semantic_profile"]
        elif mutation == "v1":
            manifest["semantic_profile"] = "nautilus-execution-simulation-v1"
        else:
            manifest["semantic_profile"] = "transport-only"
        manifest_path.write_text(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        manifest_path.chmod(0o400)
        config.runtime_root.chmod(0o500)

        with pytest.raises(EngineSpawnError, match="semantic|fields"):
            attest_nautilus_backtest_closure(
                config, expected_profile="execution-simulation"
            )
    finally:
        for directory, child_directories, files in os.walk(base, topdown=False):
            current = Path(directory)
            for name in files:
                (current / name).chmod(0o600)
            for name in child_directories:
                (current / name).chmod(0o700)
            current.chmod(0o700)
        shutil.rmtree(base)
