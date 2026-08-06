#!/usr/bin/env python3
"""Build and verify an external, offline Nautilus CPython 3.12 wheel."""
from __future__ import annotations

import argparse
from email.parser import BytesParser
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
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


def _thaw_tree(root: Path) -> None:
    for current, directories, names in os.walk(root):
        for name in names:
            os.chmod(Path(current) / name, 0o600)
        for directory in directories:
            os.chmod(Path(current) / directory, 0o700)
    os.chmod(root, 0o700)


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
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--build", action="store_true")
    actions.add_argument("--verify", action="store_true")
    parser.add_argument("--offline", action="store_true")
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
