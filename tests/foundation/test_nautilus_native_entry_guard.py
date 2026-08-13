from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

import pytest

import scripts.materialize_nautilus_runtime_closure as materializer_module


ROOT = Path(__file__).resolve().parents[2]
GUARD_PROJECT = ROOT / "engines/nautilus/native_entry_guard"
RUNTIME_POLICY = ROOT / "engines/nautilus/runtime-closure-policy.json"
PAPER_POLICY = (
    ROOT / "engines/nautilus/paper-compatibility-runtime-closure-policy.json"
)
PRIVATE_RUST = Path(
    "/home/thenam176/.cache/trading-agent/nautilus/rust-1.95.0"
)
PRIVATE_LLVM = Path(
    "/home/thenam176/.cache/trading-agent/nautilus/llvm-22.1.3-resource-toolchain"
)
PRIVATE_TOOLCHAINS_UNAVAILABLE_REASON = (
    "required sealed private Rust or LLVM toolchain executable is unavailable"
)
TARGET = "x86_64-unknown-linux-gnu"
GUARD_TARGET = "/engine/bin/nautilus-entry-guard"
LAUNCHER_TARGET = "/engine/launcher/nautilus_backtest.py"
PAPER_LAUNCHER_TARGET = "/engine/launcher/nautilus_paper_compat.py"
REQUEST_TARGET = "/inputs/request.json"
SIDECAR_TARGET = "/inputs/request.sha256"


@pytest.fixture
def secure_build_root() -> Path:
    root = Path(tempfile.mkdtemp(prefix="nautilus-native-guard-test-", dir="/tmp"))
    root.chmod(0o700)
    try:
        yield root
    finally:
        shutil.rmtree(root)


def _build_guard(
    tmp_path: Path,
    guarded_executable: Path,
    *,
    profile: str = "execution-simulation",
) -> Path:
    cargo = PRIVATE_RUST / "bin/cargo"
    rustc = PRIVATE_RUST / "bin/rustc"
    linker = PRIVATE_LLVM / "bin/clang"
    if not all(path.is_file() for path in (cargo, rustc, linker)):
        pytest.skip(PRIVATE_TOOLCHAINS_UNAVAILABLE_REASON)

    cargo_home = tmp_path / "cargo-home"
    target_directory = tmp_path / "target"
    compiler_tmp = tmp_path / "compiler-tmp"
    for directory in (cargo_home, target_directory, compiler_tmp):
        directory.mkdir(mode=0o700)
    environment = {
        "CARGO_HOME": str(cargo_home),
        "CARGO_INCREMENTAL": "0",
        "CARGO_NET_OFFLINE": "true",
        "CARGO_TARGET_DIR": str(target_directory),
        "HOME": str(tmp_path),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "NAUTILUS_GUARD_ENTRYPOINT": GUARD_TARGET,
        "NAUTILUS_GUARD_LAUNCHER": (
            LAUNCHER_TARGET
            if profile == "execution-simulation"
            else PAPER_LAUNCHER_TARGET
        ),
        "NAUTILUS_GUARD_PROFILE": profile,
        "NAUTILUS_GUARD_PYTHON": str(guarded_executable),
        "NAUTILUS_GUARD_REQUEST": REQUEST_TARGET,
        "NAUTILUS_GUARD_SIDECAR": SIDECAR_TARGET,
        "PATH": f"{PRIVATE_RUST / 'bin'}:{PRIVATE_LLVM / 'bin'}",
        "RUSTC": str(rustc),
        "RUSTFLAGS": (
            f"-C linker={linker} -C link-arg=-fuse-ld=lld "
            "-C link-arg=-Wl,--build-id=none"
        ),
        "SOURCE_DATE_EPOCH": "0",
        "TEMP": str(compiler_tmp),
        "TMP": str(compiler_tmp),
        "TMPDIR": str(compiler_tmp),
    }
    subprocess.run(
        [
            str(cargo),
            "build",
            "--manifest-path",
            str(GUARD_PROJECT / "Cargo.toml"),
            "--locked",
            "--offline",
            "--release",
            "--target",
            TARGET,
        ],
        check=True,
        cwd=GUARD_PROJECT,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
    )
    return target_directory / TARGET / "release/nautilus-entry-guard"


def _inert_fixture(tmp_path: Path) -> tuple[Path, Path]:
    marker = tmp_path / "guard-exec-marker"
    executable = tmp_path / "inert-guarded-executable"
    executable.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$@\" > {marker}\n"
        f"printf '%s\\n' \"${{POISON-unset}}\" >> {marker}\n",
        encoding="ascii",
    )
    executable.chmod(0o500)
    return executable, marker


def _exact_guard_argv(
    guarded_executable: Path,
    *,
    profile: str = "execution-simulation",
) -> list[str]:
    return [
        GUARD_TARGET,
        str(guarded_executable),
        "-I",
        "-S",
        (
            LAUNCHER_TARGET
            if profile == "execution-simulation"
            else PAPER_LAUNCHER_TARGET
        ),
        "--profile",
        profile,
        REQUEST_TARGET,
        SIDECAR_TARGET,
    ]


def test_native_guard_execs_the_guarded_fixture_only_for_exact_os_argv(
    tmp_path: Path,
) -> None:
    guarded_executable, marker = _inert_fixture(tmp_path)
    guard = _build_guard(tmp_path, guarded_executable)

    completed = subprocess.run(
        _exact_guard_argv(guarded_executable),
        executable=guard,
        check=False,
        env={"POISON": "ambient-value"},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )

    assert completed.returncode == 0
    assert completed.stdout == b""
    assert completed.stderr == b""
    assert marker.read_text(encoding="ascii").splitlines() == [
        "-I",
        "-S",
        LAUNCHER_TARGET,
        "--profile",
        "execution-simulation",
        REQUEST_TARGET,
        SIDECAR_TARGET,
        "unset",
    ]


@pytest.mark.parametrize(
    "mutation",
    (
        "missing-argument",
        "wrong-guard-argv0",
        "foreign-executable",
        "wrong-isolation-flag",
        "extra-argument",
    ),
)
def test_native_guard_rejects_every_non_exact_os_argv_without_exec(
    tmp_path: Path,
    mutation: str,
) -> None:
    guarded_executable, marker = _inert_fixture(tmp_path)
    guard = _build_guard(tmp_path, guarded_executable)
    argv = _exact_guard_argv(guarded_executable)
    if mutation == "missing-argument":
        argv.pop()
    elif mutation == "wrong-guard-argv0":
        argv[0] = "/engine/bin/foreign-guard"
    elif mutation == "foreign-executable":
        argv[1] = "/bin/false"
    elif mutation == "wrong-isolation-flag":
        argv[2] = "-E"
    else:
        argv.append("unexpected")

    completed = subprocess.run(
        argv,
        executable=guard,
        check=False,
        env={},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )

    assert completed.returncode != 0
    assert completed.stdout == b""
    assert not marker.exists()


def test_profile_specific_guards_reject_the_other_fixed_launcher_and_profile(
    tmp_path: Path,
) -> None:
    executable, marker = _inert_fixture(tmp_path)
    simulation_root = tmp_path / "simulation"
    paper_root = tmp_path / "paper"
    simulation_root.mkdir(mode=0o700)
    paper_root.mkdir(mode=0o700)
    simulation_guard = _build_guard(
        simulation_root,
        executable,
        profile="execution-simulation",
    )
    paper_guard = _build_guard(
        paper_root,
        executable,
        profile="paper-compatibility",
    )

    for guard, profile in (
        (simulation_guard, "execution-simulation"),
        (paper_guard, "paper-compatibility"),
    ):
        completed = subprocess.run(
            _exact_guard_argv(executable, profile=profile),
            executable=guard,
            check=False,
            env={},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
        assert completed.returncode == 0
        assert marker.exists()
        marker.unlink()

    crossed = (
        (simulation_guard, "paper-compatibility"),
        (paper_guard, "execution-simulation"),
    )
    for guard, profile in crossed:
        completed = subprocess.run(
            _exact_guard_argv(executable, profile=profile),
            executable=guard,
            check=False,
            env={},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
        assert completed.returncode != 0
        assert completed.stdout == b""
        assert completed.stderr == b""
        assert not marker.exists()


@pytest.mark.parametrize("policy_path", (RUNTIME_POLICY, PAPER_POLICY))
def test_materializer_builds_the_policy_bound_guard_reproducibly_offline(
    secure_build_root: Path,
    policy_path: Path,
) -> None:
    if not all(
        path.is_file()
        for path in (
            PRIVATE_RUST / "bin/cargo",
            PRIVATE_RUST / "bin/rustc",
            PRIVATE_LLVM / "bin/clang",
        )
    ):
        pytest.skip(PRIVATE_TOOLCHAINS_UNAVAILABLE_REASON)
    policy = materializer_module._load_policy(policy_path)
    guard_policy = policy["native_entry_guard"]
    first_stage = secure_build_root / "first"
    second_stage = secure_build_root / "second"
    drift_stage = secure_build_root / "binary-drift"
    first_stage.mkdir(mode=0o700)
    second_stage.mkdir(mode=0o700)
    drift_stage.mkdir(mode=0o700)
    first_stage.chmod(0o700)
    second_stage.chmod(0o700)
    drift_stage.chmod(0o700)

    first = materializer_module._build_native_entry_guard(
        staging=first_stage,
        policy=policy,
        cargo=PRIVATE_RUST / "bin/cargo",
        llvm_toolchain=PRIVATE_LLVM,
    )
    second = materializer_module._build_native_entry_guard(
        staging=second_stage,
        policy=policy,
        cargo=PRIVATE_RUST / "bin/cargo",
        llvm_toolchain=PRIVATE_LLVM,
    )

    assert isinstance(guard_policy, dict)
    assert first == second
    assert first["provenance"] == guard_policy
    assert first["file"] == {
        "mode": "0500",
        "path": "files/engine/bin/nautilus-entry-guard",
        "sha256": first["provenance"]["binary_sha256"],
        "size": first["provenance"]["binary_size"],
        "target": GUARD_TARGET,
    }
    first_binary = first_stage / first["file"]["path"]
    second_binary = second_stage / second["file"]["path"]
    assert first_binary.read_bytes() == second_binary.read_bytes()
    assert hashlib.sha256(first_binary.read_bytes()).hexdigest() == first["file"][
        "sha256"
    ]
    assert stat.S_IMODE(first_binary.stat(follow_symlinks=False).st_mode) == 0o500

    drifted_policy = {
        **policy,
        "native_entry_guard": {
            **guard_policy,
            "binary_sha256": "0" * 64,
        },
    }
    with pytest.raises(
        materializer_module.RuntimeClosureMaterializationError,
        match="binary identity drifted",
    ):
        materializer_module._build_native_entry_guard(
            staging=drift_stage,
            policy=drifted_policy,
            cargo=PRIVATE_RUST / "bin/cargo",
            llvm_toolchain=PRIVATE_LLVM,
        )


def test_materializer_rechecks_guard_source_at_the_build_boundary(
    secure_build_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = materializer_module._load_policy(RUNTIME_POLICY)
    stage = secure_build_root / "source-drift"
    stage.mkdir(mode=0o700)
    real_read_file = materializer_module._read_file

    def drift_source(path: Path, *, label: str, sealed: bool) -> bytes:
        raw = real_read_file(path, label=label, sealed=sealed)
        if (
            label == "native entry guard build source"
            and path == GUARD_PROJECT / "src/main.rs"
        ):
            return raw + b"\n"
        return raw

    monkeypatch.setattr(materializer_module, "_read_file", drift_source)
    monkeypatch.setattr(
        materializer_module,
        "_verify_native_guard_toolchains",
        lambda **_kwargs: (
            "cargo 1.95.0 (f2d3ce0bd 2026-03-21)",
            "rustc 1.95.0 (59807616e 2026-04-14)",
        ),
    )

    with pytest.raises(
        materializer_module.RuntimeClosureMaterializationError,
        match="build input drifted",
    ):
        materializer_module._build_native_entry_guard(
            staging=stage,
            policy=policy,
            cargo=PRIVATE_RUST / "bin/cargo",
            llvm_toolchain=PRIVATE_LLVM,
        )


def test_materializer_rejects_ambient_cargo_configuration_before_build(
    secure_build_root: Path,
) -> None:
    policy = materializer_module._load_policy(RUNTIME_POLICY)
    stage = secure_build_root / "ambient-cargo-config"
    stage.mkdir(mode=0o700)
    cargo_configuration = stage / ".cargo"
    cargo_configuration.mkdir(mode=0o700)
    (cargo_configuration / "config.toml").write_text(
        "[build]\nrustc-wrapper = '/unreviewed/wrapper'\n",
        encoding="ascii",
    )

    with pytest.raises(
        materializer_module.RuntimeClosureMaterializationError,
        match="ambient Cargo configuration",
    ):
        materializer_module._build_native_entry_guard(
            staging=stage,
            policy=policy,
            cargo=PRIVATE_RUST / "bin/cargo",
            llvm_toolchain=PRIVATE_LLVM,
        )


def test_materializer_hash_verifies_toolchains_before_executing_compilers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guard = materializer_module._load_policy(RUNTIME_POLICY)["native_entry_guard"]
    events: list[str] = []

    class RustVerifier:
        @staticmethod
        def load_manifest(_path: Path) -> dict[str, object]:
            return {}

        @staticmethod
        def verify_materialized_toolchain(
            _path: Path, _policy: dict[str, object]
        ) -> None:
            events.append("rust-tree-verified")

    class LlvmVerifier:
        @staticmethod
        def load_policy(_path: Path) -> dict[str, object]:
            return {}

        @staticmethod
        def verify_materialized(
            _path: Path, _policy: dict[str, object]
        ) -> None:
            events.append("llvm-tree-verified")

    class PrivateToolVerifier:
        @staticmethod
        def validate_private_cargo(_path: Path, _version: str) -> str:
            assert events == ["rust-tree-verified", "llvm-tree-verified"]
            events.append("cargo-executed")
            return "cargo 1.95.0 (f2d3ce0bd 2026-03-21)"

        @staticmethod
        def validate_private_rustc(_path: Path, _version: str) -> str:
            assert events == [
                "rust-tree-verified",
                "llvm-tree-verified",
                "cargo-executed",
            ]
            events.append("rustc-executed")
            return "rustc 1.95.0 (59807616e 2026-04-14)"

    verifiers = {
        "native_guard_private_tool_verifier": PrivateToolVerifier,
        "native_guard_rust_toolchain_verifier": RustVerifier,
        "native_guard_llvm_toolchain_verifier": LlvmVerifier,
    }
    monkeypatch.setattr(
        materializer_module,
        "_load_local_tool",
        lambda _path, name: verifiers[name],
    )

    identities = materializer_module._verify_native_guard_toolchains(
        cargo=Path("/sealed/rust/bin/cargo"),
        llvm_toolchain=Path("/sealed/llvm"),
        guard=guard,
    )

    assert identities == (
        "cargo 1.95.0 (f2d3ce0bd 2026-03-21)",
        "rustc 1.95.0 (59807616e 2026-04-14)",
    )
    assert events == [
        "rust-tree-verified",
        "llvm-tree-verified",
        "cargo-executed",
        "rustc-executed",
    ]
