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
PAPER_LAUNCHER = ROOT / "engines/nautilus/launcher/nautilus_paper_compat.py"
STRATEGY = ROOT / "engines/nautilus/launcher/target_portfolio_strategy.py"
CHECKED_IN_POLICY = ROOT / "engines/nautilus/runtime-closure-policy.json"
PAPER_POLICY = (
    ROOT / "engines/nautilus/paper-compatibility-runtime-closure-policy.json"
)
PRIVATE_CARGO = Path(
    "/home/thenam176/.cache/trading-agent/nautilus/rust-1.95.0/bin/cargo"
)
PRIVATE_LLVM = Path(
    "/home/thenam176/.cache/trading-agent/nautilus/llvm-22.1.3-resource-toolchain"
)
SOURCE_COMMIT = "280ae1762df51a492a4ce71506a40b5c8706def5"
DEPENDENCY_IMPORT_POLICY = (
    "native-guarded-stdlib-first-sealed-wheel-path-v1"
)
RUNTIME_SOURCE_LEAVES = (
    "engines/nautilus/launcher/nautilus_backtest.py",
    "engines/nautilus/launcher/nautilus_paper_compat.py",
    "engines/nautilus/launcher/target_portfolio_strategy.py",
    "engines/nautilus/native_entry_guard/src/main.rs",
    "engines/nautilus/native_entry_guard/Cargo.toml",
    "engines/nautilus/native_entry_guard/Cargo.lock",
    "scripts/materialize_nautilus_runtime_closure.py",
    "services/job_worker/engine_spawn.py",
    "services/job_worker/nautilus_closure.py",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_output(arguments: list[str], *, repository: Path = ROOT) -> bytes:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=False,
        stdin=subprocess.DEVNULL,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr.decode(
        "utf-8", errors="replace"
    )
    return completed.stdout


def _git_source_blob(
    source_commit: str, source_path: str, *, repository: Path = ROOT
) -> bytes:
    return _git_output(
        ["show", f"{source_commit}:{source_path}"], repository=repository
    )


def _checked_in_policy_source_commit() -> str:
    policy = json.loads(CHECKED_IN_POLICY.read_text(encoding="ascii"))
    source_commit = policy["source_commit"]

    assert isinstance(source_commit, str)
    assert re.fullmatch(r"[0-9a-f]{40}", source_commit)
    return source_commit


def _assert_runtime_policy_source_authority(policy: dict[str, object]) -> None:
    source_commit = policy["source_commit"]
    assert isinstance(source_commit, str)
    assert re.fullmatch(r"[0-9a-f]{40}", source_commit)
    _git_output(["cat-file", "-e", f"{source_commit}^{{commit}}"])

    bound_digests: dict[str, str] = {}
    launcher_inventory = policy["launcher_inventory"]
    assert isinstance(launcher_inventory, list)
    for record in launcher_inventory:
        assert isinstance(record, dict)
        source_path = record["source"]
        source_digest = record["sha256"]
        assert isinstance(source_path, str)
        assert isinstance(source_digest, str)
        bound_digests[source_path] = source_digest

    native_guard = policy["native_entry_guard"]
    assert isinstance(native_guard, dict)
    for source_field, digest_field in (
        ("source", "source_sha256"),
        ("cargo_manifest", "cargo_manifest_sha256"),
        ("cargo_lock", "cargo_lock_sha256"),
    ):
        source_path = native_guard[source_field]
        source_digest = native_guard[digest_field]
        assert isinstance(source_path, str)
        assert isinstance(source_digest, str)
        bound_digests[source_path] = source_digest

    for source_path, digest_field in (
        ("engines/nautilus/toolchain-inputs.json", "rust_toolchain_policy_sha256"),
        ("engines/nautilus/llvm-toolchain-policy.json", "llvm_toolchain_policy_sha256"),
    ):
        source_digest = native_guard[digest_field]
        assert isinstance(source_digest, str)
        bound_digests[source_path] = source_digest

    for source_path, expected_digest in bound_digests.items():
        source_at_commit = _git_source_blob(source_commit, source_path)
        assert hashlib.sha256(source_at_commit).hexdigest() == expected_digest
        assert _sha256(ROOT / source_path) == expected_digest

    for source_path in RUNTIME_SOURCE_LEAVES:
        assert (ROOT / source_path).read_bytes() == _git_source_blob(
            source_commit, source_path
        )


def test_git_source_blob_remains_bound_after_a_later_nonruntime_commit(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "authority-repository"
    repository.mkdir()
    _git_output(["init", "--quiet"], repository=repository)
    _git_output(
        ["config", "user.email", "authority-test@local.invalid"],
        repository=repository,
    )
    _git_output(["config", "user.name", "Authority Test"], repository=repository)
    runtime_leaf = repository / "runtime/entry.py"
    runtime_leaf.parent.mkdir()
    runtime_leaf.write_bytes(b"reviewed-runtime-leaf\\n")
    _git_output(["add", "runtime/entry.py"], repository=repository)
    _git_output(["commit", "--quiet", "-m", "reviewed runtime"], repository=repository)
    source_commit = _git_output(["rev-parse", "HEAD"], repository=repository).strip()

    (repository / "docs").mkdir()
    (repository / "docs/later.md").write_bytes(b"later non-runtime commit\\n")
    _git_output(["add", "docs/later.md"], repository=repository)
    _git_output(["commit", "--quiet", "-m", "later documentation"], repository=repository)

    assert source_commit != _git_output(
        ["rev-parse", "HEAD"], repository=repository
    ).strip()
    assert _git_source_blob(
        source_commit.decode("ascii"),
        "runtime/entry.py",
        repository=repository,
    ) == b"reviewed-runtime-leaf\\n"


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


def _closure_inputs(*, require_native_sandbox: bool):
    if require_native_sandbox and not Path("/usr/bin/bwrap").is_file():
        pytest.skip("Bubblewrap is required for runtime closure tests")
    root = Path(
        tempfile.mkdtemp(
            prefix="nautilus-runtime-materializer-test-",
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
                "/usr/bin/python3.12",
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
            "dependency_import_policy": DEPENDENCY_IMPORT_POLICY,
            "engine_name": "nautilus_trader",
            "engine_upstream_commit": SOURCE_COMMIT,
            "engine_version": "1.227.0",
            "engine_wheel_mode": "0400",
            "engine_wheel_target": f"/engine/wheels/{wheel_name}",
            "entrypoint": "/engine/bin/nautilus-entry-guard",
            "launcher_inventory": [
                {"mode": "0400", "sha256": _sha256(LAUNCHER), "source": "engines/nautilus/launcher/nautilus_backtest.py", "target": "/engine/launcher/nautilus_backtest.py"},
                {"mode": "0400", "sha256": _sha256(STRATEGY), "source": "engines/nautilus/launcher/target_portfolio_strategy.py", "target": "/engine/launcher/target_portfolio_strategy.py"},
            ],
            "native_entry_guard": json.loads(
                CHECKED_IN_POLICY.read_text(encoding="ascii")
            )["native_entry_guard"],
            "profile": "execution-simulation",
            "profile_manifest_schema_version": 6,
            "python_identity": "CPython 3.12.3",
            "result_validator_id": "nautilus-backtest-simulation-result-v1",
            "schema_version": 1,
            "semantic_profile": "nautilus-execution-simulation-v2",
            "source_commit": _checked_in_policy_source_commit(),
            "timeout_seconds": 120,
        }
        policy_path = root / "runtime-closure-policy.json"
        policy_path.write_bytes(_canonical(policy) + b"\n")
        policy_path.chmod(0o400)
        destination = root / "runtime-closure-v5-simulation"
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


@pytest.fixture
def closure_inputs():
    yield from _closure_inputs(require_native_sandbox=True)


@pytest.fixture
def preflight_rejection_inputs():
    yield from _closure_inputs(require_native_sandbox=False)


@pytest.fixture
def forbid_native_materialization(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("native materialization ran before primary rejection")

    monkeypatch.setattr(materializer_module, "_build_native_entry_guard", forbidden)
    monkeypatch.setattr(
        materializer_module,
        "attest_nautilus_backtest_closure",
        forbidden,
    )


def _materialize(
    inputs: tuple[Path, Path, Path, Path, Path],
    *,
    sandbox_executable: Path = Path("/usr/bin/bwrap"),
) -> Path:
    _root, base, artifacts, policy, destination = inputs
    return materialize_runtime_closure(
        policy_path=policy,
        base_runtime=base,
        artifact_directory=artifacts,
        destination=destination,
        sandbox_executable=sandbox_executable,
        cargo=PRIVATE_CARGO,
        llvm_toolchain=PRIVATE_LLVM,
    )


def test_policy_byte_core_matches_the_path_acquisition_wrapper() -> None:
    byte_core = getattr(materializer_module, "_validate_policy_bytes", None)
    assert callable(byte_core), "shared policy byte validator is missing"

    wrapped = materializer_module._load_policy(CHECKED_IN_POLICY)
    direct = byte_core(
        CHECKED_IN_POLICY.read_bytes(),
        source_reader=lambda path, label: materializer_module._read_file(
            path,
            label=label,
            sealed=False,
        ),
    )

    assert direct == wrapped


def test_policy_byte_core_admits_two_exact_profiles_without_a_wildcard() -> None:
    simulation = json.loads(CHECKED_IN_POLICY.read_text(encoding="ascii"))
    guard = dict(simulation["native_entry_guard"])
    guard["source_sha256"] = _sha256(
        ROOT / "engines/nautilus/native_entry_guard/src/main.rs"
    )
    paper = {
        **simulation,
        "argv_prefix": [
            "/usr/bin/python3.12",
            "-I",
            "-S",
            "/engine/launcher/nautilus_paper_compat.py",
            "--profile",
            "paper-compatibility",
        ],
        "launcher_inventory": [
            {
                "mode": "0400",
                "sha256": _sha256(PAPER_LAUNCHER),
                "source": "engines/nautilus/launcher/nautilus_paper_compat.py",
                "target": "/engine/launcher/nautilus_paper_compat.py",
            },
            {
                "mode": "0400",
                "sha256": _sha256(LAUNCHER),
                "source": "engines/nautilus/launcher/nautilus_backtest.py",
                "target": "/engine/launcher/nautilus_backtest.py",
            },
            {
                "mode": "0400",
                "sha256": _sha256(STRATEGY),
                "source": "engines/nautilus/launcher/target_portfolio_strategy.py",
                "target": "/engine/launcher/target_portfolio_strategy.py",
            },
        ],
        "native_entry_guard": guard,
        "profile": "paper-compatibility",
        "result_validator_id": "nautilus-paper-compatibility-result-v1",
        "semantic_profile": "nautilus-paper-compatibility-v1",
    }
    validate = materializer_module._validate_policy_bytes
    reader = lambda path, label: materializer_module._read_file(
        path, label=label, sealed=False
    )

    validated = validate(_canonical(paper) + b"\n", source_reader=reader)

    assert validated["profile"] == "paper-compatibility"
    with pytest.raises(RuntimeClosureMaterializationError, match="profile"):
        validate(
            _canonical({**paper, "profile": "runtime-selected"}) + b"\n",
            source_reader=reader,
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
    guard = policy["native_entry_guard"]
    assert guard["source_sha256"] == _sha256(
        ROOT / guard["source"]
    )
    assert guard["cargo_manifest_sha256"] == _sha256(
        ROOT / guard["cargo_manifest"]
    )
    assert guard["cargo_lock_sha256"] == _sha256(
        ROOT / guard["cargo_lock"]
    )


@pytest.mark.parametrize(
    ("policy_path", "profile", "launcher", "semantic_profile", "validator"),
    (
        (
            CHECKED_IN_POLICY,
            "execution-simulation",
            "/engine/launcher/nautilus_backtest.py",
            "nautilus-execution-simulation-v2",
            "nautilus-backtest-simulation-result-v1",
        ),
        (
            PAPER_POLICY,
            "paper-compatibility",
            "/engine/launcher/nautilus_paper_compat.py",
            "nautilus-paper-compatibility-v1",
            "nautilus-paper-compatibility-result-v1",
        ),
    ),
)
def test_checked_in_policies_bind_exact_finite_profile_leaves(
    policy_path: Path,
    profile: str,
    launcher: str,
    semantic_profile: str,
    validator: str,
) -> None:
    policy = materializer_module._load_policy(policy_path)

    assert policy["source_commit"] == _checked_in_policy_source_commit()
    _assert_runtime_policy_source_authority(policy)
    assert policy["profile_manifest_schema_version"] == 6
    assert policy["dependency_import_policy"] == DEPENDENCY_IMPORT_POLICY
    assert policy["profile"] == profile
    assert policy["argv_prefix"] == [
        "/usr/bin/python3.12",
        "-I",
        "-S",
        launcher,
        "--profile",
        profile,
    ]
    assert policy["semantic_profile"] == semantic_profile
    assert policy["result_validator_id"] == validator
    assert policy["native_entry_guard"]["source_sha256"] == _sha256(
        ROOT / "engines/nautilus/native_entry_guard/src/main.rs"
    )


def test_checked_in_profile_guards_have_distinct_binary_identities() -> None:
    simulation = materializer_module._load_policy(CHECKED_IN_POLICY)
    paper = materializer_module._load_policy(PAPER_POLICY)

    assert simulation["native_entry_guard"]["binary_sha256"] != paper[
        "native_entry_guard"
    ]["binary_sha256"]


def test_checked_in_policy_binds_the_reviewed_final_source_and_launcher_inventory() -> None:
    policy = json.loads(CHECKED_IN_POLICY.read_text(encoding="ascii"))
    source_commit = policy["source_commit"]

    assert isinstance(source_commit, str)
    assert re.fullmatch(r"[0-9a-f]{40}", source_commit)
    _assert_runtime_policy_source_authority(policy)
    assert policy["profile_manifest_schema_version"] == 6
    assert policy["dependency_import_policy"] == DEPENDENCY_IMPORT_POLICY
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

    _assert_runtime_policy_source_authority(raw_policy)
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
    assert manifest["source_commit"] == raw_policy["source_commit"]
    assert manifest["source_commit"] != policy["engine_upstream_commit"]


def test_materializer_output_manifest_carries_split_engine_identity() -> None:
    policy = {
        "argv_prefix": [
            "/usr/bin/python3.12",
            "-I",
            "-S",
            "/engine/launcher/nautilus_backtest.py",
            "--profile",
            "execution-simulation",
        ],
        "artifact_manifest_sha256": "a" * 64,
        "dependency_import_policy": DEPENDENCY_IMPORT_POLICY,
        "engine_name": "nautilus_trader",
        "engine_upstream_commit": SOURCE_COMMIT,
        "engine_version": "1.227.0",
        "entrypoint": "/engine/bin/nautilus-entry-guard",
        "native_entry_guard": json.loads(
            CHECKED_IN_POLICY.read_text(encoding="ascii")
        )["native_entry_guard"],
        "profile": "execution-simulation",
        "profile_manifest_schema_version": 6,
        "python_identity": "CPython 3.12.3",
        "result_validator_id": "nautilus-backtest-simulation-result-v1",
        "semantic_profile": "nautilus-execution-simulation-v2",
        "source_commit": _checked_in_policy_source_commit(),
        "timeout_seconds": 120,
    }

    manifest = materializer_module._build_output_manifest(policy, [])

    assert manifest["schema_version"] == 6
    assert manifest["dependency_import_policy"] == DEPENDENCY_IMPORT_POLICY
    assert manifest["source_commit"] == policy["source_commit"]
    assert manifest["engine_upstream_commit"] == SOURCE_COMMIT
    native_guard = manifest["native_entry_guard"]
    with pytest.raises(
        RuntimeClosureMaterializationError,
        match="output provenance drifted",
    ):
        materializer_module._build_output_manifest(
            policy,
            [],
            native_entry_guard={**native_guard, "source_sha256": "0" * 64},
        )


def test_materializer_policy_rejects_identical_repository_and_engine_identities(
    tmp_path: Path,
) -> None:
    policy = json.loads(CHECKED_IN_POLICY.read_text(encoding="ascii"))
    policy["engine_upstream_commit"] = policy["source_commit"]
    policy_path = tmp_path / "runtime-closure-policy.json"
    policy_path.write_bytes(_canonical(policy) + b"\n")

    with pytest.raises(
        RuntimeClosureMaterializationError, match="profile or identity"
    ):
        materializer_module._load_policy(policy_path)


@pytest.mark.parametrize(
    "dependency_import_policy",
    (None, "ambient-site-packages-first", True),
)
def test_materializer_schema_6_requires_exact_dependency_import_policy(
    tmp_path: Path,
    dependency_import_policy: object,
) -> None:
    loaded = materializer_module._load_policy(CHECKED_IN_POLICY)
    assert loaded["profile_manifest_schema_version"] == 6
    assert loaded["dependency_import_policy"] == DEPENDENCY_IMPORT_POLICY

    policy = json.loads(CHECKED_IN_POLICY.read_text(encoding="ascii"))
    if dependency_import_policy is None:
        del policy["dependency_import_policy"]
    else:
        policy["dependency_import_policy"] = dependency_import_policy
    policy_path = tmp_path / "runtime-closure-policy.json"
    policy_path.write_bytes(_canonical(policy) + b"\n")

    with pytest.raises(RuntimeClosureMaterializationError):
        materializer_module._load_policy(policy_path)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("source_sha256", "0" * 64, "source digest drifted"),
        ("cargo_manifest_sha256", "0" * 64, "cargo_manifest digest drifted"),
        ("cargo_lock_sha256", "0" * 64, "cargo_lock digest drifted"),
        ("rust_toolchain_policy_sha256", "0" * 64, "toolchain policy digest"),
        ("llvm_toolchain_policy_sha256", "0" * 64, "toolchain policy digest"),
        ("mode", "0400", "policy identity"),
    ),
)
def test_materializer_policy_rejects_native_guard_source_or_build_input_drift(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    policy = json.loads(CHECKED_IN_POLICY.read_text(encoding="ascii"))
    policy["native_entry_guard"][field] = value
    policy_path = tmp_path / "runtime-closure-policy.json"
    policy_path.write_bytes(_canonical(policy) + b"\n")

    with pytest.raises(RuntimeClosureMaterializationError, match=message):
        materializer_module._load_policy(policy_path)


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
    assert manifest["schema_version"] == 6
    assert manifest["dependency_import_policy"] == DEPENDENCY_IMPORT_POLICY
    assert manifest["entrypoint"] == "/engine/bin/nautilus-entry-guard"
    assert manifest["argv_prefix"][0] == "/usr/bin/python3.12"
    assert manifest["native_entry_guard"]["target"] == manifest["entrypoint"]
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
    assert closure.dependency_import_policy == DEPENDENCY_IMPORT_POLICY


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
    preflight_rejection_inputs,
    forbid_native_materialization,
) -> None:
    root, base, artifacts, policy, destination = preflight_rejection_inputs
    destination.mkdir(mode=0o700)
    marker = destination / "operator-owned"
    marker.write_text("retain", encoding="ascii")

    with pytest.raises(RuntimeClosureMaterializationError, match="already exists"):
        materialize_runtime_closure(
            policy_path=policy,
            base_runtime=base,
            artifact_directory=artifacts,
            destination=destination,
            sandbox_executable=root / "must-not-execute-bwrap",
            cargo=PRIVATE_CARGO,
            llvm_toolchain=PRIVATE_LLVM,
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
    preflight_rejection_inputs, forbid_native_materialization, field: str
) -> None:
    root, _base, _artifacts, policy_path, destination = preflight_rejection_inputs
    policy_path.chmod(0o600)
    policy = json.loads(policy_path.read_text())
    if field == "launcher_inventory":
        policy[field][0]["sha256"] = "0" * 64
    else:
        policy[field] = "0" * 64
    policy_path.write_bytes(_canonical(policy) + b"\n")
    policy_path.chmod(0o400)

    with pytest.raises(RuntimeClosureMaterializationError, match="digest"):
        _materialize(
            preflight_rejection_inputs,
            sandbox_executable=root / "must-not-execute-bwrap",
        )

    assert not destination.exists()


@pytest.mark.parametrize(
    "mutation", ["unlisted-file", "unsafe-mode", "profile", "semantic-profile"]
)
def test_materializer_fails_closed_on_inventory_mode_or_profile_drift(
    preflight_rejection_inputs, forbid_native_materialization, mutation: str
) -> None:
    root, base, _artifacts, policy_path, destination = preflight_rejection_inputs
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
        _materialize(
            preflight_rejection_inputs,
            sandbox_executable=root / "must-not-execute-bwrap",
        )

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
