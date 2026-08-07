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
            (current / name).chmod(0o500 if name == "python3.12" else 0o400)
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
    manifest = {
        "schema_version": (
            4
            if engine_upstream_commit is not None
            else 3 if semantic_profile is not None else 2
        ),
        "profile": profile,
        "engine_name": "nautilus_trader",
        "engine_version": "1.227.0",
        "python_identity": "CPython 3.12.3",
        "source_commit": source_commit,
        "artifact_manifest_sha256": _sha256(artifact_manifest),
        "entrypoint": "/engine/bin/python3.12",
        "argv_prefix": argv_prefix,
        "timeout_seconds": 120,
        "result_validator_id": validator_id,
        "files": records,
    }
    if semantic_profile is not None:
        manifest["semantic_profile"] = semantic_profile
    if engine_upstream_commit is not None:
        manifest["engine_upstream_commit"] = engine_upstream_commit
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


def test_attestor_separates_repository_and_engine_upstream_identities_before_sandbox(
    monkeypatch: pytest.MonkeyPatch,
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
        )

        closure = attest_nautilus_backtest_closure(
            valid_config, expected_profile="execution-simulation"
        )

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


def test_v4_closure_digest_binds_engine_upstream_identity() -> None:
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
    first = nautilus_closure_module._closure_digest(
        closure_manifest={**common, "engine_upstream_commit": SOURCE_COMMIT},
        artifact_digest="a" * 64,
        profile="execution-simulation",
        semantic_profile="nautilus-execution-simulation-v2",
        mounts=(),
        entrypoint=PurePosixPath("/engine/bin/python3.12"),
        timeout=120,
        closure_manifest_sidecar=first_manifest,
    )
    second = nautilus_closure_module._closure_digest(
        closure_manifest={**common, "engine_upstream_commit": "f" * 40},
        artifact_digest="a" * 64,
        profile="execution-simulation",
        semantic_profile="nautilus-execution-simulation-v2",
        mounts=(),
        entrypoint=PurePosixPath("/engine/bin/python3.12"),
        timeout=120,
        closure_manifest_sidecar=first_manifest,
    )

    assert first != second


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
            entrypoint=PurePosixPath("/engine/bin/python3.12"),
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
def test_v1_v3_closure_digests_ignore_optional_v4_manifest_sidecar(
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


def test_v4_attestor_binds_manifest_as_a_separate_fixed_sidecar(
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
    base = Path(tempfile.mkdtemp(prefix="nautilus-v4-manifest-sidecar-", dir="/tmp"))
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


def test_v3_attestor_preserves_legacy_closure_without_manifest_sidecar(
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
    base = Path(tempfile.mkdtemp(prefix="nautilus-v3-manifest-compat-", dir="/tmp"))
    try:
        config = _build_closure_config(
            base,
            base / "not-invoked-bwrap",
            profile="execution-simulation",
            semantic_profile="nautilus-execution-simulation-v2",
        )

        closure = attest_nautilus_backtest_closure(
            config, expected_profile="execution-simulation"
        )

        assert closure.closure_manifest is None
    finally:
        for directory, child_directories, file_names in os.walk(base, topdown=False):
            current = Path(directory)
            for name in file_names:
                (current / name).chmod(0o600)
            for name in child_directories:
                (current / name).chmod(0o700)
            current.chmod(0o700)
        shutil.rmtree(base)


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
