#!/usr/bin/env python3
"""Build and publish one policy-bound sealed UV executor without network access."""
from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "engines/nautilus/sealed-uv-exec-policy.json"
RUST_TOOLCHAIN_POLICY = "engines/nautilus/toolchain-inputs.json"
LLVM_TOOLCHAIN_POLICY = "engines/nautilus/llvm-toolchain-policy.json"
POLICY_SOURCE_PATHS = {
    "rust_source": "engines/nautilus/sealed_uv_exec/src/main.rs",
    "cargo_manifest": "engines/nautilus/sealed_uv_exec/Cargo.toml",
    "cargo_lock": "engines/nautilus/sealed_uv_exec/Cargo.lock",
    "materializer_source": "scripts/materialize_sealed_uv_exec.py",
    "rust_toolchain_policy": RUST_TOOLCHAIN_POLICY,
    "llvm_toolchain_policy": LLVM_TOOLCHAIN_POLICY,
}
POLICY_FIELDS = {
    "schema_version",
    "source_commit",
    "rust_source",
    "rust_source_sha256",
    "cargo_manifest",
    "cargo_manifest_sha256",
    "cargo_lock",
    "cargo_lock_sha256",
    "materializer_source",
    "materializer_source_sha256",
    "rust_toolchain_policy",
    "rust_toolchain_policy_sha256",
    "llvm_toolchain_policy",
    "llvm_toolchain_policy_sha256",
    "target_triple",
    "binary_name",
    "binary_mode",
}
MANIFEST_NAME = "sealed-uv-exec-manifest.json"
TARGET_TRIPLE = "x86_64-unknown-linux-gnu"
BINARY_NAME = "nautilus-sealed-uv-exec"
BINARY_MODE = 0o500
_SHA256_LENGTH = 64
_RENAME_NOREPLACE = 1


class MaterializationError(ValueError):
    """Raised when one materialization authority or output check fails."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_json(document: dict[str, object]) -> bytes:
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "ascii"
    )


def _is_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _SHA256_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


def _is_commit(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 40
        and all(character in "0123456789abcdef" for character in value)
    )


def _require_direct_file(path: Path, label: str, *, mode: int | None = None) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as error:
        raise MaterializationError(f"{label} is missing") from error
    if (
        path.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != os.geteuid()
        or (mode is not None and stat.S_IMODE(info.st_mode) != mode)
    ):
        raise MaterializationError(f"{label} is not an owner-controlled direct file")
    return info


def _lexical_absolute(path: Path, label: str) -> Path:
    if not path.is_absolute():
        raise MaterializationError(f"{label} must be absolute")
    lexical = Path(os.path.abspath(path))
    if any(part in {"", ".", ".."} for part in lexical.parts[1:]):
        raise MaterializationError(f"{label} has unsafe traversal")
    return lexical


def _reject_symlink_ancestors(path: Path, label: str, *, include_leaf: bool = True) -> None:
    lexical = _lexical_absolute(path, label)
    current = Path(lexical.anchor)
    for index, part in enumerate(lexical.parts[1:]):
        current /= part
        if not include_leaf and index == len(lexical.parts) - 2:
            break
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            raise MaterializationError(f"{label} has a symlinked ancestor")


def _repository_file(relative: object, label: str) -> Path:
    if not isinstance(relative, str):
        raise MaterializationError(f"{label} path is invalid")
    pure = PurePosixPath(relative)
    if (
        not relative
        or pure.is_absolute()
        or pure.as_posix() != relative
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise MaterializationError(f"{label} path is invalid")
    path = ROOT.joinpath(*pure.parts)
    _require_direct_file(path, label)
    return path


def _validate_policy(document: object) -> dict[str, object]:
    if not isinstance(document, dict) or set(document) != POLICY_FIELDS:
        raise MaterializationError("sealed UV executor policy fields are invalid")
    if document["schema_version"] != 1:
        raise MaterializationError("unsupported sealed UV executor policy")
    if not _is_commit(document["source_commit"]):
        raise MaterializationError("sealed UV executor source commit is invalid")
    for name, relative in POLICY_SOURCE_PATHS.items():
        digest = document.get(f"{name}_sha256")
        if document.get(name) != relative or not _is_digest(digest):
            raise MaterializationError(f"sealed UV executor {name} binding is invalid")
    if document["target_triple"] != TARGET_TRIPLE:
        raise MaterializationError("sealed UV executor target is invalid")
    if document["binary_name"] != BINARY_NAME or document["binary_mode"] != "0500":
        raise MaterializationError("sealed UV executor output identity is invalid")
    return document


def load_policy(path: Path) -> dict[str, object]:
    path = _lexical_absolute(path, "policy")
    _reject_symlink_ancestors(path, "policy")
    _require_direct_file(path, "policy")
    try:
        document = json.loads(path.read_text(encoding="ascii"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MaterializationError("sealed UV executor policy is invalid JSON") from error
    return _validate_policy(document)


def _verify_policy_sources(policy: dict[str, object]) -> None:
    for name in POLICY_SOURCE_PATHS:
        source = _repository_file(policy[name], name)
        if _sha256(source) != policy[f"{name}_sha256"]:
            raise MaterializationError(f"sealed UV executor {name} source digest drifted")


def _load_local_tool(path: Path, module_name: str) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise MaterializationError(f"cannot load {module_name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _verify_toolchains(policy: dict[str, object], cargo: Path, llvm_toolchain: Path) -> None:
    cargo = _lexical_absolute(cargo, "cargo")
    llvm_toolchain = _lexical_absolute(llvm_toolchain, "LLVM toolchain")
    if cargo.name != "cargo" or cargo.parent.name != "bin":
        raise MaterializationError("toolchain verification failed")
    try:
        rust_tool = _load_local_tool(
            ROOT / "scripts/prepare_nautilus_toolchain.py", "sealed_uv_exec_rust"
        )
        llvm_tool = _load_local_tool(
            ROOT / "scripts/prepare_nautilus_llvm_toolchain.py", "sealed_uv_exec_llvm"
        )
        input_tool = _load_local_tool(
            ROOT / "scripts/prepare_nautilus_input_cache.py", "sealed_uv_exec_input"
        )
        rust_policy = rust_tool.load_manifest(
            _repository_file(policy["rust_toolchain_policy"], "rust toolchain policy")
        )
        rust_tool.verify_materialized_toolchain(cargo.parent.parent, rust_policy)
        llvm_policy = llvm_tool.load_policy(
            _repository_file(policy["llvm_toolchain_policy"], "LLVM toolchain policy")
        )
        llvm_tool.verify_materialized(llvm_toolchain, llvm_policy)
        input_tool.validate_private_cargo(cargo, "1.95.0")
        input_tool.validate_private_rustc(cargo.with_name("rustc"), "1.95.0")
    except (MaterializationError, OSError, ValueError) as error:
        raise MaterializationError("toolchain verification failed") from error


def _prepare_destination(destination: Path) -> tuple[Path, int, tuple[int, int]]:
    destination = _lexical_absolute(destination, "destination")
    if destination == ROOT or ROOT in destination.parents:
        raise MaterializationError("destination must remain external to the checkout")
    _reject_symlink_ancestors(destination, "destination", include_leaf=False)
    try:
        parent_info = destination.parent.lstat()
    except OSError as error:
        raise MaterializationError("destination parent is unavailable") from error
    if (
        not stat.S_ISDIR(parent_info.st_mode)
        or destination.parent.is_symlink()
        or parent_info.st_uid != os.geteuid()
        or stat.S_IMODE(parent_info.st_mode) != 0o700
    ):
        raise MaterializationError("destination parent must be private mode 0700")
    try:
        destination.lstat()
    except FileNotFoundError:
        pass
    else:
        raise MaterializationError("destination already exists")
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        parent_fd = os.open(destination.parent, flags)
        observed = os.fstat(parent_fd)
    except OSError as error:
        raise MaterializationError("destination parent cannot be opened safely") from error
    return destination, parent_fd, (observed.st_dev, observed.st_ino)


def _create_staging(parent_fd: int) -> tuple[str, Path]:
    for _ in range(32):
        name = f".sealed-uv-exec-{os.getpid()}-{os.urandom(16).hex()}"
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
        except FileExistsError:
            continue
        return name, Path(f"/proc/self/fd/{parent_fd}") / name
    raise MaterializationError("unable to create sealed UV executor staging")


def _cleanup_staging(stage: Path) -> None:
    if not stage.exists() or stage.is_symlink():
        return
    for current, directories, files in os.walk(stage, topdown=False, followlinks=False):
        current_path = Path(current)
        for name in files:
            candidate = current_path / name
            if not candidate.is_symlink():
                candidate.chmod(0o600)
        for name in directories:
            candidate = current_path / name
            if not candidate.is_symlink():
                candidate.chmod(0o700)
        current_path.chmod(0o700)
    shutil.rmtree(stage)


def _create_build_root(stage: Path, label: str) -> Path:
    root = stage / label
    root.mkdir(mode=0o700)
    for name in ("cargo-home", "target", "tmp"):
        (root / name).mkdir(mode=0o700)
    return root


def _build_once(
    policy: dict[str, object], build_root: Path, cargo: Path, llvm_toolchain: Path
) -> Path:
    rustc = cargo.with_name("rustc")
    clang = llvm_toolchain / "bin/clang"
    environment = {
        "CARGO_HOME": str(build_root / "cargo-home"),
        "CARGO_INCREMENTAL": "0",
        "CARGO_NET_OFFLINE": "true",
        "CARGO_TARGET_DIR": str(build_root / "target"),
        "HOME": str(build_root),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": f"{cargo.parent}:{llvm_toolchain / 'bin'}:/usr/bin:/bin",
        "RUSTC": str(rustc),
        "RUSTFLAGS": (
            f"-C linker={clang} -C link-arg=-fuse-ld=lld "
            "-C link-arg=-Wl,--build-id=none"
        ),
        "SOURCE_DATE_EPOCH": "0",
        "TEMP": str(build_root / "tmp"),
        "TMP": str(build_root / "tmp"),
        "TMPDIR": str(build_root / "tmp"),
    }
    try:
        subprocess.run(
            [
                str(cargo),
                "build",
                "--manifest-path",
                str(ROOT / str(policy["cargo_manifest"])),
                "--locked",
                "--offline",
                "--release",
                "--target",
                str(policy["target_triple"]),
            ],
            check=True,
            cwd=ROOT / "engines/nautilus/sealed_uv_exec",
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=180,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise MaterializationError("sealed UV executor offline build failed") from error
    binary = build_root / "target" / str(policy["target_triple"]) / "release" / str(policy["binary_name"])
    info = _require_build_output(binary, "built sealed UV executor")
    if info.st_size <= 0:
        raise MaterializationError("built sealed UV executor is empty")
    return binary


def _require_build_output(path: Path, label: str) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as error:
        raise MaterializationError(f"{label} is missing") from error
    if (
        path.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink < 1
        or info.st_uid != os.geteuid()
        or not info.st_mode & stat.S_IXUSR
        or info.st_mode & 0o022
    ):
        raise MaterializationError(f"{label} is not a private release output")
    return info


def _open_build_output(path: Path, build_root: Path, policy: dict[str, object]) -> tuple[int, int, str]:
    expected = (
        build_root
        / "target"
        / str(policy["target_triple"])
        / "release"
        / str(policy["binary_name"])
    )
    if path != expected:
        raise MaterializationError("built sealed UV executor path is not the expected release output")
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
    except OSError as error:
        raise MaterializationError("built sealed UV executor cannot be opened safely") from error
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink < 1
            or info.st_uid != os.geteuid()
            or not info.st_mode & stat.S_IXUSR
            or info.st_mode & 0o022
            or info.st_size <= 0
        ):
            raise MaterializationError("built sealed UV executor descriptor is unsafe")
        digest = hashlib.sha256()
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            digest.update(block)
        os.lseek(descriptor, 0, os.SEEK_SET)
        return descriptor, info.st_size, digest.hexdigest()
    except BaseException:
        os.close(descriptor)
        raise


def _copy_open_build_output(source_fd: int, destination: Path) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    try:
        destination_fd = os.open(destination, flags, BINARY_MODE)
    except OSError as error:
        raise MaterializationError("sealed UV executor destination cannot be created") from error
    try:
        while True:
            block = os.read(source_fd, 1024 * 1024)
            if not block:
                break
            view = memoryview(block)
            while view:
                written = os.write(destination_fd, view)
                if written <= 0:
                    raise MaterializationError("sealed UV executor destination write failed")
                view = view[written:]
        os.fsync(destination_fd)
    except BaseException:
        os.close(destination_fd)
        raise
    os.close(destination_fd)


def _renameat2_noreplace(parent_fd: int, source_name: bytes, destination_name: bytes) -> None:
    if sys.platform != "linux":
        raise OSError(errno.ENOSYS, "renameat2 is unavailable")
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        renameat2 = libc.renameat2
    except AttributeError as error:
        raise OSError(errno.ENOSYS, "renameat2 is unavailable") from error
    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    ctypes.set_errno(0)
    if renameat2(parent_fd, source_name, parent_fd, destination_name, _RENAME_NOREPLACE) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _manifest(policy: dict[str, object], binary: Path) -> dict[str, object]:
    return {
        "schema_version": 1,
        "policy_sha256": hashlib.sha256(_canonical_json(policy)).hexdigest(),
        "source_commit": policy["source_commit"],
        "binary": {
            "name": policy["binary_name"],
            "sha256": _sha256(binary),
            "size": binary.stat().st_size,
            "mode": policy["binary_mode"],
        },
    }


def _verify_destination_parent(parent_fd: int, identity: tuple[int, int]) -> None:
    observed = os.fstat(parent_fd)
    if (
        not stat.S_ISDIR(observed.st_mode)
        or (observed.st_dev, observed.st_ino) != identity
        or observed.st_uid != os.geteuid()
        or stat.S_IMODE(observed.st_mode) != 0o700
    ):
        raise MaterializationError("destination parent identity changed before publish")


def verify_materialized(destination: Path, policy: dict[str, object]) -> dict[str, object]:
    policy = _validate_policy(policy)
    destination = _lexical_absolute(destination, "destination")
    if destination == ROOT or ROOT in destination.parents:
        raise MaterializationError("destination must remain external to the checkout")
    _reject_symlink_ancestors(destination, "destination")
    try:
        root_info = destination.lstat()
    except OSError as error:
        raise MaterializationError("materialized sealed UV executor is missing") from error
    if (
        destination.is_symlink()
        or not stat.S_ISDIR(root_info.st_mode)
        or root_info.st_uid != os.geteuid()
        or stat.S_IMODE(root_info.st_mode) != 0o500
    ):
        raise MaterializationError("materialized sealed UV executor root is unsafe")
    expected_names = {str(policy["binary_name"]), MANIFEST_NAME}
    try:
        entries = {entry.name for entry in destination.iterdir()}
    except OSError as error:
        raise MaterializationError("materialized sealed UV executor inventory is unreadable") from error
    if entries != expected_names:
        raise MaterializationError("materialized sealed UV executor inventory mismatch")
    binary = destination / str(policy["binary_name"])
    manifest_path = destination / MANIFEST_NAME
    binary_info = _require_direct_file(binary, "materialized sealed UV executor", mode=BINARY_MODE)
    _require_direct_file(manifest_path, "sealed UV executor manifest", mode=0o400)
    try:
        manifest_raw = manifest_path.read_bytes()
        manifest = json.loads(manifest_raw.decode("ascii"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MaterializationError("sealed UV executor manifest is invalid") from error
    expected = {
        "schema_version": 1,
        "policy_sha256": hashlib.sha256(_canonical_json(policy)).hexdigest(),
        "source_commit": policy["source_commit"],
        "binary": {
            "name": policy["binary_name"],
            "sha256": _sha256(binary),
            "size": binary_info.st_size,
            "mode": policy["binary_mode"],
        },
    }
    if manifest != expected or manifest_raw != _canonical_json(expected):
        raise MaterializationError("sealed UV executor manifest is not policy-bound")
    return manifest


def materialize(
    *, policy_path: Path, destination: Path, cargo: Path, llvm_toolchain: Path
) -> dict[str, object]:
    policy = load_policy(policy_path)
    destination, parent_fd, parent_identity = _prepare_destination(destination)
    stage_name: str | None = None
    stage: Path | None = None
    try:
        _verify_policy_sources(policy)
        _verify_toolchains(policy, cargo, llvm_toolchain)
        stage_name, stage = _create_staging(parent_fd)
        first_root = _create_build_root(stage, "first-build")
        second_root = _create_build_root(stage, "second-build")
        first = _build_once(policy, first_root, cargo, llvm_toolchain)
        second = _build_once(policy, second_root, cargo, llvm_toolchain)
        first_fd, first_size, first_digest = _open_build_output(first, first_root, policy)
        try:
            second_fd, second_size, second_digest = _open_build_output(second, second_root, policy)
            try:
                if first_size != second_size or first_digest != second_digest:
                    raise MaterializationError("sealed UV executor builds are not reproducible")
            finally:
                os.close(second_fd)
            binary = stage / str(policy["binary_name"])
            _copy_open_build_output(first_fd, binary)
        finally:
            os.close(first_fd)
        binary.chmod(BINARY_MODE)
        _cleanup_staging(first_root)
        _cleanup_staging(second_root)
        manifest = _manifest(policy, binary)
        manifest_path = stage / MANIFEST_NAME
        manifest_path.write_bytes(_canonical_json(manifest))
        manifest_path.chmod(0o400)
        stage.chmod(0o500)
        _verify_destination_parent(parent_fd, parent_identity)
        try:
            _renameat2_noreplace(
                parent_fd, os.fsencode(stage_name), os.fsencode(destination.name)
            )
        except OSError as error:
            if error.errno == errno.EEXIST:
                raise MaterializationError("destination already exists at atomic publish") from error
            raise MaterializationError("atomic no-clobber publish failed") from error
        stage = None
        return verify_materialized(destination, policy)
    finally:
        if stage is not None:
            _cleanup_staging(stage)
        os.close(parent_fd)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--cargo", required=True, type=Path)
    parser.add_argument("--llvm-toolchain", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        materialize(
            policy_path=arguments.policy,
            destination=arguments.destination,
            cargo=arguments.cargo,
            llvm_toolchain=arguments.llvm_toolchain,
        )
    except (MaterializationError, OSError, ValueError) as error:
        print(f"sealed UV executor materialization: FAIL: {error}", file=sys.stderr)
        return 2
    print("sealed UV executor materialization: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
