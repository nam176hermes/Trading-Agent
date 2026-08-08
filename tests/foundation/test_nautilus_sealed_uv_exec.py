from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import errno
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


def _v4_binary(parent: Path) -> Path:
    return parent / "sealed-uv-exec-v4.bin"


def _v4_manifest(binary: Path) -> Path:
    return binary.with_name("sealed-uv-exec-v4.manifest.json")


def _task3_policy(
    module,
    *,
    source_drift: bool = False,
    source_root: Path | None = None,
    source_commit: str | None = None,
    binary_payload: bytes = b"same",
) -> dict[str, object]:
    paths = module.POLICY_SOURCE_PATHS
    source_root = source_root or module.ROOT
    sandbox = Path("/usr/bin/bwrap")
    sandbox_info = sandbox.stat()
    document: dict[str, object] = {
        "schema_version": 2,
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
        "binary_sha256": hashlib.sha256(binary_payload).hexdigest(),
        "binary_size": len(binary_payload),
        "sandbox_path": str(sandbox),
        "sandbox_sha256": _sha256(sandbox),
        "sandbox_uid": sandbox_info.st_uid,
        "sandbox_gid": sandbox_info.st_gid,
        "sandbox_mode": f"{stat.S_IMODE(sandbox_info.st_mode):04o}",
        "sandbox_version": subprocess.run(
            [str(sandbox), "--version"],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ).stdout.strip(),
        "sandbox_capabilities": ["--clearenv", "--perms", "--ro-bind-data", "--tmpfs"],
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
    binary_payload: bytes = b"same",
) -> Path:
    policy = tmp_path / "sealed-uv-exec-policy.json"
    policy.write_bytes(
        _canonical_json(
            _task3_policy(
                module,
                source_drift=source_drift,
                source_root=source_root,
                source_commit=source_commit,
                binary_payload=binary_payload,
            )
        )
    )
    policy.chmod(0o400)
    return policy


def _fake_verified_toolchains(
    _policy: dict[str, object], _cargo: Path, _llvm: Path, _bundle
) -> None:
    return None


def _fake_builder(payloads: list[bytes]):
    def build(
        _policy: dict[str, object],
        _cargo: Path,
        _llvm: Path,
        bundle,
        sandbox_fd: int,
    ) -> bytes:
        assert sandbox_fd >= 0
        assert set(bundle.source_names()) == {
            "rust_source",
            "cargo_manifest",
            "cargo_lock",
            "materializer_source",
            "rust_toolchain_validator",
            "llvm_toolchain_validator",
            "input_cache_validator",
            "rust_toolchain_policy",
            "llvm_toolchain_policy",
        }
        return payloads.pop(0)

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
    destination = _v4_binary(_private_parent(native_tmp_path))

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
    destination = _v4_binary(_private_parent(native_tmp_path))

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
    destination = _v4_binary(_private_parent(native_tmp_path))

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
            destination=_v4_binary(_private_parent(native_tmp_path)),
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


def test_prepare_destination_closes_parent_descriptor_when_inventory_precondition_fails(
    native_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Removing this close leaks the opened private-parent descriptor."""
    module = _load_materializer()
    parent = _private_parent(native_tmp_path)
    destination = _v4_binary(parent)
    original_open = os.open
    opened: list[int] = []

    def record_open(*args, **kwargs) -> int:
        descriptor = original_open(*args, **kwargs)
        opened.append(descriptor)
        return descriptor

    monkeypatch.setattr(module.os, "open", record_open)
    monkeypatch.setattr(
        module,
        "_verify_destination_parent",
        lambda *_args: (_ for _ in ()).throw(module.MaterializationError("induced inventory failure")),
    )

    with pytest.raises(module.MaterializationError, match="induced inventory failure"):
        module._prepare_destination(destination)

    assert len(opened) == 1
    with pytest.raises(OSError):
        os.fstat(opened[0])


def test_open_unlinked_publish_file_closes_descriptor_when_mode_seal_fails(
    native_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Removing this close leaks the anonymous O_TMPFILE descriptor."""
    module = _load_materializer()
    parent = _private_parent(native_tmp_path)
    parent_fd = os.open(
        parent,
        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    original_open = os.open
    opened: list[int] = []

    def record_open(*args, **kwargs) -> int:
        descriptor = original_open(*args, **kwargs)
        opened.append(descriptor)
        return descriptor

    try:
        monkeypatch.setattr(module.os, "open", record_open)
        monkeypatch.setattr(
            module.os,
            "fchmod",
            lambda *_args: (_ for _ in ()).throw(OSError(errno.EIO, "induced fchmod failure")),
        )

        with pytest.raises(module.MaterializationError, match="cannot be created by descriptor") as error:
            module._open_unlinked_publish_file(
                parent_fd, label="injected publish file", mode=0o500
            )

        assert isinstance(error.value.__cause__, OSError)
        assert error.value.__cause__.errno == errno.EIO
        assert len(opened) == 1
        with pytest.raises(OSError):
            os.fstat(opened[0])
    finally:
        os.close(parent_fd)


def test_materializer_rejects_existing_destination_without_clobbering(
    native_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_materializer()
    repository, _ = _provenance_repository(native_tmp_path, module)
    monkeypatch.setattr(module, "ROOT", repository)
    parent = _private_parent(native_tmp_path)
    destination = _v4_binary(parent)
    destination.write_bytes(b"retain")
    sentinel = destination
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
            destination=_v4_binary(_private_parent(native_tmp_path)),
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
            destination=_v4_binary(_private_parent(native_tmp_path)),
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
    destination = _v4_binary(parent)
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
    parent = _private_parent(native_tmp_path)
    payload = b"reproducible-sealed-uv-exec"
    policy = _write_policy(native_tmp_path, module, binary_payload=payload)
    destination = _v4_binary(parent)
    monkeypatch.setattr(module, "_verify_toolchains", _fake_verified_toolchains)
    monkeypatch.setattr(module, "_build_once", _fake_builder([payload, payload]))

    manifest = module.materialize(
        policy_path=policy,
        destination=destination,
        cargo=Path("/tmp/cargo"),
        llvm_toolchain=Path("/tmp/llvm"),
    )

    manifest_path = _v4_manifest(destination)
    assert destination.is_file()
    assert manifest_path.is_file()
    assert set(path.name for path in parent.iterdir()) == {
        "sealed-uv-exec-v4.bin",
        "sealed-uv-exec-v4.manifest.json",
    }
    assert stat.S_IMODE(destination.stat().st_mode) == 0o500
    assert stat.S_IMODE(manifest_path.stat().st_mode) == 0o400
    assert json.loads(manifest_path.read_text(encoding="ascii")) == manifest
    assert manifest_path.read_bytes() == _canonical_json(manifest)
    assert _sha256(destination) == hashlib.sha256(payload).hexdigest()

    with pytest.raises(module.MaterializationError, match="destination already exists"):
        module.materialize(
            policy_path=policy,
            destination=destination,
            cargo=Path("/tmp/cargo"),
            llvm_toolchain=Path("/tmp/llvm"),
        )

    assert _sha256(destination) == hashlib.sha256(payload).hexdigest()
    assert not hasattr(module, "_create_staging")
    assert not hasattr(module, "_cleanup_staging")
    assert not hasattr(module, "_renameat2_noreplace")


def test_materializer_rejects_a_reproducible_output_not_bound_by_policy(
    native_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_materializer()
    repository, _ = _provenance_repository(native_tmp_path, module)
    monkeypatch.setattr(module, "ROOT", repository)
    approved = b"reviewed-two-build-output"
    policy = _write_policy(native_tmp_path, module, binary_payload=approved)
    destination = _v4_binary(_private_parent(native_tmp_path))
    monkeypatch.setattr(module, "_verify_toolchains", _fake_verified_toolchains)
    monkeypatch.setattr(module, "_build_once", _fake_builder([b"unbound", b"unbound"]))

    with pytest.raises(module.MaterializationError, match="output authority"):
        module.materialize(
            policy_path=policy,
            destination=destination,
            cargo=Path("/tmp/cargo"),
            llvm_toolchain=Path("/tmp/llvm"),
        )

    assert not destination.exists()
    assert not _v4_manifest(destination).exists()


def test_descriptor_publication_ignores_a_replacement_left_at_an_old_staging_name(
    native_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_materializer()
    repository, _ = _provenance_repository(native_tmp_path, module)
    monkeypatch.setattr(module, "ROOT", repository)
    payload = b"reviewed-two-build-output"
    policy = _write_policy(native_tmp_path, module, binary_payload=payload)
    parent = _private_parent(native_tmp_path)
    destination = _v4_binary(parent)
    replacement = parent / ".sealed-uv-exec-attacker-replacement"
    monkeypatch.setattr(module, "_verify_toolchains", _fake_verified_toolchains)
    monkeypatch.setattr(module, "_build_once", _fake_builder([payload, payload]))
    original_link = module._linkat_empty_path
    calls: list[str] = []

    def interposed_link(source_fd: int, parent_fd: int, name: str) -> None:
        if not calls:
            replacement.mkdir(mode=0o700)
            (replacement / "sentinel").write_text("retain", encoding="ascii")
        calls.append(name)
        original_link(source_fd, parent_fd, name)

    monkeypatch.setattr(module, "_linkat_empty_path", interposed_link)

    module.materialize(
        policy_path=policy,
        destination=destination,
        cargo=Path("/tmp/cargo"),
        llvm_toolchain=Path("/tmp/llvm"),
    )

    assert calls == [module.PAIR_BINARY_NAME, module.PAIR_MANIFEST_NAME]
    assert replacement.is_dir()
    assert (replacement / "sentinel").read_text(encoding="ascii") == "retain"
    assert _sha256(destination) == hashlib.sha256(payload).hexdigest()


def test_manifest_link_failure_retains_binary_orphan_and_never_removes_replacement(
    native_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_materializer()
    repository, _ = _provenance_repository(native_tmp_path, module)
    monkeypatch.setattr(module, "ROOT", repository)
    payload = b"reviewed-two-build-output"
    policy = _write_policy(native_tmp_path, module, binary_payload=payload)
    parent = _private_parent(native_tmp_path)
    destination = _v4_binary(parent)
    replacement = parent / ".sealed-uv-exec-attacker-replacement"
    monkeypatch.setattr(module, "_verify_toolchains", _fake_verified_toolchains)
    monkeypatch.setattr(module, "_build_once", _fake_builder([payload, payload]))
    original_link = module._linkat_empty_path

    def interposed_link(source_fd: int, parent_fd: int, name: str) -> None:
        if name == module.PAIR_MANIFEST_NAME:
            replacement.mkdir(mode=0o700)
            (replacement / "sentinel").write_text("retain", encoding="ascii")
            raise OSError(errno.EEXIST, "manifest destination replaced")
        original_link(source_fd, parent_fd, name)

    monkeypatch.setattr(module, "_linkat_empty_path", interposed_link)

    with pytest.raises(module.MaterializationError, match="manifest publication"):
        module.materialize(
            policy_path=policy,
            destination=destination,
            cargo=Path("/tmp/cargo"),
            llvm_toolchain=Path("/tmp/llvm"),
        )

    assert destination.read_bytes() == payload
    assert stat.S_IMODE(destination.stat().st_mode) == 0o500
    assert not _v4_manifest(destination).exists()
    assert replacement.is_dir()
    assert (replacement / "sentinel").read_text(encoding="ascii") == "retain"
    with pytest.raises(module.MaterializationError, match="pair"):
        module.verify_materialized(destination, module.load_policy(policy))


def test_materializer_source_has_no_staging_rename_or_recursive_cleanup_paths() -> None:
    source = MATERIALIZER.read_text(encoding="utf-8")

    assert "_create_staging" not in source
    assert "_cleanup_staging" not in source
    assert "_renameat2_noreplace" not in source
    assert "shutil.rmtree" not in source
    assert ".unlink(" not in source


def _substitute_checkout_sources_after_verification(
    repository: Path, module
) -> None:
    replacements = {
        "cargo_manifest": b"[package]\nname = \"checkout-substitution\"\nversion = \"9.9.9\"\n",
        "cargo_lock": b"version = 4\n# checkout lockfile was reopened\n",
        "rust_source": b'compile_error!("checkout Rust source was reopened");\n',
        "rust_toolchain_validator": b'raise RuntimeError("checkout rust validator was imported")\n',
        "llvm_toolchain_validator": b'raise RuntimeError("checkout LLVM validator was imported")\n',
        "input_cache_validator": b'raise RuntimeError("checkout input validator was imported")\n',
    }
    for source_name, replacement in replacements.items():
        source = repository / module.POLICY_SOURCE_PATHS[source_name]
        source.chmod(0o600)
        source.write_bytes(replacement)
        source.chmod(0o600)


def _assert_sealed_bundle(bundle, module, source_bytes: dict[str, bytes]) -> None:
    assert set(bundle.source_names()) == set(module.POLICY_SOURCE_PATHS)
    assert not hasattr(bundle, "root")
    for source_name, relative in module.POLICY_SOURCE_PATHS.items():
        descriptor = bundle.descriptor(source_name)
        assert bundle.read(source_name) == source_bytes[relative]
        assert module._sealed_descriptor_state(descriptor, 0o400) == module._ALL_SEALS


def test_verified_git_blob_bundle_retains_only_sealed_descriptors_after_checkout_substitution(
    native_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_materializer()
    repository, source_bytes = _provenance_repository(native_tmp_path, module)
    monkeypatch.setattr(module, "ROOT", repository)
    policy = module.load_policy(_write_policy(native_tmp_path, module))

    bundle = module._create_verified_source_bundle(
        policy, module._verify_policy_source_commit(policy)
    )
    try:
        _substitute_checkout_sources_after_verification(repository, module)
        _assert_sealed_bundle(bundle, module, source_bytes)
    finally:
        bundle.close()


def test_dynamic_validators_execute_compiled_sealed_bytes_after_checkout_substitution(
    native_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_materializer()
    repository, _ = _provenance_repository(native_tmp_path, module)
    monkeypatch.setattr(module, "ROOT", repository)
    policy = module.load_policy(_write_policy(native_tmp_path, module))
    bundle = module._create_verified_source_bundle(
        policy, module._verify_policy_source_commit(policy)
    )
    try:
        _substitute_checkout_sources_after_verification(repository, module)
        for source_name, module_name in (
            ("rust_toolchain_validator", "sealed_uv_exec_rust"),
            ("llvm_toolchain_validator", "sealed_uv_exec_llvm"),
            ("input_cache_validator", "sealed_uv_exec_input"),
        ):
            loaded = module._load_verified_tool(bundle, source_name, module_name)
            assert loaded.__file__.startswith("<sealed:")
    finally:
        bundle.close()


def test_cargo_topology_consumes_only_sealed_git_blob_descriptors_after_substitution(
    native_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_materializer()
    repository, source_bytes = _provenance_repository(native_tmp_path, module)
    monkeypatch.setattr(module, "ROOT", repository)
    policy = module.load_policy(_write_policy(native_tmp_path, module))
    bundle = module._create_verified_source_bundle(
        policy, module._verify_policy_source_commit(policy)
    )
    sandbox_fd = module._sealed_memfd("test-bwrap", b"bwrap", mode=0o500)
    try:
        _substitute_checkout_sources_after_verification(repository, module)
        argv, environment, passed = module._sandbox_argv(
            policy,
            bundle,
            cargo=Path("/private/rust/bin/cargo"),
            llvm_toolchain=Path("/private/llvm"),
            sandbox_fd=sandbox_fd,
        )
        assert environment == {}
        assert str(repository) not in "\n".join(argv)
        assert "--ro-bind-data" in argv
        assert "--ro-bind" in argv
        assert "--ro-bind / /" not in " ".join(argv)
        assert all(bundle.descriptor(name) in passed for name in (
            "cargo_manifest", "cargo_lock", "rust_source"
        ))
        for source_name in ("cargo_manifest", "cargo_lock", "rust_source"):
            assert bundle.read(source_name) == source_bytes[module.POLICY_SOURCE_PATHS[source_name]]
            assert module._sealed_descriptor_state(bundle.descriptor(source_name), 0o400) == module._ALL_SEALS
    finally:
        os.close(sandbox_fd)
        bundle.close()


def test_sandbox_topology_hides_hostile_cargo_configuration_ancestry(
    native_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_materializer()
    repository, _ = _provenance_repository(native_tmp_path, module)
    (repository / ".cargo").mkdir(mode=0o700)
    (repository / ".cargo/config.toml").write_text(
        '[build]\nrustc-wrapper = "/hostile-wrapper"\n', encoding="ascii"
    )
    monkeypatch.setattr(module, "ROOT", repository)
    policy = module.load_policy(_write_policy(native_tmp_path, module))
    bundle = module._create_verified_source_bundle(
        policy, module._verify_policy_source_commit(policy)
    )
    sandbox_fd = module._sealed_memfd("test-bwrap", b"bwrap", mode=0o500)
    try:
        argv, environment, _ = module._sandbox_argv(
            policy,
            bundle,
            cargo=Path("/private/rust/bin/cargo"),
            llvm_toolchain=Path("/private/llvm"),
            sandbox_fd=sandbox_fd,
        )
        joined = "\n".join(argv)
        assert "--tmpfs\n/" in joined
        assert "--clearenv" in argv
        assert str(repository / ".cargo") not in joined
        assert "/work/cargo-home" in argv
        assert environment == {}
        assert "--ro-bind\n/\n/" not in joined
    finally:
        os.close(sandbox_fd)
        bundle.close()


def test_isolated_cargo_build_ignores_hostile_checkout_and_home_configuration(
    native_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cargo, _, _ = _require_private_toolchains()
    module = _load_materializer()
    repository, _ = _provenance_repository(native_tmp_path, module)
    marker = native_tmp_path / "hostile-cargo-config-was-read"
    hostile_wrapper = native_tmp_path / "hostile-wrapper"
    hostile_wrapper.write_text(
        f"#!/bin/sh\nprintf hostile > {marker}\nexit 97\n", encoding="ascii"
    )
    hostile_wrapper.chmod(0o700)
    for root in (repository, native_tmp_path / "host-home"):
        config = root / ".cargo/config.toml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(
            f'[build]\nrustc-wrapper = "{hostile_wrapper}"\n', encoding="ascii"
        )
    monkeypatch.setenv("HOME", str(native_tmp_path / "host-home"))
    monkeypatch.setenv("CARGO_HOME", str(native_tmp_path / "host-home/.cargo"))
    monkeypatch.setattr(module, "ROOT", repository)
    policy = module.load_policy(_write_policy(native_tmp_path, module))
    bundle = module._create_verified_source_bundle(
        policy, module._verify_policy_source_commit(policy)
    )
    _substitute_checkout_sources_after_verification(repository, module)
    sandbox_fd = module._verify_sandbox(policy)
    try:
        first = module._build_once(policy, cargo, PRIVATE_LLVM, bundle, sandbox_fd)
        second = module._build_once(policy, cargo, PRIVATE_LLVM, bundle, sandbox_fd)
    finally:
        os.close(sandbox_fd)
        bundle.close()

    assert first.startswith(b"\x7fELF")
    assert len(first) == len(second)
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()
    assert not marker.exists()


@pytest.mark.parametrize("field, value", (("sandbox_sha256", "0" * 64), ("sandbox_uid", 1)))
def test_sandbox_binding_rejects_changed_identity(
    native_tmp_path: Path, field: str, value: object
) -> None:
    module = _load_materializer()
    policy = _task3_policy(module)
    policy[field] = value

    with pytest.raises(module.MaterializationError, match="Bubblewrap"):
        module._verify_sandbox(policy)


def test_policy_rejects_missing_required_sandbox_capability() -> None:
    module = _load_materializer()
    policy = _task3_policy(module)
    policy["sandbox_capabilities"] = ["--clearenv"]

    with pytest.raises(module.MaterializationError, match="sandbox binding"):
        module._validate_policy(policy)


def test_sandbox_output_capture_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_materializer()
    monkeypatch.setattr(module, "_MAX_BINARY_BYTES", 1024)

    with pytest.raises(module.MaterializationError, match="output exceeded"):
        module._run_sandbox(
            ("/usr/bin/sh", "-c", "/usr/bin/head -c 1025 /dev/zero"), {}, ()
        )


def test_sandbox_failure_never_attempts_direct_pair_publication(
    native_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_materializer()
    repository, _ = _provenance_repository(native_tmp_path, module)
    monkeypatch.setattr(module, "ROOT", repository)
    policy = _write_policy(native_tmp_path, module)
    destination = _v4_binary(_private_parent(native_tmp_path))
    monkeypatch.setattr(module, "_verify_toolchains", _fake_verified_toolchains)
    monkeypatch.setattr(
        module, "_verify_sandbox", lambda _policy: module._sealed_memfd("test", b"bwrap", mode=0o500)
    )
    monkeypatch.setattr(
        module,
        "_build_once",
        lambda *_args: (_ for _ in ()).throw(module.MaterializationError("sandbox failed")),
    )
    with pytest.raises(module.MaterializationError, match="sandbox failed"):
        module.materialize(
            policy_path=policy,
            destination=destination,
            cargo=Path("/tmp/cargo"),
            llvm_toolchain=Path("/tmp/llvm"),
        )

    assert not destination.exists()
    assert not _v4_manifest(destination).exists()


def test_materialized_pair_rejects_an_orphan_binary(
    native_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_materializer()
    repository, _ = _provenance_repository(native_tmp_path, module)
    monkeypatch.setattr(module, "ROOT", repository)
    policy = _write_policy(native_tmp_path, module)
    parent = _private_parent(native_tmp_path)
    destination = _v4_binary(parent)
    monkeypatch.setattr(module, "_verify_toolchains", _fake_verified_toolchains)
    monkeypatch.setattr(module, "_build_once", _fake_builder([b"same", b"same"]))
    module.materialize(
        policy_path=policy,
        destination=destination,
        cargo=Path("/tmp/cargo"),
        llvm_toolchain=Path("/tmp/llvm"),
    )
    _v4_manifest(destination).unlink()

    with pytest.raises(module.MaterializationError, match="pair"):
        module.verify_materialized(destination, module.load_policy(policy))


def test_committed_policy_binds_all_task3_sources_and_private_toolchain_policies() -> None:
    module = _load_materializer()
    document = module.load_policy(POLICY)

    assert document["schema_version"] == 2
    assert document["source_commit"] == _policy_commit_parent(POLICY)
    assert document["target_triple"] == TARGET
    assert document["binary_name"] == "nautilus-sealed-uv-exec"
    assert document["binary_mode"] == "0500"
    assert len(document["binary_sha256"]) == 64
    assert isinstance(document["binary_size"], int)
    assert document["binary_size"] > 0
    sandbox = Path("/usr/bin/bwrap")
    sandbox_info = sandbox.stat()
    assert document["sandbox_path"] == str(sandbox)
    assert document["sandbox_sha256"] == _sha256(sandbox)
    assert document["sandbox_uid"] == sandbox_info.st_uid
    assert document["sandbox_gid"] == sandbox_info.st_gid
    assert document["sandbox_mode"] == f"{stat.S_IMODE(sandbox_info.st_mode):04o}"
    assert document["sandbox_version"] == subprocess.run(
        [str(sandbox), "--version"], check=True, capture_output=True, text=True
    ).stdout.strip()
    assert document["sandbox_capabilities"] == [
        "--clearenv",
        "--perms",
        "--ro-bind-data",
        "--tmpfs",
    ]
    for name, relative in module.POLICY_SOURCE_PATHS.items():
        field = f"{name}_sha256"
        assert document[name] == relative
        assert document[field] == _sha256(ROOT / relative)


def test_task8_recipe_uses_only_the_materialized_sealed_uv_executor() -> None:
    text = ARCHITECTURE_PLAN.read_text(encoding="utf-8")
    start = text.index(
        "phase4_sealed_uv=/home/thenam176/.cache/trading-agent/nautilus/sealed-uv-exec-v4.bin"
    )
    end = text.index('mkdir -m 0700 "${phase4_runtime_root}/legacy-records"', start)
    block = text[start:end]

    assert (
        'phase4_sealed_uv_manifest=/home/thenam176/.cache/trading-agent/nautilus/'
        'sealed-uv-exec-v4.manifest.json'
    ) in block
    assert 'materialize_sealed_uv_exec.py --verify-pair' in block
    assert (
        '--policy "${phase4_source_root}/engines/nautilus/sealed-uv-exec-policy.json"'
    ) in block
    assert block.count('"${phase4_sealed_uv}" --program /home/thenam176/.local/bin/uv') == 2
    assert "--action version" in block
    assert "--action sync-frozen-test" in block
    assert "/proc/self/fd" not in block
    assert "stat -L" not in block
    assert '"${phase4_uv}"' not in block
    assert '"${phase4_uv_exec}"' not in block
    assert "/proc/self/fd" not in text
    assert "Bash opens it once" not in text
    assert "only the materialized sealed-uv-exec-v4 helper pair" in text


def test_task8_pair_verification_invocation_accepts_an_absolute_policy_path(
    native_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relative Task 8 policy path stops the campaign before verification."""
    module = _load_materializer()
    destination = _v4_binary(_private_parent(native_tmp_path))
    observed: dict[str, object] = {}

    def record_pair_verification(path: Path, policy: dict[str, object]) -> dict[str, object]:
        observed["path"] = path
        observed["policy"] = policy
        return policy

    monkeypatch.setattr(module, "verify_materialized", record_pair_verification)
    status = module.main(
        [
            "--verify-pair",
            "--policy",
            str(POLICY),
            "--destination",
            str(destination),
        ]
    )

    assert status == 0
    assert observed["path"] == destination
    assert observed["policy"]["source_commit"] == _policy_commit_parent(POLICY)
