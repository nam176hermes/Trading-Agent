#!/usr/bin/env python3
"""Acquire and offline-verify the sealed Nautilus CPython 3.12 wheel cache."""
from __future__ import annotations

import argparse
from email.parser import BytesParser
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile


_ROOT = Path(__file__).resolve().parents[1]
_MANIFEST = "wheel-cache-manifest.json"
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_NAME_NORMALIZER = re.compile(r"[-_.]+")
_POLICY_FIELDS = {
    "schema_version",
    "python_implementation",
    "python_minor",
    "index_url",
    "engine_policy_sha256",
    "packages",
}
_ENGINE_POLICY_FIELDS = {"required_build_wheels", "required_unpinned_build_wheels", "python_minor"}
_MANIFEST_FIELDS = {"schema_version", "python_minor", "artifacts"}
_ARTIFACT_FIELDS = {"filename", "package", "version", "role", "sha256", "size"}


class VerificationError(ValueError):
    """Raised when acquisition or offline verification fails closed."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _normalize_name(value: str) -> str:
    return _NAME_NORMALIZER.sub("-", value).lower()


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
        raise VerificationError(f"{label} must have mode {mode:04o}")
    return info


def _require_absolute_external(path: Path, label: str) -> None:
    if (
        not path.is_absolute()
        or path == Path("/")
        or any(part in {"", ".", ".."} for part in path.parts[1:])
    ):
        raise VerificationError(f"{label} must be an absolute non-root path")
    try:
        path.relative_to(_ROOT)
    except ValueError:
        return
    raise VerificationError(f"{label} must remain external to the Git checkout")


def _reject_symlinked_ancestors(path: Path, label: str, *, allow_missing_leaf: bool = False) -> None:
    current = path.parent if allow_missing_leaf else path
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


def _load_json(path: Path, label: str) -> dict[str, object]:
    _regular_file(path, label)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError(f"{label} is invalid JSON") from exc
    if not isinstance(document, dict):
        raise VerificationError(f"{label} is not an object")
    return document


def load_policy(policy_path: Path, engine_policy_path: Path) -> dict[str, object]:
    policy = _load_json(policy_path, "wheel cache policy")
    engine = _load_json(engine_policy_path, "engine build policy")
    if set(policy) != _POLICY_FIELDS:
        raise VerificationError("wheel cache policy fields are missing or unknown")
    if policy.get("schema_version") != 1:
        raise VerificationError("wheel cache policy schema is unsupported")
    if policy.get("python_implementation") != "CPython" or policy.get("python_minor") != "3.12":
        raise VerificationError("wheel cache policy must target CPython 3.12")
    if policy.get("index_url") != "https://pypi.org/simple":
        raise VerificationError("wheel cache policy must use the public PyPI index")
    if _sha256(engine_policy_path) != _require_sha256(
        policy.get("engine_policy_sha256"), "engine policy digest"
    ):
        raise VerificationError("engine build policy digest drift")
    if not _ENGINE_POLICY_FIELDS.issubset(engine) or engine.get("python_minor") != "3.12":
        raise VerificationError("engine build policy has an incompatible wheel contract")
    pinned = engine.get("required_build_wheels")
    unpinned = engine.get("required_unpinned_build_wheels")
    packages = policy.get("packages")
    if not isinstance(pinned, dict) or not isinstance(unpinned, list) or not isinstance(packages, dict):
        raise VerificationError("wheel cache closure is invalid")
    expected_names = {*pinned, *unpinned}
    if set(packages) != expected_names or any(packages.get(name) != version for name, version in pinned.items()):
        raise VerificationError("wheel cache policy does not match the exact Task 3 closure")
    for name, version in packages.items():
        if _normalize_name(str(name)) != name or not isinstance(version, str) or not version:
            raise VerificationError("wheel cache policy package or version is invalid")
    return policy


def _validate_python(python: Path, required_minor: str) -> str:
    _require_absolute_external(python, "acquisition Python")
    _reject_symlinked_ancestors(python, "acquisition Python")
    info = _regular_file(python, "acquisition Python")
    if info.st_mode & 0o022 or not info.st_mode & stat.S_IXUSR:
        raise VerificationError("acquisition Python is writable by another user or not executable")
    try:
        result = subprocess.run(
            [os.fspath(python), "-I", "-c", "import platform; print(f'CPython {platform.python_version()}')"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise VerificationError("acquisition Python could not report its identity") from exc
    identity = result.stdout.strip()
    if re.fullmatch(rf"CPython {re.escape(required_minor)}\.\d+", identity) is None:
        raise VerificationError(f"acquisition Python must be explicit CPython {required_minor}")
    return identity


def _safe_filename(value: object) -> str:
    if not isinstance(value, str) or not value.endswith(".whl"):
        raise VerificationError("wheel cache artifact filename is invalid")
    relative = PurePosixPath(value)
    if relative.name != value or any(part in {"", ".", ".."} for part in relative.parts):
        raise VerificationError("wheel cache artifact filename is invalid")
    return value


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
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        raise VerificationError("cached wheel is invalid") from exc
    package = _normalize_name(metadata.get("Name", ""))
    version = metadata.get("Version", "")
    if not package or not version:
        raise VerificationError("cached wheel metadata is incomplete")
    return package, version


def _wheel_targets_python312(filename: str) -> bool:
    parts = filename.removesuffix(".whl").rsplit("-", 3)
    if len(parts) != 4:
        return False
    python_tags = set(parts[1].split("."))
    abi_tags = set(parts[2].split("."))
    platform_tags = set(parts[3].split("."))
    if "cp312" in python_tags:
        return bool(abi_tags & {"cp312", "abi3"}) and platform_tags != {"any"}
    return bool(python_tags & {"py3", "py2"}) and abi_tags == {"none"} and platform_tags == {"any"}


def _validate_document(
    cache: Path, document: dict[str, object], policy: dict[str, object], *, sealed: bool
) -> dict[str, object]:
    if set(document) != _MANIFEST_FIELDS:
        raise VerificationError("wheel cache manifest fields are missing or unknown")
    if document.get("schema_version") != 1 or document.get("python_minor") != "3.12":
        raise VerificationError("wheel cache manifest targets the wrong Python")
    records = document.get("artifacts")
    packages = policy["packages"]
    assert isinstance(packages, dict)
    if not isinstance(records, list) or len(records) != len(packages):
        raise VerificationError("wheel cache manifest does not contain the exact closure")
    expected_files = {_MANIFEST}
    observed_packages: dict[str, str] = {}
    for record in records:
        if not isinstance(record, dict) or set(record) != _ARTIFACT_FIELDS:
            raise VerificationError("wheel cache artifact fields are invalid")
        filename = _safe_filename(record.get("filename"))
        if filename in expected_files or not _wheel_targets_python312(filename):
            raise VerificationError("wheel cache artifact is duplicate or incompatible with Python 3.12")
        expected_files.add(filename)
        package = record.get("package")
        version = record.get("version")
        if record.get("role") != "build" or not isinstance(package, str) or not isinstance(version, str):
            raise VerificationError("wheel cache artifact identity is invalid")
        _require_sha256(record.get("sha256"), "wheel cache artifact digest")
        if not isinstance(record.get("size"), int) or record["size"] <= 0:
            raise VerificationError("wheel cache artifact size is invalid")
        artifact = cache / filename
        info = _regular_file(artifact, "wheel cache artifact")
        if sealed and stat.S_IMODE(info.st_mode) != 0o400:
            raise VerificationError("wheel cache artifact is mutable")
        if info.st_size != record["size"] or _sha256(artifact) != record["sha256"]:
            raise VerificationError("wheel cache artifact digest or size drift")
        metadata_package, metadata_version = _wheel_metadata(artifact)
        if package != metadata_package or version != metadata_version or package in observed_packages:
            raise VerificationError("wheel cache package metadata is invalid or duplicate")
        observed_packages[package] = version
    if observed_packages != packages:
        raise VerificationError("wheel cache packages do not match the exact policy")
    if {path.name for path in cache.iterdir()} != expected_files:
        raise VerificationError("wheel cache has missing or unexpected files")
    return document


def verify(
    cache: Path,
    *,
    expected_manifest_sha256: str,
    policy_path: Path,
    engine_policy_path: Path,
) -> dict[str, object]:
    policy = load_policy(policy_path, engine_policy_path)
    _require_absolute_external(cache, "wheel cache")
    _reject_symlinked_ancestors(cache, "wheel cache")
    _directory(cache, "wheel cache", 0o500)
    manifest = cache / _MANIFEST
    info = _regular_file(manifest, "wheel cache manifest")
    expected = _require_sha256(expected_manifest_sha256, "wheel cache manifest digest")
    if stat.S_IMODE(info.st_mode) != 0o400 or _sha256(manifest) != expected:
        raise VerificationError("wheel cache manifest is mutable or has digest drift")
    document = _load_json(manifest, "wheel cache manifest")
    return _validate_document(cache, document, policy, sealed=True)


def acquire(
    cache: Path,
    *,
    python: Path,
    policy_path: Path,
    engine_policy_path: Path,
) -> tuple[dict[str, object], str]:
    policy = load_policy(policy_path, engine_policy_path)
    _require_absolute_external(cache, "wheel cache")
    _reject_symlinked_ancestors(cache, "wheel cache", allow_missing_leaf=True)
    _directory(cache.parent, "wheel cache parent", 0o700)
    if cache.exists() or cache.is_symlink():
        raise VerificationError("wheel cache destination already exists")
    _validate_python(python, str(policy["python_minor"]))
    staging = Path(tempfile.mkdtemp(prefix=".nautilus-wheel-cache.", dir=cache.parent))
    os.chmod(staging, 0o700)
    try:
        downloads = staging / "downloads"
        pip_cache = staging / ".pip-cache"
        home = staging / ".home"
        downloads.mkdir(mode=0o700)
        pip_cache.mkdir(mode=0o700)
        home.mkdir(mode=0o700)
        packages = policy["packages"]
        assert isinstance(packages, dict)
        requirements = [f"{name}=={version}" for name, version in sorted(packages.items())]
        environment = {
            "HOME": os.fspath(home),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": "/usr/bin:/bin",
            "PIP_CACHE_DIR": os.fspath(pip_cache),
            "PIP_CONFIG_FILE": os.devnull,
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_INPUT": "1",
            "PYTHONNOUSERSITE": "1",
        }
        command = [
            os.fspath(python),
            "-I",
            "-m",
            "pip",
            "download",
            "--isolated",
            "--index-url",
            str(policy["index_url"]),
            "--only-binary=:all:",
            "--no-deps",
            "--implementation",
            "cp",
            "--python-version",
            "3.12",
            "--dest",
            os.fspath(downloads),
            "--cache-dir",
            os.fspath(pip_cache),
            *requirements,
        ]
        try:
            subprocess.run(command, check=True, env=environment, timeout=600)
        except (OSError, subprocess.SubprocessError) as exc:
            raise VerificationError("public wheel acquisition failed") from exc
        wheels = sorted(downloads.iterdir(), key=lambda item: item.name)
        if len(wheels) != len(packages) or any(path.is_symlink() or not path.name.endswith(".whl") for path in wheels):
            raise VerificationError("public index did not return the exact wheel closure")
        records: list[dict[str, object]] = []
        observed: set[str] = set()
        for wheel in wheels:
            info = _regular_file(wheel, "downloaded wheel")
            package, version = _wheel_metadata(wheel)
            if packages.get(package) != version or package in observed or not _wheel_targets_python312(wheel.name):
                raise VerificationError("downloaded wheel does not match the exact CPython 3.12 policy")
            observed.add(package)
            destination = staging / wheel.name
            os.replace(wheel, destination)
            records.append(
                {
                    "filename": wheel.name,
                    "package": package,
                    "version": version,
                    "role": "build",
                    "sha256": _sha256(destination),
                    "size": info.st_size,
                }
            )
        if observed != set(packages):
            raise VerificationError("downloaded wheel closure is incomplete")
        shutil.rmtree(downloads)
        shutil.rmtree(pip_cache)
        shutil.rmtree(home)
        records.sort(key=lambda item: str(item["package"]))
        document: dict[str, object] = {
            "schema_version": 1,
            "python_minor": "3.12",
            "artifacts": records,
        }
        manifest = staging / _MANIFEST
        manifest.write_text(json.dumps(document, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        _validate_document(staging, document, policy, sealed=False)
        for path in staging.iterdir():
            os.chmod(path, 0o400)
        os.chmod(staging, 0o500)
        digest = _sha256(manifest)
        os.replace(staging, cache)
        verify(
            cache,
            expected_manifest_sha256=digest,
            policy_path=policy_path,
            engine_policy_path=engine_policy_path,
        )
        return document, digest
    finally:
        if staging.exists():
            os.chmod(staging, 0o700)
            for path in staging.iterdir():
                if not path.is_symlink():
                    os.chmod(path, 0o700 if path.is_dir() else 0o600)
            shutil.rmtree(staging)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--engine-policy", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--python", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--acquire", action="store_true")
    mode.add_argument("--verify", action="store_true")
    parser.add_argument("--manifest-sha256")
    args = parser.parse_args(argv)
    try:
        if args.acquire:
            if args.python is None or args.manifest_sha256 is not None:
                raise VerificationError("acquisition requires --python and does not accept a manifest digest")
            _document, digest = acquire(
                args.cache,
                python=args.python,
                policy_path=args.policy,
                engine_policy_path=args.engine_policy,
            )
            print(f"wheel-cache-manifest.json sha256: {digest}")
        else:
            if args.manifest_sha256 is None or args.python is not None:
                raise VerificationError("offline verification requires only --manifest-sha256")
            verify(
                args.cache,
                expected_manifest_sha256=args.manifest_sha256,
                policy_path=args.policy,
                engine_policy_path=args.engine_policy,
            )
            print("nautilus wheel cache verification: PASS")
    except VerificationError as exc:
        print(f"nautilus wheel cache verification failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
