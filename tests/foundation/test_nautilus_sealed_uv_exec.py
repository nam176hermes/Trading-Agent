from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "engines/nautilus/sealed_uv_exec"
POLICY = ROOT / "engines/nautilus/sealed-uv-exec-policy.json"
MATERIALIZER = ROOT / "scripts/materialize_sealed_uv_exec.py"
ARCHITECTURE_PLAN = (
    ROOT / "docs/superpowers/plans/2026-08-08-phase4-architectural-closure.md"
)
PRIVATE_RUST = Path(
    "/home/thenam176/.cache/trading-agent/nautilus/rust-1.95.0"
)
PRIVATE_LLVM = Path(
    "/home/thenam176/.cache/trading-agent/nautilus/llvm-22.1.3-resource-toolchain"
)
TARGET = "x86_64-unknown-linux-gnu"
FIXED_ENVIRONMENT = (
    "PATH=/usr/bin:/bin",
    "PYTHONDONTWRITEBYTECODE=1",
    "PYTHONHASHSEED=0",
    "PYTHONNOUSERSITE=1",
    "UV_OFFLINE=1",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require_private_toolchains() -> tuple[Path, Path, Path]:
    cargo = PRIVATE_RUST / "bin/cargo"
    rustc = PRIVATE_RUST / "bin/rustc"
    clang = PRIVATE_LLVM / "bin/clang"
    if not all(path.is_file() for path in (cargo, rustc, clang)):
        pytest.skip("sealed private Rust 1.95.0 and LLVM toolchains are unavailable")
    return cargo, rustc, clang


def _build_helper(build_root: Path) -> Path:
    cargo, rustc, clang = _require_private_toolchains()
    cargo_home = build_root / "cargo-home"
    target_directory = build_root / "target"
    compiler_tmp = build_root / "tmp"
    for directory in (cargo_home, target_directory, compiler_tmp):
        directory.mkdir(mode=0o700)
    environment = {
        "CARGO_HOME": str(cargo_home),
        "CARGO_INCREMENTAL": "0",
        "CARGO_NET_OFFLINE": "true",
        "CARGO_TARGET_DIR": str(target_directory),
        "HOME": str(build_root),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": f"{PRIVATE_RUST / 'bin'}:{PRIVATE_LLVM / 'bin'}:/usr/bin:/bin",
        "RUSTC": str(rustc),
        "RUSTFLAGS": (
            f"-C linker={clang} -C link-arg=-fuse-ld=lld "
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
            str(PROJECT / "Cargo.toml"),
            "--locked",
            "--offline",
            "--release",
            "--target",
            TARGET,
        ],
        check=True,
        cwd=PROJECT,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
    )
    return target_directory / TARGET / "release/nautilus-sealed-uv-exec"


@pytest.fixture(scope="session")
def helper(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _build_helper(tmp_path_factory.mktemp("sealed-uv-helper", numbered=True))


@pytest.fixture
def native_tmp_path() -> Path:
    root = Path(tempfile.mkdtemp(prefix="sealed-uv-fixture-", dir="/tmp"))
    root.chmod(0o700)
    try:
        yield root
    finally:
        for current, directories, files in os.walk(root, topdown=False):
            current_path = Path(current)
            for name in files:
                (current_path / name).chmod(0o600)
            for name in directories:
                (current_path / name).chmod(0o700)
            current_path.chmod(0o700)
        shutil.rmtree(root)


def _compile_fixture(tmp_path: Path) -> tuple[Path, Path]:
    _, _, clang = _require_private_toolchains()
    marker = tmp_path / "fixture-executed"
    source = tmp_path / "fixture.c"
    binary = tmp_path / "fixture-uv"
    source.write_text(
        "#include <stdio.h>\n"
        "#include <stdlib.h>\n"
        "#include <unistd.h>\n"
        "extern char **environ;\n"
        "int main(int argc, char **argv) {\n"
        f'  FILE *marker = fopen("{marker}", "w");\n'
        "  if (marker == NULL) return 91;\n"
        "  char executable[4096];\n"
        "  ssize_t executable_length = readlink(\"/proc/self/exe\", executable, sizeof(executable) - 1);\n"
        "  if (executable_length < 0) return 93;\n"
        "  executable[executable_length] = '\\0';\n"
        "  fprintf(marker, \"exe=%s\\nargv=%s\\n\", executable, argv[0]);\n"
        "  fclose(marker);\n"
        "  for (char **entry = environ; *entry != NULL; ++entry)\n"
        "    puts(*entry);\n"
        "  return argc == 2 ? 0 : 92;\n"
        "}\n",
        encoding="ascii",
    )
    subprocess.run(
        [str(clang), "-O2", "-Wl,--build-id=none", "-o", str(binary), str(source)],
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )
    binary.chmod(0o755)
    return binary, marker


def _command(
    helper: Path,
    program: Path | str,
    cwd: Path | str,
    *,
    digest: str | None = None,
    uid: int | None = None,
    gid: int | None = None,
    mode: str = "0755",
    action: str = "version",
) -> list[str]:
    program_path = Path(program)
    info = program_path.stat() if program_path.exists() else None
    return [
        str(helper),
        "--program",
        str(program),
        "--sha256",
        _sha256(program_path) if digest is None and info is not None else digest or "0" * 64,
        "--uid",
        str(info.st_uid if uid is None and info is not None else uid if uid is not None else 0),
        "--gid",
        str(info.st_gid if gid is None and info is not None else gid if gid is not None else 0),
        "--mode",
        mode,
        "--cwd",
        str(cwd),
        "--action",
        action,
    ]


def _run(command: list[str], *, poison: bool = True) -> subprocess.CompletedProcess[bytes]:
    environment = {"POISON": "ambient-value"} if poison else {}
    return subprocess.run(
        command,
        check=False,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
    )


@pytest.mark.parametrize(
    ("mutation"),
    (
        "relative-program",
        "relative-cwd",
        "digest-mismatch",
        "owner-mismatch",
        "mode-mismatch",
        "arbitrary-action",
    ),
)
def test_native_helper_rejects_invalid_authority_without_executing_fixture(
    helper: Path, native_tmp_path: Path, mutation: str
) -> None:
    fixture, marker = _compile_fixture(native_tmp_path)
    program: Path | str = fixture
    cwd: Path | str = native_tmp_path
    options: dict[str, object] = {}
    if mutation == "relative-program":
        program = fixture.name
    elif mutation == "relative-cwd":
        cwd = "."
    elif mutation == "digest-mismatch":
        options["digest"] = "0" * 64
    elif mutation == "owner-mismatch":
        options["uid"] = fixture.stat().st_uid + 1
    elif mutation == "mode-mismatch":
        fixture.chmod(0o700)
    else:
        options["action"] = "arbitrary-command"

    completed = _run(_command(helper, program, cwd, **options), poison=False)

    assert completed.returncode != 0
    assert completed.stdout == b""
    assert len(completed.stderr) <= 256
    assert not marker.exists()


def test_native_helper_executes_a_sealed_image_with_exact_fixed_environment(
    helper: Path, native_tmp_path: Path
) -> None:
    fixture, marker = _compile_fixture(native_tmp_path)

    completed = _run(_command(helper, fixture, native_tmp_path), poison=True)

    assert completed.returncode == 0
    assert completed.stderr == b""
    marker_lines = marker.read_text(encoding="ascii").splitlines()
    assert marker_lines[0].startswith("exe=/memfd:nautilus-sealed-uv")
    assert str(fixture) not in marker_lines[0]
    assert marker_lines[1] == "argv=uv"
    assert completed.stdout.decode("ascii").splitlines() == list(FIXED_ENVIRONMENT)


def test_native_helper_discards_inherited_poison_before_exec(
    helper: Path, native_tmp_path: Path
) -> None:
    fixture, marker = _compile_fixture(native_tmp_path)

    completed = _run(_command(helper, fixture, native_tmp_path), poison=True)

    assert completed.returncode == 0
    assert completed.stderr == b""
    assert marker.exists()
    assert completed.stdout.decode("ascii").splitlines() == list(FIXED_ENVIRONMENT)


def test_native_helper_source_uses_only_execveat_for_the_sealed_image() -> None:
    source = (PROJECT / "src/main.rs").read_text(encoding="utf-8")

    assert "/proc/self/fd" not in source
    assert "fn execve(" not in source
    assert "SYS_EXECVEAT" in source
    assert "SYS_EXECVEAT,\n                fd," in source
    assert "AT_EMPTY_PATH," in source


def test_native_helper_build_is_reproducible_across_private_build_roots(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir(mode=0o700)
    second_root.mkdir(mode=0o700)

    first = _build_helper(first_root)
    second = _build_helper(second_root)

    assert first.stat().st_size == second.stat().st_size
    assert _sha256(first) == _sha256(second)


def _load_materializer():
    spec = importlib.util.spec_from_file_location("materialize_sealed_uv_exec", MATERIALIZER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _canonical_json(document: dict[str, object]) -> bytes:
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "ascii"
    )


def _policy_commit_parent(policy: Path) -> str:
    relative = policy.relative_to(ROOT)
    policy_commit = subprocess.run(
        ["git", "log", "-1", "--format=%H", "--", str(relative)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert policy_commit
    return subprocess.run(
        ["git", "rev-parse", f"{policy_commit}^"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _private_parent(tmp_path: Path) -> Path:
    parent = tmp_path / "private"
    parent.mkdir(mode=0o700)
    parent.chmod(0o700)
    return parent


def _task3_policy(
    module,
    *,
    source_drift: bool = False,
    source_root: Path | None = None,
    source_commit: str | None = None,
) -> dict[str, object]:
    paths = module.POLICY_SOURCE_PATHS
    source_root = source_root or module.ROOT
    document: dict[str, object] = {
        "schema_version": 1,
        "source_commit": source_commit
        or subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=source_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip(),
        "target_triple": TARGET,
        "binary_name": "nautilus-sealed-uv-exec",
        "binary_mode": "0500",
    }
    for name, relative in paths.items():
        document[name] = relative
        document[f"{name}_sha256"] = _sha256(source_root / relative)
    if source_drift:
        document["rust_source_sha256"] = "0" * 64
    return document


def _write_policy(
    tmp_path: Path,
    module,
    *,
    source_drift: bool = False,
    source_root: Path | None = None,
    source_commit: str | None = None,
) -> Path:
    policy = tmp_path / "sealed-uv-exec-policy.json"
    policy.write_bytes(
        _canonical_json(
            _task3_policy(
                module,
                source_drift=source_drift,
                source_root=source_root,
                source_commit=source_commit,
            )
        )
    )
    policy.chmod(0o400)
    return policy


def _fake_verified_toolchains(_policy: dict[str, object], _cargo: Path, _llvm: Path) -> None:
    return None


def _fake_builder(payloads: list[bytes]):
    def build(_policy: dict[str, object], build_root: Path, _cargo: Path, _llvm: Path) -> Path:
        assert stat.S_IMODE(build_root.stat().st_mode) == 0o700
        for name in ("cargo-home", "target", "tmp"):
            directory = build_root / name
            assert directory.is_dir()
            assert stat.S_IMODE(directory.stat().st_mode) == 0o700
        output = build_root / "target" / TARGET / "release" / "nautilus-sealed-uv-exec"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(payloads.pop(0))
        output.chmod(0o500)
        return output

    return build


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()


def _commit(repository: Path, message: str) -> str:
    _git(repository, "add", "--all")
    _git(repository, "commit", "-qm", message)
    return _git(repository, "rev-parse", "HEAD")


def _provenance_repository(native_tmp_path: Path, module) -> tuple[Path, dict[str, bytes]]:
    repository = native_tmp_path / "source-repository"
    repository.mkdir(mode=0o700)
    source_bytes: dict[str, bytes] = {}
    for relative in module.POLICY_SOURCE_PATHS.values():
        source = ROOT / relative
        destination = repository / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        contents = source.read_bytes()
        destination.write_bytes(contents)
        destination.chmod(0o600)
        source_bytes[relative] = contents
    _git(repository, "init", "-q")
    _git(repository, "config", "user.email", "sealed-uv-test@local.invalid")
    _git(repository, "config", "user.name", "Sealed UV Test")
    _commit(repository, "fixture source")
    return repository, source_bytes


def _forbid_materialization_authority(
    monkeypatch: pytest.MonkeyPatch, module
) -> list[str]:
    calls: list[str] = []

    def forbidden(label: str):
        def reject(*_args, **_kwargs):
            calls.append(label)
            raise AssertionError(f"{label} reached before source-commit rejection")

        return reject

    monkeypatch.setattr(module, "_verify_toolchains", forbidden("importer"))
    monkeypatch.setattr(module, "_build_once", forbidden("cargo"))
    monkeypatch.setattr(module, "_create_staging", forbidden("staging"))
    monkeypatch.setattr(module, "_renameat2_noreplace", forbidden("destination"))
    return calls


def _rewrite_policy_source_commit(policy: Path, source_commit: str) -> None:
    document = json.loads(policy.read_text(encoding="ascii"))
    document["source_commit"] = source_commit
    policy.chmod(0o600)
    policy.write_bytes(_canonical_json(document))
    policy.chmod(0o400)


def test_materializer_rejects_nonexistent_source_commit_before_authority_use(
    native_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_materializer()
    repository, _ = _provenance_repository(native_tmp_path, module)
    monkeypatch.setattr(module, "ROOT", repository)
    policy = _write_policy(native_tmp_path, module, source_root=repository)
    _rewrite_policy_source_commit(policy, "0" * 40)
    calls = _forbid_materialization_authority(monkeypatch, module)
    destination = _private_parent(native_tmp_path) / "sealed-uv-exec"

    with pytest.raises(module.MaterializationError, match="source commit"):
        module.materialize(
            policy_path=policy,
            destination=destination,
            cargo=Path("/tmp/cargo"),
            llvm_toolchain=Path("/tmp/llvm"),
        )

    assert calls == []
    assert not destination.exists()


@pytest.mark.parametrize(
    "source_name",
    (
        "materializer_source",
        "rust_source",
        "rust_toolchain_validator",
        "llvm_toolchain_validator",
        "input_cache_validator",
    ),
)
def test_materializer_rejects_source_commit_bytes_mismatch_before_authority_use(
    native_tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source_name: str
) -> None:
    module = _load_materializer()
    repository, source_bytes = _provenance_repository(native_tmp_path, module)
    monkeypatch.setattr(module, "ROOT", repository)
    relative = module.POLICY_SOURCE_PATHS[source_name]
    source = repository / relative
    source.write_bytes(source_bytes[relative] + b"\n# committed mismatch\n")
    source.chmod(0o600)
    source_commit = _commit(repository, "commit source mismatch")
    source.write_bytes(source_bytes[relative])
    source.chmod(0o600)
    policy = _write_policy(
        native_tmp_path,
        module,
        source_root=repository,
        source_commit=source_commit,
    )
    calls = _forbid_materialization_authority(monkeypatch, module)
    destination = _private_parent(native_tmp_path) / "sealed-uv-exec"

    with pytest.raises(module.MaterializationError, match="source commit"):
        module.materialize(
            policy_path=policy,
            destination=destination,
            cargo=Path("/tmp/cargo"),
            llvm_toolchain=Path("/tmp/llvm"),
        )

    assert calls == []
    assert not destination.exists()


def test_materializer_rejects_source_commit_worktree_only_source_before_authority_use(
    native_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_materializer()
    repository, source_bytes = _provenance_repository(native_tmp_path, module)
    monkeypatch.setattr(module, "ROOT", repository)
    relative = module.POLICY_SOURCE_PATHS["rust_source"]
    source = repository / relative
    source.unlink()
    source_commit = _commit(repository, "remove committed rust source")
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(source_bytes[relative])
    source.chmod(0o600)
    policy = _write_policy(
        native_tmp_path,
        module,
        source_root=repository,
        source_commit=source_commit,
    )
    calls = _forbid_materialization_authority(monkeypatch, module)
    destination = _private_parent(native_tmp_path) / "sealed-uv-exec"

    with pytest.raises(module.MaterializationError, match="source commit"):
        module.materialize(
            policy_path=policy,
            destination=destination,
            cargo=Path("/tmp/cargo"),
            llvm_toolchain=Path("/tmp/llvm"),
        )

    assert calls == []
    assert not destination.exists()


@pytest.mark.parametrize(
    "source_name",
    (
        "rust_toolchain_validator",
        "llvm_toolchain_validator",
        "input_cache_validator",
    ),
)
def test_policy_binds_each_dynamically_loaded_private_toolchain_verifier(
    source_name: str,
) -> None:
    module = _load_materializer()

    relative = module.POLICY_SOURCE_PATHS[source_name]

    assert relative.startswith("scripts/")
    assert (ROOT / relative).is_file()


@pytest.mark.parametrize(
    "source_name",
    (
        "rust_toolchain_validator",
        "llvm_toolchain_validator",
        "input_cache_validator",
    ),
)
def test_materializer_rejects_drifted_verifier_source_before_toolchain_import(
    native_tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source_name: str
) -> None:
    module = _load_materializer()
    repository, _ = _provenance_repository(native_tmp_path, module)
    monkeypatch.setattr(module, "ROOT", repository)
    policy = _write_policy(native_tmp_path, module)
    document = json.loads(policy.read_text(encoding="ascii"))
    document[f"{source_name}_sha256"] = "0" * 64
    policy.chmod(0o600)
    policy.write_bytes(_canonical_json(document))
    policy.chmod(0o400)
    calls: list[str] = []
    monkeypatch.setattr(
        module,
        "_verify_toolchains",
        lambda *_args: calls.append("toolchains"),
    )

    with pytest.raises(module.MaterializationError, match="source digest drift"):
        module.materialize(
            policy_path=policy,
            destination=_private_parent(native_tmp_path) / "sealed-uv-exec",
            cargo=Path("/tmp/cargo"),
            llvm_toolchain=Path("/tmp/llvm"),
        )

    assert calls == []


def test_materializer_rejects_nonabsolute_paths_before_authority_use(
    native_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_materializer()
    repository, _ = _provenance_repository(native_tmp_path, module)
    monkeypatch.setattr(module, "ROOT", repository)
    policy = _write_policy(native_tmp_path, module)

    with pytest.raises(module.MaterializationError, match="policy must be absolute"):
        module.load_policy(Path("relative-policy.json"))
    with pytest.raises(module.MaterializationError, match="destination must be absolute"):
        module.materialize(
            policy_path=policy,
            destination=Path("relative-destination"),
            cargo=Path("/tmp/cargo"),
            llvm_toolchain=Path("/tmp/llvm"),
        )


def test_materializer_rejects_existing_destination_without_clobbering(
    native_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_materializer()
    repository, _ = _provenance_repository(native_tmp_path, module)
    monkeypatch.setattr(module, "ROOT", repository)
    parent = _private_parent(native_tmp_path)
    destination = parent / "sealed-uv-exec"
    destination.mkdir(mode=0o700)
    sentinel = destination / "sentinel"
    sentinel.write_text("retain", encoding="ascii")
    policy = _write_policy(native_tmp_path, module)
    monkeypatch.setattr(module, "_verify_toolchains", _fake_verified_toolchains)
    monkeypatch.setattr(module, "_build_once", _fake_builder([b"same", b"same"]))

    with pytest.raises(module.MaterializationError, match="destination already exists"):
        module.materialize(
            policy_path=policy,
            destination=destination,
            cargo=Path("/tmp/cargo"),
            llvm_toolchain=Path("/tmp/llvm"),
        )

    assert sentinel.read_text(encoding="ascii") == "retain"


def test_materializer_rejects_policy_source_drift_before_build(
    native_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_materializer()
    repository, _ = _provenance_repository(native_tmp_path, module)
    monkeypatch.setattr(module, "ROOT", repository)
    policy = _write_policy(native_tmp_path, module, source_drift=True)
    calls: list[str] = []
    monkeypatch.setattr(
        module,
        "_verify_toolchains",
        lambda *_args: calls.append("toolchains"),
    )

    with pytest.raises(module.MaterializationError, match="source digest drift"):
        module.materialize(
            policy_path=policy,
            destination=_private_parent(native_tmp_path) / "sealed-uv-exec",
            cargo=Path("/tmp/cargo"),
            llvm_toolchain=Path("/tmp/llvm"),
        )

    assert calls == []


def test_materializer_rejects_unverified_toolchain_before_build(
    native_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_materializer()
    repository, _ = _provenance_repository(native_tmp_path, module)
    monkeypatch.setattr(module, "ROOT", repository)
    policy = _write_policy(native_tmp_path, module)
    called: list[str] = []
    monkeypatch.setattr(
        module,
        "_build_once",
        lambda *_args: called.append("build") or Path("/tmp/never"),
    )

    with pytest.raises(module.MaterializationError, match="toolchain verification failed"):
        module.materialize(
            policy_path=policy,
            destination=_private_parent(native_tmp_path) / "sealed-uv-exec",
            cargo=native_tmp_path / "unverified-cargo",
            llvm_toolchain=native_tmp_path / "unverified-llvm",
        )

    assert called == []


def test_materializer_requires_two_identical_private_builds(
    native_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_materializer()
    repository, _ = _provenance_repository(native_tmp_path, module)
    monkeypatch.setattr(module, "ROOT", repository)
    policy = _write_policy(native_tmp_path, module)
    parent = _private_parent(native_tmp_path)
    destination = parent / "sealed-uv-exec"
    monkeypatch.setattr(module, "_verify_toolchains", _fake_verified_toolchains)
    monkeypatch.setattr(module, "_build_once", _fake_builder([b"first", b"second"]))

    with pytest.raises(module.MaterializationError, match="not reproducible"):
        module.materialize(
            policy_path=policy,
            destination=destination,
            cargo=Path("/tmp/cargo"),
            llvm_toolchain=Path("/tmp/llvm"),
        )

    assert not destination.exists()


def test_materializer_publishes_only_exact_sealed_inventory_atomically(
    native_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_materializer()
    repository, _ = _provenance_repository(native_tmp_path, module)
    monkeypatch.setattr(module, "ROOT", repository)
    policy = _write_policy(native_tmp_path, module)
    parent = _private_parent(native_tmp_path)
    destination = parent / "sealed-uv-exec"
    payload = b"reproducible-sealed-uv-exec"
    monkeypatch.setattr(module, "_verify_toolchains", _fake_verified_toolchains)
    monkeypatch.setattr(module, "_build_once", _fake_builder([payload, payload]))

    manifest = module.materialize(
        policy_path=policy,
        destination=destination,
        cargo=Path("/tmp/cargo"),
        llvm_toolchain=Path("/tmp/llvm"),
    )

    assert set(path.name for path in destination.iterdir()) == {
        "nautilus-sealed-uv-exec",
        "sealed-uv-exec-manifest.json",
    }
    assert stat.S_IMODE(destination.stat().st_mode) == 0o500
    binary = destination / "nautilus-sealed-uv-exec"
    manifest_path = destination / "sealed-uv-exec-manifest.json"
    assert stat.S_IMODE(binary.stat().st_mode) == 0o500
    assert stat.S_IMODE(manifest_path.stat().st_mode) == 0o400
    assert json.loads(manifest_path.read_text(encoding="ascii")) == manifest
    assert manifest_path.read_bytes() == _canonical_json(manifest)
    binary_sha256 = _sha256(binary)

    with pytest.raises(module.MaterializationError, match="destination already exists"):
        module.materialize(
            policy_path=policy,
            destination=destination,
            cargo=Path("/tmp/cargo"),
            llvm_toolchain=Path("/tmp/llvm"),
        )

    assert _sha256(binary) == binary_sha256


def test_materializer_accepts_only_the_expected_cargo_hardlinked_release_output(
    native_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_materializer()
    policy = _task3_policy(module)
    build_root = module._create_build_root(native_tmp_path, "private-build")

    def cargo_build(*_args, **_kwargs):
        output = build_root / "target" / TARGET / "release" / "nautilus-sealed-uv-exec"
        output.parent.mkdir(parents=True, exist_ok=True)
        backing = output.with_name("backing-artifact")
        backing.write_bytes(b"cargo-hardlinked-output")
        backing.chmod(0o500)
        os.link(backing, output)
        assert output.stat().st_nlink == 2
        return subprocess.CompletedProcess([], 0)

    monkeypatch.setattr(module.subprocess, "run", cargo_build)

    output = module._build_once(
        policy,
        build_root,
        cargo=Path("/tmp/cargo"),
        llvm_toolchain=Path("/tmp/llvm"),
    )

    assert output.stat().st_nlink == 2


def test_materializer_never_passes_a_cloexec_proc_fd_alias_to_the_cargo_child() -> None:
    source = MATERIALIZER.read_text(encoding="utf-8")

    assert 'Path(f"/proc/self/fd/{parent_fd}") / name' not in source


def test_materialized_inventory_rejects_an_extra_file(
    native_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_materializer()
    repository, _ = _provenance_repository(native_tmp_path, module)
    monkeypatch.setattr(module, "ROOT", repository)
    policy = _write_policy(native_tmp_path, module)
    parent = _private_parent(native_tmp_path)
    destination = parent / "sealed-uv-exec"
    monkeypatch.setattr(module, "_verify_toolchains", _fake_verified_toolchains)
    monkeypatch.setattr(module, "_build_once", _fake_builder([b"same", b"same"]))
    module.materialize(
        policy_path=policy,
        destination=destination,
        cargo=Path("/tmp/cargo"),
        llvm_toolchain=Path("/tmp/llvm"),
    )
    destination.chmod(0o700)
    (destination / "extra").write_bytes(b"unexpected")
    destination.chmod(0o500)

    with pytest.raises(module.MaterializationError, match="inventory"):
        module.verify_materialized(destination, module.load_policy(policy))


def test_committed_policy_binds_all_task3_sources_and_private_toolchain_policies() -> None:
    module = _load_materializer()
    document = module.load_policy(POLICY)

    assert document["schema_version"] == 1
    assert document["source_commit"] == _policy_commit_parent(POLICY)
    assert document["target_triple"] == TARGET
    assert document["binary_name"] == "nautilus-sealed-uv-exec"
    assert document["binary_mode"] == "0500"
    for name, relative in module.POLICY_SOURCE_PATHS.items():
        field = f"{name}_sha256"
        assert document[name] == relative
        assert document[field] == _sha256(ROOT / relative)


def test_task8_recipe_uses_only_the_materialized_sealed_uv_executor() -> None:
    text = ARCHITECTURE_PLAN.read_text(encoding="utf-8")
    start = text.index("phase4_sealed_uv=/home/thenam176/.cache/trading-agent/nautilus/sealed-uv-exec-v2/")
    end = text.index('mkdir -m 0700 "${phase4_runtime_root}/legacy-records"', start)
    block = text[start:end]

    assert 'phase4_sealed_uv_manifest=/home/thenam176/.cache/trading-agent/nautilus/sealed-uv-exec-v2/' in block
    assert 'test -x "${phase4_sealed_uv}" && test -r "${phase4_sealed_uv_manifest}"' in block
    assert block.count('"${phase4_sealed_uv}" --program /home/thenam176/.local/bin/uv') == 2
    assert "--action version" in block
    assert "--action sync-frozen-test" in block
    assert "/proc/self/fd" not in block
    assert "stat -L" not in block
    assert '"${phase4_uv}"' not in block
    assert '"${phase4_uv_exec}"' not in block
    assert "/proc/self/fd" not in text
    assert "Bash opens it once" not in text
    assert "only the materialized sealed-uv-exec-v2 helper" in text
