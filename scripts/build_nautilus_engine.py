#!/usr/bin/env python3
"""Build and verify an external, offline Nautilus CPython 3.12 wheel."""
from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable
import ctypes
from email.parser import BytesParser
from email.policy import compat32
import errno
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from typing import Any, BinaryIO
import unicodedata
import zipfile


_ROOT = Path(__file__).resolve().parents[1]
_INPUT_CACHE_TOOL = _ROOT / "scripts/prepare_nautilus_input_cache.py"
_INPUT_CACHE_POLICY = _ROOT / "engines/nautilus/input-cache-policy.json"
_RUST_TOOLCHAIN_TOOL = _ROOT / "scripts/prepare_nautilus_toolchain.py"
_RUST_TOOLCHAIN_POLICY = _ROOT / "engines/nautilus/toolchain-inputs.json"
_LLVM_TOOLCHAIN_TOOL = _ROOT / "scripts/prepare_nautilus_llvm_toolchain.py"
_LLVM_TOOLCHAIN_POLICY = _ROOT / "engines/nautilus/llvm-toolchain-policy.json"
_AMBIENT_BUILD_PATH = (Path("/usr/bin"), Path("/bin"))
_ARTIFACT_MANIFEST = "artifact-manifest.json"
_WHEEL_CACHE_MANIFEST = "wheel-cache-manifest.json"
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_PYTHON_IDENTITY_RE = re.compile(r"CPython 3\.12\.\d+")
_NATIVE_RE = re.compile(r"(?:\.so(?:\.\d+)*|\.pyd|\.dylib|\.dll)$", re.IGNORECASE)
_POLICY_FIELDS = {
    "schema_version",
    "engine_name",
    "engine_version",
    "python_implementation",
    "python_minor",
    "wheel_python_tag",
    "upstream_repository",
    "upstream_tag",
    "upstream_tag_object",
    "upstream_commit",
    "source_sha256",
    "cargo_lock_sha256",
    "pyproject_sha256",
    "required_rust_version",
    "required_build_wheels",
    "required_unpinned_build_wheels",
}
_EXPECTED_POLICY = {
    "schema_version": 1,
    "engine_name": "nautilus_trader",
    "engine_version": "1.227.0",
    "python_implementation": "CPython",
    "python_minor": "3.12",
    "wheel_python_tag": "cp312",
    "upstream_repository": "https://github.com/nautechsystems/nautilus_trader.git",
    "upstream_tag": "v1.227.0",
    "upstream_tag_object": "0ccb5b55879c072a6e07fc7cbe5297c53c378107",
    "upstream_commit": "280ae1762df51a492a4ce71506a40b5c8706def5",
    "source_sha256": "a00d3ab0c5b2ba1e4a4ac4c9af70f5b3fe30717d9b42a328e51696e3894a45e2",
    "cargo_lock_sha256": "083652294183947a352d1443ed0245311bf7ee5a716b66ccc21e814be25851ed",
    "pyproject_sha256": "f707cbe27b183ba598c31f1b3b6ec67e36f36e878c4228d3fef80741efb81b28",
    "required_rust_version": "1.95.0",
    "required_build_wheels": {"cython": "3.2.4", "poetry-core": "2.3.1"},
    "required_unpinned_build_wheels": ["numpy", "packaging", "pip", "setuptools"],
}
_ARTIFACT_FIELDS = {
    "schema_version",
    "engine_name",
    "engine_version",
    "python_identity",
    "wheel_python_tag",
    "network_mode",
    "upstream_tag_object",
    "upstream_commit",
    "source_sha256",
    "cargo_lock_sha256",
    "pyproject_sha256",
    "cargo_identity",
    "rustc_identity",
    "input_cache_manifest_sha256",
    "wheel_cache_manifest_sha256",
    "wheel",
    "native_libraries",
}
_WHEEL_FIELDS = {"filename", "sha256", "size"}
_NATIVE_FIELDS = {"path", "sha256", "size"}
_WHEEL_CACHE_FIELDS = {"schema_version", "python_minor", "artifacts"}
_WHEEL_CACHE_ARTIFACT_FIELDS = {"filename", "package", "version", "role", "sha256", "size"}
_PIP_BOOTSTRAP = (
    "import runpy,sys; "
    "wheel=sys.argv.pop(1); "
    "sys.path.insert(0,wheel); "
    "runpy.run_module('pip',run_name='__main__')"
)

_CANDIDATE_DIRECTORY = _ROOT / "engines/nautilus/candidates/v1.231"
_CANDIDATE_ENGINE_POLICY = _CANDIDATE_DIRECTORY / "engine-build-policy.json"
_CANDIDATE_INPUT_POLICY = _CANDIDATE_DIRECTORY / "input-cache-policy.json"
_CANDIDATE_WHEEL_POLICY = _CANDIDATE_DIRECTORY / "wheel-cache-policy.json"
_CANDIDATE_CARGO_POLICY = _CANDIDATE_DIRECTORY / "cargo-registry-policy.json"
_CANDIDATE_TOOLCHAIN_INPUTS = _CANDIDATE_DIRECTORY / "toolchain-inputs.json"
_CANDIDATE_PROVENANCE = _ROOT / "engines/nautilus/v1.231-provenance-policy.json"
_CANDIDATE_INPUT_GENERATOR = _ROOT / "scripts/write_nautilus_toolchain_inputs.py"
_CANDIDATE_RUNTIME_CLOSURE_TOOL = (
    _ROOT / "scripts/materialize_nautilus_runtime_closure.py"
)
_CANDIDATE_SANDBOX = Path("/usr/bin/bwrap")
_CANDIDATE_COMMIT = "27a8e54e7ac3c57d6cbf8891f0283dfbaee97317"
_CANDIDATE_EXT_SUFFIX = ".cpython-312-x86_64-linux-gnu.so"
_CANDIDATE_WHEEL_FILENAME = "nautilus_trader-1.231.0-cp312-cp312-manylinux_2_39_x86_64.whl"
_CANDIDATE_WHEEL_TAG = "cp312-cp312-manylinux_2_39_x86_64"
_CANDIDATE_DIST_INFO = "nautilus_trader-1.231.0.dist-info"
_CANDIDATE_RAW_WHEEL_DIAGNOSTIC_LIMIT = 64
_CANDIDATE_RAW_WHEEL_DIAGNOSTIC_PREFIX = "CANDIDATE_RAW_WHEEL_DIAGNOSTIC="
_CANDIDATE_FORENSIC_MANIFEST = "forensic-manifest.json"
_X4_RECEIPT_SCHEMA = "p1-u04-x4-authority-preflight-v1"
_X4_COMPLETE_AUTHORITY_RECEIPT_PATH = (
    ".superpowers/sdd/P1_U04_X4_X9_EXECUTION_PLAN/task-4-receipt.json"
)
_X4_COMPLETE_AUTHORITY_RECEIPT = _ROOT / _X4_COMPLETE_AUTHORITY_RECEIPT_PATH
_BUILD_A_DIRECTORY = "build-a"
_BUILD_B_DIRECTORY = "build-b"
_CANDIDATE_BUILD_RESULT_SCHEMA = "p1-u04-candidate-build-result-v1"
_CANDIDATE_ARTIFACT_CORE = "artifact-core.json"
_CANDIDATE_BUILD_RECEIPT = "build-receipt.json"
_CANDIDATE_RAW_WHEEL_BYTE_LIMIT = 1024**3
_CANDIDATE_RAW_WHEEL_MEMBER_LIMIT = 16_384
_CANDIDATE_RAW_WHEEL_COMPRESSED_SIZE_LIMIT = 2 * 1024**3
_CANDIDATE_RAW_WHEEL_DECLARED_SIZE_LIMIT = 8 * 1024**3
_CANDIDATE_RAW_WHEEL_STREAMED_SIZE_LIMIT = 8 * 1024**3
_CANDIDATE_RAW_WHEEL_OUTPUT_BYTE_LIMIT = 256 * 1024
_CANDIDATE_RAW_WHEEL_NAME_BYTE_LIMIT = 512
_CANDIDATE_SOURCE_INPUTS = {
    "build.py": (24158, "8a8e46de9c58b83bbfdb3b7415538d2444f9dd126d61c66a8a63416606e9bc40"),
    "pyproject.toml": (15841, "5dbc4591408bd65f7b35c2274348a7a02ff7b034a15f46d5f8628d3c8fbafa36"),
}
_CANDIDATE_POETRY_MODULES = {
    "poetry/core/__init__.py": "08ac729512fd7a60d013b913f5c769f462ea115812329e3061da7f28df8337e4",
    "poetry/core/_vendor/packaging/__init__.py": "ff470388f55fd92f9b35f566660bb1c739ab2185a5c804b1a6aa61e2ab095947",
    "poetry/core/factory.py": "e705689fdd110147a19c8b3a895c1e0f646ae6757e958e09f9d9e4707652447e",
    "poetry/core/masonry/builders/builder.py": "1df30902f5d772f08078e516adf56fade51b24a816b5abcd0660f09abc9db804",
    "poetry/core/masonry/builders/wheel.py": "e004bcb876c6f773848b65112b64f97f3b31a445ee34afe8f89468f49b2ae679",
    "poetry/core/masonry/metadata.py": "27e852770d523f8e0d3bf3847a9a662f2c67725d2506dd83d6ef5bb67d9945a0",
    "poetry/core/vcs/__init__.py": "bd5cda7ba598464acec640b645e156d06cb8445458576913c9601f6ad6aa19ba",
}
_RENAME_NOREPLACE = 1


class VerificationError(ValueError):
    """Raised when a build input or produced artifact fails closed."""


def _load_llvm_toolchain_tool():
    spec = importlib.util.spec_from_file_location(
        "prepare_nautilus_llvm_toolchain", _LLVM_TOOLCHAIN_TOOL
    )
    if spec is None or spec.loader is None:
        raise VerificationError("private LLVM verifier is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_rust_toolchain_tool():
    spec = importlib.util.spec_from_file_location(
        "prepare_nautilus_toolchain", _RUST_TOOLCHAIN_TOOL
    )
    if spec is None or spec.loader is None:
        raise VerificationError("private Rust toolchain verifier is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build_tool_environment(
    llvm_bin: Path, cargo_bin: Path, venv_bin: Path
) -> dict[str, str]:
    return {
        "CC": str(llvm_bin / "clang"),
        "CXX": str(llvm_bin / "clang++"),
        "LD": str(llvm_bin / "ld.lld"),
        "CARGO_BUILD_TARGET": "x86_64-unknown-linux-gnu",
        "CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER": str(llvm_bin / "clang"),
        "RUSTFLAGS": f"-C linker={llvm_bin / 'clang'}",
        "PATH": f"{llvm_bin}:{cargo_bin}:{venv_bin}:/usr/bin:/bin",
    }


def _toolchain_root_for_cargo(cargo: Path) -> Path:
    if cargo.name != "cargo" or cargo.parent.name != "bin":
        raise VerificationError("private Cargo must be the bin/cargo entrypoint of a toolchain")
    return cargo.parent.parent


def _stage_compiler_temp_environment(stage: Path) -> dict[str, str]:
    compiler_tmp = stage / "compiler-tmp"
    compiler_tmp.mkdir(mode=0o700)
    return {
        "TMPDIR": str(compiler_tmp),
        "TEMP": str(compiler_tmp),
        "TMP": str(compiler_tmp),
    }


def _reject_ambient_compilers(search_path: tuple[Path, ...]) -> None:
    for directory in search_path:
        for name in ("clang", "clang++", "ld.lld"):
            candidate = directory / name
            if candidate.exists() or candidate.is_symlink():
                raise VerificationError(
                    f"ambient compiler fallback is present: {candidate}"
                )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _require_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise VerificationError(f"{label} is not a SHA-256 digest")
    return value


def _regular_file(path: Path, label: str) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        raise VerificationError(f"{label} is missing") from exc
    if not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_nlink != 1:
        raise VerificationError(f"{label} must be one regular non-symlink file")
    return info


def _directory(path: Path, label: str, mode: int | None = None) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        raise VerificationError(f"{label} is missing") from exc
    if not stat.S_ISDIR(info.st_mode) or path.is_symlink() or info.st_uid != os.geteuid():
        raise VerificationError(f"{label} is unsafe")
    if mode is not None and stat.S_IMODE(info.st_mode) != mode:
        raise VerificationError(f"{label} is mutable or has an unsafe mode")
    return info


def _absolute(path: Path, label: str) -> None:
    if (
        not path.is_absolute()
        or path == Path("/")
        or any(part in {"", ".", ".."} for part in path.parts[1:])
    ):
        raise VerificationError(f"{label} must be an absolute non-root path")


def _require_external(path: Path, label: str) -> None:
    _absolute(path, label)
    try:
        path.relative_to(_ROOT)
    except ValueError:
        return
    raise VerificationError(f"{label} must remain external to the Git checkout")


def _reject_symlinked_ancestors(path: Path, label: str) -> None:
    _absolute(path, label)
    current = path
    while True:
        try:
            info = current.lstat()
        except OSError as exc:
            raise VerificationError(f"{label} has a missing ancestor") from exc
        if stat.S_ISLNK(info.st_mode):
            raise VerificationError(f"{label} has a symlinked ancestor")
        if current == current.parent:
            return
        current = current.parent


def _safe_relative(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise VerificationError(f"{label} is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise VerificationError(f"{label} is invalid")
    return path.as_posix()


def load_policy(path: Path) -> dict[str, object]:
    _regular_file(path, "engine build policy")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError("engine build policy is invalid JSON") from exc
    if not isinstance(document, dict) or set(document) != _POLICY_FIELDS or document != _EXPECTED_POLICY:
        raise VerificationError("engine build policy does not match the reviewed WS-01C boundary")
    return document


def _run_identity(command: list[str], label: str) -> str:
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise VerificationError(f"{label} could not report its identity") from exc
    return result.stdout.strip()


def validate_python(python: Path, required_minor: str) -> str:
    _reject_symlinked_ancestors(python, "engine Python")
    info = _regular_file(python, "engine Python")
    if info.st_mode & 0o022 or not info.st_mode & stat.S_IXUSR:
        raise VerificationError("engine Python is writable by another user or not executable")
    identity = _run_identity(
        [str(python), "-I", "-c", "import platform; print(f'CPython {platform.python_version()}')"],
        "engine Python",
    )
    expected = re.compile(rf"CPython {re.escape(required_minor)}\.\d+")
    if expected.fullmatch(identity) is None:
        raise VerificationError(f"engine Python must be CPython Python {required_minor}")
    return identity


def _load_input_cache_tool():
    spec = importlib.util.spec_from_file_location("nautilus_input_cache_for_engine", _INPUT_CACHE_TOOL)
    if spec is None or spec.loader is None:
        raise VerificationError("Nautilus input cache verifier could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _wheel_members(wheel: Path, policy: dict[str, object]) -> tuple[list[dict[str, object]], str, str]:
    native: list[dict[str, object]] = []
    filename_parts = wheel.name.removesuffix(".whl").split("-")
    if (
        len(filename_parts) < 5
        or filename_parts[0] != policy["engine_name"]
        or filename_parts[1] != policy["engine_version"]
        or filename_parts[2] != policy["wheel_python_tag"]
    ):
        raise VerificationError("engine wheel filename does not target the pinned CPython 3.12 package")
    try:
        with zipfile.ZipFile(wheel) as archive:
            names: set[str] = set()
            for info in archive.infolist():
                name = _safe_relative(info.filename.rstrip("/"), "wheel member path")
                if name in names:
                    raise VerificationError("engine wheel has a duplicate member")
                names.add(name)
                unix_mode = info.external_attr >> 16
                if stat.S_ISLNK(unix_mode):
                    raise VerificationError("engine wheel contains a symlink")
                if info.is_dir():
                    continue
                if _NATIVE_RE.search(PurePosixPath(name).name):
                    if not name.startswith("nautilus_trader/"):
                        raise VerificationError("engine wheel has a native library outside its package")
                    payload = archive.read(info)
                    native.append({"path": name, "sha256": _sha256_bytes(payload), "size": len(payload)})
            metadata_names = sorted(name for name in names if name.endswith(".dist-info/METADATA"))
            wheel_names = sorted(name for name in names if name.endswith(".dist-info/WHEEL"))
            if len(metadata_names) != 1 or len(wheel_names) != 1:
                raise VerificationError("engine wheel metadata layout is invalid")
            metadata = BytesParser().parsebytes(archive.read(metadata_names[0]))
            if metadata.get("Name", "").replace("-", "_").lower() != policy["engine_name"]:
                raise VerificationError("engine wheel package name is invalid")
            if metadata.get("Version") != policy["engine_version"]:
                raise VerificationError("engine wheel version is invalid")
            requires_python = metadata.get("Requires-Python", "")
            if set(requires_python.replace(" ", "").split(",")) != {">=3.12", "<3.15"}:
                raise VerificationError("engine wheel Python requirement is invalid")
            wheel_text = archive.read(wheel_names[0]).decode("utf-8")
            tags = [line.removeprefix("Tag: ").strip() for line in wheel_text.splitlines() if line.startswith("Tag: ")]
            if not tags or any(tag.split("-", 1)[0] != policy["wheel_python_tag"] for tag in tags):
                raise VerificationError("engine wheel does not target only CPython 3.12")
    except (OSError, UnicodeDecodeError, zipfile.BadZipFile) as exc:
        raise VerificationError("engine wheel is invalid") from exc
    native.sort(key=lambda item: str(item["path"]))
    if not native or not any("nautilus_pyo3" in str(item["path"]) for item in native):
        raise VerificationError("engine wheel is missing the required native PyO3 library")
    return native, requires_python, tags[0]


def _validate_artifact_document(document: object, policy: dict[str, object]) -> dict[str, object]:
    if not isinstance(document, dict) or set(document) != _ARTIFACT_FIELDS:
        raise VerificationError("artifact manifest fields are missing or unknown")
    expected = {
        "schema_version": 1,
        "engine_name": policy["engine_name"],
        "engine_version": policy["engine_version"],
        "wheel_python_tag": policy["wheel_python_tag"],
        "network_mode": "offline-bwrap-unshare-net",
        "upstream_tag_object": policy["upstream_tag_object"],
        "upstream_commit": policy["upstream_commit"],
        "source_sha256": policy["source_sha256"],
        "cargo_lock_sha256": policy["cargo_lock_sha256"],
        "pyproject_sha256": policy["pyproject_sha256"],
    }
    if any(document.get(key) != value for key, value in expected.items()):
        raise VerificationError("artifact manifest does not match the engine policy")
    if (
        not isinstance(document.get("python_identity"), str)
        or _PYTHON_IDENTITY_RE.fullmatch(str(document["python_identity"])) is None
    ):
        raise VerificationError("artifact manifest Python identity is invalid")
    for field, prefix in (("cargo_identity", "cargo 1.95.0 "), ("rustc_identity", "rustc 1.95.0 ")):
        if not isinstance(document.get(field), str) or not str(document[field]).startswith(prefix):
            raise VerificationError(f"artifact manifest {field} is invalid")
    for field in ("input_cache_manifest_sha256", "wheel_cache_manifest_sha256"):
        _require_sha256(document.get(field), f"artifact manifest {field}")
    wheel = document.get("wheel")
    if not isinstance(wheel, dict) or set(wheel) != _WHEEL_FIELDS:
        raise VerificationError("artifact manifest wheel is invalid")
    _safe_relative(wheel.get("filename"), "artifact wheel filename")
    if "/" in str(wheel["filename"]) or not str(wheel["filename"]).endswith(".whl"):
        raise VerificationError("artifact wheel filename is invalid")
    _require_sha256(wheel.get("sha256"), "artifact wheel digest")
    if not isinstance(wheel.get("size"), int) or int(wheel["size"]) <= 0:
        raise VerificationError("artifact wheel size is invalid")
    libraries = document.get("native_libraries")
    if not isinstance(libraries, list) or not libraries:
        raise VerificationError("artifact manifest native libraries are invalid")
    paths: set[str] = set()
    for library in libraries:
        if not isinstance(library, dict) or set(library) != _NATIVE_FIELDS:
            raise VerificationError("artifact manifest native library fields are invalid")
        path = _safe_relative(library.get("path"), "native library path")
        if path in paths:
            raise VerificationError("artifact manifest has duplicate native libraries")
        paths.add(path)
        _require_sha256(library.get("sha256"), "native library digest")
        if not isinstance(library.get("size"), int) or int(library["size"]) <= 0:
            raise VerificationError("native library size is invalid")
    return document


def write_artifact_manifest(
    artifacts: Path,
    wheel: Path,
    policy: dict[str, object],
    *,
    python_identity: str,
    cargo_identity: str,
    rustc_identity: str,
    input_cache_manifest_sha256: str,
    wheel_cache_manifest_sha256: str,
) -> dict[str, object]:
    _directory(artifacts, "artifact staging directory", 0o700)
    wheel_info = _regular_file(wheel, "built engine wheel")
    if wheel.parent != artifacts or wheel.name == _ARTIFACT_MANIFEST:
        raise VerificationError("built engine wheel is outside artifact staging")
    if set(path.name for path in artifacts.iterdir()) != {wheel.name}:
        raise VerificationError("artifact staging contains unexpected files")
    native, _requires_python, _tag = _wheel_members(wheel, policy)
    document: dict[str, object] = {
        "schema_version": 1,
        "engine_name": policy["engine_name"],
        "engine_version": policy["engine_version"],
        "python_identity": python_identity,
        "wheel_python_tag": policy["wheel_python_tag"],
        "network_mode": "offline-bwrap-unshare-net",
        "upstream_tag_object": policy["upstream_tag_object"],
        "upstream_commit": policy["upstream_commit"],
        "source_sha256": policy["source_sha256"],
        "cargo_lock_sha256": policy["cargo_lock_sha256"],
        "pyproject_sha256": policy["pyproject_sha256"],
        "cargo_identity": cargo_identity,
        "rustc_identity": rustc_identity,
        "input_cache_manifest_sha256": _require_sha256(input_cache_manifest_sha256, "input cache manifest digest"),
        "wheel_cache_manifest_sha256": _require_sha256(wheel_cache_manifest_sha256, "wheel cache manifest digest"),
        "wheel": {"filename": wheel.name, "sha256": _sha256(wheel), "size": wheel_info.st_size},
        "native_libraries": native,
    }
    _validate_artifact_document(document, policy)
    manifest = artifacts / _ARTIFACT_MANIFEST
    manifest.write_text(json.dumps(document, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.chmod(wheel, 0o400)
    os.chmod(manifest, 0o400)
    os.chmod(artifacts, 0o500)
    return document


def _publish_artifacts(artifacts: Path, destination: Path) -> None:
    """Atomically publish an already sealed artifact directory.

    The staging directory is deliberately non-writable after its manifest is
    written.  Some filesystems reject a directory rename in that state, so
    temporarily restore owner-write permission solely for the rename, then
    re-seal the published destination before it is verified or returned.
    """
    _directory(artifacts, "sealed artifact staging directory", 0o500)
    if destination.exists() or destination.is_symlink():
        raise VerificationError("engine artifact destination changed during build")
    os.chmod(artifacts, 0o700)
    try:
        os.replace(artifacts, destination)
    except OSError:
        try:
            os.chmod(artifacts, 0o500)
        except OSError:
            pass
        raise
    os.chmod(destination, 0o500)


def verify_artifacts(artifacts: Path, policy: dict[str, object], *, python: Path) -> dict[str, object]:
    _reject_symlinked_ancestors(artifacts, "engine artifact directory")
    _directory(artifacts, "engine artifact directory", 0o500)
    manifest_path = artifacts / _ARTIFACT_MANIFEST
    manifest_info = _regular_file(manifest_path, "artifact manifest")
    if stat.S_IMODE(manifest_info.st_mode) != 0o400:
        raise VerificationError("artifact manifest is mutable")
    try:
        document = _validate_artifact_document(json.loads(manifest_path.read_text(encoding="utf-8")), policy)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError("artifact manifest is invalid JSON") from exc
    python_identity = validate_python(python, str(policy["python_minor"]))
    if document["python_identity"] != python_identity:
        raise VerificationError("artifact manifest was produced by a different Python")
    wheel_record = document["wheel"]
    assert isinstance(wheel_record, dict)
    wheel = artifacts / str(wheel_record["filename"])
    wheel_info = _regular_file(wheel, "engine wheel")
    if stat.S_IMODE(wheel_info.st_mode) != 0o400:
        raise VerificationError("engine wheel is mutable")
    observed_names = {path.name for path in artifacts.iterdir()}
    if observed_names != {_ARTIFACT_MANIFEST, wheel.name}:
        raise VerificationError("engine artifact directory has missing or unexpected files")
    if wheel_info.st_size != wheel_record["size"] or _sha256(wheel) != wheel_record["sha256"]:
        raise VerificationError("engine wheel digest or size drift")
    observed_native, _requires_python, _tag = _wheel_members(wheel, policy)
    recorded_native = document["native_libraries"]
    assert isinstance(recorded_native, list)
    observed_paths = {str(item["path"]) for item in observed_native}
    recorded_paths = {str(item["path"]) for item in recorded_native}
    if observed_paths - recorded_paths:
        raise VerificationError("engine wheel has an unexpected native library")
    if recorded_paths - observed_paths:
        raise VerificationError("engine wheel is missing a manifested native library")
    if observed_native != recorded_native:
        raise VerificationError("engine native library digest or size drift")
    return document


def _wheel_metadata(path: Path) -> tuple[str, str]:
    try:
        with zipfile.ZipFile(path) as archive:
            names = [
                name
                for name in archive.namelist()
                if name.count("/") == 1 and name.endswith(".dist-info/METADATA")
            ]
            if len(names) != 1:
                raise VerificationError("cached wheel metadata layout is invalid")
            metadata = BytesParser().parsebytes(archive.read(names[0]))
    except (OSError, zipfile.BadZipFile) as exc:
        raise VerificationError("cached wheel is invalid") from exc
    name = metadata.get("Name", "").replace("_", "-").lower()
    version = metadata.get("Version", "")
    if not name or not version:
        raise VerificationError("cached wheel metadata is incomplete")
    return name, version


def verify_wheel_cache(cache: Path, expected_manifest_sha256: str, policy: dict[str, object]) -> dict[str, object]:
    expected_digest = _require_sha256(expected_manifest_sha256, "approved wheel cache manifest digest")
    _reject_symlinked_ancestors(cache, "wheel cache")
    _directory(cache, "wheel cache", 0o500)
    manifest_path = cache / _WHEEL_CACHE_MANIFEST
    info = _regular_file(manifest_path, "wheel cache manifest")
    if stat.S_IMODE(info.st_mode) != 0o400 or _sha256(manifest_path) != expected_digest:
        raise VerificationError("wheel cache manifest is mutable or not operator-approved")
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError("wheel cache manifest is invalid JSON") from exc
    if not isinstance(document, dict) or set(document) != _WHEEL_CACHE_FIELDS:
        raise VerificationError("wheel cache manifest fields are missing or unknown")
    if document["schema_version"] != 1 or document["python_minor"] != policy["python_minor"]:
        raise VerificationError("wheel cache targets the wrong Python")
    records = document.get("artifacts")
    if not isinstance(records, list) or not records:
        raise VerificationError("wheel cache manifest has no artifacts")
    expected_files = {_WHEEL_CACHE_MANIFEST}
    packages: dict[str, str] = {}
    for record in records:
        if not isinstance(record, dict) or set(record) != _WHEEL_CACHE_ARTIFACT_FIELDS:
            raise VerificationError("wheel cache artifact fields are invalid")
        filename = _safe_relative(record.get("filename"), "wheel cache filename")
        if "/" in filename or not filename.endswith(".whl") or filename in expected_files:
            raise VerificationError("wheel cache artifact filename is invalid or duplicate")
        expected_files.add(filename)
        if record.get("role") != "build":
            raise VerificationError("wheel cache has an unsupported artifact role")
        _require_sha256(record.get("sha256"), "wheel cache artifact digest")
        if not isinstance(record.get("size"), int) or int(record["size"]) <= 0:
            raise VerificationError("wheel cache artifact size is invalid")
        path = cache / filename
        artifact_info = _regular_file(path, "wheel cache artifact")
        if stat.S_IMODE(artifact_info.st_mode) != 0o400:
            raise VerificationError("wheel cache artifact is mutable")
        if artifact_info.st_size != record["size"] or _sha256(path) != record["sha256"]:
            raise VerificationError("wheel cache artifact digest or size drift")
        package, version = _wheel_metadata(path)
        if record.get("package") != package or record.get("version") != version or package in packages:
            raise VerificationError("wheel cache package metadata is invalid or duplicate")
        packages[package] = version
    observed_files = {path.name for path in cache.iterdir()}
    if observed_files != expected_files:
        raise VerificationError("wheel cache has missing or unexpected artifacts")
    required = policy["required_build_wheels"]
    assert isinstance(required, dict)
    if any(packages.get(name) != version for name, version in required.items()):
        raise VerificationError("wheel cache is missing an exact pinned build wheel")
    unpinned = policy["required_unpinned_build_wheels"]
    assert isinstance(unpinned, list)
    if any(name not in packages for name in unpinned):
        raise VerificationError("wheel cache is missing a required build wheel")
    if set(packages) != {*required, *unpinned}:
        raise VerificationError("wheel cache contains an unapproved build package")
    return document


def _validate_sandbox(sandbox: Path) -> str:
    _reject_symlinked_ancestors(sandbox, "network sandbox")
    info = _regular_file(sandbox, "network sandbox")
    if info.st_uid != 0 or info.st_mode & 0o022 or not info.st_mode & stat.S_IXUSR:
        raise VerificationError("network sandbox must be a root-owned, non-writable executable")
    identity = _run_identity([str(sandbox), "--version"], "network sandbox")
    if not identity.startswith("bubblewrap "):
        raise VerificationError("network sandbox must be Bubblewrap")
    return identity


def verify_sealed_input_bindings(
    *,
    policy: dict[str, object],
    artifact_manifest: dict[str, object],
    input_cache: Path,
    wheel_cache: Path,
    wheel_cache_manifest_sha256: str,
    cargo: Path,
    llvm_toolchain: Path,
    sandbox: Path,
    offline: bool,
) -> None:
    """Read-only verification of every sealed input bound to an engine artifact.

    The artifact manifest records the input-cache, wheel-cache, Cargo, and
    rustc identities.  This verifier checks those records against the supplied
    immutable inputs; it never materializes a toolchain or invokes a build.
    """
    if not offline:
        raise VerificationError("sealed input verification requires offline mode")
    for path, label in (
        (input_cache, "source/Cargo input cache"),
        (wheel_cache, "wheel cache"),
        (cargo, "private Cargo toolchain"),
        (llvm_toolchain, "private LLVM toolchain"),
        (sandbox, "network sandbox"),
    ):
        _require_external(path, label)
    _validate_sandbox(sandbox)
    input_tool = _load_input_cache_tool()
    input_policy = input_tool.load_policy(_INPUT_CACHE_POLICY)
    try:
        input_tool.verify(input_cache, input_policy)
        cargo_identity = input_tool.validate_private_cargo(cargo, str(policy["required_rust_version"]))
        rustc_identity = input_tool.validate_private_rustc(cargo.parent / "rustc", str(policy["required_rust_version"]))
    except (OSError, ValueError) as exc:
        raise VerificationError(f"Nautilus source/Cargo input verification failed: {exc}") from exc
    rust_tool = _load_rust_toolchain_tool()
    try:
        rust_manifest = rust_tool.load_manifest(_RUST_TOOLCHAIN_POLICY)
        rust_tool.verify_materialized_toolchain(_toolchain_root_for_cargo(cargo), rust_manifest)
    except (OSError, ValueError) as exc:
        raise VerificationError(f"private Rust toolchain verification failed: {exc}") from exc
    llvm_tool = _load_llvm_toolchain_tool()
    try:
        llvm_policy = llvm_tool.load_policy(_LLVM_TOOLCHAIN_POLICY)
        llvm_tool.verify_materialized(llvm_toolchain, llvm_policy)
    except (OSError, ValueError) as exc:
        raise VerificationError(f"private LLVM toolchain verification failed: {exc}") from exc
    verify_wheel_cache(wheel_cache, wheel_cache_manifest_sha256, policy)
    input_manifest_digest = _sha256(input_cache / "input-cache-manifest.json")
    if artifact_manifest["input_cache_manifest_sha256"] != input_manifest_digest:
        raise VerificationError("artifact manifest input-cache binding drift")
    if artifact_manifest["wheel_cache_manifest_sha256"] != wheel_cache_manifest_sha256:
        raise VerificationError("artifact manifest wheel-cache binding drift")
    if artifact_manifest["cargo_identity"] != cargo_identity:
        raise VerificationError("artifact manifest Cargo identity drift")
    if artifact_manifest["rustc_identity"] != rustc_identity:
        raise VerificationError("artifact manifest rustc identity drift")


def _sandbox_run(
    sandbox: Path,
    stage: Path,
    cwd: Path,
    environment: dict[str, str],
    command: list[str],
    *,
    timeout: int,
) -> None:
    invocation = [
        str(sandbox),
        "--die-with-parent",
        "--unshare-net",
        "--ro-bind",
        "/",
        "/",
        "--bind",
        str(stage),
        str(stage),
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--chdir",
        str(cwd),
        "--clearenv",
    ]
    for key, value in sorted(environment.items()):
        invocation.extend(("--setenv", key, value))
    invocation.extend(("--", *command))
    try:
        subprocess.run(invocation, check=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise VerificationError("offline sandboxed engine build command failed") from exc


def _candidate_json(path: Path) -> dict[str, object]:
    try:
        return _load_candidate_generator().load_json(path)
    except (OSError, RuntimeError, ValueError) as exc:
        raise VerificationError(f"invalid candidate authority: {path}") from exc


def _candidate_roots(engine: dict[str, object]) -> dict[str, Path]:
    isolation = engine.get("external_cache_isolation")
    if not isinstance(isolation, dict) or not isinstance(isolation.get("external_roots"), dict):
        raise VerificationError("candidate external-root authority is invalid")
    roots = {name: Path(value) for name, value in isolation["external_roots"].items() if isinstance(value, str)}
    if len(roots) != len(isolation["external_roots"]) or any(not path.is_absolute() for path in roots.values()):
        raise VerificationError("candidate external roots are not exact absolute paths")
    return roots


def _candidate_forensic_destination(
    engine: dict[str, object],
    *,
    expected_parent_identity: tuple[int, int] | None = None,
) -> tuple[Path, tuple[int, int]]:
    destination = _candidate_roots(engine).get("candidate_forensic_root")
    if destination is None:
        raise VerificationError("candidate forensic root authority is missing")
    if destination.exists() or destination.is_symlink():
        raise VerificationError("candidate forensic destination is not absent")
    try:
        parent = destination.parent.lstat()
    except OSError as exc:
        raise VerificationError("candidate forensic parent is unavailable") from exc
    parent_identity = (parent.st_dev, parent.st_ino)
    if (
        not stat.S_ISDIR(parent.st_mode)
        or stat.S_ISLNK(parent.st_mode)
        or parent.st_uid != os.geteuid()
        or stat.S_IMODE(parent.st_mode) != 0o700
        or (
            expected_parent_identity is not None
            and parent_identity != expected_parent_identity
        )
    ):
        raise VerificationError("candidate forensic parent is not stable and private")
    return destination, parent_identity


def _candidate_environment(
    engine: dict[str, object], logical_stage: Path, source_fd: int
) -> tuple[dict[str, str], dict[str, str]]:
    policy = engine.get("native_build_environment")
    if not isinstance(policy, dict):
        raise VerificationError("candidate environment authority is invalid")
    generator = _load_candidate_generator()
    try:
        contract = generator._verify_build_environment(
            policy,
            {},
            logical_stage,
            verified_source_fd=source_fd,
            mount_destinations=[logical_stage],
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise VerificationError(f"U03 candidate build environment rejected: {exc}") from exc
    environments = tuple(contract.get(name) for name in ("initial_environment", "effective_environment"))
    if any(
        not isinstance(environment, dict)
        or not all(isinstance(name, str) and isinstance(value, str) for name, value in environment.items())
        for environment in environments
    ):
        raise VerificationError("U03 candidate build environments are invalid")
    return environments  # type: ignore[return-value]


def _candidate_mounts(engine: dict[str, object]) -> tuple[Path, ...]:
    roots = _candidate_roots(engine)
    admitted = (
        Path("/usr/bin/python3.12"),
        Path("/usr/lib/python3.12"),
        Path("/usr/lib/x86_64-linux-gnu"),
        Path("/usr/lib/gcc/x86_64-linux-gnu/13"),
        Path("/usr/libexec/gcc/x86_64-linux-gnu/13"),
        Path("/usr/include"),
        Path("/usr/local/include"),
        Path("/usr/bin/ar"),
        Path("/usr/bin/ld"),
        Path("/usr/bin/strip"),
        Path("/usr/bin/x86_64-linux-gnu-ar"),
        Path("/usr/bin/x86_64-linux-gnu-ld"),
        Path("/usr/bin/x86_64-linux-gnu-ld.bfd"),
        Path("/usr/bin/x86_64-linux-gnu-strip"),
        roots["candidate_input_root"] / "wheels",
        roots["candidate_cargo_home_root"],
        roots["candidate_vendor_root"],
        roots["candidate_llvm_toolchain_root"],
        roots["candidate_rust_toolchain_root"],
        roots["candidate_toolchain_root"],
    )
    rollback = roots["rollback_root"]
    if any(path == rollback or rollback in path.parents for path in admitted):
        raise VerificationError("rollback root cannot be mounted into the candidate sandbox")
    return admitted


def _candidate_command(
    action: str,
    logical_stage: Path,
    inputs: dict[str, object],
    environment: dict[str, str] | None = None,
) -> tuple[str, ...]:
    python = "/usr/bin/python3.12"
    venv_python = str(logical_stage / "venv/bin/python")
    wheels = inputs.get("build_wheels")
    if not isinstance(wheels, list) or not all(isinstance(record, dict) for record in wheels):
        raise VerificationError("candidate build wheel authority is invalid")
    wheel_root = Path(str(inputs["python"]["explicit_build_path_admission"]["root"]))  # type: ignore[index]
    filenames = [str(record["filename"]) for record in wheels]
    pip_names = [str(record["filename"]) for record in wheels if record.get("package") == "pip"]
    if len(pip_names) != 1:
        raise VerificationError("candidate build authority must contain one pip wheel")
    if action == "venv":
        return (python, "-I", "-m", "venv", "--without-pip", "--copies", str(logical_stage / "venv"))
    if action == "install":
        return (
            python,
            "-I",
            "-c",
            _PIP_BOOTSTRAP,
            str(wheel_root / pip_names[0]),
            "--python",
            venv_python,
            "install",
            "--no-index",
            "--no-deps",
            "--no-cache-dir",
            *(str(wheel_root / filename) for filename in filenames),
        )
    if action == "native":
        return (
            venv_python,
            "-I",
            str(logical_stage / "source/build.py"),
        )
    if action == "package":
        python_policy = inputs.get("python")
        if not isinstance(python_policy, dict) or python_policy.get("admitted_sys_path") != [
            "/usr/lib/python312.zip",
            "/usr/lib/python3.12",
            "/usr/lib/python3.12/lib-dynload",
        ]:
            raise VerificationError("candidate package Python path authority is invalid")
        site_packages = logical_stage / "venv/lib/python3.12/site-packages"
        package_script = (
            "import hashlib,sys\n"
            "from pathlib import Path\n"
            f"expected_stdlib={python_policy['admitted_sys_path']!r}\n"
            "if sys.path != expected_stdlib: raise RuntimeError('ambient package site is not prohibited')\n"
            f"site=Path({str(site_packages)!r})\n"
            "vendor=site/'poetry/core/_vendor'\n"
            "sys.path.insert(0,str(site))\n"
            "import poetry.core.factory as factory_module\n"
            "import packaging as packaging_module\n"
            "import poetry.core as core_module\n"
            "import poetry.core.masonry.builders.builder as builder_module\n"
            "import poetry.core.masonry.builders.wheel as wheel_module\n"
            "import poetry.core.masonry.metadata as metadata_module\n"
            "import poetry.core.vcs as vcs_module\n"
            "from poetry.core.factory import Factory\n"
            "from poetry.core.masonry.builders.wheel import WheelBuilder\n"
            f"modules={{core_module:('poetry/core/__init__.py',{_CANDIDATE_POETRY_MODULES['poetry/core/__init__.py']!r}),packaging_module:('poetry/core/_vendor/packaging/__init__.py',{_CANDIDATE_POETRY_MODULES['poetry/core/_vendor/packaging/__init__.py']!r}),factory_module:('poetry/core/factory.py',{_CANDIDATE_POETRY_MODULES['poetry/core/factory.py']!r}),builder_module:('poetry/core/masonry/builders/builder.py',{_CANDIDATE_POETRY_MODULES['poetry/core/masonry/builders/builder.py']!r}),wheel_module:('poetry/core/masonry/builders/wheel.py',{_CANDIDATE_POETRY_MODULES['poetry/core/masonry/builders/wheel.py']!r}),metadata_module:('poetry/core/masonry/metadata.py',{_CANDIDATE_POETRY_MODULES['poetry/core/masonry/metadata.py']!r}),vcs_module:('poetry/core/vcs/__init__.py',{_CANDIDATE_POETRY_MODULES['poetry/core/vcs/__init__.py']!r})}}\n"
            "for module,(relative,digest) in modules.items():\n"
            "    path=Path(module.__file__)\n"
            "    if path != site/relative or hashlib.sha256(path.read_bytes()).hexdigest() != digest:\n"
            "        raise RuntimeError('Poetry module origin or hash drifted')\n"
            "if sys.path != [str(vendor),str(site),*expected_stdlib]: raise RuntimeError('package import path drifted')\n"
            "vcs_calls=0\n"
            "def no_vcs(path):\n"
            "    global vcs_calls\n"
            "    if Path(path).resolve() != Path.cwd().resolve(): raise RuntimeError('foreign VCS lookup')\n"
            "    vcs_calls += 1\n"
            "    return None\n"
            "vcs_module.get_vcs = no_vcs\n"
            "class PrebuiltWheelBuilder(WheelBuilder):\n"
            "    hook_calls=0\n"
            "    def _run_build_script(self, build_script):\n"
            "        if build_script != 'build.py':\n"
            "            raise RuntimeError('unexpected Poetry build script')\n"
            "        self.hook_calls += 1\n"
            "poetry = Factory().create_poetry(Path().resolve(), with_groups=False)\n"
            f"builder=PrebuiltWheelBuilder(poetry, executable=Path({venv_python!r}))\n"
            f"builder.build(target_dir=Path({str(logical_stage / 'dist')!r}))\n"
            "if builder.hook_calls != 1: raise RuntimeError('local prebuilt hook divergence')\n"
            "if vcs_calls != 1: raise RuntimeError('local no-VCS hook divergence')\n"
        )
        return (
            venv_python,
            "-I",
            "-S",
            "-c",
            package_script,
        )
    if action == "policy-probe":
        if environment is None:
            raise VerificationError("candidate policy probe environment is missing")
        probe = (
            "import os,sys,sysconfig; "
            f"expected={environment!r}; "
            "assert dict(os.environ)==expected; "
            "s=os.stat('.'); "
            "assert (str(s.st_dev),str(s.st_ino))==(os.environ['P1_U04_SOURCE_ST_DEV'],os.environ['P1_U04_SOURCE_ST_INO']); "
            "assert not os.path.exists('/usr/bin/gcc'); "
            "assert not os.path.exists('/usr/bin/git'); "
            "assert not os.path.exists('/home/thenam176/.cache/trading-agent/nautilus'); "
            "assert os.listdir('/lib64')==['ld-linux-x86-64.so.2']; "
            "assert os.path.samefile('/lib64/ld-linux-x86-64.so.2',"
            "'/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2'); "
            "assert sys.path==['/usr/lib/python312.zip','/usr/lib/python3.12','/usr/lib/python3.12/lib-dynload']; "
            f"assert sysconfig.get_config_var('EXT_SUFFIX')=={_CANDIDATE_EXT_SUFFIX!r}"
        )
        return (python, "-I", "-S", "-c", probe)
    raise VerificationError("candidate sandbox action is not exact")


def _candidate_sandbox_run(
    *,
    physical_stage: Path,
    logical_stage: Path,
    action: str,
    timeout: int = 7200,
    expected_source_identity: dict[str, str] | None = None,
) -> dict[str, str]:
    """Run one fixed candidate action through the reviewed verified-fd handoff."""
    engine = _candidate_json(_CANDIDATE_ENGINE_POLICY)
    inputs = _candidate_json(_CANDIDATE_TOOLCHAIN_INPUTS)
    _validate_sandbox(_CANDIDATE_SANDBOX)
    stage_flags = os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW
    stage_fd = os.open(physical_stage, stage_flags)
    source_fd = -1
    try:
        stage_info = os.fstat(stage_fd)
        if not stat.S_ISDIR(stage_info.st_mode) or stat.S_IMODE(stage_info.st_mode) != 0o700:
            raise VerificationError("physical candidate stage is not a private directory")
        try:
            source_fd = os.open("source", stage_flags, dir_fd=stage_fd)
        except OSError as exc:
            raise VerificationError("offline candidate sandbox command failed") from exc
        observed_source_identity = _candidate_source_identity_from_stat(os.fstat(source_fd))
        if expected_source_identity is None:
            if action != "policy-probe":
                raise VerificationError("candidate source directory identity is missing")
            expected_source_identity = observed_source_identity
        if observed_source_identity != expected_source_identity:
            raise VerificationError("candidate source directory identity drifted")
        initial_environment, effective_environment = _candidate_environment(
            engine, logical_stage, source_fd
        )
        environment = (
            effective_environment
            if action in {"install", "package"}
            else initial_environment
        )
        mounts = _candidate_mounts(engine)
        command = _candidate_command(action, logical_stage, inputs, environment)
        invocation = [
            str(_CANDIDATE_SANDBOX),
            "--die-with-parent",
            "--unshare-all",
            "--new-session",
            "--tmpfs",
            "/",
            "--dev",
            "/dev",
            "--proc",
            "/proc",
            "--symlink",
            "usr/lib",
            "/lib",
        ]
        directories: set[Path] = {Path("/"), Path("/lib64")}
        for destination in (*mounts, logical_stage):
            parent = destination.parent
            while parent != Path("/"):
                directories.add(parent)
                parent = parent.parent
        for directory in sorted(directories, key=lambda item: (len(item.parts), str(item))):
            if directory != Path("/") and directory != Path("/lib"):
                invocation.extend(("--dir", str(directory)))
        invocation.extend(
            (
                "--symlink",
                "../usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2",
                "/lib64/ld-linux-x86-64.so.2",
            )
        )
        for mount in mounts:
            invocation.extend(("--ro-bind", str(mount), str(mount)))
        invocation.extend(("--bind-fd", str(stage_fd), str(logical_stage)))
        invocation.extend(("--chdir", str(logical_stage / "source"), "--clearenv"))
        for key, value in sorted(environment.items()):
            if key != "PWD":
                invocation.extend(("--setenv", key, value))
        invocation.extend(("--", *command))
        command_error: BaseException | None = None
        try:
            subprocess.run(
                invocation,
                check=True,
                timeout=timeout,
                env={},
                pass_fds=(stage_fd,),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            command_error = exc
        try:
            final_descriptor_identity = _candidate_source_identity_from_stat(
                os.fstat(source_fd)
            )
            final_path_identity = _candidate_source_identity_from_stat(
                os.stat("source", dir_fd=stage_fd, follow_symlinks=False)
            )
        except (OSError, VerificationError) as exc:
            raise VerificationError("candidate source directory identity drifted") from exc
        if (
            final_descriptor_identity != expected_source_identity
            or final_path_identity != expected_source_identity
        ):
            raise VerificationError("candidate source directory identity drifted")
        if command_error is not None:
            raise VerificationError("offline candidate sandbox command failed") from command_error
    finally:
        if source_fd >= 0:
            os.close(source_fd)
        os.close(stage_fd)
    return {
        key: expected_source_identity[key]
        for key in ("P1_U04_SOURCE_ST_DEV", "P1_U04_SOURCE_ST_INO")
    }


def _candidate_source_identity_from_stat(info: os.stat_result) -> dict[str, str]:
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise VerificationError("candidate source directory identity drifted")
    return {
        "P1_U04_SOURCE_ST_UID": str(info.st_uid),
        "P1_U04_SOURCE_TYPE": "directory",
        "P1_U04_SOURCE_MODE": "0700",
        "P1_U04_SOURCE_ST_DEV": str(info.st_dev),
        "P1_U04_SOURCE_ST_INO": str(info.st_ino),
    }


def _candidate_source_identity(source: Path) -> dict[str, str]:
    flags = os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        descriptor = os.open(source, flags)
    except OSError as exc:
        raise VerificationError("candidate source directory identity drifted") from exc
    try:
        return _candidate_source_identity_from_stat(os.fstat(descriptor))
    finally:
        os.close(descriptor)


def _thaw_tree(root: Path) -> None:
    for current, directories, names in os.walk(root):
        for name in names:
            os.chmod(Path(current) / name, 0o600)
        for directory in directories:
            os.chmod(Path(current) / directory, 0o700)
    os.chmod(root, 0o700)


def _load_candidate_generator():
    spec = importlib.util.spec_from_file_location(
        "write_nautilus_toolchain_inputs", _CANDIDATE_INPUT_GENERATOR
    )
    if spec is None or spec.loader is None:
        raise VerificationError("candidate authority verifier is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _candidate_runtime_authority_module(name: str) -> bool:
    return name in {"packages", "services"} or name.startswith(
        ("packages.", "services.")
    )


def _load_candidate_runtime_closure_tool():
    try:
        root = _ROOT.resolve(strict=True)
        _regular_file(
            _CANDIDATE_RUNTIME_CLOSURE_TOOL,
            "candidate rollback authority verifier",
        )
        tool = _CANDIDATE_RUNTIME_CLOSURE_TOOL.resolve(strict=True)
        if not tool.is_relative_to(root) or tool.suffix != ".py":
            raise VerificationError(
                "candidate rollback authority verifier provenance is invalid"
            )
        if any(_candidate_runtime_authority_module(name) for name in sys.modules):
            raise VerificationError(
                "candidate rollback authority verifier has preloaded checkout imports"
            )
        spec = importlib.util.spec_from_file_location(
            "materialize_nautilus_runtime_closure_for_candidate_build",
            tool,
        )
        if spec is None or spec.loader is None:
            raise VerificationError(
                "candidate rollback authority verifier is unavailable"
            )
        module = importlib.util.module_from_spec(spec)
        code = compile(tool.read_bytes(), str(tool), "exec", dont_inherit=True)
        original_path = tuple(sys.path)
        original_dont_write_bytecode = sys.dont_write_bytecode
        original_pycache_prefix = sys.pycache_prefix
        try:
            sys.path[:] = [str(root), *(path for path in original_path if path != str(root))]
            sys.dont_write_bytecode = True
            sys.pycache_prefix = os.devnull
            exec(code, module.__dict__)
            for name, imported in tuple(sys.modules.items()):
                if not _candidate_runtime_authority_module(name):
                    continue
                source = getattr(imported, "__file__", None)
                if (
                    not isinstance(source, str)
                    or Path(source).resolve(strict=True).suffix != ".py"
                    or not Path(source).resolve(strict=True).is_relative_to(root)
                ):
                    raise VerificationError(
                        "candidate rollback authority verifier import provenance is invalid"
                    )
        finally:
            for name in tuple(sys.modules):
                if _candidate_runtime_authority_module(name):
                    sys.modules.pop(name, None)
            sys.path[:] = original_path
            sys.dont_write_bytecode = original_dont_write_bytecode
            sys.pycache_prefix = original_pycache_prefix
    except VerificationError:
        raise
    except Exception as exc:
        raise VerificationError(
            "candidate rollback authority verifier is unavailable"
        ) from exc
    return module


def _candidate_live_rollback_authority(rollback_root: Path) -> dict[str, object]:
    materializer = _load_candidate_runtime_closure_tool()
    try:
        policy = materializer._load_policy(materializer._CANDIDATE_BASE_POLICY)
        historical_manifest, historical_records = materializer._validate_base_runtime(
            rollback_root / "runtime-closure-v3", policy
        )
        selected = materializer._selected_base_authority(
            rollback_root,
            base_policy=policy,
            historical_manifest=historical_manifest,
            historical_records=historical_records,
        )
        return {
            "artifact_generation": selected["artifact_generation"],
            "artifact_manifest_sha256": selected["artifact_manifest_sha256"],
            "closure_sha256": selected["closure_sha256"],
            "generation": selected["generation"],
            "manifest_mode": selected["manifest_mode"],
            "manifest_sha256": selected["manifest_sha256"],
            "result": "PASS",
            "schema": 6,
        }
    except Exception as exc:
        raise VerificationError(
            "X4 authority receipt live rollback authority is invalid"
        ) from exc


def _verify_candidate_authority() -> tuple[dict[str, object], dict[str, object]]:
    engine = _candidate_json(_CANDIDATE_ENGINE_POLICY)
    manifest = _candidate_json(_CANDIDATE_TOOLCHAIN_INPUTS)
    roots = _candidate_roots(engine)
    hashes = manifest.get("policy_hashes")
    expected_paths = {
        "engine_build_policy_sha256": _CANDIDATE_ENGINE_POLICY,
        "input_cache_policy_sha256": _CANDIDATE_INPUT_POLICY,
        "wheel_cache_policy_sha256": _CANDIDATE_WHEEL_POLICY,
        "cargo_registry_policy_sha256": _CANDIDATE_CARGO_POLICY,
        "generator_sha256": _CANDIDATE_INPUT_GENERATOR,
        "llvm_toolchain_policy_sha256": _LLVM_TOOLCHAIN_POLICY,
        "llvm_toolchain_validator_sha256": _LLVM_TOOLCHAIN_TOOL,
        "release_provenance_policy_sha256": _CANDIDATE_PROVENANCE,
    }
    if not isinstance(hashes, dict):
        raise VerificationError("candidate policy-hash authority is invalid")
    for name, path in expected_paths.items():
        if hashes.get(name) != _sha256(path):
            raise VerificationError(f"candidate {name} authority drifted")
    provenance = _candidate_json(_CANDIDATE_PROVENANCE)
    if (
        provenance.get("candidate_closure_schema") != 7
        or provenance.get("engine_version") != "1.231.0"
        or provenance.get("activation_status") != "CANDIDATE_ONLY_NOT_ACTIVATED"
        or engine.get("activation_status") != "POLICY_ONLY_NOT_ACTIVATED"
    ):
        raise VerificationError("candidate provenance or activation boundary drifted")
    input_root = roots["candidate_input_root"]
    generator = _load_candidate_generator()
    try:
        regenerated = generator.generate(input_root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise VerificationError(f"U03 candidate input verification failed: {exc}") from exc
    if regenerated != manifest:
        raise VerificationError("committed U03 candidate manifest does not match sealed inputs")
    if roots["rollback_root"] in input_root.parents or input_root == roots["rollback_root"]:
        raise VerificationError("candidate input authority overlaps rollback")
    return engine, manifest


def _candidate_git_identity() -> dict[str, str]:
    command = [
        "/usr/bin/git",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.hooksPath=/dev/null",
        "-C",
        str(_ROOT),
    ]
    environment = {"LC_ALL": "C", "LANG": "C"}

    def run(*arguments: str) -> str:
        try:
            result = subprocess.run(
                [*command, *arguments],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
                env=environment,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise VerificationError("X4 authority receipt Git identity failed") from exc
        return result.stdout.strip()

    if run("status", "--porcelain=v1", "--untracked-files=no"):
        raise VerificationError("X4 authority receipt requires a clean tracked tree")
    identity = {
        "head": run("rev-parse", "HEAD"),
        "tree": run("rev-parse", "HEAD^{tree}"),
    }
    if any(re.fullmatch(r"[0-9a-f]{40}", value) is None for value in identity.values()):
        raise VerificationError("X4 authority receipt Git identity is invalid")
    return identity


def _candidate_process_identity() -> dict[str, object]:
    try:
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(
            encoding="ascii"
        ).strip()
        process_stat = Path("/proc/self/stat").read_text(encoding="ascii")
        fields = process_stat.rsplit(")", 1)[1].split()
        start_time_ticks = int(fields[19])
    except (OSError, UnicodeDecodeError, IndexError, ValueError) as exc:
        raise VerificationError("candidate process identity is unavailable") from exc
    identity: dict[str, object] = {
        "boot_id": boot_id,
        "pid": os.getpid(),
        "start_time_ticks": start_time_ticks,
    }
    _validate_candidate_process_identity(identity)
    return identity


def _validate_candidate_process_identity(identity: object) -> None:
    if (
        not isinstance(identity, dict)
        or set(identity) != {"boot_id", "pid", "start_time_ticks"}
        or not isinstance(identity.get("boot_id"), str)
        or re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            identity["boot_id"],
        )
        is None
        or any(
            not isinstance(identity.get(field), int)
            or isinstance(identity[field], bool)
            or identity[field] <= 0
            for field in ("pid", "start_time_ticks")
        )
    ):
        raise VerificationError("candidate process identity is invalid")


def _validate_candidate_source_identity(identity: object) -> None:
    fields = {"P1_U04_SOURCE_ST_DEV", "P1_U04_SOURCE_ST_INO"}
    if (
        not isinstance(identity, dict)
        or set(identity) != fields
        or any(
            not isinstance(value, str)
            or not value.isascii()
            or not value.isdecimal()
            or value.startswith("0")
            for value in identity.values()
        )
    ):
        raise VerificationError("candidate source identity is invalid")


def _candidate_external_identities(
    engine: dict[str, object], inputs: dict[str, object]
) -> dict[str, object]:
    roots = _candidate_roots(engine)
    python = inputs.get("python")
    rust = engine.get("rust")
    llvm = engine.get("llvm_toolchain")
    if not all(isinstance(value, dict) for value in (python, rust, llvm)):
        raise VerificationError("X4 authority receipt identity policy is invalid")
    assert isinstance(python, dict) and isinstance(rust, dict) and isinstance(llvm, dict)

    python_path = Path(str(python["executable"]))
    python_stat = _regular_file(python_path, "candidate CPython")
    sandbox_stat = _regular_file(_CANDIDATE_SANDBOX, "candidate Bubblewrap")
    cargo = roots["candidate_rust_toolchain_root"] / "bin/cargo"
    rustc = roots["candidate_rust_toolchain_root"] / "bin/rustc"
    clang = roots["candidate_llvm_toolchain_root"] / "bin/clang"
    cargo_stat = _regular_file(cargo, "candidate Cargo")
    clang_stat = _regular_file(clang, "candidate LLVM")
    _regular_file(rustc, "candidate rustc")
    _verify_candidate_rust(inputs, roots["candidate_rust_toolchain_root"])
    llvm_tool = _load_llvm_toolchain_tool()
    llvm_policy = llvm_tool.load_policy(_LLVM_TOOLCHAIN_POLICY)
    llvm_tool.verify_materialized(roots["candidate_llvm_toolchain_root"], llvm_policy)
    return {
        "bubblewrap": {
            "gid": sandbox_stat.st_gid,
            "mode": f"{stat.S_IMODE(sandbox_stat.st_mode):04o}",
            "owner": sandbox_stat.st_uid,
            "sha256": _sha256(_CANDIDATE_SANDBOX),
            "size": sandbox_stat.st_size,
            "version": _validate_sandbox(_CANDIDATE_SANDBOX),
        },
        "cargo": {
            "gid": cargo_stat.st_gid,
            "mode": f"{stat.S_IMODE(cargo_stat.st_mode):04o}",
            "owner": cargo_stat.st_uid,
            "size": cargo_stat.st_size,
            "version": _run_identity([str(cargo), "--version"], "candidate Cargo"),
        },
        "cpython": {
            "gid": python_stat.st_gid,
            "mode": f"{stat.S_IMODE(python_stat.st_mode):04o}",
            "owner": python_stat.st_uid,
            "sha256": _sha256(python_path),
            "size": python_stat.st_size,
            "version": python["identity"],
        },
        "llvm": {
            "mode": f"{stat.S_IMODE(clang_stat.st_mode):04o}",
            "owner": clang_stat.st_uid,
            "version": f"clang {llvm['version']}",
        },
        "rustc": {
            "version": _run_identity([str(rustc), "--version"], "candidate rustc")
        },
    }


def _candidate_policy_receipt(inputs: dict[str, object]) -> dict[str, str]:
    hashes = inputs.get("policy_hashes")
    if not isinstance(hashes, dict):
        raise VerificationError("X4 authority receipt policy binding is invalid")
    mapping = {
        "cargo_registry": "cargo_registry_policy_sha256",
        "engine_build": "engine_build_policy_sha256",
        "input_cache": "input_cache_policy_sha256",
        "release_provenance": "release_provenance_policy_sha256",
        "wheel_cache": "wheel_cache_policy_sha256",
    }
    receipt = {name: hashes.get(source) for name, source in mapping.items()}
    if any(
        not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None
        for value in receipt.values()
    ):
        raise VerificationError("X4 authority receipt policy binding is invalid")
    return receipt  # type: ignore[return-value]


def _read_x4_receipt_file(
    path: Path, *, label: str, maximum_size: int = 1024 * 1024
) -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) != 0o400
            or before.st_nlink != 1
            or before.st_size > maximum_size
        ):
            raise VerificationError(f"X4 authority receipt {label} identity is invalid")
        with os.fdopen(descriptor, "rb", closefd=True) as source:
            descriptor = -1
            raw = source.read(before.st_size + 1)
            after = os.fstat(source.fileno())
    except VerificationError:
        raise
    except OSError as exc:
        raise VerificationError(f"X4 authority receipt {label} is unavailable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if (
        len(raw) != before.st_size
        or (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_nlink,
            after.st_size,
            after.st_uid,
        )
        != (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_nlink,
            before.st_size,
            before.st_uid,
        )
    ):
        raise VerificationError(f"X4 authority receipt {label} identity drifted")
    return raw


def _validate_x4_authority_receipt(
    path: Path,
    expected_sha256: str,
    *,
    phase: str,
) -> dict[str, object]:
    if phase not in {"A", "B", "FINAL"}:
        raise VerificationError("X4 authority receipt phase is invalid")
    if re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
        raise VerificationError("X4 authority receipt digest is invalid")
    raw = _read_x4_receipt_file(path, label="file")
    if _sha256_bytes(raw) != expected_sha256:
        raise VerificationError("X4 authority receipt digest mismatched")
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError("X4 authority receipt JSON is invalid") from exc
    if (
        not isinstance(document, dict)
        or raw != (json.dumps(document, sort_keys=True, indent=2) + "\n").encode("ascii")
        or set(document)
        != {
            "build_a_authorized",
            "candidate",
            "checks",
            "complete_authority_receipt",
            "identities",
            "policy_sha256",
            "recorded_at_utc",
            "review_round",
            "schema",
            "verdict",
        }
        or document.get("schema") != _X4_RECEIPT_SCHEMA
        or document.get("verdict") != "X4_READY_FOR_BUILD_A"
        or document.get("build_a_authorized") is not True
        or not isinstance(document.get("review_round"), int)
        or isinstance(document["review_round"], bool)
        or document["review_round"] <= 0
        or not isinstance(document.get("recorded_at_utc"), str)
        or re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", document["recorded_at_utc"])
        is None
    ):
        raise VerificationError("X4 authority receipt object is invalid")

    candidate = document.get("candidate")
    complete = document.get("complete_authority_receipt")
    if (
        not isinstance(candidate, dict)
        or set(candidate) != {"head", "tree"}
        or candidate != _candidate_git_identity()
        or not isinstance(complete, dict)
        or set(complete) != {"path", "sha256", "size"}
        or complete.get("path") != _X4_COMPLETE_AUTHORITY_RECEIPT_PATH
        or not isinstance(complete.get("sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", complete["sha256"]) is None
        or not isinstance(complete.get("size"), int)
        or isinstance(complete["size"], bool)
        or complete["size"] <= 0
    ):
        raise VerificationError("X4 authority receipt source binding is invalid")
    complete_raw = _read_x4_receipt_file(
        _X4_COMPLETE_AUTHORITY_RECEIPT, label="complete authority file"
    )
    if (
        len(complete_raw) != complete["size"]
        or _sha256_bytes(complete_raw) != complete["sha256"]
    ):
        raise VerificationError("X4 authority receipt complete authority binding drifted")

    engine, inputs = _verify_candidate_authority()
    if (
        document.get("policy_sha256") != _candidate_policy_receipt(inputs)
        or document.get("identities") != _candidate_external_identities(engine, inputs)
    ):
        raise VerificationError("X4 authority receipt external authority drifted")
    roots = _candidate_roots(engine)
    build_root = roots["candidate_build_root"]
    try:
        build_parent = build_root.lstat()
    except OSError as exc:
        raise VerificationError("X4 authority receipt build parent is unavailable") from exc
    if (
        not stat.S_ISDIR(build_parent.st_mode)
        or stat.S_ISLNK(build_parent.st_mode)
        or build_parent.st_uid != os.geteuid()
        or stat.S_IMODE(build_parent.st_mode) != 0o700
    ):
        raise VerificationError("X4 authority receipt build parent is not private")
    checks = document.get("checks")
    build_parent_check = checks.get("build_parent") if isinstance(checks, dict) else None
    output_roots = (
        checks.get("candidate_output_roots") if isinstance(checks, dict) else None
    )
    host_lane = checks.get("host_authority_lane") if isinstance(checks, dict) else None
    release = checks.get("release_provenance") if isinstance(checks, dict) else None
    rollback = checks.get("rollback_authority") if isinstance(checks, dict) else None
    toolchain = checks.get("toolchain_inputs") if isinstance(checks, dict) else None
    candidate_input = inputs.get("candidate")
    source = inputs.get("source")
    source_artifact = source.get("artifact") if isinstance(source, dict) else None
    provenance = _candidate_json(_CANDIDATE_PROVENANCE)
    provenance_upstream = provenance.get("upstream")
    release_assets = provenance.get("release_assets")
    cpython_wheel = (
        release_assets.get("cpython312_linux_wheel")
        if isinstance(release_assets, dict)
        else None
    )
    expected_release_check = {
        "exit_code": 0,
        "network": "DISABLED_BY_CONSTRUCTION",
        "peeled_commit": (
            provenance_upstream.get("peeled_commit")
            if isinstance(provenance_upstream, dict)
            else None
        ),
        "primary_sha256": (
            source_artifact.get("sha256")
            if isinstance(source_artifact, dict)
            else None
        ),
        "result": "PASS",
        "tag_object": (
            provenance_upstream.get("tag_object")
            if isinstance(provenance_upstream, dict)
            else None
        ),
        "wheel_sha256": (
            cpython_wheel.get("sha256") if isinstance(cpython_wheel, dict) else None
        ),
    }
    if (
        not isinstance(checks, dict)
        or set(checks)
        != {
            "ambient_fallback_reachable",
            "build_parent",
            "candidate_output_roots",
            "host_authority_lane",
            "network_capability",
            "release_provenance",
            "rollback_authority",
            "roots_disjoint",
            "toolchain_inputs",
        }
        or checks.get("ambient_fallback_reachable") is not False
        or checks.get("network_capability")
        != "DISABLED_BY_BUBBLEWRAP_UNSHARE_ALL"
        or checks.get("roots_disjoint") is not True
        or not isinstance(build_parent_check, dict)
        or set(build_parent_check) != {"empty", "gid", "mode", "owner"}
        or build_parent_check
        != {
            "empty": True,
            "gid": build_parent.st_gid,
            "mode": "0700",
            "owner": build_parent.st_uid,
        }
        or output_roots
        != {
            "artifact_root": "ABSENT",
            "closure_root": "ABSENT",
            "forensic_root": "ABSENT",
        }
        or host_lane
        != {
            "environment": {"TEMP": "/tmp", "TMP": "/tmp", "TMPDIR": "/tmp"},
            "exit_code": 0,
            "reason": "HOST_TESTS_PASSED",
            "result": "PASS",
        }
        or release != expected_release_check
        or not isinstance(candidate_input, dict)
        or release.get("peeled_commit") != candidate_input.get("upstream_commit")
        or not isinstance(source_artifact, dict)
        or release.get("primary_sha256") != source_artifact.get("sha256")
        or not isinstance(rollback, dict)
        or set(rollback)
        != {
            "artifact_generation",
            "artifact_manifest_sha256",
            "closure_sha256",
            "generation",
            "manifest_mode",
            "manifest_sha256",
            "result",
            "schema",
        }
        or rollback.get("result") != "PASS"
        or rollback.get("schema") != 6
        or rollback.get("manifest_mode") != "0400"
        or any(
            not isinstance(rollback.get(field), str) or not rollback[field]
            for field in ("artifact_generation", "generation")
        )
        or any(
            not isinstance(rollback.get(field), str)
            or re.fullmatch(r"[0-9a-f]{64}", rollback[field]) is None
            for field in (
                "artifact_manifest_sha256",
                "closure_sha256",
                "manifest_sha256",
            )
        )
        or toolchain
        != {
            "exit_code": 0,
            "result": "PASS",
            "sha256": _sha256(_CANDIDATE_TOOLCHAIN_INPUTS),
        }
    ):
        raise VerificationError("X4 authority receipt checks are invalid")
    if rollback != _candidate_live_rollback_authority(roots["rollback_root"]):
        raise VerificationError("X4 authority receipt live rollback authority drifted")
    expected_names = {
        "A": set(),
        "B": {_BUILD_A_DIRECTORY},
        "FINAL": {_BUILD_A_DIRECTORY, _BUILD_B_DIRECTORY},
    }[phase]
    if {entry.name for entry in build_root.iterdir()} != expected_names:
        raise VerificationError("X4 authority receipt candidate root state drifted")
    for name in ("candidate_forensic_root", "candidate_runtime_root"):
        candidate_path = roots.get(name)
        if candidate_path is not None and (
            candidate_path.exists() or candidate_path.is_symlink()
        ):
            raise VerificationError("X4 authority receipt candidate root state drifted")
    return document


def _verify_candidate_source_contract(
    source: Path, engine: dict[str, object], inputs: dict[str, object]
) -> None:
    candidate = inputs.get("candidate")
    engine_candidate = engine.get("candidate")
    source_authority = inputs.get("source")
    if (
        not isinstance(candidate, dict)
        or not isinstance(engine_candidate, dict)
        or candidate.get("release") != "1.231.0"
        or candidate.get("upstream_commit") != _CANDIDATE_COMMIT
        or engine_candidate.get("release") != "1.231.0"
        or engine_candidate.get("upstream_commit") != _CANDIDATE_COMMIT
        or not isinstance(source_authority, dict)
        or not isinstance(source_authority.get("build_inputs"), list)
    ):
        raise VerificationError("candidate source build authority is not exact")
    build_inputs = {
        record.get("path"): (record.get("size"), record.get("sha256"))
        for record in source_authority["build_inputs"]
        if isinstance(record, dict)
    }
    if any(build_inputs.get(path) != identity for path, identity in _CANDIDATE_SOURCE_INPUTS.items()):
        raise VerificationError("candidate source build authority is not exact")
    for relative, (size, digest) in _CANDIDATE_SOURCE_INPUTS.items():
        path = source / relative
        info = _regular_file(path, f"candidate upstream {relative}")
        if info.st_size != size or _sha256(path) != digest:
            raise VerificationError("candidate source build authority drifted")
    generator = _load_candidate_generator()
    try:
        trace = engine["native_build_environment"]["sealed_source_trace"]["build.py"]  # type: ignore[index]
        generator._verify_build_script_trace((source / "build.py").read_bytes(), trace)
        pyproject = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))
    except (KeyError, OSError, RuntimeError, tomllib.TOMLDecodeError) as exc:
        raise VerificationError("candidate source build authority rejected") from exc
    if (
        pyproject.get("project", {}).get("name") != "nautilus_trader"
        or pyproject.get("project", {}).get("version") != "1.231.0"
        or pyproject.get("project", {}).get("requires-python") != ">=3.12,<3.15"
        or pyproject.get("build-system")
        != {
            "requires": [
                "setuptools>=83",
                "poetry-core==2.3.1",
                "numpy>=1.26.4",
                "cython==3.2.9",
            ],
            "build-backend": "poetry.core.masonry.api",
        }
        or pyproject.get("tool", {}).get("poetry", {}).get("build")
        != {"script": "build.py", "generate-setup-file": False}
        or pyproject.get("tool", {}).get("poetry", {}).get("include")
        != [
            {"path": "crates/*", "format": "sdist"},
            {"path": "Cargo.lock", "format": "sdist"},
            {"path": "Cargo.toml", "format": "sdist"},
            {"path": ".cargo/*", "format": "sdist"},
            {"path": "nautilus_trader/**/*.so", "format": "wheel"},
            {"path": "nautilus_trader/**/*.pyd", "format": "wheel"},
            {"path": "nautilus_trader/py.typed", "format": "sdist"},
            {"path": "nautilus_trader/py.typed", "format": "wheel"},
            {"path": "nautilus_trader/**/*.pyi", "format": "sdist"},
            {"path": "nautilus_trader/**/*.pyi", "format": "wheel"},
        ]
    ):
        raise VerificationError("candidate source build symbols drifted")


def _verify_candidate_native_outputs(source: Path) -> dict[str, dict[str, object]]:
    package = source / "nautilus_trader"
    pyx = sorted(path for path in package.rglob("*.pyx") if path.is_file() and not path.is_symlink())
    if not pyx:
        raise VerificationError("candidate sealed source has no Cython extensions")
    cython_outputs = {
        path.relative_to(source).with_suffix(_CANDIDATE_EXT_SUFFIX).as_posix() for path in pyx
    }
    expected = cython_outputs | {f"nautilus_trader/core/nautilus_pyo3{_CANDIDATE_EXT_SUFFIX}"}
    observed: dict[str, dict[str, object]] = {}
    for path in package.rglob("*"):
        if path.is_dir() or not _NATIVE_RE.search(path.name):
            continue
        info = _regular_file(path, "candidate source native output")
        relative = path.relative_to(source).as_posix()
        observed[relative] = {
            "mode": f"{stat.S_IMODE(info.st_mode):04o}",
            "size": info.st_size,
            "sha256": _sha256(path),
        }
    if set(observed) != expected:
        raise VerificationError("candidate source native output set is incomplete or extra")
    build_roots = sorted(path for path in (source / "build").glob("lib.*") if path.is_dir())
    if len(build_roots) != 1:
        raise VerificationError("candidate native build output root is not exact")
    build_outputs = {
        path.relative_to(build_roots[0]).as_posix()
        for path in build_roots[0].rglob("*")
        if path.is_file() and not path.is_symlink() and _NATIVE_RE.search(path.name)
    }
    if build_outputs != cython_outputs:
        raise VerificationError("candidate native build output set is incomplete or extra")
    return observed


def _seal_candidate_tree(root: Path) -> None:
    for current, directories, files in os.walk(root, topdown=False, followlinks=False):
        current_path = Path(current)
        for name in files:
            path = current_path / name
            if not path.is_symlink():
                os.chmod(path, 0o500 if path.stat().st_mode & stat.S_IXUSR else 0o400)
        for name in directories:
            path = current_path / name
            if not path.is_symlink():
                os.chmod(path, 0o500)
    os.chmod(root, 0o500)


def _safe_archive_relative(name: str, top: str, label: str) -> PurePosixPath:
    raw = name.rstrip("/")
    if (
        not raw
        or "\\" in raw
        or raw.startswith("/")
        or re.match(r"^[A-Za-z]:", raw)
        or "//" in raw
        or "\x00" in raw
    ):
        raise VerificationError(f"unsafe {label} archive member")
    parts = raw.split("/")
    if any(part in {"", ".", ".."} for part in parts) or parts[0] != top:
        raise VerificationError(f"unsafe {label} archive member")
    relative = "/".join(parts[1:])
    if not relative:
        raise VerificationError(f"empty {label} archive member")
    if re.match(r"^[A-Za-z]:", relative):
        raise VerificationError(f"unsafe {label} archive member")
    return PurePosixPath(relative)


def _register_archive_regular_path(
    path: str,
    nodes: dict[str, tuple[str, str]],
    label: str,
) -> None:
    key = unicodedata.normalize("NFC", path).casefold()
    parts = path.split("/")
    parents = ["/".join(parts[:index]) for index in range(1, len(parts))]
    if key in nodes:
        raise VerificationError(
            f"{label} has a duplicate, component-ancestor, or NFC/case-fold collision"
        )
    for parent in parents:
        parent_key = unicodedata.normalize("NFC", parent).casefold()
        prior = nodes.get(parent_key)
        if prior is not None and prior != ("directory", parent):
            raise VerificationError(
                f"{label} has a duplicate, component-ancestor, or NFC/case-fold collision"
            )
    nodes[key] = ("file", path)
    for parent in parents:
        parent_key = unicodedata.normalize("NFC", parent).casefold()
        nodes.setdefault(parent_key, ("directory", parent))


def _candidate_source_archive_inventory(
    archive_path: Path, source_record: dict[str, object]
) -> tuple[dict[str, tuple[int, int, str]], str]:
    return _read_candidate_source_archive(archive_path, source_record)


def _read_candidate_source_archive(
    archive_path: Path,
    source_record: dict[str, object],
    destination: Path | None = None,
) -> tuple[dict[str, tuple[int, int, str]], str]:
    top = source_record.get("top_level_root")
    if (
        not isinstance(top, str)
        or top in {"", ".", "..", "/"}
        or PurePosixPath(top).parts != (top,)
    ):
        raise VerificationError("candidate primary source archive root is invalid")
    expected: dict[str, tuple[int, int, str]] = {}
    path_nodes: dict[str, tuple[str, str]] = {}
    member_count = 0
    try:
        descriptor = os.open(
            archive_path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
        )
        with os.fdopen(descriptor, "rb") as raw_archive:
            initial = os.fstat(raw_archive.fileno())
            initial_identity = (
                initial.st_dev,
                initial.st_ino,
                initial.st_mode,
                initial.st_uid,
                initial.st_gid,
                initial.st_nlink,
                initial.st_size,
                initial.st_mtime_ns,
                initial.st_ctime_ns,
            )
            if (
                not stat.S_ISREG(initial.st_mode)
                or initial.st_nlink != 1
                or initial.st_uid != os.geteuid()
                or stat.S_IMODE(initial.st_mode) != 0o400
                or initial.st_size != source_record.get("size")
            ):
                raise VerificationError(
                    "candidate primary source archive identity drifted"
                )
            raw_digest = hashlib.sha256()
            raw_size = 0
            for chunk in iter(lambda: raw_archive.read(1024 * 1024), b""):
                raw_digest.update(chunk)
                raw_size += len(chunk)
            if (
                raw_size != initial.st_size
                or raw_digest.hexdigest() != source_record.get("sha256")
            ):
                raise VerificationError(
                    "candidate primary source archive identity drifted"
                )
            raw_archive.seek(0)
            with gzip.GzipFile(fileobj=raw_archive, mode="rb") as gzip_archive:
                with tarfile.open(fileobj=gzip_archive, mode="r:") as archive:
                    for member in archive:
                        member_count += 1
                        if (
                            not member.isfile()
                            or member.issym()
                            or member.islnk()
                            or member.isdev()
                        ):
                            raise VerificationError(
                                "candidate primary source is not regular-file materialization"
                            )
                        relative = _safe_archive_relative(
                            member.name, top, "candidate source"
                        )
                        path = relative.as_posix()
                        _register_archive_regular_path(
                            path, path_nodes, "candidate primary source archive"
                        )
                        source = archive.extractfile(member)
                        if source is None:
                            raise VerificationError(
                                "candidate source member is unreadable"
                            )
                        output = None
                        if destination is not None:
                            output_path = destination.joinpath(*relative.parts)
                            output_path.parent.mkdir(
                                parents=True, exist_ok=True, mode=0o700
                            )
                            output = output_path.open("xb")
                        digest = hashlib.sha256()
                        size = 0
                        try:
                            with source:
                                for chunk in iter(
                                    lambda: source.read(1024 * 1024), b""
                                ):
                                    digest.update(chunk)
                                    size += len(chunk)
                                    if output is not None:
                                        output.write(chunk)
                        finally:
                            if output is not None:
                                output.close()
                        if size != member.size:
                            raise VerificationError(
                                "candidate source member size drifted during streaming"
                            )
                        mode = member.mode & 0o777
                        if destination is not None:
                            os.chmod(output_path, mode)
                        expected[path] = (mode, size, digest.hexdigest())
                    archive_end = archive.offset
                gzip_archive.seek(archive_end)
                trailing_size = 0
                for chunk in iter(lambda: gzip_archive.read(1024 * 1024), b""):
                    trailing_size += len(chunk)
                    if any(chunk):
                        raise VerificationError(
                            "candidate primary source archive termination is invalid"
                        )
                if trailing_size < 1024:
                    raise VerificationError(
                        "candidate primary source archive termination is invalid"
                    )
            final = os.fstat(raw_archive.fileno())
            final_identity = (
                final.st_dev,
                final.st_ino,
                final.st_mode,
                final.st_uid,
                final.st_gid,
                final.st_nlink,
                final.st_size,
                final.st_mtime_ns,
                final.st_ctime_ns,
            )
            try:
                path_info = archive_path.lstat()
            except OSError as exc:
                raise VerificationError(
                    "candidate primary source archive identity drifted"
                ) from exc
            path_identity = (
                path_info.st_dev,
                path_info.st_ino,
                path_info.st_mode,
                path_info.st_uid,
                path_info.st_gid,
                path_info.st_nlink,
                path_info.st_size,
                path_info.st_mtime_ns,
                path_info.st_ctime_ns,
            )
            if final_identity != initial_identity or path_identity != final_identity:
                raise VerificationError(
                    "candidate primary source archive identity drifted"
                )
    except (OSError, EOFError, gzip.BadGzipFile, tarfile.TarError) as exc:
        raise VerificationError("candidate primary source inventory failed") from exc
    if member_count != source_record.get("member_count"):
        raise VerificationError("candidate primary source member count drifted")
    if len(expected) != source_record.get("entry_count"):
        raise VerificationError("candidate primary source entry count drifted")
    encoded = json.dumps(
        [
            {
                "path": path,
                "mode": f"{record[0]:04o}",
                "size": record[1],
                "sha256": record[2],
            }
            for path, record in sorted(expected.items())
        ],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return expected, hashlib.sha256(encoded).hexdigest()


def _extract_candidate_source(
    archive_path: Path, destination: Path, source_record: dict[str, object]
) -> str:
    destination.mkdir(mode=0o700)
    expected, source_tree_sha256 = _read_candidate_source_archive(
        archive_path, source_record, destination
    )
    observed: dict[str, tuple[int, int, str]] = {}
    for path in sorted(destination.rglob("*")):
        relative = path.relative_to(destination).as_posix()
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode) or path.is_symlink():
            raise VerificationError("candidate extracted source has an unsafe entry")
        observed[relative] = (stat.S_IMODE(info.st_mode), info.st_size, _sha256(path))
    if observed != expected or len(observed) != source_record.get("entry_count"):
        raise VerificationError("candidate extracted source full inventory drifted")
    return source_tree_sha256


def _read_candidate_input_archive(
    path: Path, record: dict[str, object], label: str
) -> bytes:
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise VerificationError(f"{label} is missing or unsafe") from exc
    try:
        initial = os.fstat(descriptor)
        with os.fdopen(os.dup(descriptor), "rb") as source:
            payload = source.read()
        final = os.fstat(descriptor)
        current = path.lstat()
    except OSError as exc:
        raise VerificationError(f"{label} is unreadable") from exc
    finally:
        os.close(descriptor)
    identities = {(item.st_dev, item.st_ino) for item in (initial, final, current)}
    if (
        len(identities) != 1
        or any(not stat.S_ISREG(item.st_mode) or item.st_nlink != 1 for item in (initial, final, current))
        or record.get("mode") != "0400"
        or any(item.st_uid != os.geteuid() for item in (initial, final, current))
        or any(stat.S_IMODE(item.st_mode) != 0o400 for item in (initial, final, current))
        or any(item.st_size != record.get("size") for item in (initial, final, current))
        or len(payload) != record.get("size")
        or _sha256_bytes(payload)
        != _require_sha256(record.get("sha256"), f"{label} digest")
    ):
        raise VerificationError(f"{label} drifted")
    return payload


def _candidate_rust_expected_files(
    inputs: dict[str, object],
) -> tuple[set[str], dict[str, tuple[int, int, str]]]:
    rust = inputs.get("rust")
    if not isinstance(rust, dict) or not isinstance(rust.get("components"), list):
        raise VerificationError("candidate Rust authority is invalid")
    by_name = {
        record["name"]: record
        for record in rust["components"]
        if isinstance(record, dict) and isinstance(record.get("name"), str)
    }
    if len(by_name) != len(rust["components"]) or set(by_name) != {
        "cargo",
        "rust-std",
        "rustc",
    }:
        raise VerificationError("candidate Rust component set is not exact")
    cache = (
        Path(
            str(
                inputs["external_cache_isolation"]["external_roots"][  # type: ignore[index]
                    "candidate_input_root"
                ]
            )
        )
        / "rust-inputs"
    )
    directories: set[str] = set()
    expected: dict[str, tuple[int, int, str]] = {}
    try:
        for name in ("rustc", "rust-std", "cargo"):
            record = by_name[name]
            archive_path = cache / str(record["filename"])
            payload = _read_candidate_input_archive(
                archive_path, record, f"candidate Rust {name} archive"
            )
            component = (
                "rust-std-x86_64-unknown-linux-gnu" if name == "rust-std" else name
            )
            with tarfile.open(fileobj=io.BytesIO(payload), mode="r:xz") as archive:
                members = archive.getmembers()
                component_roots = {
                    PurePosixPath(member.name).parts[1]
                    for member in members
                    if len(PurePosixPath(member.name).parts) > 1
                }
                if component not in component_roots:
                    raise VerificationError(
                        "candidate Rust component payload root drifted"
                    )
                for member in members:
                    member_path = PurePosixPath(member.name)
                    if (
                        member_path.is_absolute()
                        or ".." in member_path.parts
                        or member.issym()
                        or member.islnk()
                        or member.isdev()
                    ):
                        raise VerificationError(
                            "unsafe candidate Rust component archive"
                        )
                    parts = member_path.parts
                    if len(parts) < 3 or parts[1] != component:
                        continue
                    relative_path = PurePosixPath(*parts[2:])
                    relative = relative_path.as_posix()
                    if relative == "manifest.in" or member.isdir():
                        if member.isdir() and relative:
                            for index in range(1, len(relative_path.parts) + 1):
                                directory = PurePosixPath(
                                    *relative_path.parts[:index]
                                ).as_posix()
                                if directory in expected:
                                    raise VerificationError(
                                        "candidate Rust reviewed payload set collides"
                                    )
                                directories.add(directory)
                        continue
                    for index in range(1, len(relative_path.parts)):
                        directory = PurePosixPath(
                            *relative_path.parts[:index]
                        ).as_posix()
                        if directory in expected:
                            raise VerificationError(
                                "candidate Rust reviewed payload set collides"
                            )
                        directories.add(directory)
                    if (
                        not member.isfile()
                        or relative in expected
                        or relative in directories
                    ):
                        raise VerificationError(
                            "candidate Rust reviewed payload set collides"
                        )
                    source = archive.extractfile(member)
                    if source is None:
                        raise VerificationError(
                            "candidate Rust payload member is unreadable"
                        )
                    digest = hashlib.sha256()
                    size = 0
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        size += len(block)
                        digest.update(block)
                    if size != member.size:
                        raise VerificationError(
                            "candidate Rust payload member size drifted"
                        )
                    expected[relative] = (
                        0o500 if member.mode & stat.S_IXUSR else 0o400,
                        member.size,
                        digest.hexdigest(),
                    )
    except (OSError, tarfile.TarError) as exc:
        raise VerificationError("candidate Rust archive is invalid") from exc
    return directories, expected


def _candidate_vendor_expected_files(
    inputs: dict[str, object],
) -> tuple[set[str], dict[str, tuple[int, int, str]]]:
    cargo = _candidate_json(_CANDIDATE_CARGO_POLICY)
    packages = cargo.get("packages")
    if not isinstance(packages, list) or len(packages) != cargo.get("package_count"):
        raise VerificationError("candidate Cargo package authority is invalid")
    cache = (
        Path(
            str(
                inputs["external_cache_isolation"]["external_roots"][  # type: ignore[index]
                    "candidate_input_root"
                ]
            )
        )
        / "cargo-registry"
    )
    directories: set[str] = set()
    expected: dict[str, tuple[int, int, str]] = {}
    try:
        for record in packages:
            if not isinstance(record, dict):
                raise VerificationError("candidate Cargo package record is invalid")
            directory = f"{record['name']}-{record['version']}"
            if directory in directories:
                raise VerificationError("candidate vendor package directory collides")
            directories.add(directory)
            archive_path = cache / str(record["filename"])
            payload = _read_candidate_input_archive(
                archive_path, record, f"candidate crate archive: {archive_path.name}"
            )
            package_hashes: dict[str, str] = {}
            with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
                members = archive.getmembers()
                roots = {
                    PurePosixPath(member.name).parts[0]
                    for member in members
                    if PurePosixPath(member.name).parts
                }
                if roots != {directory}:
                    raise VerificationError("candidate crate archive root is ambiguous")
                for member in members:
                    path = PurePosixPath(member.name)
                    if (
                        path.is_absolute()
                        or ".." in path.parts
                        or member.issym()
                        or member.islnk()
                        or member.isdev()
                    ):
                        raise VerificationError("unsafe candidate crate archive")
                    relative = PurePosixPath(*path.parts[1:]).as_posix()
                    if not relative:
                        continue
                    full_path = PurePosixPath(directory, relative)
                    limit = (
                        len(full_path.parts)
                        if member.isdir()
                        else len(full_path.parts) - 1
                    )
                    for index in range(1, limit + 1):
                        parent = PurePosixPath(*full_path.parts[:index]).as_posix()
                        if parent in expected:
                            raise VerificationError(
                                "candidate crate payload collides"
                            )
                        directories.add(parent)
                    if member.isdir():
                        continue
                    key = f"{directory}/{relative}"
                    if (
                        not member.isfile()
                        or key in expected
                        or key in directories
                    ):
                        raise VerificationError("candidate crate payload collides")
                    source = archive.extractfile(member)
                    if source is None:
                        raise VerificationError("candidate crate member is unreadable")
                    digest = hashlib.sha256()
                    size = 0
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        size += len(block)
                        digest.update(block)
                    if size != member.size:
                        raise VerificationError("candidate crate member size drifted")
                    package_hashes[relative] = digest.hexdigest()
                    expected[key] = (
                        0o400,
                        member.size,
                        digest.hexdigest(),
                    )
            checksum = json.dumps(
                {
                    "files": dict(sorted(package_hashes.items())),
                    "package": record["checksum"],
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii")
            expected[f"{directory}/.cargo-checksum.json"] = (
                0o400,
                len(checksum),
                _sha256_bytes(checksum),
            )
    except (OSError, tarfile.TarError) as exc:
        raise VerificationError("candidate crate archive is invalid") from exc
    return directories, expected


def _candidate_materialized_regular_files(
    root: Path, label: str
) -> dict[str, tuple[int, int, str]]:
    observed: dict[str, tuple[int, int, str]] = {}
    for path in root.rglob("*"):
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            continue
        if path.is_symlink() or info.st_nlink != 1 or info.st_uid != os.geteuid():
            raise VerificationError(f"{label} has an unsafe regular file")
        observed[path.relative_to(root).as_posix()] = (
            stat.S_IMODE(info.st_mode),
            info.st_size,
            _sha256(path),
        )
    return observed


def _verify_candidate_materialized_directories(
    root: Path, expected: set[str], label: str
) -> None:
    try:
        root_info = root.lstat()
    except OSError as exc:
        raise VerificationError(f"{label} directory authority drifted") from exc
    if (
        not stat.S_ISDIR(root_info.st_mode)
        or stat.S_ISLNK(root_info.st_mode)
        or root_info.st_uid != os.geteuid()
        or stat.S_IMODE(root_info.st_mode) != 0o500
    ):
        raise VerificationError(f"{label} directory authority drifted")
    observed: set[str] = set()
    for path in root.rglob("*"):
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            if (
                stat.S_ISLNK(info.st_mode)
                or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o500
            ):
                raise VerificationError(f"{label} directory authority drifted")
            observed.add(path.relative_to(root).as_posix())
        elif not stat.S_ISREG(info.st_mode):
            raise VerificationError(f"{label} directory authority drifted")
    if observed != expected:
        raise VerificationError(f"{label} directory authority drifted")


def _materialize_candidate_rust(inputs: dict[str, object], destination: Path) -> None:
    rust = inputs.get("rust")
    if not isinstance(rust, dict) or not isinstance(rust.get("components"), list):
        raise VerificationError("candidate Rust authority is invalid")
    cache = Path(str(inputs["external_cache_isolation"]["external_roots"]["candidate_input_root"])) / "rust-inputs"  # type: ignore[index]
    expected_directories, expected_files = _candidate_rust_expected_files(inputs)
    destination.mkdir(mode=0o700)
    try:
        by_name = {record["name"]: record for record in rust["components"] if isinstance(record, dict)}
        if set(by_name) != {"cargo", "rust-std", "rustc"}:
            raise VerificationError("candidate Rust component set is not exact")
        for name in ("rustc", "rust-std", "cargo"):
            record = by_name[name]
            archive_path = cache / str(record["filename"])
            info = _regular_file(archive_path, f"candidate Rust {name} archive")
            if info.st_size != record["size"] or _sha256(archive_path) != record["sha256"] or stat.S_IMODE(info.st_mode) != 0o400:
                raise VerificationError(f"candidate Rust {name} archive drifted")
            with tarfile.open(archive_path, "r:xz") as archive:
                    members = archive.getmembers()
                    expected_component = "rust-std-x86_64-unknown-linux-gnu" if name == "rust-std" else name
                    component_roots = {
                        PurePosixPath(member.name).parts[1]
                        for member in members
                        if len(PurePosixPath(member.name).parts) > 1
                    }
                    if expected_component not in component_roots:
                        raise VerificationError("candidate Rust component payload root drifted")
                    for member in members:
                        member_path = PurePosixPath(member.name)
                        if member_path.is_absolute() or ".." in member_path.parts or member.issym() or member.islnk() or member.isdev():
                            raise VerificationError("unsafe candidate Rust component archive")
                        parts = member_path.parts
                        if len(parts) < 3 or parts[1] != expected_component:
                            continue
                        relative = PurePosixPath(*parts[2:])
                        if relative == PurePosixPath("manifest.in"):
                            continue
                        output = destination.joinpath(*relative.parts)
                        if member.isdir():
                            output.mkdir(parents=True, exist_ok=True, mode=0o700)
                        elif member.isfile():
                            output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                            source = archive.extractfile(member)
                            if source is None:
                                raise VerificationError("candidate Rust payload member is unreadable")
                            _write_candidate_rust_payload(
                                output, source, member.mode & 0o777
                            )
                        else:
                            raise VerificationError("candidate Rust payload has an unsupported entry")
        _seal_candidate_tree(destination)
        _verify_candidate_materialized_directories(
            destination,
            expected_directories,
            "candidate Rust materialized tree",
        )
        if _candidate_materialized_regular_files(
            destination, "candidate Rust materialized tree"
        ) != expected_files:
            raise VerificationError("candidate Rust materialized file authority drifted")
        engine = _candidate_json(_CANDIDATE_ENGINE_POLICY)
        for executable, expected_identity in ((destination / "bin/cargo", engine["rust"]["cargo_identity"]), (destination / "bin/rustc", engine["rust"]["rustc_identity"])):  # type: ignore[index]
            if _run_identity([str(executable), "--version"], executable.name) != expected_identity:
                raise VerificationError(f"candidate {executable.name} identity drifted")
    except BaseException:
        if destination.exists():
            _thaw_tree(destination)
            shutil.rmtree(destination)
        raise


def _write_candidate_rust_payload(
    output: Path, source: BinaryIO, mode: int
) -> None:
    if output.exists() or output.is_symlink():
        raise VerificationError("candidate Rust component payload collision")
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with output.open("xb") as handle:
        shutil.copyfileobj(source, handle)
    os.chmod(output, mode)


def _extract_candidate_crate(archive_path: Path, destination: Path, record: dict[str, object]) -> None:
    expected_files: dict[str, str] = {}
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        roots = {PurePosixPath(member.name).parts[0] for member in members if PurePosixPath(member.name).parts}
        if len(roots) != 1:
            raise VerificationError("candidate crate archive root is ambiguous")
        top = next(iter(roots))
        destination.mkdir(mode=0o700)
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or member.issym() or member.islnk() or member.isdev():
                raise VerificationError("unsafe candidate crate archive")
            relative = PurePosixPath(*path.parts[1:])
            if not relative.parts:
                continue
            output = destination.joinpath(*relative.parts)
            if member.isdir():
                output.mkdir(parents=True, exist_ok=True, mode=0o700)
            elif member.isfile():
                output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                source = archive.extractfile(member)
                if source is None:
                    raise VerificationError("candidate crate member is unreadable")
                with output.open("xb") as handle:
                    shutil.copyfileobj(source, handle)
                expected_files[relative.as_posix()] = _sha256(output)
            else:
                raise VerificationError("candidate crate has a non-file entry")
    checksum = destination / ".cargo-checksum.json"
    checksum.write_text(json.dumps({"files": dict(sorted(expected_files.items())), "package": record["checksum"]}, sort_keys=True, separators=(",", ":")), encoding="ascii")


def _materialize_candidate_vendor(inputs: dict[str, object], destination: Path) -> None:
    cargo = _candidate_json(_CANDIDATE_CARGO_POLICY)
    packages = cargo.get("packages")
    if not isinstance(packages, list) or len(packages) != cargo.get("package_count"):
        raise VerificationError("candidate Cargo package authority is invalid")
    cache = Path(str(inputs["external_cache_isolation"]["external_roots"]["candidate_input_root"])) / "cargo-registry"  # type: ignore[index]
    expected_directories, expected_files = _candidate_vendor_expected_files(inputs)
    destination.mkdir(mode=0o700)
    try:
        for record in packages:
            if not isinstance(record, dict):
                raise VerificationError("candidate Cargo package record is invalid")
            archive = cache / str(record["filename"])
            info = _regular_file(archive, "candidate crate archive")
            if info.st_size != record["size"] or _sha256(archive) != record["sha256"] or stat.S_IMODE(info.st_mode) != 0o400:
                raise VerificationError(f"candidate crate archive drifted: {archive.name}")
            package_destination = destination / f"{record['name']}-{record['version']}"
            if package_destination.exists():
                raise VerificationError("candidate vendor directory collision")
            _extract_candidate_crate(archive, package_destination, record)
        _seal_candidate_tree(destination)
        _verify_candidate_materialized_directories(
            destination,
            expected_directories,
            "candidate vendor materialized tree",
        )
        if _candidate_materialized_regular_files(
            destination, "candidate vendor materialized tree"
        ) != expected_files:
            raise VerificationError("candidate vendor materialized file authority drifted")
    except BaseException:
        if destination.exists():
            _thaw_tree(destination)
            shutil.rmtree(destination)
        raise


def _materialize_candidate_cargo_home(inputs: dict[str, object], destination: Path) -> None:
    cargo = inputs.get("cargo_registry")
    if not isinstance(cargo, dict) or not isinstance(cargo.get("offline_cargo_config"), dict):
        raise VerificationError("candidate Cargo home authority is invalid")
    config = cargo["offline_cargo_config"]
    destination.mkdir(mode=0o700)
    path = destination / str(config["config_relative_path"])
    payload = str(config["contents"]).encode("ascii")
    if hashlib.sha256(payload).hexdigest() != config["sha256"]:
        raise VerificationError("candidate Cargo config authority drifted")
    path.write_bytes(payload)
    os.chmod(path, 0o400)
    os.chmod(destination, 0o500)


def _materialize_candidate_router(inputs: dict[str, object], destination: Path) -> None:
    router = inputs.get("command_router")
    if not isinstance(router, dict) or not isinstance(router.get("entries"), list):
        raise VerificationError("candidate command-router authority is invalid")
    destination.mkdir(mode=0o700)
    (destination / "bin").mkdir(mode=0o700)
    for record in router["entries"]:
        if not isinstance(record, dict):
            raise VerificationError("candidate command-router entry is invalid")
        path = destination / str(record["path"])
        if record.get("type") == "file":
            payload = str(record["contents"]).encode("ascii")
            if len(payload) != record["size"] or hashlib.sha256(payload).hexdigest() != record["sha256"]:
                raise VerificationError("candidate command-router file authority drifted")
            path.write_bytes(payload)
            os.chmod(path, int(str(record["mode"]), 8))
        elif record.get("type") == "symlink":
            path.symlink_to(str(record["link_target"]))
            resolved = path.resolve(strict=True)
            expected = record.get("resolved")
            if not isinstance(expected, dict) or _sha256(resolved) != expected.get("sha256") or resolved.stat().st_size != expected.get("size"):
                raise VerificationError("candidate command-router link authority drifted")
        else:
            raise VerificationError("candidate command-router entry type is invalid")
    if {path.relative_to(destination).as_posix() for path in destination.rglob("*") if not path.is_dir()} != set(router.get("file_set", [])):
        raise VerificationError("candidate command-router file set drifted")
    os.chmod(destination / "bin", 0o500)
    os.chmod(destination, 0o500)


def _verify_candidate_sealed_tree(
    root: Path, label: str, *, allow_symlinks: bool = False
) -> None:
    _directory(root, label, 0o500)
    for path in root.rglob("*"):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            if not allow_symlinks:
                raise VerificationError(f"{label} contains an unexpected symlink")
            continue
        if stat.S_ISDIR(info.st_mode):
            if stat.S_IMODE(info.st_mode) != 0o500:
                raise VerificationError(f"{label} directory mode drifted")
        elif stat.S_ISREG(info.st_mode):
            if stat.S_IMODE(info.st_mode) not in {0o400, 0o500}:
                raise VerificationError(f"{label} file mode drifted")
        else:
            raise VerificationError(f"{label} contains an unsafe entry")


def _verify_candidate_rust(inputs: dict[str, object], destination: Path) -> None:
    _verify_candidate_sealed_tree(destination, "candidate Rust toolchain")
    expected_directories, expected_files = _candidate_rust_expected_files(inputs)
    _verify_candidate_materialized_directories(
        destination,
        expected_directories,
        "candidate Rust materialized tree",
    )
    if _candidate_materialized_regular_files(
        destination, "candidate Rust materialized tree"
    ) != expected_files:
        raise VerificationError("candidate Rust materialized file authority drifted")
    engine = _candidate_json(_CANDIDATE_ENGINE_POLICY)
    router = inputs["command_router"]
    assert isinstance(router, dict) and isinstance(router["entries"], list)
    targets = {
        record["name"]: record["exec_target"]
        for record in router["entries"]
        if isinstance(record, dict) and record.get("type") == "file"
    }
    cargo_record = targets["cargo"]
    assert isinstance(cargo_record, dict)
    cargo = destination / "bin/cargo"
    rustc = destination / "bin/rustc"
    if cargo.stat().st_size != cargo_record["size"] or _sha256(cargo) != cargo_record["sha256"]:
        raise VerificationError("candidate Cargo executable drifted")
    if _run_identity([str(cargo), "--version"], "candidate cargo") != engine["rust"]["cargo_identity"]:  # type: ignore[index]
        raise VerificationError("candidate Cargo identity drifted")
    if _run_identity([str(rustc), "--version"], "candidate rustc") != engine["rust"]["rustc_identity"]:  # type: ignore[index]
        raise VerificationError("candidate rustc identity drifted")


def _verify_candidate_vendor(inputs: dict[str, object], destination: Path) -> None:
    _verify_candidate_sealed_tree(destination, "candidate vendor tree")
    expected_directories, expected_files = _candidate_vendor_expected_files(inputs)
    _verify_candidate_materialized_directories(
        destination,
        expected_directories,
        "candidate vendor materialized tree",
    )
    if _candidate_materialized_regular_files(
        destination, "candidate vendor materialized tree"
    ) != expected_files:
        raise VerificationError("candidate vendor materialized file authority drifted")


def _verify_candidate_cargo_home(inputs: dict[str, object], destination: Path) -> None:
    _verify_candidate_sealed_tree(destination, "candidate Cargo home")
    config = inputs["cargo_registry"]["offline_cargo_config"]  # type: ignore[index]
    path = destination / str(config["config_relative_path"])
    payload = path.read_bytes()
    if set(path.relative_to(destination).as_posix() for path in destination.rglob("*") if path.is_file()) != {str(config["config_relative_path"])}:
        raise VerificationError("candidate Cargo home file set drifted")
    if payload != str(config["contents"]).encode("ascii") or _sha256(path) != config["sha256"]:
        raise VerificationError("candidate Cargo home bytes drifted")


def _verify_candidate_router(inputs: dict[str, object], destination: Path) -> None:
    _verify_candidate_sealed_tree(
        destination, "candidate command router", allow_symlinks=True
    )
    router = inputs["command_router"]
    assert isinstance(router, dict) and isinstance(router["entries"], list)
    observed = {path.relative_to(destination).as_posix() for path in destination.rglob("*") if not path.is_dir()}
    if observed != set(router["file_set"]):
        raise VerificationError("candidate command-router file set drifted")
    for record in router["entries"]:
        assert isinstance(record, dict)
        path = destination / str(record["path"])
        if record["type"] == "file":
            if path.read_text(encoding="ascii") != record["contents"] or _sha256(path) != record["sha256"]:
                raise VerificationError("candidate command-router wrapper drifted")
        elif os.readlink(path) != record["link_target"]:
            raise VerificationError("candidate command-router link drifted")
        resolved = record.get("resolved")
        if isinstance(resolved, dict):
            target = path.resolve(strict=True)
            if target.stat().st_size != resolved["size"] or _sha256(target) != resolved["sha256"]:
                raise VerificationError("candidate command-router target drifted")


def _materialize_candidate_inputs(
    engine: dict[str, object],
    inputs: dict[str, object],
    *,
    allowed_build_children: frozenset[str] = frozenset(),
) -> dict[str, Path]:
    roots = _candidate_roots(engine)
    if roots["candidate_runtime_root"].exists() or roots["candidate_runtime_root"].is_symlink():
        raise VerificationError("candidate runtime root is not absent")
    build_root = roots["candidate_build_root"]
    _require_external(build_root, "candidate_build_root")
    if build_root.exists():
        _directory(build_root, "candidate build root", 0o700)
        if {entry.name for entry in build_root.iterdir()} != set(
            allowed_build_children
        ):
            raise VerificationError("candidate build root contains unexpected state")
    else:
        build_root.mkdir(mode=0o700)
    llvm_tool = _load_llvm_toolchain_tool()
    llvm_policy = llvm_tool.load_policy(_LLVM_TOOLCHAIN_POLICY)
    llvm_cache = roots["rollback_root"] / "llvm-22.1.3-resource-cache"
    llvm_tool.verify_cache(llvm_cache, llvm_policy)
    llvm_destination = roots["candidate_llvm_toolchain_root"]
    if llvm_destination.exists():
        llvm_tool.verify_materialized(llvm_destination, llvm_policy)
    else:
        llvm_tool.materialize(llvm_cache, llvm_destination, llvm_policy)
    for name, materialize, verify in (
        ("candidate_rust_toolchain_root", lambda: _materialize_candidate_rust(inputs, roots["candidate_rust_toolchain_root"]), lambda: _verify_candidate_rust(inputs, roots["candidate_rust_toolchain_root"])),
        ("candidate_vendor_root", lambda: _materialize_candidate_vendor(inputs, roots["candidate_vendor_root"]), lambda: _verify_candidate_vendor(inputs, roots["candidate_vendor_root"])),
        ("candidate_cargo_home_root", lambda: _materialize_candidate_cargo_home(inputs, roots["candidate_cargo_home_root"]), lambda: _verify_candidate_cargo_home(inputs, roots["candidate_cargo_home_root"])),
        ("candidate_toolchain_root", lambda: _materialize_candidate_router(inputs, roots["candidate_toolchain_root"]), lambda: _verify_candidate_router(inputs, roots["candidate_toolchain_root"])),
    ):
        path = roots[name]
        _require_external(path, name)
        if path.exists() or path.is_symlink():
            verify()
        else:
            materialize()
            verify()
    return roots


def _rename_noreplace(
    source: Path,
    destination: Path,
    *,
    expected_identity: tuple[int, int],
    parent_identity: tuple[int, int],
    publication_committed: Callable[[], None],
) -> None:
    parent_fd = -1
    renamed = False
    try:
        parent_fd = os.open(
            destination.parent,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        parent = os.fstat(parent_fd)
        if (
            not stat.S_ISDIR(parent.st_mode)
            or (parent.st_dev, parent.st_ino) != parent_identity
            or parent.st_uid != os.geteuid()
            or stat.S_IMODE(parent.st_mode) & 0o077
        ):
            raise VerificationError(
                "candidate artifact parent identity changed before publication"
            )
        try:
            staged = os.stat(source.name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError as exc:
            raise VerificationError(
                "candidate artifact staging identity changed before publication"
            ) from exc
        if (
            not stat.S_ISDIR(staged.st_mode)
            or (staged.st_dev, staged.st_ino) != expected_identity
        ):
            raise VerificationError(
                "candidate artifact staging identity changed before publication"
            )

        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            raise VerificationError("renameat2 NOREPLACE is unavailable")
        renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        ctypes.set_errno(0)
        result = renameat2(
            parent_fd,
            os.fsencode(source.name),
            parent_fd,
            os.fsencode(destination.name),
            _RENAME_NOREPLACE,
        )
        if result != 0:
            error = ctypes.get_errno()
            if error == errno.EEXIST:
                raise VerificationError(
                    "candidate publication destination already exists"
                )
            raise VerificationError(
                f"candidate NOREPLACE publication failed: {os.strerror(error)}"
            )
        renamed = True
        publication_committed()
        try:
            published = os.stat(
                destination.name, dir_fd=parent_fd, follow_symlinks=False
            )
        except OSError as exc:
            raise VerificationError(
                "published artifact identity changed during atomic rename"
            ) from exc
        if (
            not stat.S_ISDIR(published.st_mode)
            or (published.st_dev, published.st_ino) != expected_identity
            or published.st_uid != os.geteuid()
            or stat.S_IMODE(published.st_mode) != 0o500
        ):
            raise VerificationError(
                "published artifact identity changed during atomic rename"
            )
    finally:
        if parent_fd >= 0:
            try:
                os.close(parent_fd)
            except OSError:
                if not renamed:
                    raise


def _elf_string(data: bytes, offset: int, limit: int) -> str:
    if offset < 0 or offset >= limit or limit > len(data):
        raise VerificationError("ELF string offset is outside DT_STRSZ")
    end = data.find(b"\0", offset, limit)
    if end < 0:
        raise VerificationError("ELF string is not terminated inside DT_STRSZ")
    try:
        return data[offset:end].decode("ascii")
    except UnicodeDecodeError as exc:
        raise VerificationError("ELF dynamic string is not ASCII") from exc


def _elf_metadata(data: bytes, label: str) -> dict[str, object]:
    if (
        len(data) < 64
        or data[:4] != b"\x7fELF"
        or data[4] != 2
        or data[5] != 1
        or data[6] != 1
    ):
        raise VerificationError(f"candidate native library is not ELF64 little-endian: {label}")
    header = struct.unpack_from("<16sHHIQQQIHHHHHH", data, 0)
    elf_type, machine, version, header_size = header[1], header[2], header[3], header[8]
    phoff, phentsize, phnum = header[5], header[9], header[10]
    if elf_type not in {2, 3}:
        raise VerificationError(f"candidate ELF type is unsupported: {label}")
    if (
        machine != 62
        or version != 1
        or header_size != 64
        or phentsize != 56
        or phnum == 0
        or phoff < header_size
        or phoff + phentsize * phnum > len(data)
    ):
        raise VerificationError(f"candidate native library ABI drifted: {label}")
    loads: list[tuple[int, int, int]] = []
    dynamics: list[tuple[int, int]] = []
    interpreters: list[str] = []
    interpreter: str | None = None
    for index in range(phnum):
        values = struct.unpack_from("<IIQQQQQQ", data, phoff + index * phentsize)
        kind, offset, vaddr, filesz, memsz = (
            values[0],
            values[2],
            values[3],
            values[5],
            values[6],
        )
        if filesz > memsz or offset + filesz > len(data):
            raise VerificationError(f"candidate ELF segment exceeds file: {label}")
        if kind == 1:
            loads.append((vaddr, offset, filesz))
        elif kind == 2:
            dynamics.append((offset, filesz))
        elif kind == 3:
            raw = data[offset : offset + filesz]
            if len(raw) < 2 or raw[-1:] != b"\0" or b"\0" in raw[:-1]:
                raise VerificationError(f"candidate ELF interpreter termination is invalid: {label}")
            try:
                interpreters.append(raw[:-1].decode("ascii"))
            except UnicodeDecodeError as exc:
                raise VerificationError(f"candidate ELF interpreter is invalid: {label}") from exc
    if not loads or len(dynamics) != 1:
        raise VerificationError(f"candidate ELF dynamic structure is not exact: {label}")
    if len(interpreters) > 1:
        raise VerificationError(f"candidate ELF interpreter structure is not exact: {label}")
    if interpreters:
        interpreter = interpreters[0]
    needed_indexes: list[int] = []
    singleton: dict[int, int] = {}
    offset, size = dynamics[0]
    if size == 0 or size % 16:
        raise VerificationError(f"candidate ELF dynamic table bounds are invalid: {label}")
    if not any(
        load_offset <= offset
        and offset + size <= load_offset + load_size
        for _load_vaddr, load_offset, load_size in loads
    ):
        raise VerificationError(f"candidate ELF dynamic table is outside PT_LOAD: {label}")
    found_null = False
    for position in range(offset, offset + size, 16):
        tag, value = struct.unpack_from("<qQ", data, position)
        if tag == 0:
            found_null = True
            break
        if tag == 1:
            needed_indexes.append(value)
        elif tag in {5, 10, 14, 15, 29}:
            if tag in singleton:
                raise VerificationError(f"candidate ELF dynamic singleton tag is duplicated: {label}")
            singleton[tag] = value
    if not found_null:
        raise VerificationError(f"candidate ELF dynamic table has no DT_NULL: {label}")
    string_address = singleton.get(5)
    string_size = singleton.get(10)
    indexed = [*needed_indexes, *(singleton[tag] for tag in (14, 15, 29) if tag in singleton)]
    if (string_address is None) != (string_size is None) or (indexed and string_address is None):
        raise VerificationError(f"candidate ELF dynamic strings are unresolved: {label}")
    strings_offset: int | None = None
    if string_address is not None and string_size is not None:
        if string_size == 0:
            raise VerificationError(f"candidate ELF DT_STRSZ is invalid: {label}")
        for vaddr, offset, filesz in loads:
            if vaddr <= string_address and string_address + string_size <= vaddr + filesz:
                strings_offset = offset + string_address - vaddr
                break
    if indexed and strings_offset is None:
        raise VerificationError(f"candidate ELF dynamic strings are unresolved: {label}")
    def lookup(index: int | None) -> str | None:
        if index is None:
            return None
        assert strings_offset is not None and string_size is not None
        return _elf_string(
            data,
            strings_offset + index,
            strings_offset + string_size,
        )

    soname = lookup(singleton.get(14))
    if soname is not None and (not soname or "/" in soname or PurePosixPath(soname).name != soname):
        raise VerificationError(f"candidate ELF SONAME is unsafe: {label}")
    return {
        "abi_class": "ELF64",
        "abi_data": "little-endian",
        "machine": "EM_X86_64",
        "interpreter": interpreter,
        "needed": [lookup(index) for index in needed_indexes],
        "rpath": lookup(singleton.get(15)),
        "runpath": lookup(singleton.get(29)),
        "soname": soname,
    }


def _candidate_native_inventory(wheel: Path | bytes) -> list[dict[str, object]]:
    source = io.BytesIO(wheel) if isinstance(wheel, bytes) else wheel
    records: list[dict[str, object]] = []
    try:
        with zipfile.ZipFile(source) as archive:
            for member in sorted(archive.infolist(), key=lambda item: item.filename):
                if member.is_dir() or not _NATIVE_RE.search(member.filename):
                    continue
                relative = _safe_relative(member.filename, "candidate native wheel member")
                payload = archive.read(member)
                mode = (member.external_attr >> 16) & 0o777
                records.append(
                    {
                        "path": relative,
                        "mode": f"{mode:04o}",
                        "size": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "elf": _elf_metadata(payload, relative),
                    }
                )
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        raise VerificationError("candidate native wheel inventory failed") from exc
    if not records:
        raise VerificationError("candidate wheel contains no native libraries")
    return records


def _candidate_runtime_direct_versions(
    inputs: dict[str, object],
) -> dict[str, str]:
    hashes = inputs.get("policy_hashes")
    if (
        not isinstance(hashes, dict)
        or hashes.get("wheel_cache_policy_sha256")
        != _sha256(_CANDIDATE_WHEEL_POLICY)
    ):
        raise VerificationError("candidate wheel cache policy authority drifted")
    policy = _candidate_json(_CANDIDATE_WHEEL_POLICY)
    direct = policy.get("runtime_direct")
    transitive = policy.get("runtime_transitive")
    runtime_wheels = inputs.get("runtime_wheels")
    if (
        not isinstance(direct, list)
        or not isinstance(transitive, list)
        or not all(isinstance(name, str) and name for name in direct + transitive)
        or not isinstance(runtime_wheels, list)
        or not all(isinstance(record, dict) for record in runtime_wheels)
    ):
        raise VerificationError("candidate runtime wheel dependency authority is invalid")
    versions: dict[str, str] = {}
    for record in runtime_wheels:
        package = record.get("package")
        version = record.get("version")
        if (
            not isinstance(package, str)
            or not package
            or not isinstance(version, str)
            or not version
            or package in versions
        ):
            raise VerificationError(
                "candidate runtime wheel dependency authority is invalid"
            )
        versions[package] = version
    names = direct + transitive
    if len(set(names)) != len(names) or set(names) != set(versions):
        raise VerificationError("candidate runtime wheel dependency authority is invalid")
    return {name: versions[name] for name in direct}


def _candidate_requires_dist(inputs: dict[str, object]) -> tuple[str, ...]:
    hashes = inputs.get("policy_hashes")
    if (
        not isinstance(hashes, dict)
        or hashes.get("engine_build_policy_sha256")
        != _sha256(_CANDIDATE_ENGINE_POLICY)
    ):
        raise VerificationError("candidate engine build policy authority drifted")
    generator = _load_candidate_generator()
    try:
        return generator._candidate_wheel_requires_dist(
            _candidate_json(_CANDIDATE_ENGINE_POLICY)
        )
    except (RuntimeError, ValueError) as exc:
        raise VerificationError("candidate wheel metadata authority is invalid") from exc


def _verify_candidate_wheel_archive(wheel: Path | bytes) -> None:
    """Apply U03's safe member/path and exact RECORD verifier to a sealed wheel."""
    wheel_name = _CANDIDATE_WHEEL_FILENAME if isinstance(wheel, bytes) else wheel.name
    if wheel_name != _CANDIDATE_WHEEL_FILENAME:
        raise VerificationError("candidate wheel identity is not exact")
    source = io.BytesIO(wheel) if isinstance(wheel, bytes) else wheel
    generator = _load_candidate_generator()
    inputs = _candidate_json(_CANDIDATE_TOOLCHAIN_INPUTS)
    try:
        with zipfile.ZipFile(source) as archive:
            archive_infos = archive.infolist()
            if not archive_infos:
                raise VerificationError("candidate wheel is empty")
            nodes: dict[str, tuple[str, str]] = {}
            explicit_directories: set[str] = set()
            dist_info_roots: set[str] = set()
            top_level_roots: set[str] = set()
            infos: dict[str, zipfile.ZipInfo] = {}
            for info in archive_infos:
                name = generator._wheel_path(info.filename)
                top_level = name.split("/", 1)[0]
                top_level_roots.add(top_level)
                if top_level.casefold().endswith(".dist-info"):
                    dist_info_roots.add(top_level)
                mode = (info.external_attr >> 16) & 0xFFFF
                file_type = stat.S_IFMT(mode)
                if info.is_dir():
                    if file_type not in {0, stat.S_IFDIR} or info.file_size != 0:
                        raise VerificationError("candidate wheel contains a non-directory member")
                    generator._register_wheel_directory(name, nodes, explicit_directories)
                    continue
                generator._register_wheel_path(name, nodes)
                if stat.S_ISLNK(mode) or file_type not in {0, stat.S_IFREG} or info.flag_bits & 0x1:
                    raise VerificationError("candidate wheel contains a non-regular member")
                infos[name] = info
            metadata = [
                name
                for name in infos
                if name.endswith(".dist-info/METADATA") and name.count("/") == 1
            ]
            wheel_metadata = [
                name
                for name in infos
                if name.endswith(".dist-info/WHEEL") and name.count("/") == 1
            ]
            records = [
                name
                for name in infos
                if name.endswith(".dist-info/RECORD") and name.count("/") == 1
            ]
            if dist_info_roots != {_CANDIDATE_DIST_INFO}:
                raise VerificationError("candidate wheel metadata root is not exact")
            if any(
                not (
                    name.startswith("nautilus_trader/")
                    or name.startswith(f"{_CANDIDATE_DIST_INFO}/")
                )
                for name in infos
            ):
                raise VerificationError("candidate wheel payload namespace is not exact")
            if top_level_roots != {"nautilus_trader", _CANDIDATE_DIST_INFO}:
                raise VerificationError("candidate wheel payload namespace is not exact")
            if (
                metadata != [f"{_CANDIDATE_DIST_INFO}/METADATA"]
                or wheel_metadata != [f"{_CANDIDATE_DIST_INFO}/WHEEL"]
                or records != [f"{_CANDIDATE_DIST_INFO}/RECORD"]
            ):
                raise VerificationError("candidate wheel METADATA/WHEEL/RECORD layout is invalid")
            generator._verify_wheel_record(archive, infos, records[0])
            parsed_metadata = BytesParser(policy=compat32).parsebytes(archive.read(metadata[0]))
            parsed_wheel = BytesParser(policy=compat32).parsebytes(archive.read(wheel_metadata[0]))
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise VerificationError(f"candidate wheel archive verification failed: {wheel_name}") from exc
    identity = {
        "Metadata-Version": "2.4",
        "Name": "nautilus_trader",
        "Version": "1.231.0",
        "Requires-Python": ">=3.12,<3.15",
    }
    if any(parsed_metadata.get_all(name, []) != [value] for name, value in identity.items()):
        raise VerificationError("candidate wheel METADATA identity is not exact")
    wheel_identity = {
        "Wheel-Version": "1.0",
        "Generator": "poetry-core 2.3.1",
        "Root-Is-Purelib": "false",
        "Tag": _CANDIDATE_WHEEL_TAG,
    }
    if (
        set(parsed_wheel.keys()) != set(wheel_identity)
        or any(parsed_wheel.get_all(name, []) != [value] for name, value in wheel_identity.items())
    ):
        raise VerificationError("candidate wheel WHEEL metadata is not exact")
    requirements = parsed_metadata.get_all("Requires-Dist", [])
    if requirements != list(_candidate_requires_dist(inputs)):
        raise VerificationError("candidate wheel METADATA Requires-Dist is not exact")
    expected_dependencies = _candidate_runtime_direct_versions(inputs)
    observed_dependencies: dict[str, str] = {}
    for requirement in requirements:
        try:
            name, specifier, marker = generator._parse_requirement(str(requirement))
            active = generator._metadata_marker_active(marker)
        except RuntimeError as exc:
            raise VerificationError("candidate wheel METADATA dependency is invalid") from exc
        if not active:
            continue
        if name in observed_dependencies or name not in expected_dependencies:
            raise VerificationError("candidate wheel METADATA dependency set drifted")
        version = expected_dependencies[name]
        if specifier and not generator._satisfies(version, specifier):
            raise VerificationError("candidate wheel METADATA dependency constraint drifted")
        observed_dependencies[name] = version
    if observed_dependencies != expected_dependencies:
        raise VerificationError("candidate wheel METADATA dependency set drifted")


def _candidate_single_wheel(dist: Path) -> Path:
    outputs = list(dist.iterdir())
    if (
        len(outputs) != 1
        or outputs[0].is_symlink()
        or not outputs[0].is_file()
        or outputs[0].name != _CANDIDATE_WHEEL_FILENAME
    ):
        raise VerificationError("candidate build did not produce exactly one wheel")
    return outputs[0]


def _candidate_read_raw_wheel(wheel: Path) -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(
            wheel,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise VerificationError("candidate raw wheel is not one regular file")
        if before.st_size > _CANDIDATE_RAW_WHEEL_BYTE_LIMIT:
            raise VerificationError("candidate raw wheel exceeds bounded size")
        with os.fdopen(descriptor, "rb", closefd=True) as source:
            descriptor = -1
            payload = source.read(before.st_size + 1)
            after = os.fstat(source.fileno())
    except VerificationError:
        raise
    except OSError as exc:
        raise VerificationError("candidate raw wheel bounded read failed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if (
        (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
        or after.st_size != before.st_size
        or len(payload) != before.st_size
        or len(payload) > _CANDIDATE_RAW_WHEEL_BYTE_LIMIT
    ):
        raise VerificationError("candidate raw wheel size or identity drifted")
    return payload


def _candidate_artifact_core(
    wheel_payload: bytes,
    engine: dict[str, object],
    inputs: dict[str, object],
    source_tree_sha256: str,
    source_native: dict[str, dict[str, object]],
) -> dict[str, object]:
    _verify_candidate_wheel_archive(wheel_payload)
    native = _candidate_native_inventory(wheel_payload)
    wheel_record = {
        "filename": _CANDIDATE_WHEEL_FILENAME,
        "size": len(wheel_payload),
        "sha256": _sha256_bytes(wheel_payload),
    }
    wheel_native = {str(record["path"]): record for record in native}
    if set(wheel_native) != set(source_native) or any(
        wheel_native[path][field] != source_native[path][field]
        for path in source_native
        for field in ("size", "sha256")
    ):
        raise VerificationError("candidate wheel native output set or bytes drifted")
    runtime_wheels = inputs.get("runtime_wheels")
    if not isinstance(runtime_wheels, list):
        raise VerificationError("candidate runtime wheel authority is invalid")
    return {
        "schema_version": 7,
        "manifest_kind": "NAUTILUS_V1_231_CANDIDATE_ARTIFACT",
        "activation_status": "CANDIDATE_ONLY_NOT_ACTIVATED",
        "engine": {
            "name": "nautilus_trader",
            "version": "1.231.0",
            "upstream_tag": engine["candidate"]["upstream_tag"],  # type: ignore[index]
            "upstream_commit": engine["candidate"]["upstream_commit"],  # type: ignore[index]
        },
        "python": {
            "identity": inputs["python"]["identity"],  # type: ignore[index]
            "abi": inputs["python"]["abi"],  # type: ignore[index]
            "executable_sha256": inputs["python"]["executable_sha256"],  # type: ignore[index]
            "stdlib_tree_sha256": inputs["python"]["stdlib_inventory"]["tree_sha256"],  # type: ignore[index]
        },
        "source": {
            **inputs["source"]["artifact"],  # type: ignore[index]
            "verified_extracted_tree_sha256": source_tree_sha256,
        },
        "policy_hashes": inputs["policy_hashes"],
        "toolchain": {
            "rustc_identity": engine["rust"]["rustc_identity"],  # type: ignore[index]
            "cargo_identity": engine["rust"]["cargo_identity"],  # type: ignore[index]
            "llvm_version": engine["llvm_toolchain"]["version"],  # type: ignore[index]
            "command_router_authority": inputs["command_router"]["authority"],  # type: ignore[index]
        },
        "network": "DISABLED_BY_BUBBLEWRAP_UNSHARE_ALL",
        "wheel": wheel_record,
        "native_libraries": native,
        "runtime_wheels": [
            {key: record[key] for key in ("filename", "package", "version", "mode", "size", "sha256")}
            for record in runtime_wheels
        ],
    }


def _candidate_stage_token() -> str:
    return "stage-" + secrets.token_hex(8)


def _build_candidate_once(
    engine: dict[str, object], inputs: dict[str, object], roots: dict[str, Path]
) -> tuple[bytes, dict[str, int], dict[str, object], dict[str, str], int]:
    logical_stage = roots["candidate_build_root"] / _candidate_stage_token()
    if logical_stage.exists() or logical_stage.is_symlink():
        raise VerificationError("fresh candidate logical stage collided")
    retained_source_fd = -1
    try:
        with tempfile.TemporaryDirectory(
            prefix="p1-u04-physical-stage-", dir="/tmp"
        ) as raw:
            physical_stage = Path(raw)
            os.chmod(physical_stage, 0o700)
            source = physical_stage / "source"
            source_record = inputs["source"]["artifact"]  # type: ignore[index]
            input_root = roots["candidate_input_root"]
            archive = input_root / "source-inputs" / str(source_record["filename"])
            source_tree = _extract_candidate_source(archive, source, source_record)
            _verify_candidate_source_contract(source, engine, inputs)
            try:
                retained_source_fd = os.open(
                    source, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW
                )
            except OSError as exc:
                raise VerificationError(
                    "candidate source directory identity drifted"
                ) from exc
            source_identity = _candidate_source_identity_from_stat(
                os.fstat(retained_source_fd)
            )
            for name in ("artifacts", "cargo-target", "dist", "home", "tmp"):
                (physical_stage / name).mkdir(mode=0o700)
            _candidate_sandbox_run(
                physical_stage=physical_stage,
                logical_stage=logical_stage,
                action="venv",
                timeout=180,
                expected_source_identity=source_identity,
            )
            _candidate_sandbox_run(
                physical_stage=physical_stage,
                logical_stage=logical_stage,
                action="install",
                timeout=900,
                expected_source_identity=source_identity,
            )
            _candidate_sandbox_run(
                physical_stage=physical_stage,
                logical_stage=logical_stage,
                action="native",
                timeout=7200,
                expected_source_identity=source_identity,
            )
            native_outputs = _verify_candidate_native_outputs(source)
            _candidate_sandbox_run(
                physical_stage=physical_stage,
                logical_stage=logical_stage,
                action="package",
                timeout=900,
                expected_source_identity=source_identity,
            )
            if _verify_candidate_native_outputs(source) != native_outputs:
                raise VerificationError(
                    "candidate native outputs drifted during packaging"
                )
            wheel = _candidate_single_wheel(physical_stage / "dist")
            payload = _candidate_read_raw_wheel(wheel)
            preflight = _candidate_wheel_structural_preflight(payload)
            core = _candidate_artifact_core(
                payload, engine, inputs, source_tree, native_outputs
            )
        if logical_stage.exists() or logical_stage.is_symlink():
            raise VerificationError("candidate logical scratch stage escaped the sandbox")
        return (
            payload,
            preflight,
            core,
            {
                key: source_identity[key]
                for key in ("P1_U04_SOURCE_ST_DEV", "P1_U04_SOURCE_ST_INO")
            },
            retained_source_fd,
        )
    except BaseException:
        if retained_source_fd >= 0:
            try:
                os.close(retained_source_fd)
            except OSError:
                pass
        raise


def _candidate_build_policy_binding() -> tuple[dict[str, str], str]:
    _engine, inputs = _verify_candidate_authority()
    environment = inputs.get("native_build_environment")
    if not isinstance(environment, dict):
        raise VerificationError("candidate native build environment is invalid")
    return (
        _candidate_policy_receipt(inputs),
        _sha256_bytes(
            json.dumps(
                environment,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("ascii")
        ),
    )


def _publish_candidate_build_result(
    roots: dict[str, Path],
    *,
    label: str,
    wheel_payload: bytes,
    artifact_core: dict[str, object],
    source_identity: dict[str, str],
    process_identity: dict[str, object],
    x4_authority: dict[str, object],
    x4_receipt_sha256: str,
) -> dict[str, object]:
    if label not in {"A", "B"}:
        raise VerificationError("candidate build result label is invalid")
    _validate_candidate_source_identity(source_identity)
    _validate_candidate_process_identity(process_identity)
    if re.fullmatch(r"[0-9a-f]{64}", x4_receipt_sha256) is None:
        raise VerificationError("candidate build result X4 digest is invalid")
    policy_sha256, sanitized_environment_sha256 = (
        _candidate_build_policy_binding()
    )
    candidate = x4_authority.get("candidate")
    authority_identities = x4_authority.get("identities")
    if (
        not isinstance(candidate, dict)
        or candidate != _candidate_git_identity()
        or x4_authority.get("policy_sha256") != policy_sha256
        or not isinstance(authority_identities, dict)
        or not authority_identities
    ):
        raise VerificationError("candidate build result X4 authority is invalid")
    destination = roots["candidate_build_root"] / (
        _BUILD_A_DIRECTORY if label == "A" else _BUILD_B_DIRECTORY
    )
    if destination.exists() or destination.is_symlink():
        raise VerificationError(f"Build {label} destination is not absent")
    parent = destination.parent.lstat()
    if (
        not stat.S_ISDIR(parent.st_mode)
        or stat.S_ISLNK(parent.st_mode)
        or parent.st_uid != os.geteuid()
        or stat.S_IMODE(parent.st_mode) != 0o700
    ):
        raise VerificationError(f"Build {label} parent is not private")
    parent_identity = (parent.st_dev, parent.st_ino)
    core_raw = (
        json.dumps(artifact_core, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    ).encode("ascii")
    wheel_record = artifact_core.get("wheel")
    if (
        not isinstance(wheel_record, dict)
        or wheel_record
        != {
            "filename": _CANDIDATE_WHEEL_FILENAME,
            "sha256": _sha256_bytes(wheel_payload),
            "size": len(wheel_payload),
        }
    ):
        raise VerificationError(f"Build {label} wheel authority is invalid")
    receipt: dict[str, object] = {
        "artifact_core": {
            "filename": _CANDIDATE_ARTIFACT_CORE,
            "sha256": _sha256_bytes(core_raw),
            "size": len(core_raw),
        },
        "authority_identities": authority_identities,
        "candidate": candidate,
        "file_set": [
            _CANDIDATE_WHEEL_FILENAME,
            _CANDIDATE_ARTIFACT_CORE,
            _CANDIDATE_BUILD_RECEIPT,
        ],
        "kind": f"P1_U04_BUILD_{label}",
        "label": label,
        "policy_sha256": policy_sha256,
        "process_identity": process_identity,
        "sanitized_environment_sha256": sanitized_environment_sha256,
        "schema": _CANDIDATE_BUILD_RESULT_SCHEMA,
        "source_identity": source_identity,
        "wheel": wheel_record,
        "x4_authority_receipt_sha256": x4_receipt_sha256,
    }
    receipt_raw = (
        json.dumps(receipt, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    ).encode("ascii")
    stage = destination.parent / f".{destination.name}-{secrets.token_hex(8)}"
    stage.mkdir(mode=0o700)
    published = False

    def publication_committed() -> None:
        nonlocal published
        published = True

    try:
        wheel = stage / _CANDIDATE_WHEEL_FILENAME
        core = stage / _CANDIDATE_ARTIFACT_CORE
        build_receipt = stage / _CANDIDATE_BUILD_RECEIPT
        wheel.write_bytes(wheel_payload)
        core.write_bytes(core_raw)
        build_receipt.write_bytes(receipt_raw)
        _seal_candidate_tree(stage)
        stage_info = stage.lstat()
        stage_identity = (stage_info.st_dev, stage_info.st_ino)
        expected_files = {wheel, core, build_receipt}
        if (
            stat.S_IMODE(stage_info.st_mode) != 0o500
            or set(stage.iterdir()) != expected_files
            or any(stat.S_IMODE(path.lstat().st_mode) != 0o400 for path in expected_files)
            or wheel.read_bytes() != wheel_payload
            or core.read_bytes() != core_raw
            or build_receipt.read_bytes() != receipt_raw
        ):
            raise VerificationError(f"Build {label} staging confirmation failed")
        _rename_noreplace(
            stage,
            destination,
            expected_identity=stage_identity,
            parent_identity=parent_identity,
            publication_committed=publication_committed,
        )
    except BaseException:
        if not published and (stage.exists() or stage.is_symlink()):
            if stage.is_dir() and not stage.is_symlink():
                _thaw_tree(stage)
                shutil.rmtree(stage)
            else:
                stage.unlink()
        raise
    return receipt


def _load_candidate_build_result(
    roots: dict[str, Path],
    *,
    label: str,
) -> tuple[bytes, dict[str, object], dict[str, object], str]:
    if label not in {"A", "B"}:
        raise VerificationError("candidate build result label is invalid")
    directory = roots["candidate_build_root"] / (
        _BUILD_A_DIRECTORY if label == "A" else _BUILD_B_DIRECTORY
    )
    try:
        info = directory.lstat()
    except OSError as exc:
        raise VerificationError(f"Build {label} result is unavailable") from exc
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o500
    ):
        raise VerificationError(f"Build {label} result is not sealed")
    wheel = directory / _CANDIDATE_WHEEL_FILENAME
    core_path = directory / _CANDIDATE_ARTIFACT_CORE
    receipt_path = directory / _CANDIDATE_BUILD_RECEIPT
    if set(directory.iterdir()) != {wheel, core_path, receipt_path}:
        raise VerificationError(f"Build {label} file set drifted")
    try:
        wheel_payload = _read_x4_receipt_file(
            wheel,
            label=f"Build {label} wheel",
            maximum_size=_CANDIDATE_RAW_WHEEL_BYTE_LIMIT,
        )
        core_raw = _read_x4_receipt_file(
            core_path, label=f"Build {label} artifact core", maximum_size=16 * 1024 * 1024
        )
        receipt_raw = _read_x4_receipt_file(
            receipt_path, label=f"Build {label} receipt"
        )
        artifact_core = json.loads(core_raw)
        receipt = json.loads(receipt_raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError(f"Build {label} JSON is invalid") from exc
    if (
        not isinstance(artifact_core, dict)
        or not isinstance(receipt, dict)
        or core_raw
        != (
            json.dumps(artifact_core, ensure_ascii=True, sort_keys=True, indent=2)
            + "\n"
        ).encode("ascii")
        or receipt_raw
        != (
            json.dumps(receipt, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
        ).encode("ascii")
        or set(receipt)
        != {
            "artifact_core",
            "authority_identities",
            "candidate",
            "file_set",
            "kind",
            "label",
            "policy_sha256",
            "process_identity",
            "sanitized_environment_sha256",
            "schema",
            "source_identity",
            "wheel",
            "x4_authority_receipt_sha256",
        }
        or receipt.get("schema") != _CANDIDATE_BUILD_RESULT_SCHEMA
        or receipt.get("candidate") != _candidate_git_identity()
        or not isinstance(receipt.get("authority_identities"), dict)
        or not receipt["authority_identities"]
        or receipt.get("kind") != f"P1_U04_BUILD_{label}"
        or receipt.get("label") != label
        or receipt.get("file_set")
        != [_CANDIDATE_WHEEL_FILENAME, _CANDIDATE_ARTIFACT_CORE, _CANDIDATE_BUILD_RECEIPT]
        or receipt.get("wheel") != artifact_core.get("wheel")
        or receipt.get("wheel")
        != {
            "filename": _CANDIDATE_WHEEL_FILENAME,
            "sha256": _sha256_bytes(wheel_payload),
            "size": len(wheel_payload),
        }
        or receipt.get("artifact_core")
        != {
            "filename": _CANDIDATE_ARTIFACT_CORE,
            "sha256": _sha256_bytes(core_raw),
            "size": len(core_raw),
        }
    ):
        raise VerificationError(f"Build {label} authority drifted")
    _validate_candidate_source_identity(receipt.get("source_identity"))
    _validate_candidate_process_identity(receipt.get("process_identity"))
    policy_sha256, sanitized_environment_sha256 = _candidate_build_policy_binding()
    if (
        receipt.get("policy_sha256") != policy_sha256
        or receipt.get("sanitized_environment_sha256")
        != sanitized_environment_sha256
        or not isinstance(receipt.get("x4_authority_receipt_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", receipt["x4_authority_receipt_sha256"])
        is None
    ):
        raise VerificationError(f"Build {label} policy authority drifted")
    receipt_sha256 = _sha256_bytes(receipt_raw)
    return wheel_payload, artifact_core, receipt, receipt_sha256


def _publish_candidate_artifacts(
    roots: dict[str, Path], wheel_payload: bytes, core: dict[str, object], receipt: dict[str, object]
) -> Path:
    destination = roots["candidate_build_root"] / "artifacts"
    if destination.exists() or destination.is_symlink():
        raise VerificationError("candidate artifact destination is not absent")
    parent = destination.parent.lstat()
    if (
        not stat.S_ISDIR(parent.st_mode)
        or stat.S_ISLNK(parent.st_mode)
        or parent.st_uid != os.geteuid()
        or stat.S_IMODE(parent.st_mode) & 0o077
    ):
        raise VerificationError("candidate artifact parent is not private")
    parent_identity = (parent.st_dev, parent.st_ino)
    stage = roots["candidate_build_root"] / f".artifacts-{secrets.token_hex(8)}"
    stage.mkdir(mode=0o700)
    published = False
    stage_identity: tuple[int, int] | None = None

    def publication_committed() -> None:
        nonlocal published
        published = True

    try:
        wheel_record = core["wheel"]
        assert isinstance(wheel_record, dict)
        wheel = stage / str(wheel_record["filename"])
        wheel.write_bytes(wheel_payload)
        manifest = {**core, "reproducible_build": receipt}
        manifest_path = stage / _ARTIFACT_MANIFEST
        manifest_raw = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode("ascii")
        manifest_path.write_bytes(manifest_raw)
        _seal_candidate_tree(stage)
        staged = stage.lstat()
        stage_identity = (staged.st_dev, staged.st_ino)
        if _candidate_json(manifest_path) != manifest:
            raise VerificationError("candidate artifact staging manifest drifted")
        observed = stage.lstat()
        if (observed.st_dev, observed.st_ino) != stage_identity:
            raise VerificationError("candidate artifact staging identity changed during validation")
        if (
            manifest_path.read_bytes() != manifest_raw
            or wheel.read_bytes() != wheel_payload
            or set(stage.iterdir()) != {manifest_path, wheel}
        ):
            raise VerificationError("candidate artifact staging bytes drifted")
        observed = stage.lstat()
        if (observed.st_dev, observed.st_ino) != stage_identity:
            raise VerificationError("candidate artifact staging identity changed during validation")
        current_parent = destination.parent.lstat()
        if (current_parent.st_dev, current_parent.st_ino) != parent_identity:
            raise VerificationError("candidate artifact parent identity changed before publication")
        _rename_noreplace(
            stage,
            destination,
            expected_identity=stage_identity,
            parent_identity=parent_identity,
            publication_committed=publication_committed,
        )
    except BaseException:
        if not published and stage_identity is None and stage.exists():
            _thaw_tree(stage)
            shutil.rmtree(stage)
        raise
    return destination


def _retain_candidate_raw_wheel_pair(
    destination: Path,
    parent_identity: tuple[int, int],
    first_payload: bytes,
    second_payload: bytes,
    source_fd_identities: tuple[dict[str, str], dict[str, str]],
) -> None:
    identity_keys = {"P1_U04_SOURCE_ST_DEV", "P1_U04_SOURCE_ST_INO"}
    for identity in source_fd_identities:
        if set(identity) != identity_keys or any(
            not isinstance(value, str)
            or not value.isascii()
            or not value.isdecimal()
            or value.startswith("0")
            for value in identity.values()
        ):
            raise VerificationError("candidate forensic source identity is invalid")
    first_name = f"first-{_CANDIDATE_WHEEL_FILENAME}"
    second_name = f"second-{_CANDIDATE_WHEEL_FILENAME}"
    manifest = {
        "activation_status": "CANDIDATE_ONLY_NOT_ACTIVATED",
        "build_count": 2,
        "engine_build_policy_sha256": _sha256(_CANDIDATE_ENGINE_POLICY),
        "kind": "P1_U04_RAW_WHEEL_PAIR",
        "raw_wheel_equality": first_payload == second_payload,
        "raw_wheels": [
            {
                "filename": first_name,
                "label": "first",
                "sha256": _sha256_bytes(first_payload),
                "size": len(first_payload),
            },
            {
                "filename": second_name,
                "label": "second",
                "sha256": _sha256_bytes(second_payload),
                "size": len(second_payload),
            },
        ],
        "schema_version": 1,
        "source_fd_identities": list(source_fd_identities),
        "toolchain_inputs_sha256": _sha256(_CANDIDATE_TOOLCHAIN_INPUTS),
    }
    manifest_raw = (_candidate_serialize_diagnostic(manifest) + "\n").encode("ascii")
    stage = destination.parent / f".{destination.name}-{secrets.token_hex(8)}"
    stage.mkdir(mode=0o700)
    first_path = stage / first_name
    second_path = stage / second_name
    manifest_path = stage / _CANDIDATE_FORENSIC_MANIFEST
    first_path.write_bytes(first_payload)
    second_path.write_bytes(second_payload)
    manifest_path.write_bytes(manifest_raw)
    _seal_candidate_tree(stage)
    staged = stage.lstat()
    stage_identity = (staged.st_dev, staged.st_ino)
    expected_files = {first_path, second_path, manifest_path}
    if (
        stat.S_IMODE(staged.st_mode) != 0o500
        or set(stage.iterdir()) != expected_files
        or any(
            stat.S_IMODE(path.lstat().st_mode) != 0o400
            for path in expected_files
        )
        or first_path.read_bytes() != first_payload
        or second_path.read_bytes() != second_payload
        or manifest_path.read_bytes() != manifest_raw
        or _candidate_json(manifest_path) != manifest
    ):
        raise VerificationError("candidate forensic staging confirmation failed")
    observed = stage.lstat()
    if (observed.st_dev, observed.st_ino) != stage_identity:
        raise VerificationError("candidate forensic staging identity changed")
    current_parent = destination.parent.lstat()
    if (current_parent.st_dev, current_parent.st_ino) != parent_identity:
        raise VerificationError("candidate forensic parent identity changed")
    _rename_noreplace(
        stage,
        destination,
        expected_identity=stage_identity,
        parent_identity=parent_identity,
        publication_committed=lambda: None,
    )


def _candidate_serialize_diagnostic(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _candidate_zip_eocd(payload: bytes) -> tuple[int, int, int, int]:
    eocd_size = 22
    maximum_comment_size = 65_535
    if len(payload) < eocd_size:
        raise VerificationError("candidate diagnostic ZIP EOCD is missing")
    tail_offset = max(0, len(payload) - eocd_size - maximum_comment_size)
    tail = payload[tail_offset:]
    signature = b"PK\x05\x06"
    candidate: tuple[int, tuple[int, ...]] | None = None
    offset = 0
    while True:
        offset = tail.find(signature, offset)
        if offset < 0:
            break
        if len(tail) - offset >= eocd_size:
            fields = struct.unpack_from("<4s4H2LH", tail, offset)
            comment_size = fields[-1]
            absolute_offset = tail_offset + offset
            if absolute_offset + eocd_size + comment_size == len(payload):
                if candidate is not None:
                    raise VerificationError(
                        "candidate diagnostic ZIP EOCD is ambiguous"
                    )
                candidate = (absolute_offset, fields[1:])
        offset += 1
    if candidate is None:
        raise VerificationError("candidate diagnostic ZIP EOCD is ambiguous")
    eocd_offset, fields = candidate
    (
        disk_number,
        central_directory_disk,
        disk_entries,
        total_entries,
        central_directory_size,
        central_directory_offset,
        _comment_size,
    ) = fields
    if (
        disk_number != 0
        or central_directory_disk != 0
        or disk_entries != total_entries
    ):
        raise VerificationError("candidate diagnostic multi-disk ZIP is unsupported")
    if (
        total_entries == 0xFFFF
        or central_directory_size == 0xFFFFFFFF
        or central_directory_offset == 0xFFFFFFFF
    ):
        raise VerificationError("candidate diagnostic ZIP64 is unsupported")
    if (
        central_directory_size > eocd_offset
        or central_directory_offset > eocd_offset
        or central_directory_size + central_directory_offset > eocd_offset
    ):
        raise VerificationError("candidate diagnostic ZIP central directory is invalid")
    return (
        eocd_offset,
        total_entries,
        central_directory_offset,
        central_directory_size,
    )


def _candidate_zip_extra_fields(payload: bytes, start: int, length: int) -> None:
    end = start + length
    if start < 0 or length < 0 or end > len(payload):
        raise VerificationError("candidate diagnostic ZIP extra field is invalid")
    cursor = start
    while cursor < end:
        if cursor + 4 > end:
            raise VerificationError("candidate diagnostic ZIP extra field is invalid")
        header_id, data_size = struct.unpack_from("<HH", payload, cursor)
        cursor += 4
        if cursor + data_size > end:
            raise VerificationError("candidate diagnostic ZIP extra field is invalid")
        if header_id == 0x0001:
            raise VerificationError("candidate diagnostic ZIP64 is unsupported")
        cursor += data_size


def _candidate_wheel_structural_preflight(payload: bytes) -> dict[str, int]:
    eocd_offset, declared_count, central_offset, central_size = (
        _candidate_zip_eocd(payload)
    )
    central_end = central_offset + central_size
    if central_end != eocd_offset:
        locator_offset = eocd_offset - 20
        if (
            payload.startswith((b"PK\x06\x06", b"PK\x06\x07"), central_end)
            or locator_offset >= central_end
            and payload.startswith(b"PK\x06\x07", locator_offset)
        ):
            raise VerificationError("candidate diagnostic ZIP64 is unsupported")
        raise VerificationError("candidate diagnostic ZIP central directory is invalid")

    values = {
        "compressed_size": 0,
        "declared_uncompressed_size": 0,
        "invalid_member_size_count": 0,
        "member_count": 0,
        "streamed_expanded_bytes": 0,
    }
    if declared_count > _CANDIDATE_RAW_WHEEL_MEMBER_LIMIT:
        raise VerificationError("candidate diagnostic ZIP resource limit exceeded")
    records: list[tuple[int, int, int, int, int, int, int, int, int]] = []
    local_offsets: set[int] = set()
    view = memoryview(payload)
    cursor = central_offset
    while cursor < central_end:
        if cursor + 46 > central_end or not payload.startswith(
            b"PK\x01\x02", cursor
        ):
            raise VerificationError("candidate diagnostic ZIP central directory is invalid")
        (
            _signature,
            _made_version,
            needed_version,
            flags,
            compression_method,
            _modified_time,
            _modified_date,
            crc,
            compressed_size,
            uncompressed_size,
            name_size,
            extra_size,
            comment_size,
            disk_start,
            _internal_attributes,
            _external_attributes,
            local_offset,
        ) = struct.unpack_from(
            "<4s6H3L5H2L", payload, cursor
        )
        entry_end = cursor + 46 + name_size + extra_size + comment_size
        if entry_end > central_end:
            raise VerificationError("candidate diagnostic ZIP central directory is invalid")
        if (
            compressed_size == 0xFFFFFFFF
            or uncompressed_size == 0xFFFFFFFF
            or local_offset == 0xFFFFFFFF
            or disk_start == 0xFFFF
        ):
            raise VerificationError("candidate diagnostic ZIP64 is unsupported")
        if disk_start != 0:
            raise VerificationError(
                "candidate diagnostic multi-disk ZIP is unsupported"
            )
        if (
            needed_version != 20
            or flags & ~0x0800
            or flags & 0x0008
            or compression_method
            not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
        ):
            raise VerificationError("candidate diagnostic ZIP metadata is unsupported")
        if local_offset in local_offsets:
            raise VerificationError(
                "candidate diagnostic ZIP local header is duplicated"
            )
        local_offsets.add(local_offset)
        values["member_count"] += 1
        values["compressed_size"] += compressed_size
        values["declared_uncompressed_size"] += uncompressed_size
        if (
            values["member_count"] > _CANDIDATE_RAW_WHEEL_MEMBER_LIMIT
            or values["compressed_size"]
            > _CANDIDATE_RAW_WHEEL_COMPRESSED_SIZE_LIMIT
            or values["declared_uncompressed_size"]
            > _CANDIDATE_RAW_WHEEL_DECLARED_SIZE_LIMIT
        ):
            raise VerificationError("candidate diagnostic ZIP resource limit exceeded")
        central_name = cursor + 46
        _candidate_zip_extra_fields(
            payload, central_name + name_size, extra_size
        )
        records.append(
            (
                local_offset,
                central_name,
                name_size,
                needed_version,
                flags,
                compression_method,
                crc,
                compressed_size,
                uncompressed_size,
            )
        )
        cursor = entry_end
    if cursor != central_end or values["member_count"] != declared_count:
        raise VerificationError("candidate diagnostic ZIP entry count is inconsistent")

    previous_region_end = 0
    for (
        local_offset,
        central_name,
        central_name_size,
        central_needed_version,
        central_flags,
        central_compression_method,
        central_crc,
        central_compressed_size,
        central_uncompressed_size,
    ) in sorted(records):
        if local_offset + 30 > central_offset or not payload.startswith(
            b"PK\x03\x04", local_offset
        ):
            raise VerificationError("candidate diagnostic ZIP local header is invalid")
        (
            _local_signature,
            local_needed_version,
            local_flags,
            local_compression_method,
            _local_modified_time,
            _local_modified_date,
            local_crc,
            local_compressed_size,
            local_uncompressed_size,
            local_name_size,
            local_extra_size,
        ) = struct.unpack_from(
            "<4s5H3L2H", payload, local_offset
        )
        local_name = local_offset + 30
        data_start = local_name + local_name_size + local_extra_size
        region_end = data_start + local_compressed_size
        if data_start > central_offset or region_end > central_offset:
            raise VerificationError("candidate diagnostic ZIP local header is invalid")
        if view[local_name : local_name + local_name_size] != view[
            central_name : central_name + central_name_size
        ]:
            raise VerificationError("candidate diagnostic ZIP member name is inconsistent")
        if (
            local_compressed_size == 0xFFFFFFFF
            or local_uncompressed_size == 0xFFFFFFFF
        ):
            raise VerificationError("candidate diagnostic ZIP64 is unsupported")
        if (
            local_needed_version != central_needed_version
            or local_flags != central_flags
            or local_compression_method != central_compression_method
            or local_crc != central_crc
            or local_compressed_size != central_compressed_size
            or local_uncompressed_size != central_uncompressed_size
        ):
            raise VerificationError("candidate diagnostic ZIP metadata is inconsistent")
        if local_offset != previous_region_end:
            raise VerificationError("candidate diagnostic ZIP local regions are invalid")
        _candidate_zip_extra_fields(
            payload, local_name + local_name_size, local_extra_size
        )
        previous_region_end = region_end
    if previous_region_end != central_offset:
        raise VerificationError("candidate diagnostic ZIP local regions are invalid")
    return values


def _candidate_sequence_digest(values: Iterable[object]) -> str:
    digest = hashlib.sha256()
    digest.update(b"[")
    for index, value in enumerate(values):
        if index:
            digest.update(b",")
        digest.update(_candidate_serialize_diagnostic(value).encode("ascii"))
    digest.update(b"]")
    return digest.hexdigest()


def _candidate_difference_digest_update(
    digest: Any,
    index: int,
    value: dict[str, object],
) -> None:
    if index:
        digest.update(b",")
    digest.update(_candidate_serialize_diagnostic(value).encode("ascii"))


def _candidate_difference_summary(
    total: int,
    retained: list[dict[str, object]],
    digest: Any,
) -> dict[str, object]:
    digest.update(b"]")
    return {
        "emitted": len(retained),
        "entries": retained,
        "omitted": total - len(retained),
        "sha256": digest.hexdigest(),
        "total": total,
    }


def _candidate_diagnostic_member_name(name: str) -> str:
    serialized = _candidate_serialize_diagnostic(name).encode("ascii")
    if len(serialized) <= _CANDIDATE_RAW_WHEEL_NAME_BYTE_LIMIT:
        return name
    name_digest = _sha256_bytes(name.encode("utf-8", errors="surrogatepass"))
    suffix = f"...[utf8_sha256={name_digest}]"
    low = 0
    high = len(name)
    while low < high:
        middle = (low + high + 1) // 2
        candidate = name[:middle] + suffix
        if (
            len(_candidate_serialize_diagnostic(candidate).encode("ascii"))
            <= _CANDIDATE_RAW_WHEEL_NAME_BYTE_LIMIT
        ):
            low = middle
        else:
            high = middle - 1
    return name[:low] + suffix


def _candidate_order_difference(
    index: int,
    first_name: str | None,
    second_name: str | None,
) -> dict[str, object]:
    return {
        "first": (
            _candidate_diagnostic_member_name(first_name)
            if first_name is not None
            else None
        ),
        "first_name_utf8_sha256": (
            _sha256_bytes(first_name.encode("utf-8", errors="surrogatepass"))
            if first_name is not None
            else None
        ),
        "index": index,
        "second": (
            _candidate_diagnostic_member_name(second_name)
            if second_name is not None
            else None
        ),
        "second_name_utf8_sha256": (
            _sha256_bytes(second_name.encode("utf-8", errors="surrogatepass"))
            if second_name is not None
            else None
        ),
    }


class _CandidateDiagnosticResourceLimit(RuntimeError):
    def __init__(
        self,
        limit_type: str,
        limit: int,
        observed: int,
        resources: dict[str, object],
    ) -> None:
        super().__init__(limit_type)
        self.limit_type = limit_type
        self.limit = limit
        self.observed = observed
        self.resources = resources


def _candidate_wheel_preflight(infos: list[zipfile.ZipInfo]) -> dict[str, int]:
    compressed_size = 0
    declared_size = 0
    invalid_member_size_count = 0
    for info in infos:
        if info.compress_size < 0 or info.file_size < 0:
            invalid_member_size_count += 1
        compressed_size += max(info.compress_size, 0)
        declared_size += max(info.file_size, 0)
    return {
        "compressed_size": compressed_size,
        "declared_uncompressed_size": declared_size,
        "invalid_member_size_count": invalid_member_size_count,
        "member_count": len(infos),
        "streamed_expanded_bytes": 0,
    }


def _candidate_resource_limits() -> dict[str, int]:
    return {
        "compressed_size": _CANDIDATE_RAW_WHEEL_COMPRESSED_SIZE_LIMIT,
        "declared_uncompressed_size": _CANDIDATE_RAW_WHEEL_DECLARED_SIZE_LIMIT,
        "invalid_declared_member_size": 0,
        "member_count": _CANDIDATE_RAW_WHEEL_MEMBER_LIMIT,
        "serialized_output_bytes": _CANDIDATE_RAW_WHEEL_OUTPUT_BYTE_LIMIT,
        "streamed_expanded_bytes": _CANDIDATE_RAW_WHEEL_STREAMED_SIZE_LIMIT,
    }


def _candidate_check_preflight_limits(resources: dict[str, object]) -> None:
    observed = resources["observed"]
    assert isinstance(observed, dict)
    limits = _candidate_resource_limits()
    for label in ("first", "second"):
        values = observed[label]
        assert isinstance(values, dict)
        invalid_count = values["invalid_member_size_count"]
        assert isinstance(invalid_count, int)
        if invalid_count:
            raise _CandidateDiagnosticResourceLimit(
                "invalid_declared_member_size",
                0,
                invalid_count,
                resources,
            )
        for limit_type in (
            "member_count",
            "compressed_size",
            "declared_uncompressed_size",
        ):
            value = values[limit_type]
            assert isinstance(value, int)
            if value > limits[limit_type]:
                raise _CandidateDiagnosticResourceLimit(
                    limit_type,
                    limits[limit_type],
                    value,
                    resources,
                )


def _candidate_member_index(
    infos: list[zipfile.ZipInfo],
) -> dict[tuple[str, int], zipfile.ZipInfo]:
    occurrences: dict[str, int] = {}
    indexed: dict[tuple[str, int], zipfile.ZipInfo] = {}
    for info in infos:
        occurrence = occurrences.get(info.filename, 0)
        occurrences[info.filename] = occurrence + 1
        indexed[(info.filename, occurrence)] = info
    return indexed


def _candidate_wheel_member_record(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    resources: dict[str, object],
    label: str,
) -> dict[str, object]:
    observed = resources["observed"]
    assert isinstance(observed, dict)
    values = observed[label]
    assert isinstance(values, dict)
    content_digest = hashlib.sha256()
    with archive.open(info) as member:
        for block in iter(lambda: member.read(64 * 1024), b""):
            streamed = values["streamed_expanded_bytes"]
            assert isinstance(streamed, int)
            streamed += len(block)
            values["streamed_expanded_bytes"] = streamed
            if streamed > _CANDIDATE_RAW_WHEEL_STREAMED_SIZE_LIMIT:
                raise _CandidateDiagnosticResourceLimit(
                    "streamed_expanded_bytes",
                    _CANDIDATE_RAW_WHEEL_STREAMED_SIZE_LIMIT,
                    streamed,
                    resources,
                )
            content_digest.update(block)
    mode = (info.external_attr >> 16) & 0xFFFF
    return {
        "comment_sha256": _sha256_bytes(info.comment),
        "comment_size": len(info.comment),
        "compressed_size": info.compress_size,
        "compression": zipfile.compressor_names.get(
            info.compress_type, f"UNKNOWN_{info.compress_type}"
        ),
        "compression_code": info.compress_type,
        "content_sha256": content_digest.hexdigest(),
        "crc32": f"{info.CRC:08x}",
        "create_system": info.create_system,
        "create_version": info.create_version,
        "external_attributes": info.external_attr,
        "extra_sha256": _sha256_bytes(info.extra),
        "extra_size": len(info.extra),
        "extract_version": info.extract_version,
        "flag_bits": info.flag_bits,
        "internal_attributes": info.internal_attr,
        "mode": f"{mode:06o}",
        "size": info.file_size,
        "zip_local_timestamp": "%04d-%02d-%02dT%02d:%02d:%02d"
        % info.date_time,
    }


def _candidate_raw_wheel_diagnostic(
    first_payload: bytes,
    second_payload: bytes,
    first_preflight: dict[str, int],
    second_preflight: dict[str, int],
) -> dict[str, object]:
    resources: dict[str, object] = {
        "limits": _candidate_resource_limits(),
        "observed": {
            "first": dict(first_preflight),
            "second": dict(second_preflight),
        },
        "retained_detail_limit": _CANDIDATE_RAW_WHEEL_DIAGNOSTIC_LIMIT,
    }
    _candidate_check_preflight_limits(resources)
    with zipfile.ZipFile(io.BytesIO(first_payload)) as first_zip, zipfile.ZipFile(
        io.BytesIO(second_payload)
    ) as second_zip:
        first_infos = first_zip.infolist()
        second_infos = second_zip.infolist()
        first_zip_preflight = _candidate_wheel_preflight(first_infos)
        second_zip_preflight = _candidate_wheel_preflight(second_infos)
        if (
            first_zip_preflight != first_preflight
            or second_zip_preflight != second_preflight
        ):
            raise VerificationError(
                "candidate diagnostic ZIP entry count is inconsistent"
            )
        first_archive = {
            "comment_sha256": _sha256_bytes(first_zip.comment),
            "comment_size": len(first_zip.comment),
        }
        second_archive = {
            "comment_sha256": _sha256_bytes(second_zip.comment),
            "comment_size": len(second_zip.comment),
        }
        archive_metadata_equal = first_archive == second_archive
        order_digest = hashlib.sha256()
        order_digest.update(b"[")
        order_total = 0
        for index in range(max(len(first_infos), len(second_infos))):
            first_name = (
                first_infos[index].filename if index < len(first_infos) else None
            )
            second_name = (
                second_infos[index].filename if index < len(second_infos) else None
            )
            if first_name == second_name:
                continue
            entry = _candidate_order_difference(index, first_name, second_name)
            _candidate_difference_digest_update(order_digest, order_total, entry)
            order_total += 1
        first_index = _candidate_member_index(first_infos)
        second_index = _candidate_member_index(second_infos)
        first_records_digest = hashlib.sha256()
        first_records_digest.update(b"[")
        second_records_digest = hashlib.sha256()
        second_records_digest.update(b"[")
        first_record_count = 0
        second_record_count = 0
        member_digest = hashlib.sha256()
        member_digest.update(b"[")
        member_total = 0
        retained_members: list[dict[str, object]] = []
        native_content_drift = False
        non_native_content_drift = False
        for name, occurrence in sorted(set(first_index) | set(second_index)):
            first_info = first_index.get((name, occurrence))
            second_info = second_index.get((name, occurrence))
            first = (
                _candidate_wheel_member_record(
                    first_zip, first_info, resources, "first"
                )
                if first_info is not None
                else None
            )
            second = (
                _candidate_wheel_member_record(
                    second_zip, second_info, resources, "second"
                )
                if second_info is not None
                else None
            )
            if first is not None:
                _candidate_difference_digest_update(
                    first_records_digest,
                    first_record_count,
                    {
                        "name_utf8_sha256": _sha256_bytes(
                            name.encode("utf-8", errors="surrogatepass")
                        ),
                        "occurrence": occurrence,
                        "record": first,
                    },
                )
                first_record_count += 1
            if second is not None:
                _candidate_difference_digest_update(
                    second_records_digest,
                    second_record_count,
                    {
                        "name_utf8_sha256": _sha256_bytes(
                            name.encode("utf-8", errors="surrogatepass")
                        ),
                        "occurrence": occurrence,
                        "record": second,
                    },
                )
                second_record_count += 1
            if first == second:
                continue
            if first is None or second is None:
                changed_fields = ["member_presence"]
                content_changed = True
            else:
                changed_fields = sorted(
                    field for field in first if first[field] != second[field]
                )
                content_changed = (
                    first["content_sha256"] != second["content_sha256"]
                )
            if content_changed:
                if _NATIVE_RE.search(name) is not None:
                    native_content_drift = True
                else:
                    non_native_content_drift = True
            entry = {
                "changed_fields": changed_fields,
                "first": first,
                "name": _candidate_diagnostic_member_name(name),
                "name_utf8_sha256": _sha256_bytes(
                    name.encode("utf-8", errors="surrogatepass")
                ),
                "native": _NATIVE_RE.search(name) is not None,
                "occurrence": occurrence,
                "second": second,
            }
            _candidate_difference_digest_update(member_digest, member_total, entry)
            member_total += 1
            if len(retained_members) < _CANDIDATE_RAW_WHEEL_DIAGNOSTIC_LIMIT:
                retained_members.append(entry)
        first_records_digest.update(b"]")
        second_records_digest.update(b"]")
        order_equal = all(
            first.filename == second.filename
            for first, second in zip(first_infos, second_infos, strict=False)
        ) and len(first_infos) == len(second_infos)
        member_summary = _candidate_difference_summary(
            member_total, retained_members, member_digest
        )
        retained_order: list[dict[str, object]] = []
        remaining_details = (
            _CANDIDATE_RAW_WHEEL_DIAGNOSTIC_LIMIT - len(retained_members)
        )
        if remaining_details:
            for index in range(max(len(first_infos), len(second_infos))):
                first_name = (
                    first_infos[index].filename if index < len(first_infos) else None
                )
                second_name = (
                    second_infos[index].filename
                    if index < len(second_infos)
                    else None
                )
                if first_name != second_name:
                    retained_order.append(
                        _candidate_order_difference(index, first_name, second_name)
                    )
                    if len(retained_order) == remaining_details:
                        break
        order_summary = _candidate_difference_summary(
            order_total, retained_order, order_digest
        )
        resources["retained_detail_count"] = len(retained_members) + len(
            retained_order
        )
    if native_content_drift and non_native_content_drift:
        classification = "MIXED_NATIVE_AND_NON_NATIVE_CONTENT_DRIFT"
    elif native_content_drift:
        classification = "NATIVE_PAYLOAD_DRIFT"
    elif non_native_content_drift:
        classification = "NON_NATIVE_CONTENT_DRIFT"
    elif not order_equal and (member_total or not archive_metadata_equal):
        classification = "ARCHIVE_ORDER_AND_METADATA_ONLY"
    elif not order_equal:
        classification = "ARCHIVE_ORDER_ONLY"
    elif member_total or not archive_metadata_equal:
        classification = "ARCHIVE_METADATA_ONLY"
    elif first_payload != second_payload:
        classification = "ARCHIVE_CONTAINER_BYTES_ONLY"
    else:
        classification = "RAW_BYTES_EQUAL"
    return {
        "archive_metadata": {
            "equal": archive_metadata_equal,
            "first": first_archive,
            "second": second_archive,
        },
        "classification": classification,
        "kind": "CANDIDATE_RAW_WHEEL_DRIFT",
        "member_differences": member_summary,
        "member_records": {
            "first": {
                "count": first_record_count,
                "sha256": first_records_digest.hexdigest(),
            },
            "second": {
                "count": second_record_count,
                "sha256": second_records_digest.hexdigest(),
            },
        },
        "ordered_members": {
            "differences": order_summary,
            "equal": order_equal,
            "first": {
                "count": len(first_infos),
                "names_sha256": _candidate_sequence_digest(
                    info.filename for info in first_infos
                ),
            },
            "second": {
                "count": len(second_infos),
                "names_sha256": _candidate_sequence_digest(
                    info.filename for info in second_infos
                ),
            },
        },
        "raw_wheels": {
            "first": {
                "sha256": _sha256_bytes(first_payload),
                "size": len(first_payload),
            },
            "second": {
                "sha256": _sha256_bytes(second_payload),
                "size": len(second_payload),
            },
        },
        "resources": resources,
        "schema_version": 1,
    }


def _candidate_raw_wheel_resource_receipt(
    first_payload: bytes,
    second_payload: bytes,
    *,
    limit_type: str,
    limit: int,
    observed: int,
    resources: dict[str, object],
) -> dict[str, object]:
    return {
        "classification": "DIAGNOSTIC_RESOURCE_LIMIT",
        "kind": "CANDIDATE_RAW_WHEEL_DRIFT",
        "raw_wheels": {
            "first": {
                "sha256": _sha256_bytes(first_payload),
                "size": len(first_payload),
            },
            "second": {
                "sha256": _sha256_bytes(second_payload),
                "size": len(second_payload),
            },
        },
        "resource_limit": {
            "limit": limit,
            "limit_type": limit_type,
            "observed": observed,
        },
        "resources": resources,
        "schema_version": 1,
    }


def _emit_candidate_raw_wheel_diagnostic(
    first_payload: bytes,
    second_payload: bytes,
    first_preflight: dict[str, int],
    second_preflight: dict[str, int],
) -> None:
    try:
        try:
            diagnostic = _candidate_raw_wheel_diagnostic(
                first_payload,
                second_payload,
                first_preflight,
                second_preflight,
            )
        except _CandidateDiagnosticResourceLimit as exc:
            diagnostic = _candidate_raw_wheel_resource_receipt(
                first_payload,
                second_payload,
                limit_type=exc.limit_type,
                limit=exc.limit,
                observed=exc.observed,
                resources=exc.resources,
            )
        except Exception as exc:
            diagnostic = {
                "classification": "DIAGNOSTIC_ARCHIVE_PARSE_FAILURE",
                "diagnostic_error_type": type(exc).__name__,
                "kind": "CANDIDATE_RAW_WHEEL_DRIFT",
                "raw_wheels": {
                    "first": {
                        "sha256": _sha256_bytes(first_payload),
                        "size": len(first_payload),
                    },
                    "second": {
                        "sha256": _sha256_bytes(second_payload),
                        "size": len(second_payload),
                    },
                },
                "schema_version": 1,
            }
        serialized = _candidate_serialize_diagnostic(diagnostic)
        line = _CANDIDATE_RAW_WHEEL_DIAGNOSTIC_PREFIX + serialized + "\n"
        line_size = len(line.encode("ascii"))
        if (
            line_size > _CANDIDATE_RAW_WHEEL_OUTPUT_BYTE_LIMIT
            and diagnostic.get("classification") != "DIAGNOSTIC_RESOURCE_LIMIT"
        ):
            resources = diagnostic.get("resources", {})
            assert isinstance(resources, dict)
            diagnostic = _candidate_raw_wheel_resource_receipt(
                first_payload,
                second_payload,
                limit_type="serialized_output_bytes",
                limit=_CANDIDATE_RAW_WHEEL_OUTPUT_BYTE_LIMIT,
                observed=line_size,
                resources=resources,
            )
            serialized = _candidate_serialize_diagnostic(diagnostic)
            line = _CANDIDATE_RAW_WHEEL_DIAGNOSTIC_PREFIX + serialized + "\n"
        sys.stderr.write(line)
        sys.stderr.flush()
    except Exception:
        # Observability is best-effort; the unchanged raw equality gate fails next.
        return


def _close_candidate_source_descriptor(descriptor: int) -> None:
    try:
        os.close(descriptor)
    except OSError as exc:
        raise VerificationError("candidate source descriptor close failed") from exc


def build_candidate_a(
    *,
    authority_receipt: Path,
    authority_receipt_sha256: str,
) -> dict[str, object]:
    validated_x4 = _validate_x4_authority_receipt(
        authority_receipt,
        authority_receipt_sha256,
        phase="A",
    )
    engine, inputs = _verify_candidate_authority()
    roots = _materialize_candidate_inputs(engine, inputs)
    process_identity = _candidate_process_identity()
    payload, _preflight, core, source_identity, source_fd = _build_candidate_once(
        engine, inputs, roots
    )
    _close_candidate_source_descriptor(source_fd)
    confirmed_x4 = _validate_x4_authority_receipt(
        authority_receipt,
        authority_receipt_sha256,
        phase="A",
    )
    if confirmed_x4 != validated_x4:
        raise VerificationError("validated X4 authority changed before Build A publication")
    return _publish_candidate_build_result(
        roots,
        label="A",
        wheel_payload=payload,
        artifact_core=core,
        source_identity=source_identity,
        process_identity=process_identity,
        x4_authority=confirmed_x4,
        x4_receipt_sha256=authority_receipt_sha256,
    )


def build_candidate_b(
    *,
    authority_receipt: Path,
    authority_receipt_sha256: str,
    retain_raw_wheel_pair: bool = False,
) -> dict[str, object]:
    validated_x4 = _validate_x4_authority_receipt(
        authority_receipt,
        authority_receipt_sha256,
        phase="B",
    )
    engine, inputs = _verify_candidate_authority()
    forensic_destination: Path | None = None
    forensic_parent_identity: tuple[int, int] | None = None
    if retain_raw_wheel_pair:
        forensic_destination, forensic_parent_identity = (
            _candidate_forensic_destination(engine)
        )
    roots = _materialize_candidate_inputs(
        engine,
        inputs,
        allowed_build_children=frozenset({_BUILD_A_DIRECTORY}),
    )
    first_payload, first_core, first_receipt, _first_digest = (
        _load_candidate_build_result(roots, label="A")
    )
    process_identity = _candidate_process_identity()
    if first_receipt["x4_authority_receipt_sha256"] != authority_receipt_sha256:
        raise VerificationError("Build A X4 authority receipt differs from Build B")
    if any(
        first_receipt[receipt_field] != validated_x4[x4_field]
        for receipt_field, x4_field in (
            ("candidate", "candidate"),
            ("policy_sha256", "policy_sha256"),
            ("authority_identities", "identities"),
        )
    ):
        raise VerificationError("Build A X4 authority binding drifted")
    if first_receipt["process_identity"] == process_identity:
        raise VerificationError("candidate builds require a distinct process identity")

    second_payload, second_preflight, second_core, second_identity, source_fd = (
        _build_candidate_once(engine, inputs, roots)
    )
    _close_candidate_source_descriptor(source_fd)
    confirmed_x4 = _validate_x4_authority_receipt(
        authority_receipt,
        authority_receipt_sha256,
        phase="B",
    )
    if confirmed_x4 != validated_x4:
        raise VerificationError("validated X4 authority changed before Build B publication")
    second_receipt = _publish_candidate_build_result(
        roots,
        label="B",
        wheel_payload=second_payload,
        artifact_core=second_core,
        source_identity=second_identity,
        process_identity=process_identity,
        x4_authority=confirmed_x4,
        x4_receipt_sha256=authority_receipt_sha256,
    )
    confirmed_a = _load_candidate_build_result(roots, label="A")
    confirmed_b = _load_candidate_build_result(roots, label="B")
    if (
        confirmed_a[:3]
        != (first_payload, first_core, first_receipt)
    ):
        raise VerificationError("Build A changed during Build B")
    if (
        confirmed_b[:3]
        != (second_payload, second_core, second_receipt)
    ):
        raise VerificationError("Build B changed after publication")
    first_identity = first_receipt["source_identity"]
    if first_identity == second_identity:
        raise VerificationError("candidate builds require distinct source identities")
    raw_wheel_equality = first_payload == second_payload
    if not raw_wheel_equality:
        _emit_candidate_raw_wheel_diagnostic(
            first_payload,
            second_payload,
            _candidate_wheel_structural_preflight(first_payload),
            second_preflight,
        )
    if retain_raw_wheel_pair:
        assert forensic_destination is not None
        assert forensic_parent_identity is not None
        _retain_candidate_raw_wheel_pair(
            forensic_destination,
            forensic_parent_identity,
            first_payload,
            second_payload,
            (first_identity, second_identity),  # type: ignore[arg-type]
        )
        if raw_wheel_equality:
            raise VerificationError(
                "candidate forensic raw wheel pair retained without reproducing drift"
            )
        raise VerificationError("candidate raw wheel drifted across fresh builds")
    if not raw_wheel_equality:
        raise VerificationError("candidate raw wheel drifted across fresh builds")
    if first_core != second_core:
        raise VerificationError(
            "candidate native or authoritative manifest drifted across fresh builds"
        )
    final_x4 = _validate_x4_authority_receipt(
        authority_receipt,
        authority_receipt_sha256,
        phase="FINAL",
    )
    if final_x4 != validated_x4:
        raise VerificationError("validated X4 authority changed before final publication")
    final_a = _load_candidate_build_result(roots, label="A")
    final_b = _load_candidate_build_result(roots, label="B")
    if final_a[:3] != confirmed_a[:3] or final_b[:3] != confirmed_b[:3]:
        raise VerificationError("candidate build result changed before final publication")

    final_a_payload, final_a_core, final_a_receipt, final_a_digest = final_a
    final_b_payload, final_b_core, final_b_receipt, final_b_digest = final_b
    if final_a_payload != final_b_payload or final_a_core != final_b_core:
        raise VerificationError("candidate build result changed before final publication")
    reproducibility: dict[str, object] = {
        "authoritative_manifest_equality": True,
        "build_a_receipt_sha256": final_a_digest,
        "build_b_receipt_sha256": final_b_digest,
        "build_count": 2,
        "fresh_physical_stages": True,
        "logical_stages_absent_after_build": True,
        "native_inventory_equality": True,
        "process_identities": [
            final_a_receipt["process_identity"],
            final_b_receipt["process_identity"],
        ],
        "raw_wheel_equality": True,
        "source_fd_identities": [
            final_a_receipt["source_identity"],
            final_b_receipt["source_identity"],
        ],
        "wheel_sha256": final_a_core["wheel"]["sha256"],  # type: ignore[index]
        "x4_authority_receipt_sha256": authority_receipt_sha256,
    }
    manifest = {**final_a_core, "reproducible_build": reproducibility}
    _publish_candidate_artifacts(
        roots, final_a_payload, final_a_core, reproducibility
    )
    return manifest


def build_engine(
    *,
    policy_path: Path,
    input_cache: Path,
    wheel_cache: Path,
    wheel_cache_manifest_sha256: str,
    python: Path,
    cargo: Path,
    llvm_toolchain: Path,
    sandbox: Path,
    destination: Path,
    offline: bool,
) -> dict[str, object]:
    if not offline:
        raise VerificationError("Nautilus engine builds must use the offline network namespace")
    policy = load_policy(policy_path)
    python_identity = validate_python(python, str(policy["python_minor"]))
    _validate_sandbox(sandbox)
    input_tool = _load_input_cache_tool()
    input_policy = input_tool.load_policy(_INPUT_CACHE_POLICY)
    try:
        input_tool.verify(input_cache, input_policy)
        cargo_identity = input_tool.validate_private_cargo(cargo, str(policy["required_rust_version"]))
        rustc_identity = input_tool.validate_private_rustc(cargo.parent / "rustc", str(policy["required_rust_version"]))
    except (OSError, ValueError) as exc:
        raise VerificationError(f"Nautilus source/Cargo input verification failed: {exc}") from exc
    rust_tool = _load_rust_toolchain_tool()
    try:
        rust_manifest = rust_tool.load_manifest(_RUST_TOOLCHAIN_POLICY)
        rust_tool.verify_materialized_toolchain(_toolchain_root_for_cargo(cargo), rust_manifest)
    except (OSError, ValueError) as exc:
        raise VerificationError(f"private Rust toolchain verification failed: {exc}") from exc
    llvm_tool = _load_llvm_toolchain_tool()
    try:
        llvm_policy = llvm_tool.load_policy(_LLVM_TOOLCHAIN_POLICY)
        llvm_tool.verify_materialized(llvm_toolchain, llvm_policy)
        _reject_ambient_compilers(_AMBIENT_BUILD_PATH)
    except (OSError, ValueError) as exc:
        raise VerificationError(f"private LLVM toolchain verification failed: {exc}") from exc
    wheel_manifest = verify_wheel_cache(wheel_cache, wheel_cache_manifest_sha256, policy)
    input_manifest_path = input_cache / "input-cache-manifest.json"
    input_manifest_digest = _sha256(input_manifest_path)
    for path, label in (
        (input_cache, "source/Cargo input cache"),
        (wheel_cache, "wheel cache"),
        (cargo, "private Cargo toolchain"),
        (llvm_toolchain, "private LLVM toolchain"),
        (destination, "engine artifact destination"),
    ):
        _require_external(path, label)
    if destination.exists() or destination.is_symlink():
        raise VerificationError("engine artifact destination already exists")
    _reject_symlinked_ancestors(destination.parent, "engine artifact parent")
    _directory(destination.parent, "engine artifact parent", 0o700)
    with tempfile.TemporaryDirectory(prefix=".nautilus-engine-build-", dir=destination.parent) as temporary:
        stage = Path(temporary)
        os.chmod(stage, 0o700)
        source_extract = stage / "source"
        source_extract.mkdir(mode=0o700)
        source_archive = input_cache / "source" / f"nautilus_trader-{policy['upstream_commit']}.tar.gz"
        try:
            source = input_tool._safe_extract_source(source_archive, source_extract, str(policy["upstream_commit"]))
        except (OSError, tarfile.TarError, ValueError) as exc:
            raise VerificationError("verified Nautilus source could not be extracted") from exc
        cargo_home = stage / "cargo-home"
        shutil.copytree(input_cache / "cargo-home", cargo_home, symlinks=False)
        _thaw_tree(cargo_home)
        target = stage / "cargo-target"
        target.mkdir(mode=0o700)
        home = stage / "home"
        home.mkdir(mode=0o700)
        venv = stage / "python-3.12"
        dist = stage / "dist"
        dist.mkdir(mode=0o700)
        base_environment = {
            "BUILD_MODE": "release",
            "CARGO_HOME": str(cargo_home),
            "CARGO_NET_OFFLINE": "true",
            "CARGO_TARGET_DIR": str(target),
            "COPY_TO_SOURCE": "true",
            "HOME": str(home),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_INDEX": "1",
            "PYTHONNOUSERSITE": "1",
            "RUSTC": str(cargo.parent / "rustc"),
            "RUSTUP_TOOLCHAIN": "stable",
            "SOURCE_DATE_EPOCH": "0",
            "UV_OFFLINE": "1",
        }
        base_environment.update(
            _build_tool_environment(llvm_toolchain / "bin", cargo.parent, venv / "bin")
        )
        base_environment.update(_stage_compiler_temp_environment(stage))
        _sandbox_run(
            sandbox,
            stage,
            stage,
            base_environment,
            [str(python), "-I", "-m", "venv", "--without-pip", "--copies", str(venv)],
            timeout=120,
        )
        records = wheel_manifest["artifacts"]
        assert isinstance(records, list)
        build_wheels = [str(wheel_cache / str(record["filename"])) for record in records]
        pip_wheels = [
            str(wheel_cache / str(record["filename"]))
            for record in records
            if record["package"] == "pip"
        ]
        if len(pip_wheels) != 1:
            raise VerificationError("wheel cache must contain exactly one approved pip wheel")
        _sandbox_run(
            sandbox,
            stage,
            stage,
            base_environment,
            [
                str(python),
                "-I",
                "-c",
                _PIP_BOOTSTRAP,
                pip_wheels[0],
                "--python",
                str(venv / "bin/python"),
                "install",
                "--no-index",
                "--no-deps",
                "--no-cache-dir",
                *build_wheels,
            ],
            timeout=600,
        )
        _sandbox_run(
            sandbox,
            stage,
            source,
            base_environment,
            [
                str(venv / "bin/python"),
                "-I",
                "-m",
                "pip",
                "wheel",
                "--no-build-isolation",
                "--no-deps",
                "--no-index",
                "--no-cache-dir",
                "--wheel-dir",
                str(dist),
                ".",
            ],
            timeout=7200,
        )
        wheels = list(dist.glob("*.whl"))
        if len(wheels) != 1 or wheels[0].is_symlink():
            raise VerificationError("offline engine build did not produce exactly one wheel")
        artifacts = stage / "artifacts"
        artifacts.mkdir(mode=0o700)
        built_wheel = artifacts / wheels[0].name
        shutil.move(wheels[0], built_wheel)
        document = write_artifact_manifest(
            artifacts,
            built_wheel,
            policy,
            python_identity=python_identity,
            cargo_identity=cargo_identity,
            rustc_identity=rustc_identity,
            input_cache_manifest_sha256=input_manifest_digest,
            wheel_cache_manifest_sha256=wheel_cache_manifest_sha256,
        )
        _publish_artifacts(artifacts, destination)
        verify_artifacts(destination, policy, python=python)
        return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--python", type=Path)
    parser.add_argument("--artifacts", type=Path)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--build", action="store_true")
    actions.add_argument("--verify", action="store_true")
    actions.add_argument("--build-candidate-a", action="store_true")
    actions.add_argument("--build-candidate-b", action="store_true")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--authority-receipt", type=Path)
    parser.add_argument("--authority-receipt-sha256")
    parser.add_argument("--retain-raw-wheel-pair", action="store_true")
    parser.add_argument("--input-cache", type=Path)
    parser.add_argument("--wheel-cache", type=Path)
    parser.add_argument("--wheel-cache-manifest-sha256")
    parser.add_argument("--cargo", type=Path)
    parser.add_argument("--llvm-toolchain", type=Path)
    parser.add_argument("--sandbox", type=Path, default=Path("/usr/bin/bwrap"))
    parser.add_argument(
        "--verify-input-bindings",
        action="store_true",
        help="verify supplied sealed inputs against the artifact manifest",
    )
    args = parser.parse_args(argv)
    try:
        if args.build_candidate_a or args.build_candidate_b:
            supplied = (
                args.policy,
                args.python,
                args.artifacts,
                args.input_cache,
                args.wheel_cache,
                args.wheel_cache_manifest_sha256,
                args.cargo,
                args.llvm_toolchain,
            )
            if any(value is not None for value in supplied) or args.sandbox != _CANDIDATE_SANDBOX:
                raise VerificationError("candidate build accepts no caller-supplied authority")
            if not args.offline:
                raise VerificationError("candidate build requires explicit offline mode")
            if (
                args.authority_receipt is None
                or args.authority_receipt_sha256 is None
            ):
                raise VerificationError(
                    "candidate build requires X4 authority receipt path and SHA-256"
                )
            if args.retain_raw_wheel_pair and not args.build_candidate_b:
                raise VerificationError(
                    "candidate forensic retention is accepted only with Build B"
                )
            if args.build_candidate_a:
                build_candidate_a(
                    authority_receipt=args.authority_receipt,
                    authority_receipt_sha256=args.authority_receipt_sha256,
                )
            else:
                build_candidate_b(
                    authority_receipt=args.authority_receipt,
                    authority_receipt_sha256=args.authority_receipt_sha256,
                    retain_raw_wheel_pair=args.retain_raw_wheel_pair,
                )
            return 0
        if (
            args.authority_receipt is not None
            or args.authority_receipt_sha256 is not None
            or args.retain_raw_wheel_pair
        ):
            raise VerificationError(
                "legacy build/verify accepts no candidate receipt authority"
            )
        if args.policy is None or args.python is None or args.artifacts is None:
            raise VerificationError("legacy build/verify requires policy, Python, and artifacts")
        policy = load_policy(args.policy)
        if args.build:
            if None in (
                args.input_cache,
                args.wheel_cache,
                args.wheel_cache_manifest_sha256,
                args.cargo,
                args.llvm_toolchain,
            ):
                raise VerificationError(
                    "build requires explicit input, Cargo, LLVM, and approved wheel cache paths"
                )
            build_engine(
                policy_path=args.policy,
                input_cache=args.input_cache,
                wheel_cache=args.wheel_cache,
                wheel_cache_manifest_sha256=args.wheel_cache_manifest_sha256,
                python=args.python,
                cargo=args.cargo,
                llvm_toolchain=args.llvm_toolchain,
                sandbox=args.sandbox,
                destination=args.artifacts,
                offline=args.offline,
            )
        else:
            artifact_manifest = verify_artifacts(args.artifacts, policy, python=args.python)
            if args.verify_input_bindings:
                if None in (
                    args.input_cache,
                    args.wheel_cache,
                    args.wheel_cache_manifest_sha256,
                    args.cargo,
                    args.llvm_toolchain,
                ):
                    raise VerificationError(
                        "sealed-input verification requires explicit input, Cargo, LLVM, and approved wheel cache paths"
                    )
                verify_sealed_input_bindings(
                    policy=policy,
                    artifact_manifest=artifact_manifest,
                    input_cache=args.input_cache,
                    wheel_cache=args.wheel_cache,
                    wheel_cache_manifest_sha256=args.wheel_cache_manifest_sha256,
                    cargo=args.cargo,
                    llvm_toolchain=args.llvm_toolchain,
                    sandbox=args.sandbox,
                    offline=args.offline,
                )
    except (OSError, VerificationError) as exc:
        print(f"nautilus engine verification failed: {exc}", file=sys.stderr)
        return 2
    print("nautilus engine verification: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
