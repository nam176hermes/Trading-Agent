#!/usr/bin/env python3
"""Build and publish one policy-bound sealed UV executor without network access."""
from __future__ import annotations

import argparse
import ctypes
import errno
import fcntl
import hashlib
import json
import os
import platform
import selectors
import stat
import subprocess
import sys
import time
import types
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
    "rust_toolchain_validator": "scripts/prepare_nautilus_toolchain.py",
    "llvm_toolchain_validator": "scripts/prepare_nautilus_llvm_toolchain.py",
    "input_cache_validator": "scripts/prepare_nautilus_input_cache.py",
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
    "rust_toolchain_validator",
    "rust_toolchain_validator_sha256",
    "llvm_toolchain_validator",
    "llvm_toolchain_validator_sha256",
    "input_cache_validator",
    "input_cache_validator_sha256",
    "rust_toolchain_policy",
    "rust_toolchain_policy_sha256",
    "llvm_toolchain_policy",
    "llvm_toolchain_policy_sha256",
    "target_triple",
    "binary_name",
    "binary_mode",
    "binary_sha256",
    "binary_size",
    "sandbox_path",
    "sandbox_sha256",
    "sandbox_uid",
    "sandbox_gid",
    "sandbox_mode",
    "sandbox_version",
    "sandbox_capabilities",
}
TARGET_TRIPLE = "x86_64-unknown-linux-gnu"
BINARY_NAME = "nautilus-sealed-uv-exec"
BINARY_MODE = 0o500
PAIR_BINARY_NAME = "sealed-uv-exec-v4.bin"
PAIR_MANIFEST_NAME = "sealed-uv-exec-v4.manifest.json"
SANDBOX_PATH = "/usr/bin/bwrap"
SANDBOX_CAPABILITIES = ("--clearenv", "--perms", "--ro-bind-data", "--tmpfs")
_SHA256_LENGTH = 64
_MFD_ALLOW_SEALING = 0x0002
_MFD_CLOEXEC = 0x0001
_AT_EMPTY_PATH = 0x1000
_MEMFD_CREATE_SYSCALLS = {
    "x86_64": 319,
    "amd64": 319,
    "aarch64": 279,
    "arm64": 279,
}
_F_ADD_SEALS = 1033
_F_GET_SEALS = 1034
_F_SEAL_SEAL = 0x0001
_F_SEAL_SHRINK = 0x0002
_F_SEAL_GROW = 0x0004
_F_SEAL_WRITE = 0x0008
_ALL_SEALS = _F_SEAL_SEAL | _F_SEAL_SHRINK | _F_SEAL_GROW | _F_SEAL_WRITE
_MAX_BINARY_BYTES = 64 * 1024 * 1024
_MAX_SANDBOX_STDERR_BYTES = 16 * 1024
_SANDBOX_TIMEOUT_SECONDS = 180


class MaterializationError(ValueError):
    """Raised when one materialization authority or output check fails."""


class VerifiedSourceBundle:
    """Git blobs retained as kernel-sealed descriptors until all consumers exit."""

    def __init__(
        self, descriptors: dict[str, int], digests: dict[str, str], sizes: dict[str, int]
    ) -> None:
        self._descriptors = descriptors
        self._digests = digests
        self._sizes = sizes

    def source_names(self) -> tuple[str, ...]:
        return tuple(self._descriptors)

    def descriptor(self, source_name: str) -> int:
        try:
            return self._descriptors[source_name]
        except KeyError as error:
            raise MaterializationError("verified source bundle input is unavailable") from error

    def read(self, source_name: str) -> bytes:
        descriptor = self.descriptor(source_name)
        _sealed_descriptor_state(descriptor, 0o400)
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            chunks: list[bytes] = []
            while block := os.read(descriptor, 1024 * 1024):
                chunks.append(block)
            os.lseek(descriptor, 0, os.SEEK_SET)
        except OSError as error:
            raise MaterializationError("verified source bundle cannot be read") from error
        value = b"".join(chunks)
        if (
            len(value) != self._sizes[source_name]
            or hashlib.sha256(value).hexdigest() != self._digests[source_name]
        ):
            raise MaterializationError("verified source bundle digest drifted")
        return value

    def close(self) -> None:
        for descriptor in self._descriptors.values():
            try:
                os.close(descriptor)
            except OSError:
                pass
        self._descriptors.clear()


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


def _repository_file(
    relative: object, label: str, *, repository_root: Path | None = None
) -> Path:
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
    root = ROOT if repository_root is None else repository_root
    path = root.joinpath(*pure.parts)
    _require_direct_file(path, label)
    return path


def _policy_relative_parts(relative: object, label: str) -> tuple[str, ...]:
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
    return pure.parts


def _validate_policy(document: object) -> dict[str, object]:
    if not isinstance(document, dict) or set(document) != POLICY_FIELDS:
        raise MaterializationError("sealed UV executor policy fields are invalid")
    if document["schema_version"] != 2:
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
    if (
        not _is_digest(document["binary_sha256"])
        or not isinstance(document["binary_size"], int)
        or isinstance(document["binary_size"], bool)
        or document["binary_size"] <= 0
        or document["binary_size"] > _MAX_BINARY_BYTES
    ):
        raise MaterializationError("sealed UV executor output authority is invalid")
    if (
        document["sandbox_path"] != SANDBOX_PATH
        or not _is_digest(document["sandbox_sha256"])
        or not isinstance(document["sandbox_uid"], int)
        or isinstance(document["sandbox_uid"], bool)
        or document["sandbox_uid"] < 0
        or not isinstance(document["sandbox_gid"], int)
        or isinstance(document["sandbox_gid"], bool)
        or document["sandbox_gid"] < 0
        or document["sandbox_mode"] != "0755"
        or not isinstance(document["sandbox_version"], str)
        or not document["sandbox_version"].startswith("bubblewrap ")
        or not isinstance(document["sandbox_capabilities"], list)
        or tuple(document["sandbox_capabilities"]) != SANDBOX_CAPABILITIES
    ):
        raise MaterializationError("sealed UV executor sandbox binding is invalid")
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


def _git_source_commit_output(repository_root: Path, arguments: list[str]) -> bytes:
    try:
        completed = subprocess.run(
            arguments,
            check=False,
            cwd=repository_root,
            env={"LC_ALL": "C", "LANG": "C", "PATH": "/usr/bin:/bin"},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise MaterializationError("sealed UV executor source commit is unavailable") from error
    if completed.returncode != 0:
        raise MaterializationError("sealed UV executor source commit is unavailable")
    return completed.stdout


def _verify_policy_source_commit(
    policy: dict[str, object], *, repository_root: Path | None = None
) -> dict[str, bytes]:
    root = ROOT if repository_root is None else repository_root
    source_commit = str(policy["source_commit"])
    _git_source_commit_output(
        root,
        ["git", "cat-file", "-e", f"{source_commit}^{{commit}}"],
    )
    verified_bytes: dict[str, bytes] = {}
    for name in POLICY_SOURCE_PATHS:
        source = _repository_file(policy[name], name, repository_root=root)
        expected_digest = str(policy[f"{name}_sha256"])
        if _sha256(source) != expected_digest:
            raise MaterializationError(f"sealed UV executor {name} source digest drifted")
        source_at_commit = _git_source_commit_output(
            root,
            ["git", "show", f"{source_commit}:{policy[name]}"],
        )
        if hashlib.sha256(source_at_commit).hexdigest() != expected_digest:
            raise MaterializationError(
                f"sealed UV executor {name} source commit digest drifted"
            )
        verified_bytes[name] = source_at_commit
    return verified_bytes


def _sealed_memfd(name: str, value: bytes, *, mode: int) -> int:
    descriptor = -1
    try:
        creator = getattr(os, "memfd_create", None)
        if callable(creator):
            descriptor = creator(name, _MFD_CLOEXEC | _MFD_ALLOW_SEALING)
        else:
            syscall_number = _MEMFD_CREATE_SYSCALLS.get(platform.machine().lower())
            if sys.platform != "linux" or syscall_number is None:
                raise OSError("memfd_create is unavailable")
            libc = ctypes.CDLL(None, use_errno=True)
            ctypes.set_errno(0)
            descriptor = int(
                libc.syscall(
                    ctypes.c_long(syscall_number),
                    ctypes.c_char_p(name.encode("ascii")),
                    ctypes.c_uint(_MFD_CLOEXEC | _MFD_ALLOW_SEALING),
                )
            )
            if descriptor < 0:
                error = ctypes.get_errno()
                raise OSError(error, os.strerror(error))
        view = memoryview(value)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("sealed memfd write made no progress")
            view = view[written:]
        os.fchmod(descriptor, mode)
        fcntl.fcntl(descriptor, _F_ADD_SEALS, _ALL_SEALS)
        _sealed_descriptor_state(descriptor, mode)
        os.lseek(descriptor, 0, os.SEEK_SET)
        return descriptor
    except OSError as error:
        if descriptor >= 0:
            os.close(descriptor)
        raise MaterializationError("sealed memory sources are unavailable") from error


def _sealed_descriptor_state(descriptor: int, mode: int) -> int:
    try:
        info = os.fstat(descriptor)
        seals = fcntl.fcntl(descriptor, _F_GET_SEALS)
    except OSError as error:
        raise MaterializationError("sealed memory source is unavailable") from error
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) != mode
        or seals != _ALL_SEALS
    ):
        raise MaterializationError("sealed memory source is unsafe")
    return seals


def _create_verified_source_bundle(
    policy: dict[str, object], verified_bytes: dict[str, bytes]
) -> VerifiedSourceBundle:
    if set(verified_bytes) != set(POLICY_SOURCE_PATHS):
        raise MaterializationError("verified source bundle inputs are incomplete")
    descriptors: dict[str, int] = {}
    digests: dict[str, str] = {}
    sizes: dict[str, int] = {}
    complete = False
    try:
        for source_name in POLICY_SOURCE_PATHS:
            if policy[source_name] != POLICY_SOURCE_PATHS[source_name]:
                raise MaterializationError("verified source bundle policy path drifted")
            value = verified_bytes[source_name]
            descriptor = _sealed_memfd(f"sealed-uv-{source_name}", value, mode=0o400)
            descriptors[source_name] = descriptor
            digests[source_name] = hashlib.sha256(value).hexdigest()
            sizes[source_name] = len(value)
        bundle = VerifiedSourceBundle(descriptors, digests, sizes)
        complete = True
        return bundle
    finally:
        if not complete:
            for descriptor in descriptors.values():
                os.close(descriptor)


def _load_verified_tool(
    source_bundle: VerifiedSourceBundle, source_name: str, module_name: str
) -> Any:
    if source_name not in {
        "rust_toolchain_validator",
        "llvm_toolchain_validator",
        "input_cache_validator",
    }:
        raise MaterializationError("toolchain validator is outside the verified source bundle")
    logical_name = f"<sealed:{POLICY_SOURCE_PATHS[source_name]}>"
    module = types.ModuleType(module_name)
    module.__file__ = logical_name
    module.__package__ = ""
    try:
        exec(compile(source_bundle.read(source_name), logical_name, "exec"), module.__dict__)
    except (OSError, SyntaxError, ValueError) as error:
        raise MaterializationError(f"cannot load {module_name}") from error
    return module


def _rust_toolchain_manifest_from_bytes(raw: bytes) -> dict[str, object]:
    try:
        document = json.loads(raw.decode("utf-8"))
        rust = document["rust"]
        if not isinstance(rust, dict) or not isinstance(rust["components"], dict):
            raise ValueError("invalid Rust toolchain policy")
        channel_url = rust["channel_manifest_url"]
        channel_sha256 = rust["channel_manifest_sha256"]
        materialized = rust["materialized_toolchain"]
        if not isinstance(channel_url, str) or not isinstance(channel_sha256, str):
            raise ValueError("invalid Rust toolchain policy")
        if (
            not isinstance(materialized, dict)
            or set(materialized) != {"tree_sha256", "file_count"}
            or not isinstance(materialized["tree_sha256"], str)
            or len(materialized["tree_sha256"]) != _SHA256_LENGTH
            or not isinstance(materialized["file_count"], int)
            or isinstance(materialized["file_count"], bool)
            or materialized["file_count"] <= 0
        ):
            raise ValueError("invalid Rust toolchain policy")
        return {
            "rust_version": rust["version"],
            "materialized_toolchain": materialized,
            "channel_manifest": {
                "filename": Path(channel_url).name,
                "sha256": channel_sha256,
                "url": channel_url,
            },
            "components": {
                name: {**item, "filename": Path(item["url"]).name}
                for name, item in rust["components"].items()
            },
        }
    except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise MaterializationError("verified Rust toolchain policy is invalid") from error


def _json_policy_from_bundle(source_bundle: VerifiedSourceBundle, source_name: str) -> dict[str, object]:
    try:
        document = json.loads(source_bundle.read(source_name).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MaterializationError("verified toolchain policy is invalid") from error
    if not isinstance(document, dict):
        raise MaterializationError("verified toolchain policy is invalid")
    return document


def _verify_toolchains(
    policy: dict[str, object],
    cargo: Path,
    llvm_toolchain: Path,
    source_bundle: VerifiedSourceBundle,
) -> None:
    cargo = _lexical_absolute(cargo, "cargo")
    llvm_toolchain = _lexical_absolute(llvm_toolchain, "LLVM toolchain")
    if cargo.name != "cargo" or cargo.parent.name != "bin":
        raise MaterializationError("toolchain verification failed")
    try:
        rust_tool = _load_verified_tool(
            source_bundle, "rust_toolchain_validator", "sealed_uv_exec_rust"
        )
        llvm_tool = _load_verified_tool(
            source_bundle, "llvm_toolchain_validator", "sealed_uv_exec_llvm"
        )
        input_tool = _load_verified_tool(
            source_bundle, "input_cache_validator", "sealed_uv_exec_input"
        )
        rust_tool.verify_materialized_toolchain(
            cargo.parent.parent,
            _rust_toolchain_manifest_from_bytes(source_bundle.read("rust_toolchain_policy")),
        )
        llvm_tool.verify_materialized(
            llvm_toolchain,
            _json_policy_from_bundle(source_bundle, "llvm_toolchain_policy"),
        )
        input_tool.validate_private_cargo(cargo, "1.95.0")
        input_tool.validate_private_rustc(cargo.with_name("rustc"), "1.95.0")
    except (MaterializationError, OSError, ValueError) as error:
        raise MaterializationError("toolchain verification failed") from error


def _prepare_destination(
    destination: Path,
) -> tuple[Path, Path, int, tuple[int, int]]:
    destination = _lexical_absolute(destination, "destination")
    if destination == ROOT or ROOT in destination.parents:
        raise MaterializationError("destination must remain external to the checkout")
    if destination.name != PAIR_BINARY_NAME:
        raise MaterializationError("destination must name the fixed sealed UV executor binary")
    manifest = destination.with_name(PAIR_MANIFEST_NAME)
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
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    parent_fd = -1
    try:
        parent_fd = os.open(destination.parent, flags)
        observed = os.fstat(parent_fd)
        parent_identity = (observed.st_dev, observed.st_ino)
        _verify_destination_parent(parent_fd, parent_identity, destination.parent)
        _require_absent_child(parent_fd, destination.name, "binary destination")
        _require_absent_child(parent_fd, manifest.name, "manifest destination")
        return destination, manifest, parent_fd, parent_identity
    except MaterializationError:
        if parent_fd >= 0:
            try:
                os.close(parent_fd)
            except OSError:
                pass
        raise
    except OSError as error:
        if parent_fd >= 0:
            try:
                os.close(parent_fd)
            except OSError:
                pass
        raise MaterializationError("destination parent cannot be opened safely") from error


def _require_absent_child(parent_fd: int, name: str, label: str) -> None:
    try:
        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError as error:
        raise MaterializationError(f"{label} cannot be inspected") from error
    raise MaterializationError(f"{label} already exists")


def _read_bound_sandbox(policy: dict[str, object]) -> bytes:
    path = _lexical_absolute(Path(str(policy["sandbox_path"])), "Bubblewrap")
    _reject_symlink_ancestors(path, "Bubblewrap")
    try:
        named = path.lstat()
    except OSError as error:
        raise MaterializationError("Bubblewrap is unavailable") from error
    if (
        path.is_symlink()
        or not stat.S_ISREG(named.st_mode)
        or named.st_nlink != 1
        or named.st_uid != policy["sandbox_uid"]
        or named.st_gid != policy["sandbox_gid"]
        or stat.S_IMODE(named.st_mode) != int(str(policy["sandbox_mode"]), 8)
        or not named.st_mode & stat.S_IXUSR
        or named.st_mode & 0o022
    ):
        raise MaterializationError("Bubblewrap identity is unsafe")
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as error:
        raise MaterializationError("Bubblewrap cannot be opened safely") from error
    try:
        opened = os.fstat(descriptor)
        if (
            (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns)
            != (named.st_dev, named.st_ino, named.st_size, named.st_mtime_ns, named.st_ctime_ns)
        ):
            raise MaterializationError("Bubblewrap identity changed while read")
        raw = bytearray()
        while block := os.read(descriptor, 1024 * 1024):
            raw.extend(block)
        named_after = path.lstat()
        if (
            (named_after.st_dev, named_after.st_ino, named_after.st_size,
             named_after.st_mtime_ns, named_after.st_ctime_ns)
            != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns)
            or hashlib.sha256(raw).hexdigest() != policy["sandbox_sha256"]
        ):
            raise MaterializationError("Bubblewrap identity changed while read")
        return bytes(raw)
    except OSError as error:
        raise MaterializationError("Bubblewrap cannot be read safely") from error
    finally:
        os.close(descriptor)


def _verify_sandbox(policy: dict[str, object]) -> int:
    descriptor = _sealed_memfd("sealed-uv-bwrap", _read_bound_sandbox(policy), mode=0o500)
    complete = False
    command = f"/proc/self/fd/{descriptor}"
    common = {
        "cwd": Path("/"),
        "env": {},
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "check": False,
        "close_fds": True,
        "pass_fds": (descriptor,),
        "timeout": 5,
    }
    try:
        version = subprocess.run((command, "--version"), **common)
        help_result = subprocess.run((command, "--help"), **common)
        expected_version = f"{policy['sandbox_version']}\n".encode("ascii")
        if (
            version.returncode != 0
            or version.stderr != b""
            or version.stdout != expected_version
            or help_result.returncode != 0
            or help_result.stderr != b""
            or any(
                capability.encode("ascii") not in help_result.stdout
                for capability in SANDBOX_CAPABILITIES
            )
        ):
            raise MaterializationError("Bubblewrap identity or capabilities are invalid")
        complete = True
        return descriptor
    except (OSError, subprocess.SubprocessError, UnicodeEncodeError) as error:
        raise MaterializationError("Bubblewrap identity cannot be verified") from error
    finally:
        if not complete:
            os.close(descriptor)


def _sandbox_argv(
    policy: dict[str, object],
    source_bundle: VerifiedSourceBundle,
    *,
    cargo: Path,
    llvm_toolchain: Path,
    sandbox_fd: int,
) -> tuple[tuple[str, ...], dict[str, str], tuple[int, ...]]:
    source_targets = (
        ("cargo_manifest", "/src/Cargo.toml"),
        ("cargo_lock", "/src/Cargo.lock"),
        ("rust_source", "/src/src/main.rs"),
    )
    for source_name, _target in source_targets:
        _sealed_descriptor_state(source_bundle.descriptor(source_name), 0o400)
    _sealed_descriptor_state(sandbox_fd, 0o500)
    rust_root = cargo.parent.parent
    cargo_command = (
        "/toolchain/bin/cargo build --quiet --locked --offline --release --target "
        f"{policy['target_triple']} && /usr/bin/cat /work/target/"
        f"{policy['target_triple']}/release/{policy['binary_name']}"
    )
    environment_pairs = (
        ("CARGO_HOME", "/work/cargo-home"),
        ("CARGO_NET_OFFLINE", "true"),
        ("CARGO_TARGET_DIR", "/work/target"),
        ("HOME", "/work/home"),
        ("LANG", "C.UTF-8"),
        ("LC_ALL", "C.UTF-8"),
        ("PATH", "/toolchain/bin:/llvm/bin:/usr/bin:/bin"),
        ("RUSTC", "/toolchain/bin/rustc"),
        (
            "RUSTFLAGS",
            "-C linker=/llvm/bin/clang -C link-arg=-fuse-ld=lld "
            "-C link-arg=-Wl,--build-id=none",
        ),
        ("SOURCE_DATE_EPOCH", "0"),
        ("TEMP", "/work/tmp"),
        ("TMP", "/work/tmp"),
        ("TMPDIR", "/work/tmp"),
    )
    argv: list[str] = [
        f"/proc/self/fd/{sandbox_fd}",
        "--die-with-parent",
        "--unshare-user",
        "--unshare-pid",
        "--unshare-net",
        "--new-session",
        "--clearenv",
    ]
    for key, value in environment_pairs:
        argv.extend(("--setenv", key, value))
    argv.extend((
        "--tmpfs", "/",
        "--dir", "/src",
        "--dir", "/src/src",
    ))
    for source_name, target in source_targets:
        argv.extend((
            "--perms", "0400", "--ro-bind-data", str(source_bundle.descriptor(source_name)), target
        ))
    argv.extend((
        "--dir", "/work",
        "--dir", "/work/cargo-home",
        "--dir", "/work/home",
        "--dir", "/work/target",
        "--dir", "/work/tmp",
        "--dir", "/toolchain",
        "--dir", "/llvm",
        "--dir", "/usr",
        "--dir", "/etc",
        "--ro-bind", str(rust_root), "/toolchain",
        "--ro-bind", str(llvm_toolchain), "/llvm",
        "--ro-bind", "/usr/bin", "/usr/bin",
        "--ro-bind", "/usr/lib", "/usr/lib",
        "--ro-bind", "/usr/lib64", "/usr/lib64",
        "--ro-bind", "/etc/ld.so.cache", "/etc/ld.so.cache",
        "--symlink", "usr/lib", "/lib",
        "--symlink", "usr/lib64", "/lib64",
        "--symlink", "usr/bin", "/bin",
        "--proc", "/proc",
        "--dev", "/dev",
        "--chdir", "/src",
        "/usr/bin/sh", "-c", cargo_command,
    ))
    passed = (sandbox_fd, *(source_bundle.descriptor(name) for name, _ in source_targets))
    return tuple(argv), {}, passed


def _run_sandbox(
    argv: tuple[str, ...], environment: dict[str, str], passed: tuple[int, ...]
) -> tuple[int, bytes, bytes]:
    try:
        for descriptor in passed:
            os.lseek(descriptor, 0, os.SEEK_SET)
        process = subprocess.Popen(
            argv,
            cwd=Path("/"),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            pass_fds=passed,
        )
    except OSError as error:
        raise MaterializationError("sealed UV executor sandbox cannot start") from error
    assert process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    captured = {"stdout": bytearray(), "stderr": bytearray()}
    limits = {"stdout": _MAX_BINARY_BYTES, "stderr": _MAX_SANDBOX_STDERR_BYTES}
    deadline = time.monotonic() + _SANDBOX_TIMEOUT_SECONDS
    exceeded = False
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.kill()
                raise MaterializationError("sealed UV executor sandbox timed out")
            events = selector.select(remaining)
            if not events:
                continue
            for key, _event in events:
                try:
                    chunk = os.read(key.fileobj.fileno(), 64 * 1024)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                captured[key.data].extend(chunk)
                if len(captured[key.data]) > limits[key.data]:
                    exceeded = True
                    process.kill()
        returncode = process.wait(timeout=5)
    except (OSError, subprocess.SubprocessError) as error:
        process.kill()
        process.wait()
        raise MaterializationError("sealed UV executor sandbox output failed") from error
    finally:
        selector.close()
    if exceeded:
        raise MaterializationError("sealed UV executor sandbox output exceeded its bound")
    return returncode, bytes(captured["stdout"]), bytes(captured["stderr"])


def _build_once(
    policy: dict[str, object],
    cargo: Path,
    llvm_toolchain: Path,
    source_bundle: VerifiedSourceBundle,
    sandbox_fd: int,
) -> bytes:
    argv, environment, passed = _sandbox_argv(
        policy,
        source_bundle,
        cargo=cargo,
        llvm_toolchain=llvm_toolchain,
        sandbox_fd=sandbox_fd,
    )
    returncode, output, _stderr = _run_sandbox(argv, environment, passed)
    if returncode != 0:
        raise MaterializationError("sealed UV executor isolated offline build failed")
    if not output.startswith(b"\x7fELF") or len(output) > _MAX_BINARY_BYTES:
        raise MaterializationError("sealed UV executor sandbox export is invalid")
    return output


def _digest_descriptor(descriptor: int, *, limit: int) -> tuple[str, int]:
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        size = 0
        while block := os.read(descriptor, 1024 * 1024):
            size += len(block)
            if size > limit:
                raise MaterializationError("sealed UV executor publish input exceeds its bound")
            digest.update(block)
        os.lseek(descriptor, 0, os.SEEK_SET)
        return digest.hexdigest(), size
    except MaterializationError:
        raise
    except OSError as error:
        raise MaterializationError("sealed UV executor publish input cannot be read") from error


def _verify_regular_descriptor(
    descriptor: int,
    *,
    label: str,
    mode: int,
    digest: str,
    size: int,
    require_link: bool,
) -> None:
    try:
        info = os.fstat(descriptor)
    except OSError as error:
        raise MaterializationError(f"{label} cannot be inspected") from error
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != mode
        or (require_link and info.st_nlink != 1)
        or info.st_size != size
        or _digest_descriptor(descriptor, limit=max(size, 1)) != (digest, size)
    ):
        raise MaterializationError(f"{label} is not the bound direct regular file")


def _open_unlinked_publish_file(parent_fd: int, *, label: str, mode: int) -> int:
    flag = getattr(os, "O_TMPFILE", 0)
    if sys.platform != "linux" or flag == 0:
        raise MaterializationError("descriptor-bound publication is unavailable")
    descriptor = -1
    try:
        descriptor = os.open(
            ".",
            os.O_RDWR | flag | getattr(os, "O_CLOEXEC", 0),
            mode,
            dir_fd=parent_fd,
        )
        os.fchmod(descriptor, mode)
        return descriptor
    except OSError as error:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise MaterializationError(f"{label} cannot be created by descriptor") from error


def _copy_sealed_descriptor(
    source_fd: int, destination_fd: int, *, label: str, mode: int
) -> tuple[str, int]:
    _sealed_descriptor_state(source_fd, mode)
    try:
        os.lseek(source_fd, 0, os.SEEK_SET)
        while block := os.read(source_fd, 1024 * 1024):
            view = memoryview(block)
            while view:
                written = os.write(destination_fd, view)
                if written <= 0:
                    raise OSError("descriptor copy made no progress")
                view = view[written:]
        os.fsync(destination_fd)
        digest, size = _digest_descriptor(destination_fd, limit=_MAX_BINARY_BYTES)
        _verify_regular_descriptor(
            destination_fd,
            label=label,
            mode=mode,
            digest=digest,
            size=size,
            require_link=False,
        )
        return digest, size
    except OSError as error:
        raise MaterializationError(f"{label} cannot be copied by descriptor") from error


def _linkat_empty_path(source_fd: int, parent_fd: int, name: str) -> None:
    if sys.platform != "linux" or not name or "/" in name:
        raise OSError(errno.ENOSYS, "linkat AT_EMPTY_PATH is unavailable")
    libc = ctypes.CDLL(None, use_errno=True)
    linkat = libc.linkat
    linkat.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int)
    linkat.restype = ctypes.c_int
    ctypes.set_errno(0)
    if linkat(source_fd, b"", parent_fd, os.fsencode(name), _AT_EMPTY_PATH) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _manifest(policy: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "policy_sha256": hashlib.sha256(_canonical_json(policy)).hexdigest(),
        "source_commit": policy["source_commit"],
        "binary": {
            "name": PAIR_BINARY_NAME,
            "sha256": policy["binary_sha256"],
            "size": policy["binary_size"],
            "mode": policy["binary_mode"],
        },
    }


def _verify_destination_parent(
    parent_fd: int, identity: tuple[int, int], parent: Path
) -> None:
    try:
        observed = os.fstat(parent_fd)
        named = parent.lstat()
    except OSError as error:
        raise MaterializationError("destination parent identity changed before publish") from error
    if (
        not stat.S_ISDIR(observed.st_mode)
        or (observed.st_dev, observed.st_ino) != identity
        or observed.st_uid != os.geteuid()
        or stat.S_IMODE(observed.st_mode) != 0o700
        or parent.is_symlink()
        or not stat.S_ISDIR(named.st_mode)
        or (named.st_dev, named.st_ino) != identity
        or named.st_uid != os.geteuid()
        or stat.S_IMODE(named.st_mode) != 0o700
    ):
        raise MaterializationError("destination parent identity changed before publish")


def _verify_published_binary(
    parent_fd: int,
    parent_identity: tuple[int, int],
    parent: Path,
    name: str,
    policy: dict[str, object],
) -> None:
    _verify_destination_parent(parent_fd, parent_identity, parent)
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
    except OSError as error:
        raise MaterializationError("published sealed UV executor binary is unavailable") from error
    try:
        opened = os.fstat(descriptor)
        named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
            raise MaterializationError("published sealed UV executor binary identity changed")
        _verify_regular_descriptor(
            descriptor,
            label="published sealed UV executor binary",
            mode=BINARY_MODE,
            digest=str(policy["binary_sha256"]),
            size=int(policy["binary_size"]),
            require_link=True,
        )
    except OSError as error:
        raise MaterializationError("published sealed UV executor binary cannot be verified") from error
    finally:
        os.close(descriptor)


def verify_materialized(destination: Path, policy: dict[str, object]) -> dict[str, object]:
    policy = _validate_policy(policy)
    destination = _lexical_absolute(destination, "destination")
    if destination == ROOT or ROOT in destination.parents:
        raise MaterializationError("destination must remain external to the checkout")
    if destination.name != PAIR_BINARY_NAME:
        raise MaterializationError("materialized sealed UV executor pair name is invalid")
    manifest_destination = destination.with_name(PAIR_MANIFEST_NAME)
    _reject_symlink_ancestors(destination, "destination")
    binary_fd = -1
    manifest_fd = -1
    try:
        parent_fd = os.open(
            destination.parent,
            os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as error:
        raise MaterializationError("materialized sealed UV executor pair is unavailable") from error
    try:
        parent_info = os.fstat(parent_fd)
        if (
            not stat.S_ISDIR(parent_info.st_mode)
            or parent_info.st_uid != os.geteuid()
            or stat.S_IMODE(parent_info.st_mode) != 0o700
        ):
            raise MaterializationError("materialized sealed UV executor parent is unsafe")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        binary_fd = os.open(destination.name, flags, dir_fd=parent_fd)
        manifest_fd = os.open(manifest_destination.name, flags, dir_fd=parent_fd)
    except OSError as error:
        if binary_fd >= 0:
            os.close(binary_fd)
        os.close(parent_fd)
        raise MaterializationError("materialized sealed UV executor pair is incomplete") from error
    try:
        _verify_regular_descriptor(
            binary_fd,
            label="materialized sealed UV executor",
            mode=BINARY_MODE,
            digest=str(policy["binary_sha256"]),
            size=int(policy["binary_size"]),
            require_link=True,
        )
        manifest_digest, manifest_size = _digest_descriptor(manifest_fd, limit=_MAX_BINARY_BYTES)
        _verify_regular_descriptor(
            manifest_fd,
            label="sealed UV executor manifest",
            mode=0o400,
            digest=manifest_digest,
            size=manifest_size,
            require_link=True,
        )
        try:
            os.lseek(manifest_fd, 0, os.SEEK_SET)
            manifest_raw = b"".join(
                iter(lambda: os.read(manifest_fd, 1024 * 1024), b"")
            )
            manifest = json.loads(manifest_raw.decode("ascii"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise MaterializationError("sealed UV executor manifest is invalid") from error
        expected = _manifest(policy)
        if manifest != expected or manifest_raw != _canonical_json(expected):
            raise MaterializationError("sealed UV executor manifest is not policy-bound")
        return manifest
    finally:
        os.close(binary_fd)
        os.close(manifest_fd)
        os.close(parent_fd)


def materialize(
    *, policy_path: Path, destination: Path, cargo: Path, llvm_toolchain: Path
) -> dict[str, object]:
    policy = load_policy(policy_path)
    source_bundle = _create_verified_source_bundle(
        policy, _verify_policy_source_commit(policy)
    )
    parent_fd = -1
    sandbox_fd = -1
    binary_authority_fd = -1
    manifest_authority_fd = -1
    binary_publish_fd = -1
    manifest_publish_fd = -1
    try:
        destination, manifest_destination, parent_fd, parent_identity = _prepare_destination(destination)
        _verify_toolchains(policy, cargo, llvm_toolchain, source_bundle)
        sandbox_fd = _verify_sandbox(policy)
        first = _build_once(policy, cargo, llvm_toolchain, source_bundle, sandbox_fd)
        second = _build_once(policy, cargo, llvm_toolchain, source_bundle, sandbox_fd)
        if (
            len(first) != len(second)
            or hashlib.sha256(first).hexdigest() != hashlib.sha256(second).hexdigest()
        ):
            raise MaterializationError("sealed UV executor builds are not reproducible")
        first_digest = hashlib.sha256(first).hexdigest()
        if (
            first_digest != policy["binary_sha256"]
            or len(first) != policy["binary_size"]
        ):
            raise MaterializationError("sealed UV executor output authority does not match")
        binary_authority_fd = _sealed_memfd("sealed-uv-exec-v4-binary", first, mode=BINARY_MODE)
        manifest_raw = _canonical_json(_manifest(policy))
        manifest_authority_fd = _sealed_memfd(
            "sealed-uv-exec-v4-manifest", manifest_raw, mode=0o400
        )
        binary_publish_fd = _open_unlinked_publish_file(
            parent_fd, label="sealed UV executor binary", mode=BINARY_MODE
        )
        copied_digest, copied_size = _copy_sealed_descriptor(
            binary_authority_fd,
            binary_publish_fd,
            label="sealed UV executor binary",
            mode=BINARY_MODE,
        )
        if copied_digest != policy["binary_sha256"] or copied_size != policy["binary_size"]:
            raise MaterializationError("sealed UV executor binary copy authority changed")
        manifest_publish_fd = _open_unlinked_publish_file(
            parent_fd, label="sealed UV executor manifest", mode=0o400
        )
        copied_manifest_digest, copied_manifest_size = _copy_sealed_descriptor(
            manifest_authority_fd,
            manifest_publish_fd,
            label="sealed UV executor manifest",
            mode=0o400,
        )
        if (copied_manifest_digest, copied_manifest_size) != (
            hashlib.sha256(manifest_raw).hexdigest(),
            len(manifest_raw),
        ):
            raise MaterializationError("sealed UV executor manifest copy authority changed")
        _verify_destination_parent(parent_fd, parent_identity, destination.parent)
        _require_absent_child(parent_fd, destination.name, "binary destination")
        try:
            _linkat_empty_path(binary_publish_fd, parent_fd, destination.name)
        except OSError as error:
            if error.errno == errno.EEXIST:
                raise MaterializationError("binary destination already exists at publication") from error
            raise MaterializationError("sealed UV executor binary publication failed") from error
        _verify_published_binary(
            parent_fd, parent_identity, destination.parent, destination.name, policy
        )
        _verify_destination_parent(parent_fd, parent_identity, destination.parent)
        _require_absent_child(parent_fd, manifest_destination.name, "manifest destination")
        try:
            _linkat_empty_path(manifest_publish_fd, parent_fd, manifest_destination.name)
        except OSError as error:
            if error.errno == errno.EEXIST:
                raise MaterializationError("sealed UV executor manifest publication failed") from error
            raise MaterializationError("sealed UV executor manifest publication failed") from error
        return verify_materialized(destination, policy)
    finally:
        for descriptor in (
            manifest_publish_fd,
            binary_publish_fd,
            manifest_authority_fd,
            binary_authority_fd,
        ):
            if descriptor >= 0:
                os.close(descriptor)
        if sandbox_fd >= 0:
            os.close(sandbox_fd)
        if parent_fd >= 0:
            os.close(parent_fd)
        source_bundle.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--cargo", type=Path)
    parser.add_argument("--llvm-toolchain", type=Path)
    parser.add_argument("--verify-pair", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        policy = load_policy(arguments.policy)
        if arguments.verify_pair:
            if arguments.cargo is not None or arguments.llvm_toolchain is not None:
                raise MaterializationError("pair verification does not accept build toolchains")
            verify_materialized(arguments.destination, policy)
        else:
            if arguments.cargo is None or arguments.llvm_toolchain is None:
                raise MaterializationError("materialization requires both private toolchains")
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
