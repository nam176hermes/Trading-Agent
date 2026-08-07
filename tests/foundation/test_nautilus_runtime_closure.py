from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

import scripts.materialize_nautilus_runtime_closure as materializer_module
from scripts.materialize_nautilus_runtime_closure import (
    RuntimeClosureMaterializationError,
    materialize_runtime_closure,
)
from services.job_worker.engine_spawn_interface import EngineSpawnError
from services.job_worker.nautilus_closure import (
    NautilusClosureConfig,
    attest_nautilus_backtest_closure,
)


ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "engines/nautilus/launcher/nautilus_backtest.py"
STRATEGY = ROOT / "engines/nautilus/launcher/target_portfolio_strategy.py"
CHECKED_IN_POLICY = ROOT / "engines/nautilus/runtime-closure-policy.json"
SOURCE_COMMIT = "280ae1762df51a492a4ce71506a40b5c8706def5"
REPOSITORY_SOURCE_COMMIT = "4648ecaf6169b0886daf47fe27467b0292153cbb"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("ascii")


def _seal(root: Path) -> None:
    for directory, child_directories, files in os.walk(root, topdown=False):
        current = Path(directory)
        for name in files:
            path = current / name
            if path.name.endswith("python3.12"):
                path.chmod(0o500)
            else:
                path.chmod(0o400)
        for name in child_directories:
            (current / name).chmod(0o500)
    root.chmod(0o500)


def _record(root: Path, relative: str, target: str, value: bytes, mode: int) -> dict[str, object]:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)
    path.chmod(mode)
    return {
        "mode": f"{mode:04o}",
        "path": relative,
        "sha256": hashlib.sha256(value).hexdigest(),
        "size": len(value),
        "target": target,
    }


@pytest.fixture
def closure_inputs() -> tuple[Path, Path, Path, Path, Path]:
    if not Path("/usr/bin/bwrap").is_file():
        pytest.skip("Bubblewrap is required for runtime closure tests")
    root = Path(
        tempfile.mkdtemp(
            prefix="nautilus-runtime-materializer-test-",
            dir="/home/thenam176/.cache",
        )
    )
    try:
        base = root / "runtime-closure-v3"
        base.mkdir(mode=0o700)
        wheel_name = "nautilus_trader-1.227.0-cp312-cp312-manylinux_2_39_x86_64.whl"
        records = [
            _record(
                base,
                "files/usr/bin/python3.12",
                "/usr/bin/python3.12",
                b"sealed-cpython-3.12",
                0o500,
            ),
            _record(
                base,
                "files/engine/launcher/nautilus_backtest.py",
                "/engine/launcher/nautilus_backtest.py",
                b"old-zero-order-launcher",
                0o400,
            ),
            _record(
                base,
                "files/engine/launcher/target_portfolio_strategy.py",
                "/engine/launcher/target_portfolio_strategy.py",
                b"old-zero-order-strategy",
                0o400,
            ),
            _record(
                base,
                f"files/engine/wheels/{wheel_name}",
                f"/engine/wheels/{wheel_name}",
                b"old-engine-wheel",
                0o400,
            ),
        ]
        base_manifest = {
            "argv_prefix": ["-I", "-S", "/engine/launcher/nautilus_backtest.py"],
            "artifact_manifest_sha256": "a" * 64,
            "engine_name": "nautilus_trader",
            "engine_version": "1.227.0",
            "entrypoint": "/usr/bin/python3.12",
            "files": records,
            "python_identity": "CPython 3.12.3",
            "result_validator_id": "nautilus-backtest-result-v1",
            "schema_version": 1,
            "source_commit": SOURCE_COMMIT,
            "timeout_seconds": 120,
        }
        (base / "closure-manifest.json").write_bytes(_canonical(base_manifest) + b"\n")
        _seal(base)

        artifacts = root / "selected-artifacts"
        artifacts.mkdir(mode=0o700)
        selected_wheel = artifacts / wheel_name
        selected_wheel.write_bytes(b"selected-input-bound-engine-wheel")
        selected_wheel.chmod(0o400)
        artifact_manifest = {
            "engine_name": "nautilus_trader",
            "engine_version": "1.227.0",
            "python_identity": "CPython 3.12.3",
            "upstream_commit": SOURCE_COMMIT,
            "wheel": {
                "filename": wheel_name,
                "sha256": _sha256(selected_wheel),
                "size": selected_wheel.stat().st_size,
            },
        }
        artifact_manifest_path = artifacts / "artifact-manifest.json"
        artifact_manifest_path.write_bytes(_canonical(artifact_manifest) + b"\n")
        artifact_manifest_path.chmod(0o400)
        artifacts.chmod(0o500)

        policy = {
            "argv_prefix": [
                "-I",
                "-S",
                "/engine/launcher/nautilus_backtest.py",
                "--profile",
                "execution-simulation",
            ],
            "artifact_manifest_sha256": _sha256(artifact_manifest_path),
            "base_file_count": len(records),
            "base_file_inventory_sha256": hashlib.sha256(
                _canonical(records)
            ).hexdigest(),
            "base_runtime_manifest_sha256": _sha256(
                base / "closure-manifest.json"
            ),
            "engine_name": "nautilus_trader",
            "engine_upstream_commit": SOURCE_COMMIT,
            "engine_version": "1.227.0",
            "engine_wheel_mode": "0400",
            "engine_wheel_target": f"/engine/wheels/{wheel_name}",
            "entrypoint": "/usr/bin/python3.12",
            "launcher_inventory": [
                {"mode": "0400", "sha256": _sha256(LAUNCHER), "source": "engines/nautilus/launcher/nautilus_backtest.py", "target": "/engine/launcher/nautilus_backtest.py"},
                {"mode": "0400", "sha256": _sha256(STRATEGY), "source": "engines/nautilus/launcher/target_portfolio_strategy.py", "target": "/engine/launcher/target_portfolio_strategy.py"},
            ],
            "profile": "execution-simulation",
            "profile_manifest_schema_version": 3,
            "python_identity": "CPython 3.12.3",
            "result_validator_id": "nautilus-backtest-simulation-result-v1",
            "schema_version": 1,
            "semantic_profile": "nautilus-execution-simulation-v2",
            "source_commit": REPOSITORY_SOURCE_COMMIT,
            "timeout_seconds": 120,
        }
        policy_path = root / "runtime-closure-policy.json"
        policy_path.write_bytes(_canonical(policy) + b"\n")
        policy_path.chmod(0o400)
        destination = root / "runtime-closure-v4-simulation"
        yield root, base, artifacts, policy_path, destination
    finally:
        for directory, child_directories, files in os.walk(root, topdown=False):
            current = Path(directory)
            for name in files:
                path = current / name
                if not path.is_symlink():
                    path.chmod(0o600)
            for name in child_directories:
                path = current / name
                if not path.is_symlink():
                    path.chmod(0o700)
            current.chmod(0o700)
        shutil.rmtree(root)


def _materialize(inputs: tuple[Path, Path, Path, Path, Path]) -> Path:
    _root, base, artifacts, policy, destination = inputs
    return materialize_runtime_closure(
        policy_path=policy,
        base_runtime=base,
        artifact_directory=artifacts,
        destination=destination,
        sandbox_executable=Path("/usr/bin/bwrap"),
    )


def test_checked_in_policy_binds_reviewed_external_inputs_and_launcher() -> None:
    base = Path(
        "/home/thenam176/.cache/trading-agent/nautilus/runtime-closure-v3"
    )
    artifacts = Path(
        "/home/thenam176/.cache/trading-agent/nautilus/artifacts/"
        "nautilus-1.227.0-cp312-rust-bound-input-ff2e7753974c"
    )
    if not base.is_dir() or not artifacts.is_dir():
        pytest.skip("reviewed external Nautilus inputs are unavailable")
    policy = json.loads(CHECKED_IN_POLICY.read_text(encoding="ascii"))
    base_manifest = json.loads(
        (base / "closure-manifest.json").read_text(encoding="ascii")
    )

    assert policy["base_runtime_manifest_sha256"] == _sha256(
        base / "closure-manifest.json"
    )
    assert policy["base_file_count"] == len(base_manifest["files"])
    assert policy["base_file_inventory_sha256"] == hashlib.sha256(
        _canonical(base_manifest["files"])
    ).hexdigest()
    assert policy["artifact_manifest_sha256"] == _sha256(
        artifacts / "artifact-manifest.json"
    )
    assert policy["launcher_inventory"] == [
        {"mode": "0400", "sha256": _sha256(LAUNCHER), "source": "engines/nautilus/launcher/nautilus_backtest.py", "target": "/engine/launcher/nautilus_backtest.py"},
        {"mode": "0400", "sha256": _sha256(STRATEGY), "source": "engines/nautilus/launcher/target_portfolio_strategy.py", "target": "/engine/launcher/target_portfolio_strategy.py"},
    ]
    assert policy["semantic_profile"] == "nautilus-execution-simulation-v2"


def test_checked_in_policy_binds_the_reviewed_final_source_and_launcher_inventory() -> None:
    policy = json.loads(CHECKED_IN_POLICY.read_text(encoding="ascii"))
    source_commit = policy["source_commit"]

    assert isinstance(source_commit, str)
    assert re.fullmatch(r"[0-9a-f]{40}", source_commit)
    completed = subprocess.run(
        ["git", "cat-file", "-e", f"{source_commit}^{{commit}}"],
        cwd=ROOT,
        check=False,
        capture_output=True,
    )

    assert completed.returncode == 0
    assert source_commit == REPOSITORY_SOURCE_COMMIT
    assert policy["launcher_inventory"] == [
        {"mode": "0400", "sha256": _sha256(LAUNCHER), "source": "engines/nautilus/launcher/nautilus_backtest.py", "target": "/engine/launcher/nautilus_backtest.py"},
        {"mode": "0400", "sha256": _sha256(STRATEGY), "source": "engines/nautilus/launcher/target_portfolio_strategy.py", "target": "/engine/launcher/target_portfolio_strategy.py"},
    ]


def test_checked_in_policy_separates_repository_and_sealed_engine_identities() -> None:
    base = Path(
        "/home/thenam176/.cache/trading-agent/nautilus/runtime-closure-v3"
    )
    artifacts = Path(
        "/home/thenam176/.cache/trading-agent/nautilus/artifacts/"
        "nautilus-1.227.0-cp312-rust-bound-input-ff2e7753974c"
    )
    if not base.is_dir() or not artifacts.is_dir():
        pytest.skip("reviewed external Nautilus inputs are unavailable")

    raw_policy = json.loads(CHECKED_IN_POLICY.read_text(encoding="ascii"))

    assert raw_policy["source_commit"] == REPOSITORY_SOURCE_COMMIT
    assert raw_policy["engine_upstream_commit"] == SOURCE_COMMIT
    assert raw_policy["source_commit"] != raw_policy["engine_upstream_commit"]

    policy = materializer_module._load_policy(CHECKED_IN_POLICY)
    materializer_module._validate_base_runtime(base, policy)
    materializer_module._validate_artifact(artifacts, policy)

    changed_engine_identity = {**policy, "engine_upstream_commit": "f" * 40}
    with pytest.raises(RuntimeClosureMaterializationError, match="base runtime profile"):
        materializer_module._validate_base_runtime(base, changed_engine_identity)
    with pytest.raises(
        RuntimeClosureMaterializationError, match="selected artifact identity"
    ):
        materializer_module._validate_artifact(artifacts, changed_engine_identity)

    manifest = materializer_module._build_output_manifest(policy, [])
    assert manifest["source_commit"] == REPOSITORY_SOURCE_COMMIT
    assert manifest["source_commit"] != policy["engine_upstream_commit"]


def test_materializer_cli_bootstraps_only_the_checkout_authority() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            str(ROOT / "scripts/materialize_nautilus_runtime_closure.py"),
            "--help",
        ],
        cwd="/tmp",
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "execution-simulation closure" in completed.stdout


def test_materializer_publishes_a_new_attested_simulation_closure_atomically(
    closure_inputs,
) -> None:
    _root, base, artifacts, _policy, destination = closure_inputs
    base_manifest_before = (base / "closure-manifest.json").read_bytes()

    published = _materialize(closure_inputs)

    assert published == destination
    assert (base / "closure-manifest.json").read_bytes() == base_manifest_before
    manifest = json.loads((published / "closure-manifest.json").read_text())
    assert manifest["profile"] == "execution-simulation"
    assert manifest["schema_version"] == 3
    assert manifest["semantic_profile"] == "nautilus-execution-simulation-v2"
    assert manifest["argv_prefix"][-2:] == ["--profile", "execution-simulation"]
    assert manifest["artifact_manifest_sha256"] == _sha256(
        artifacts / "artifact-manifest.json"
    )
    assert all(path.stat().st_mode & 0o222 == 0 for path in published.rglob("*") if path.is_file())
    closure = attest_nautilus_backtest_closure(
        NautilusClosureConfig(published, artifacts, Path("/usr/bin/bwrap")),
        expected_profile="execution-simulation",
    )
    assert closure.profile == "execution-simulation"
    assert closure.semantic_profile == "nautilus-execution-simulation-v2"


def test_materializer_attests_staging_then_re_attests_same_tree_after_rename(
    closure_inputs, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _base, _artifacts, _policy, destination = closure_inputs
    real_attest = materializer_module.attest_nautilus_backtest_closure
    observations: list[tuple[Path, tuple[int, int], bool]] = []

    def record_attestation(config: NautilusClosureConfig, *, expected_profile: str):
        observed = config.runtime_root.lstat()
        observations.append(
            (
                config.runtime_root,
                (observed.st_dev, observed.st_ino),
                destination.exists(),
            )
        )
        return real_attest(config, expected_profile=expected_profile)

    monkeypatch.setattr(
        materializer_module,
        "attest_nautilus_backtest_closure",
        record_attestation,
    )

    assert _materialize(closure_inputs) == destination

    assert len(observations) == 2
    staging_path, staging_identity, destination_existed_during_staging = observations[0]
    published_path, published_identity, destination_existed_after_rename = observations[1]
    assert staging_path.parent == root
    assert staging_path.name.startswith(f".{destination.name}.staging-")
    assert not destination_existed_during_staging
    assert published_path == destination
    assert destination_existed_after_rename
    assert staging_identity == published_identity
    assert not staging_path.exists()


def test_failed_staging_attestation_never_exposes_selected_destination(
    closure_inputs, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _base, _artifacts, _policy, destination = closure_inputs
    attempted_staging: list[Path] = []

    def fail_attestation(
        config: NautilusClosureConfig, *, expected_profile: str
    ) -> None:
        assert expected_profile == "execution-simulation"
        assert config.runtime_root != destination
        assert not destination.exists()
        attempted_staging.append(config.runtime_root)
        raise EngineSpawnError(
            "ENGINE_CLOSURE_INVALID", "injected staging attestation failure"
        )

    monkeypatch.setattr(
        materializer_module,
        "attest_nautilus_backtest_closure",
        fail_attestation,
    )

    with pytest.raises(
        RuntimeClosureMaterializationError,
        match="staging closure attestation",
    ):
        _materialize(closure_inputs)

    assert len(attempted_staging) == 1
    assert not destination.exists()
    assert not attempted_staging[0].exists()
    assert not any(
        path.name.startswith(f".{destination.name}.staging-")
        for path in root.iterdir()
    )


def test_staging_identity_change_after_attestation_fails_before_publish(
    closure_inputs, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _base, _artifacts, _policy, destination = closure_inputs
    real_attest = materializer_module.attest_nautilus_backtest_closure
    displaced: list[Path] = []

    def attest_then_replace_staging(
        config: NautilusClosureConfig, *, expected_profile: str
    ):
        attestation = real_attest(config, expected_profile=expected_profile)
        if not displaced:
            original = root / ".attested-staging-original"
            os.rename(config.runtime_root, original)
            shutil.copytree(original, config.runtime_root)
            displaced.append(original)
        return attestation

    monkeypatch.setattr(
        materializer_module,
        "attest_nautilus_backtest_closure",
        attest_then_replace_staging,
    )

    with pytest.raises(RuntimeClosureMaterializationError, match="identity changed"):
        _materialize(closure_inputs)

    assert len(displaced) == 1
    assert displaced[0].exists()
    assert not destination.exists()


def test_destination_identity_change_during_re_attestation_fails_closed(
    closure_inputs, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _base, _artifacts, _policy, destination = closure_inputs
    real_attest = materializer_module.attest_nautilus_backtest_closure
    displaced: list[tuple[Path, tuple[int, int]]] = []

    def attest_then_replace_destination(
        config: NautilusClosureConfig, *, expected_profile: str
    ):
        attestation = real_attest(config, expected_profile=expected_profile)
        if config.runtime_root == destination:
            observed = destination.lstat()
            original = root / ".destination-attested-original"
            os.rename(destination, original)
            shutil.copytree(original, destination)
            displaced.append((original, (observed.st_dev, observed.st_ino)))
        return attestation

    monkeypatch.setattr(
        materializer_module,
        "attest_nautilus_backtest_closure",
        attest_then_replace_destination,
    )

    with pytest.raises(
        RuntimeClosureMaterializationError,
        match="destination closure identity changed after re-attestation",
    ):
        _materialize(closure_inputs)

    assert len(displaced) == 1
    original, attested_identity = displaced[0]
    observed_original = original.lstat()
    assert (observed_original.st_dev, observed_original.st_ino) == attested_identity
    assert not destination.exists()


def test_materializer_rejects_a_preexisting_destination_without_changing_it(
    closure_inputs,
) -> None:
    root, base, artifacts, policy, destination = closure_inputs
    destination.mkdir(mode=0o700)
    marker = destination / "operator-owned"
    marker.write_text("retain", encoding="ascii")

    with pytest.raises(RuntimeClosureMaterializationError, match="already exists"):
        materialize_runtime_closure(
            policy_path=policy,
            base_runtime=base,
            artifact_directory=artifacts,
            destination=destination,
            sandbox_executable=Path("/usr/bin/bwrap"),
        )

    assert marker.read_text(encoding="ascii") == "retain"
    assert set(root.iterdir()) >= {base, artifacts, policy, destination}


def test_destination_created_after_preflight_is_never_replaced(
    closure_inputs, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _base, _artifacts, _policy, destination = closure_inputs
    real_rename = materializer_module._renameat2_noreplace
    competitor_identity: list[tuple[int, int]] = []

    def create_competitor_then_rename(
        parent_fd: int,
        source_name: bytes,
        destination_name: bytes,
    ) -> None:
        destination.mkdir(mode=0o700)
        observed = destination.lstat()
        competitor_identity.append((observed.st_dev, observed.st_ino))
        real_rename(parent_fd, source_name, destination_name)

    monkeypatch.setattr(
        materializer_module,
        "_renameat2_noreplace",
        create_competitor_then_rename,
    )

    with pytest.raises(RuntimeClosureMaterializationError, match="already exists"):
        _materialize(closure_inputs)

    observed = destination.lstat()
    assert (observed.st_dev, observed.st_ino) == competitor_identity[0]
    assert list(destination.iterdir()) == []
    assert not any(
        path.name.startswith(f".{destination.name}.staging-")
        for path in root.iterdir()
    )


def test_parent_fd_close_failure_after_rename_cannot_skip_destination_attestation(
    closure_inputs, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _base, _artifacts, _policy, destination = closure_inputs
    real_attest = materializer_module.attest_nautilus_backtest_closure
    real_close = os.close
    parent_observed = root.lstat()
    parent_identity = (parent_observed.st_dev, parent_observed.st_ino)
    attested_roots: list[Path] = []
    failed_parent_close: list[int] = []

    def record_attestation(
        config: NautilusClosureConfig, *, expected_profile: str
    ):
        attested_roots.append(config.runtime_root)
        return real_attest(config, expected_profile=expected_profile)

    def close_then_fail_parent(descriptor: int) -> None:
        observed = os.fstat(descriptor)
        is_destination_parent = (
            observed.st_dev,
            observed.st_ino,
        ) == parent_identity
        real_close(descriptor)
        if is_destination_parent and not failed_parent_close:
            failed_parent_close.append(descriptor)
            raise OSError(errno.EIO, "injected parent descriptor close failure")

    monkeypatch.setattr(
        materializer_module,
        "attest_nautilus_backtest_closure",
        record_attestation,
    )
    monkeypatch.setattr(materializer_module.os, "close", close_then_fail_parent)

    with pytest.raises(RuntimeClosureMaterializationError, match="failed"):
        _materialize(closure_inputs)

    assert len(failed_parent_close) == 1
    assert len(attested_roots) == 2
    assert attested_roots[1] == destination
    assert not destination.exists()
    assert not any(
        path.name.startswith(f".{destination.name}.staging-")
        for path in root.iterdir()
    )


def test_unavailable_no_clobber_syscall_fails_without_publication(
    closure_inputs, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _base, _artifacts, _policy, destination = closure_inputs

    def unavailable(
        parent_fd: int,
        source_name: bytes,
        destination_name: bytes,
    ) -> None:
        raise OSError(errno.ENOSYS, "renameat2 unavailable")

    monkeypatch.setattr(
        materializer_module,
        "_renameat2_noreplace",
        unavailable,
        raising=False,
    )

    with pytest.raises(RuntimeClosureMaterializationError, match="unavailable"):
        _materialize(closure_inputs)

    assert not destination.exists()
    assert not any(
        path.name.startswith(f".{destination.name}.staging-")
        for path in root.iterdir()
    )


@pytest.mark.parametrize(
    "field",
    [
        "base_runtime_manifest_sha256",
        "artifact_manifest_sha256",
        "launcher_inventory",
    ],
)
def test_materializer_rejects_every_bound_input_digest_drift(
    closure_inputs, field: str
) -> None:
    _root, _base, _artifacts, policy_path, destination = closure_inputs
    policy_path.chmod(0o600)
    policy = json.loads(policy_path.read_text())
    if field == "launcher_inventory":
        policy[field][0]["sha256"] = "0" * 64
    else:
        policy[field] = "0" * 64
    policy_path.write_bytes(_canonical(policy) + b"\n")
    policy_path.chmod(0o400)

    with pytest.raises(RuntimeClosureMaterializationError, match="digest"):
        _materialize(closure_inputs)

    assert not destination.exists()


@pytest.mark.parametrize(
    "mutation", ["unlisted-file", "unsafe-mode", "profile", "semantic-profile"]
)
def test_materializer_fails_closed_on_inventory_mode_or_profile_drift(
    closure_inputs, mutation: str
) -> None:
    _root, base, _artifacts, policy_path, destination = closure_inputs
    if mutation == "unlisted-file":
        base.chmod(0o700)
        (base / "files").chmod(0o700)
        unexpected = base / "files/unlisted"
        unexpected.write_bytes(b"unexpected")
        unexpected.chmod(0o400)
        (base / "files").chmod(0o500)
        base.chmod(0o500)
    elif mutation == "unsafe-mode":
        base.chmod(0o700)
        launcher = base / "files/engine/launcher/nautilus_backtest.py"
        launcher.chmod(0o600)
        base.chmod(0o500)
    elif mutation == "profile":
        policy_path.chmod(0o600)
        policy = json.loads(policy_path.read_text())
        policy["profile"] = "zero-order"
        policy_path.write_bytes(_canonical(policy) + b"\n")
        policy_path.chmod(0o400)
    else:
        policy_path.chmod(0o600)
        policy = json.loads(policy_path.read_text())
        policy["semantic_profile"] = "transport-only"
        policy_path.write_bytes(_canonical(policy) + b"\n")
        policy_path.chmod(0o400)

    with pytest.raises(RuntimeClosureMaterializationError, match="inventory|mode|profile"):
        _materialize(closure_inputs)

    assert not destination.exists()


def test_atomic_publish_failure_removes_staging_and_leaves_no_generation(
    closure_inputs, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _base, _artifacts, _policy, destination = closure_inputs

    def fail_publish(
        source: Path,
        target: Path,
        *,
        parent_identity: tuple[int, int],
    ) -> None:
        raise OSError("injected atomic publish failure")

    monkeypatch.setattr(
        materializer_module,
        "_publish_noreplace",
        fail_publish,
        raising=False,
    )

    with pytest.raises(RuntimeClosureMaterializationError, match="publish"):
        _materialize(closure_inputs)

    assert not destination.exists()
    assert not any(path.name.startswith(f".{destination.name}.staging-") for path in root.iterdir())
