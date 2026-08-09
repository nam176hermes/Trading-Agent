from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/qualify_nautilus_sealed_imports.py"
ARCHITECTURE_PLAN = (
    ROOT / "docs/superpowers/plans/2026-08-08-phase4-architectural-closure.md"
)
TASK8_ROOT_CONTROLLER_SCRIPTS = {
    "qualify_nautilus_sealed_imports.py": 2,
    "materialize_nautilus_runtime_closure.py": 2,
    "materialize_phase4_campaign_inputs.py": 1,
    "diagnose_nautilus_v12_runtime_failure.py": 1,
    "verify_nautilus_v12_r3_parity.py": 1,
    "verify_nautilus_paper_compatibility.py": 1,
    "close_phase4_research_evidence.py": 1,
}
LAUNCHER = ROOT / "engines/nautilus/launcher/nautilus_backtest.py"
PAPER_LAUNCHER = ROOT / "engines/nautilus/launcher/nautilus_paper_compat.py"
STRATEGY = ROOT / "engines/nautilus/launcher/target_portfolio_strategy.py"
PROBE = ROOT / "engines/nautilus/launcher/import_probe.py"
CHECKED_IN_POLICY = ROOT / "engines/nautilus/runtime-closure-policy.json"
UPSTREAM_COMMIT = "280ae1762df51a492a4ce71506a40b5c8706def5"
SOURCE_COMMIT = "a7d9f3e399c979ec9ac61ffbb7d02e0f64ed09ac"
IMPORT_POLICY = "native-guarded-stdlib-first-sealed-wheel-path-v1"
WHEEL_NAME = (
    "nautilus_trader-1.227.0-cp312-cp312-manylinux_2_39_x86_64.whl"
)
SANDBOX_PROFILE = (
    b"trading-agent-engine-bwrap-v2:die-with-parent,user,pid,net,new-session,"
    b"clearenv,sealed-file-closure,fd-ro-bind-data-inputs,proc,dev,tmpfs"
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, allow_nan=False, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("ascii")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _module():
    if not SCRIPT.is_file():
        pytest.fail("sealed import qualification CLI is missing")
    specification = importlib.util.spec_from_file_location(
        "sealed_import_qualification", SCRIPT
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def _record(
    root: Path, relative: str, target: str, value: bytes, mode: int
) -> dict[str, object]:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)
    path.chmod(mode)
    return {
        "mode": f"{mode:04o}",
        "path": relative,
        "sha256": _sha256_bytes(value),
        "size": len(value),
        "target": target,
    }


def _seal_tree(root: Path) -> None:
    for directory, child_directories, files in os.walk(root, topdown=False):
        current = Path(directory)
        for name in files:
            candidate = current / name
            if not candidate.is_symlink():
                candidate.chmod(0o500 if name == "python3.12" else 0o400)
        for name in child_directories:
            candidate = current / name
            if not candidate.is_symlink():
                candidate.chmod(0o500)
        current.chmod(0o500)


def _unseal_tree(root: Path) -> None:
    if not root.exists() or root.is_symlink():
        return
    for directory, child_directories, files in os.walk(root, topdown=False):
        current = Path(directory)
        for name in files:
            candidate = current / name
            if not candidate.is_symlink():
                candidate.chmod(0o600)
        for name in child_directories:
            candidate = current / name
            if not candidate.is_symlink():
                candidate.chmod(0o700)
        current.chmod(0o700)


@pytest.fixture
def qualification_inputs() -> dict[str, object]:
    packet = Path(tempfile.mkdtemp(prefix="nautilus-import-qualification-", dir="/tmp"))
    packet.chmod(0o700)
    base = packet / "base-runtime"
    base.mkdir(mode=0o700)
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
            "files/usr/lib/python3.12/os.py",
            "/usr/lib/python3.12/os.py",
            b"sealed-stdlib",
            0o400,
        ),
        _record(
            base,
            "files/engine/wheels/numpy-2.2.6-cp312.whl",
            "/engine/wheels/numpy-2.2.6-cp312.whl",
            b"sealed-numpy-wheel",
            0o400,
        ),
        _record(
            base,
            "files/engine/wheels/pandas-2.3.0-cp312.whl",
            "/engine/wheels/pandas-2.3.0-cp312.whl",
            b"sealed-pandas-wheel",
            0o400,
        ),
        _record(
            base,
            f"files/engine/wheels/{WHEEL_NAME}",
            f"/engine/wheels/{WHEEL_NAME}",
            b"obsolete-engine-wheel",
            0o400,
        ),
        _record(
            base,
            "files/engine/launcher/nautilus_backtest.py",
            "/engine/launcher/nautilus_backtest.py",
            b"obsolete-launcher",
            0o400,
        ),
        _record(
            base,
            "files/engine/launcher/target_portfolio_strategy.py",
            "/engine/launcher/target_portfolio_strategy.py",
            b"obsolete-strategy",
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
        "source_commit": UPSTREAM_COMMIT,
        "timeout_seconds": 120,
    }
    base_manifest_path = base / "closure-manifest.json"
    base_manifest_path.write_bytes(_canonical(base_manifest) + b"\n")
    _seal_tree(base)

    artifacts = packet / "artifacts"
    artifacts.mkdir(mode=0o700)
    engine_wheel = artifacts / WHEEL_NAME
    engine_wheel.write_bytes(b"selected-nautilus-wheel")
    engine_wheel.chmod(0o400)
    artifact_manifest = {
        "engine_name": "nautilus_trader",
        "engine_version": "1.227.0",
        "python_identity": "CPython 3.12.3",
        "upstream_commit": UPSTREAM_COMMIT,
        "wheel": {
            "filename": WHEEL_NAME,
            "sha256": _sha256(engine_wheel),
            "size": engine_wheel.stat().st_size,
        },
    }
    artifact_manifest_path = artifacts / "artifact-manifest.json"
    artifact_manifest_path.write_bytes(_canonical(artifact_manifest) + b"\n")
    artifact_manifest_path.chmod(0o400)
    artifacts.chmod(0o500)

    native_entry_guard = dict(
        json.loads(CHECKED_IN_POLICY.read_text("ascii"))["native_entry_guard"]
    )
    native_entry_guard["source_sha256"] = _sha256(
        ROOT / "engines/nautilus/native_entry_guard/src/main.rs"
    )
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
        "base_file_inventory_sha256": _sha256_bytes(_canonical(records)),
        "base_runtime_manifest_sha256": _sha256(base_manifest_path),
        "dependency_import_policy": IMPORT_POLICY,
        "engine_name": "nautilus_trader",
        "engine_upstream_commit": UPSTREAM_COMMIT,
        "engine_version": "1.227.0",
        "engine_wheel_mode": "0400",
        "engine_wheel_target": f"/engine/wheels/{WHEEL_NAME}",
        "entrypoint": "/engine/bin/nautilus-entry-guard",
        "launcher_inventory": [
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
        "native_entry_guard": native_entry_guard,
        "profile": "execution-simulation",
        "profile_manifest_schema_version": 6,
        "python_identity": "CPython 3.12.3",
        "result_validator_id": "nautilus-backtest-simulation-result-v1",
        "schema_version": 1,
        "semantic_profile": "nautilus-execution-simulation-v2",
        "source_commit": SOURCE_COMMIT,
        "timeout_seconds": 120,
    }
    policy_path = packet / "runtime-closure-policy.json"
    policy_path.write_bytes(_canonical(policy) + b"\n")
    policy_path.chmod(0o644)
    sandbox = packet / "bwrap"
    sandbox.write_bytes(b"reviewed-bubblewrap")
    sandbox.chmod(0o755)
    receipt_parent = packet / "receipts"
    receipt_parent.mkdir(mode=0o700)
    receipt = receipt_parent / "import-receipt.json"
    try:
        yield {
            "packet": packet,
            "policy": policy_path,
            "policy_document": policy,
            "base": base,
            "base_manifest": base_manifest,
            "base_manifest_path": base_manifest_path,
            "records": records,
            "artifacts": artifacts,
            "artifact_manifest": artifact_manifest,
            "artifact_manifest_path": artifact_manifest_path,
            "engine_wheel": engine_wheel,
            "sandbox": sandbox,
            "receipt_parent": receipt_parent,
            "receipt": receipt,
        }
    finally:
        _unseal_tree(packet)
        shutil.rmtree(packet, ignore_errors=True)


def _probe_document(inputs: dict[str, object]) -> dict[str, object]:
    records = inputs["records"]
    assert isinstance(records, list)
    wheel_digests = {
        Path(str(record["target"])).name: str(record["sha256"])
        for record in records
        if str(record["target"]).startswith("/engine/wheels/")
    }
    wheel_digests[WHEEL_NAME] = _sha256(inputs["engine_wheel"])
    return {
        "modules": [
            {
                "name": "nautilus_trader",
                "source_wheel_sha256": wheel_digests[WHEEL_NAME],
                "version": "1.227.0",
            },
            {
                "name": "numpy",
                "source_wheel_sha256": wheel_digests["numpy-2.2.6-cp312.whl"],
                "version": "2.2.6",
            },
            {
                "name": "pandas",
                "source_wheel_sha256": wheel_digests["pandas-2.3.0-cp312.whl"],
                "version": "2.3.0",
            },
        ],
        "schema_version": "nautilus-sealed-import-probe-v1",
        "status": "passed",
        "strategy_source_sha256": _sha256(STRATEGY),
    }


class _InertRunner:
    def __init__(
        self,
        probe_document: dict[str, object],
        *,
        stderr: bytes = b"",
        returncode: int = 0,
        before_probe=None,
    ) -> None:
        self.probe_document = probe_document
        self.stderr = stderr
        self.returncode = returncode
        self.before_probe = before_probe
        self.probe_argv: tuple[str, ...] | None = None

    def __call__(self, argv, **kwargs):
        command = tuple(argv)
        assert kwargs["env"] == {}
        assert kwargs["timeout"] <= 120
        if command[1:] == ("--version",):
            return subprocess.CompletedProcess(command, 0, b"bubblewrap 0.9.0\n", b"")
        if command[1:] == ("--help",):
            return subprocess.CompletedProcess(
                command, 0, b"usage: bwrap --perms --ro-bind-data\n", b""
            )
        assert kwargs["cwd"] == Path("/")
        assert kwargs["stdin"] is subprocess.DEVNULL
        assert kwargs["stdout"] is subprocess.PIPE
        assert kwargs["stderr"] is subprocess.PIPE
        assert kwargs["check"] is False
        assert kwargs["close_fds"] is True
        assert set(kwargs["pass_fds"]) == {
            int(command[0].removeprefix("/proc/self/fd/")),
            *(
                int(command[index + 1])
                for index, value in enumerate(command)
                if value == "--ro-bind-data"
            ),
        }
        assert command[1:7] == (
            "--die-with-parent",
            "--unshare-user",
            "--unshare-pid",
            "--unshare-net",
            "--new-session",
            "--clearenv",
        )
        assert "--share-net" not in command
        assert command[-8:] == (
            "/usr/bin/python3.12",
            "-I",
            "-S",
            "/qualification/import_probe.py",
            "--entry-launcher",
            "/qualification/entry-launcher.py",
            "--wheel-directory",
            "/engine/wheels",
        )
        assert command[-16:-8] == (
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--chdir",
            "/",
        )
        targets = [
            command[index + 2]
            for index, value in enumerate(command)
            if value == "--ro-bind-data"
        ]
        assert len(targets) == len(set(targets))
        assert "/" not in targets
        assert "/engine/closure-manifest.json" in targets
        assert "/engine/launcher/nautilus_backtest.py" in targets
        assert "/engine/launcher/target_portfolio_strategy.py" in targets
        assert "/qualification/entry-launcher.py" in targets
        assert "/qualification/import_probe.py" in targets
        if self.before_probe is not None:
            self.before_probe()
        self.probe_argv = command
        return subprocess.CompletedProcess(
            command,
            self.returncode,
            _canonical(self.probe_document) + b"\n",
            self.stderr,
        )


def _qualify(module, inputs: dict[str, object]) -> Path:
    return module.qualify_sealed_imports(
        policy_path=inputs["policy"],
        base_runtime=inputs["base"],
        artifact_directory=inputs["artifacts"],
        sandbox_executable=inputs["sandbox"],
        receipt_path=inputs["receipt"],
    )


@pytest.mark.parametrize(
    "core_name",
    (
        "_validate_policy_bytes",
        "_validate_base_runtime_bytes",
        "_validate_artifact_bytes",
    ),
)
def test_gate_enforces_the_shared_materializer_byte_validation_cores(
    qualification_inputs: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    core_name: str,
) -> None:
    module = _module()

    def reject_snapshot(*_args, **_kwargs):
        raise module._closure.RuntimeClosureMaterializationError(
            f"shared {core_name} rejection"
        )

    monkeypatch.setattr(module._closure, core_name, reject_snapshot, raising=False)
    monkeypatch.setattr(
        module.subprocess,
        "run",
        _InertRunner(_probe_document(qualification_inputs)),
    )

    with pytest.raises(
        module.SealedImportQualificationError,
        match=rf"shared {core_name} rejection",
    ):
        _qualify(module, qualification_inputs)

    assert not qualification_inputs["receipt"].exists()


def test_base_runtime_byte_core_matches_the_path_acquisition_wrapper(
    qualification_inputs: dict[str, object],
) -> None:
    module = _module()
    byte_core = getattr(module._closure, "_validate_base_runtime_bytes", None)
    assert callable(byte_core), "shared base-runtime byte validator is missing"
    policy = module._closure._load_policy(qualification_inputs["policy"])

    wrapped_manifest, wrapped_records = module._closure._validate_base_runtime(
        qualification_inputs["base"], policy
    )
    direct_manifest, direct_records, direct_files = byte_core(
        qualification_inputs["base_manifest_path"].read_bytes(),
        policy,
        file_reader=lambda relative, _mode: qualification_inputs["base"]
        .joinpath(*relative.parts)
        .read_bytes(),
    )

    assert direct_manifest == wrapped_manifest
    assert direct_records == wrapped_records
    assert direct_files == {
        str(record["path"]): qualification_inputs["base"]
        .joinpath(*Path(str(record["path"])).parts)
        .read_bytes()
        for record in qualification_inputs["records"]
    }


def test_artifact_byte_core_matches_the_path_acquisition_wrapper(
    qualification_inputs: dict[str, object],
) -> None:
    module = _module()
    byte_core = getattr(module._closure, "_validate_artifact_bytes", None)
    assert callable(byte_core), "shared artifact byte validator is missing"
    policy = module._closure._load_policy(qualification_inputs["policy"])

    wrapped_manifest, wrapped_wheel = module._closure._validate_artifact(
        qualification_inputs["artifacts"], policy
    )
    direct_manifest, direct_filename, direct_wheel = byte_core(
        qualification_inputs["artifact_manifest_path"].read_bytes(),
        policy,
        wheel_reader=lambda filename: (
            qualification_inputs["artifacts"] / filename
        ).read_bytes(),
    )

    assert direct_manifest == wrapped_manifest
    assert direct_filename == wrapped_wheel.name
    assert direct_wheel == wrapped_wheel.read_bytes()


@pytest.mark.parametrize(
    ("helper_name", "materializer_wrapper"),
    (
        ("_validate_base_runtime_path_inventory", "base"),
        ("_validate_artifact_path_inventory", "artifact"),
    ),
)
def test_gate_and_materializer_enforce_stricter_shared_path_inventory_helpers(
    qualification_inputs: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    helper_name: str,
    materializer_wrapper: str,
) -> None:
    module = _module()

    def reject_path_inventory(*_args, **_kwargs) -> None:
        raise module._closure.RuntimeClosureMaterializationError(
            f"shared {helper_name} rejection"
        )

    monkeypatch.setattr(
        module._closure,
        helper_name,
        reject_path_inventory,
        raising=False,
    )
    monkeypatch.setattr(
        module.subprocess,
        "run",
        _InertRunner(_probe_document(qualification_inputs)),
    )

    with pytest.raises(
        module.SealedImportQualificationError,
        match=rf"shared {helper_name} rejection",
    ):
        _qualify(module, qualification_inputs)

    policy = qualification_inputs["policy_document"]
    with pytest.raises(
        module._closure.RuntimeClosureMaterializationError,
        match=rf"shared {helper_name} rejection",
    ):
        if materializer_wrapper == "base":
            module._closure._validate_base_runtime(
                qualification_inputs["base"], policy
            )
        else:
            module._closure._validate_artifact(
                qualification_inputs["artifacts"], policy
            )

    assert not qualification_inputs["receipt"].exists()


@pytest.mark.parametrize(
    "mutation",
    ("ambient-file", "unsafe-directory-mode", "symlinked-directory"),
)
def test_shared_base_path_inventory_and_both_wrappers_reject_the_same_mutation(
    qualification_inputs: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    module = _module()
    helper = getattr(
        module._closure,
        "_validate_base_runtime_path_inventory",
        None,
    )
    assert callable(helper), "shared base-runtime path inventory helper is missing"
    base = qualification_inputs["base"]
    files_root = base / "files"
    if mutation == "ambient-file":
        files_root.chmod(0o700)
        ambient = files_root / "ambient.py"
        ambient.write_bytes(b"ambient")
        ambient.chmod(0o400)
        files_root.chmod(0o500)
    elif mutation == "unsafe-directory-mode":
        (files_root / "usr/lib").chmod(0o700)
    else:
        usr = files_root / "usr"
        usr.chmod(0o700)
        original = usr / "lib"
        displaced = usr / "lib-sealed"
        original.rename(displaced)
        original.symlink_to(displaced.name, target_is_directory=True)
        usr.chmod(0o500)
    listed = {
        PurePosixPath(str(record["path"]))
        for record in qualification_inputs["records"]
    }
    monkeypatch.setattr(
        module.subprocess,
        "run",
        _InertRunner(_probe_document(qualification_inputs)),
    )

    with pytest.raises(
        module._closure.RuntimeClosureMaterializationError,
        match="unlisted|directory|unsafe",
    ):
        helper(base, listed)
    with pytest.raises(
        module._closure.RuntimeClosureMaterializationError,
        match="unlisted|directory|unsafe",
    ):
        module._closure._validate_base_runtime(
            base,
            qualification_inputs["policy_document"],
        )
    with pytest.raises(
        module.SealedImportQualificationError,
        match="unlisted|directory|unsafe",
    ):
        _qualify(module, qualification_inputs)

    assert not qualification_inputs["receipt"].exists()


def test_shared_artifact_path_inventory_and_both_wrappers_reject_an_extra_file(
    qualification_inputs: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    helper = getattr(module._closure, "_validate_artifact_path_inventory", None)
    assert callable(helper), "shared artifact path inventory helper is missing"
    artifacts = qualification_inputs["artifacts"]
    artifacts.chmod(0o700)
    extra = artifacts / "ambient.whl"
    extra.write_bytes(b"ambient")
    extra.chmod(0o400)
    artifacts.chmod(0o500)
    expected = {
        qualification_inputs["artifact_manifest_path"],
        qualification_inputs["engine_wheel"],
    }
    monkeypatch.setattr(
        module.subprocess,
        "run",
        _InertRunner(_probe_document(qualification_inputs)),
    )

    with pytest.raises(
        module._closure.RuntimeClosureMaterializationError,
        match="unlisted",
    ):
        helper(artifacts, expected)
    with pytest.raises(
        module._closure.RuntimeClosureMaterializationError,
        match="unlisted",
    ):
        module._closure._validate_artifact(
            artifacts,
            qualification_inputs["policy_document"],
        )
    with pytest.raises(
        module.SealedImportQualificationError,
        match="unlisted",
    ):
        _qualify(module, qualification_inputs)

    assert not qualification_inputs["receipt"].exists()


def test_gate_uses_production_fd_topology_and_writes_canonical_digest_receipt(
    qualification_inputs: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    probe_document = _probe_document(qualification_inputs)
    runner = _InertRunner(probe_document)
    monkeypatch.setattr(module.subprocess, "run", runner)

    result = _qualify(module, qualification_inputs)

    receipt = qualification_inputs["receipt"]
    assert result == receipt
    assert runner.probe_argv is not None
    raw = receipt.read_bytes()
    document = json.loads(raw)
    assert raw == _canonical(document) + b"\n"
    assert stat.S_IMODE(receipt.stat().st_mode) == 0o400
    assert set(document) == {
        "schema_version",
        "status",
        "profile",
        "manifest_schema_version",
        "dependency_import_policy",
        "policy_sha256",
        "base_runtime_manifest_sha256",
        "artifact_manifest_sha256",
        "python_sha256",
        "native_entry_guard_policy_sha256",
        "launcher_inventory_sha256",
        "entry_launcher_sha256",
        "probe_sha256",
        "strategy_source_sha256",
        "minimal_manifest_sha256",
        "wheel_inventory_sha256",
        "sandbox_sha256",
        "sandbox_profile_sha256",
        "probe_result_sha256",
        "modules",
        "receipt_sha256",
    }
    assert document["schema_version"] == "nautilus-sealed-import-qualification-v1"
    assert document["status"] == "passed"
    assert document["profile"] == "execution-simulation"
    assert document["manifest_schema_version"] == 6
    assert document["dependency_import_policy"] == IMPORT_POLICY
    assert document["policy_sha256"] == _sha256(qualification_inputs["policy"])
    assert document["base_runtime_manifest_sha256"] == _sha256(
        qualification_inputs["base_manifest_path"]
    )
    assert document["artifact_manifest_sha256"] == _sha256(
        qualification_inputs["artifact_manifest_path"]
    )
    assert document["python_sha256"] == _sha256_bytes(b"sealed-cpython-3.12")
    assert document["native_entry_guard_policy_sha256"] == _sha256_bytes(
        _canonical(qualification_inputs["policy_document"]["native_entry_guard"])
    )
    assert document["launcher_inventory_sha256"] == _sha256_bytes(
        _canonical(qualification_inputs["policy_document"]["launcher_inventory"])
    )
    assert document["entry_launcher_sha256"] == _sha256(LAUNCHER)
    assert document["probe_sha256"] == _sha256(PROBE)
    assert document["strategy_source_sha256"] == _sha256(STRATEGY)
    assert document["sandbox_sha256"] == _sha256(qualification_inputs["sandbox"])
    assert document["sandbox_profile_sha256"] == _sha256_bytes(SANDBOX_PROFILE)
    assert document["probe_result_sha256"] == _sha256_bytes(_canonical(probe_document))
    assert document["modules"] == probe_document["modules"]
    assert document["receipt_sha256"] == _sha256_bytes(
        _canonical({key: value for key, value in document.items() if key != "receipt_sha256"})
    )
    assert all("/" not in str(value) for value in document.values())


def test_gate_receipt_binds_the_explicit_paper_profile_and_entry_launcher(
    qualification_inputs: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    policy = qualification_inputs["policy_document"]
    assert isinstance(policy, dict)
    policy.update(
        {
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
                *policy["launcher_inventory"],
            ],
            "profile": "paper-compatibility",
            "result_validator_id": "nautilus-paper-compatibility-result-v1",
            "semantic_profile": "nautilus-paper-compatibility-v1",
        }
    )
    guard = policy["native_entry_guard"]
    assert isinstance(guard, dict)
    guard["source_sha256"] = _sha256(
        ROOT / "engines/nautilus/native_entry_guard/src/main.rs"
    )
    policy_path = qualification_inputs["policy"]
    assert isinstance(policy_path, Path)
    policy_path.write_bytes(_canonical(policy) + b"\n")
    runner = _InertRunner(_probe_document(qualification_inputs))
    monkeypatch.setattr(module.subprocess, "run", runner)

    _qualify(module, qualification_inputs)

    document = json.loads(qualification_inputs["receipt"].read_bytes())
    assert document["profile"] == "paper-compatibility"
    assert document["entry_launcher_sha256"] == _sha256(PAPER_LAUNCHER)
    assert runner.probe_argv is not None
    assert "/engine/launcher/nautilus_paper_compat.py" in runner.probe_argv


@pytest.mark.parametrize("field", ("policy", "base", "artifacts", "sandbox", "receipt"))
def test_gate_rejects_relative_cli_paths_before_subprocess(
    qualification_inputs: dict[str, object], field: str
) -> None:
    module = _module()
    arguments = dict(
        policy_path=qualification_inputs["policy"],
        base_runtime=qualification_inputs["base"],
        artifact_directory=qualification_inputs["artifacts"],
        sandbox_executable=qualification_inputs["sandbox"],
        receipt_path=qualification_inputs["receipt"],
    )
    original_key = {
        "policy": "policy_path",
        "base": "base_runtime",
        "artifacts": "artifact_directory",
        "sandbox": "sandbox_executable",
        "receipt": "receipt_path",
    }[field]
    if original_key not in arguments:
        pytest.fail("test argument mapping is invalid")
    arguments[original_key] = Path("relative")

    with pytest.raises(module.SealedImportQualificationError, match="absolute"):
        module.qualify_sealed_imports(**arguments)

    assert not qualification_inputs["receipt"].exists()


def test_gate_rejects_an_ambient_base_runtime_file(
    qualification_inputs: dict[str, object]
) -> None:
    module = _module()
    base = qualification_inputs["base"]
    base.chmod(0o700)
    wheels = base / "files/engine/wheels"
    wheels.chmod(0o700)
    ambient = wheels / "ambient-1.0.whl"
    ambient.write_bytes(b"ambient")
    ambient.chmod(0o400)
    wheels.chmod(0o500)
    base.chmod(0o500)
    with pytest.raises(module.SealedImportQualificationError):
        _qualify(module, qualification_inputs)
    assert not qualification_inputs["receipt"].exists()


@pytest.mark.parametrize("mutation", ("group-writable-policy", "symlinked-sandbox"))
def test_gate_rejects_group_writable_or_symlinked_named_inputs(
    qualification_inputs: dict[str, object], mutation: str
) -> None:
    module = _module()
    if mutation == "group-writable-policy":
        qualification_inputs["policy"].chmod(0o664)
    else:
        sandbox = qualification_inputs["sandbox"]
        real_sandbox = sandbox.with_name("bwrap-real")
        sandbox.rename(real_sandbox)
        sandbox.symlink_to(real_sandbox)

    with pytest.raises(module.SealedImportQualificationError):
        _qualify(module, qualification_inputs)

    assert not qualification_inputs["receipt"].exists()


def _rewrite_named_file(path: Path, value: bytes, *, mode: int) -> None:
    parent_mode = stat.S_IMODE(path.parent.stat().st_mode)
    path.parent.chmod(0o700)
    path.chmod(0o600)
    path.write_bytes(value)
    path.chmod(mode)
    path.parent.chmod(parent_mode)


@pytest.mark.parametrize(
    "authority",
    ("policy", "base-manifest", "artifact-manifest", "base-file", "selected-wheel"),
)
def test_gate_rejects_validation_to_snapshot_change_restore(
    qualification_inputs: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    authority: str,
) -> None:
    module = _module()
    qualification_inputs["policy"].chmod(0o400)
    qualification_inputs["sandbox"].chmod(0o500)
    policy = qualification_inputs["policy"]
    base_manifest = qualification_inputs["base_manifest_path"]
    artifact_manifest = qualification_inputs["artifact_manifest_path"]
    base_file = qualification_inputs["base"] / "files/usr/lib/python3.12/os.py"
    selected_wheel = qualification_inputs["engine_wheel"]
    paths = {
        "policy": policy,
        "base-manifest": base_manifest,
        "artifact-manifest": artifact_manifest,
        "base-file": base_file,
        "selected-wheel": selected_wheel,
    }
    modes = {
        "policy": 0o400,
        "base-manifest": 0o400,
        "artifact-manifest": 0o400,
        "base-file": 0o400,
        "selected-wheel": 0o400,
    }
    target = paths[authority]
    original = target.read_bytes()
    altered = (
        original[:-1] + b" \n"
        if authority in {"policy", "base-manifest", "artifact-manifest"}
        else b"qualification-race-altered-bytes"
    )

    original_open = module.os.open
    target_opens = 0

    def change_before_second_open(path, flags, *args, **kwargs):
        nonlocal target_opens
        if Path(path) == target:
            target_opens += 1
            if target_opens == 2:
                _rewrite_named_file(target, altered, mode=modes[authority])
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(module.os, "open", change_before_second_open)

    document = _probe_document(qualification_inputs)
    runner = None

    def restore() -> None:
        _rewrite_named_file(target, original, mode=modes[authority])
        if authority == "selected-wheel":
            assert runner is not None
            runner.probe_document["modules"][0]["source_wheel_sha256"] = _sha256_bytes(
                altered
            )

    runner = _InertRunner(document, before_probe=restore)
    monkeypatch.setattr(module.subprocess, "run", runner)

    with pytest.raises(module.SealedImportQualificationError, match="stale|bound|drift"):
        _qualify(module, qualification_inputs)

    assert not qualification_inputs["receipt"].exists()


def test_gate_rejects_receipt_parent_replaced_during_staging(
    qualification_inputs: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    qualification_inputs["policy"].chmod(0o400)
    qualification_inputs["sandbox"].chmod(0o500)
    runner = _InertRunner(_probe_document(qualification_inputs))
    monkeypatch.setattr(module.subprocess, "run", runner)
    parent = qualification_inputs["receipt_parent"]
    displaced = parent.with_name("receipts-displaced")
    original_open = module.os.open
    replaced = False

    def replace_then_create(path, flags, *args, **kwargs):
        nonlocal replaced
        if not replaced and kwargs.get("dir_fd") is not None and flags & os.O_CREAT:
            replaced = True
            parent.rename(displaced)
            parent.mkdir(mode=0o700)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(module.os, "open", replace_then_create)

    with pytest.raises(module.SealedImportQualificationError, match="parent|stale"):
        _qualify(module, qualification_inputs)


def test_gate_rejects_receipt_parent_replaced_after_link(
    qualification_inputs: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    qualification_inputs["policy"].chmod(0o400)
    qualification_inputs["sandbox"].chmod(0o500)
    runner = _InertRunner(_probe_document(qualification_inputs))
    monkeypatch.setattr(module.subprocess, "run", runner)
    parent = qualification_inputs["receipt_parent"]
    receipt = qualification_inputs["receipt"]
    displaced = parent.with_name("receipts-linked")
    original_link = module.os.link

    def link_then_replace(source, destination, **kwargs):
        result = original_link(source, destination, **kwargs)
        temporary_name = Path(source).name
        parent.rename(displaced)
        parent.mkdir(mode=0o700)
        replacement_temp = parent / temporary_name
        replacement_temp.write_bytes(b"replacement-temp")
        replacement_temp.chmod(0o400)
        receipt.write_bytes(b"replacement-receipt")
        receipt.chmod(0o400)
        return result

    monkeypatch.setattr(module.os, "link", link_then_replace)

    with pytest.raises(module.SealedImportQualificationError, match="parent|stale"):
        _qualify(module, qualification_inputs)


def test_gate_accepts_owner_writable_source_and_trusted_0755_sandbox(
    qualification_inputs: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    monkeypatch.setattr(
        module.subprocess,
        "run",
        _InertRunner(_probe_document(qualification_inputs)),
    )

    assert _qualify(module, qualification_inputs) == qualification_inputs["receipt"]


def test_gate_rejects_group_writable_sandbox(
    qualification_inputs: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    qualification_inputs["policy"].chmod(0o400)
    qualification_inputs["sandbox"].chmod(0o775)
    monkeypatch.setattr(
        module.subprocess,
        "run",
        _InertRunner(_probe_document(qualification_inputs)),
    )

    with pytest.raises(module.SealedImportQualificationError, match="Bubblewrap|sandbox"):
        _qualify(module, qualification_inputs)


def test_gate_rejects_group_writable_checked_in_probe_source(
    qualification_inputs: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    qualification_inputs["policy"].chmod(0o400)
    qualification_inputs["sandbox"].chmod(0o500)
    probe = qualification_inputs["packet"] / "import_probe.py"
    probe.write_bytes(PROBE.read_bytes())
    probe.chmod(0o664)
    monkeypatch.setattr(module, "_PROBE", probe)
    monkeypatch.setattr(
        module.subprocess,
        "run",
        _InertRunner(_probe_document(qualification_inputs)),
    )

    with pytest.raises(module.SealedImportQualificationError, match="probe|source|mode"):
        _qualify(module, qualification_inputs)


def test_trusted_system_owner_accepts_root_or_effective_uid_only() -> None:
    module = _module()

    assert module._trusted_system_owner(0, 1000) is True
    assert module._trusted_system_owner(1000, 1000) is True
    assert module._trusted_system_owner(1001, 1000) is False


def test_gate_rejects_artifact_hash_drift_and_preexisting_receipt(
    qualification_inputs: dict[str, object]
) -> None:
    module = _module()
    wheel = qualification_inputs["engine_wheel"]
    qualification_inputs["artifacts"].chmod(0o700)
    wheel.chmod(0o600)
    wheel.write_bytes(b"drifted")
    wheel.chmod(0o400)
    qualification_inputs["artifacts"].chmod(0o500)
    with pytest.raises(module.SealedImportQualificationError, match="wheel"):
        _qualify(module, qualification_inputs)
    qualification_inputs["receipt"].write_bytes(b"preexisting")
    qualification_inputs["receipt"].chmod(0o400)
    with pytest.raises(module.SealedImportQualificationError, match="receipt"):
        _qualify(module, qualification_inputs)


def test_gate_rejects_stale_named_inputs_after_the_probe(
    qualification_inputs: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    stdlib = qualification_inputs["base"] / "files/usr/lib/python3.12/os.py"

    def mutate() -> None:
        qualification_inputs["base"].chmod(0o700)
        stdlib.parent.chmod(0o700)
        stdlib.chmod(0o600)
        stdlib.write_bytes(b"stale")
        stdlib.chmod(0o400)
        stdlib.parent.chmod(0o500)
        qualification_inputs["base"].chmod(0o500)

    runner = _InertRunner(_probe_document(qualification_inputs), before_probe=mutate)
    monkeypatch.setattr(module.subprocess, "run", runner)

    with pytest.raises(module.SealedImportQualificationError, match="stale"):
        _qualify(module, qualification_inputs)

    assert not qualification_inputs["receipt"].exists()


@pytest.mark.parametrize(
    ("stderr", "returncode", "mutation"),
    ((b"warning", 0, "stderr"), (b"", 9, "failed"), (b"", 0, "malformed")),
)
def test_gate_rejects_non_pristine_or_malformed_probe_results(
    qualification_inputs: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    stderr: bytes,
    returncode: int,
    mutation: str,
) -> None:
    module = _module()
    document = _probe_document(qualification_inputs)
    if mutation == "malformed":
        document["modules"] = []
    runner = _InertRunner(document, stderr=stderr, returncode=returncode)
    monkeypatch.setattr(module.subprocess, "run", runner)

    with pytest.raises(module.SealedImportQualificationError):
        _qualify(module, qualification_inputs)

    assert not qualification_inputs["receipt"].exists()


def test_make_gate_is_parameterized_by_an_explicit_external_packet(
    qualification_inputs: dict[str, object],
) -> None:
    environment = {
        **os.environ,
        "NAUTILUS_IMPORT_POLICY": str(qualification_inputs["policy"]),
        "NAUTILUS_IMPORT_BASE_RUNTIME": str(qualification_inputs["base"]),
        "NAUTILUS_IMPORT_ARTIFACT_DIRECTORY": str(qualification_inputs["artifacts"]),
        "NAUTILUS_IMPORT_SANDBOX": str(qualification_inputs["sandbox"]),
        "NAUTILUS_IMPORT_RECEIPT": str(qualification_inputs["receipt"]),
    }

    result = subprocess.run(
        ("make", "-n", "qualify-nautilus-sealed-imports"),
        cwd=ROOT,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "scripts/qualify_nautilus_sealed_imports.py" in result.stdout
    for flag, value in (
        ("--policy", qualification_inputs["policy"]),
        ("--base-runtime", qualification_inputs["base"]),
        ("--artifact-directory", qualification_inputs["artifacts"]),
        ("--sandbox", qualification_inputs["sandbox"]),
        ("--receipt", qualification_inputs["receipt"]),
    ):
        assert f'{flag} "{value}"' in result.stdout


def test_task8_root_package_controllers_use_the_locked_python() -> None:
    """Task 8 uses one isolated root interpreter for every root-package CLI."""
    text = ARCHITECTURE_PLAN.read_text(encoding="utf-8")
    task8_start = text.index("### Task 8: Qualify, Publish v12-r9/v13, Run Parity, and Close 04D")
    start = text.index('phase4_runtime_root="$(mktemp -d -p /tmp phase4-v12-r9-v13-XXXXXX)"')
    end = text.index("---\n\n### Task 9:", start)
    block = text[start:end]
    task8_block = text[task8_start:end]

    assert 'phase4_source_root="$(git rev-parse --show-toplevel)"' in block
    assert 'UV_OFFLINE=1 uv sync --frozen' in block
    assert (
        'phase4_root_python="${phase4_source_root}/.venv/bin/python"'
        in block
    )
    assert (
        '"${phase4_root_python}" -I -B -c '
        "'import pydantic; assert pydantic.__version__ == \"2.13.4\"'"
    ) in block
    assert "phase4_qualification_python" not in block
    for script, expected_count in TASK8_ROOT_CONTROLLER_SCRIPTS.items():
        root_invocation = f'"${{phase4_root_python}}" -I scripts/{script}'
        assert block.count(root_invocation) == expected_count
        assert f"python3.11 -I scripts/{script}" not in block

    # These two controllers are intentionally stdlib-only and retain their
    # reviewed literal system-Python contracts.
    assert "python3.11 -I scripts/build_nautilus_engine.py" in task8_block
    assert (
        task8_block.count("python3.11 -I scripts/materialize_sealed_uv_exec.py")
        == 2
    )

    root_python = ROOT / ".venv/bin/python"
    dependency_probe = subprocess.run(
        (
            str(root_python),
            "-I",
            "-B",
            "-c",
            "import pydantic; assert pydantic.__version__ == '2.13.4'",
        ),
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        text=True,
    )

    assert dependency_probe.returncode == 0, dependency_probe.stderr

    for script in TASK8_ROOT_CONTROLLER_SCRIPTS:
        result = subprocess.run(
            (str(root_python), "-I", str(ROOT / "scripts" / script), "--help"),
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            text=True,
        )

        assert result.returncode == 0, f"{script}: {result.stderr}"
        assert "usage:" in result.stdout
