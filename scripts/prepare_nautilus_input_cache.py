#!/usr/bin/env python3
"""Acquire and verify an external, hash-bound Nautilus input cache."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any


_SHA256_LENGTH = 64
_POLICY_FIELDS = {
    "schema_version",
    "upstream_repository",
    "upstream_tag",
    "upstream_tag_object",
    "upstream_commit",
    "source_url",
    "cargo_lock_sha256",
    "pyproject_sha256",
    "required_cargo_version",
}
_MANIFEST_FIELDS = _POLICY_FIELDS - {"required_cargo_version"} | {"artifacts", "cargo_version", "rustc_version"}
_ARTIFACT_FIELDS = {"path", "kind", "sha256"}
_SOURCE_PREFIX = "source"
_DERIVED_PREFIX = "derived"
_CARGO_PREFIX = "cargo-home"
_MANIFEST_NAME = "input-cache-manifest.json"
_EXPECTED_POLICY = {
    "schema_version": 1,
    "upstream_repository": "https://github.com/nautechsystems/nautilus_trader.git",
    "upstream_tag": "v1.227.0",
    "upstream_tag_object": "0ccb5b55879c072a6e07fc7cbe5297c53c378107",
    "upstream_commit": "280ae1762df51a492a4ce71506a40b5c8706def5",
    "source_url": "https://github.com/nautechsystems/nautilus_trader/archive/280ae1762df51a492a4ce71506a40b5c8706def5.tar.gz",
    "cargo_lock_sha256": "083652294183947a352d1443ed0245311bf7ee5a716b66ccc21e814be25851ed",
    "pyproject_sha256": "f707cbe27b183ba598c31f1b3b6ec67e36f36e878c4228d3fef80741efb81b28",
    "required_cargo_version": "1.95.0",
}


class VerificationError(ValueError):
    """Raised when a source or Cargo input cannot be trusted."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_relative(value: object) -> str:
    if not isinstance(value, str):
        raise VerificationError("artifact path is invalid")
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise VerificationError("artifact path is invalid")
    return path.as_posix()


def _regular_file(path: Path, label: str) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        raise VerificationError(f"{label} is missing") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or path.is_symlink():
        raise VerificationError(f"{label} must be one regular non-symlink file")
    return info


def _directory(path: Path, label: str, mode: int) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        raise VerificationError(f"{label} is missing") from exc
    if (
        not stat.S_ISDIR(info.st_mode)
        or path.is_symlink()
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != mode
    ):
        raise VerificationError(f"{label} is unsafe or mutable")
    return info


def _safe_directory(path: Path, label: str) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        raise VerificationError(f"{label} is missing") from exc
    if not stat.S_ISDIR(info.st_mode) or path.is_symlink():
        raise VerificationError(f"{label} is unsafe")
    return info


def _validate_policy(document: object) -> dict[str, object]:
    if not isinstance(document, dict) or set(document) != _POLICY_FIELDS:
        raise VerificationError("input cache policy fields are missing or unknown")
    if document["schema_version"] != 1:
        raise VerificationError("unsupported input cache policy")
    for field in ("upstream_repository", "upstream_tag", "upstream_tag_object", "upstream_commit", "source_url", "required_cargo_version"):
        if not isinstance(document[field], str) or not document[field]:
            raise VerificationError(f"input cache policy {field} is invalid")
    for field in ("cargo_lock_sha256", "pyproject_sha256"):
        value = document[field]
        if not isinstance(value, str) or len(value) != _SHA256_LENGTH or any(character not in "0123456789abcdef" for character in value):
            raise VerificationError(f"input cache policy {field} is invalid")
    return document


def load_policy(path: Path) -> dict[str, object]:
    _regular_file(path, "input cache policy")
    try:
        document = _validate_policy(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError("input cache policy is invalid JSON") from exc
    if document != _EXPECTED_POLICY:
        raise VerificationError("input cache policy does not match the 01B provenance boundary")
    return document


def _validate_private_path(path: Path, label: str) -> None:
    if not path.is_absolute():
        raise VerificationError(f"{label} must be supplied as an absolute private path")
    info = _regular_file(path, label)
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o022:
        raise VerificationError(f"{label} is not a private toolchain executable")


def validate_private_cargo(cargo: Path, required_version: str) -> str:
    _validate_private_path(cargo, "cargo")
    try:
        result = subprocess.run(
            [str(cargo), "--version"], check=True, capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise VerificationError("private cargo could not report its version") from exc
    version = result.stdout.strip()
    if not version.startswith(f"cargo {required_version} "):
        raise VerificationError("private cargo version does not match the required toolchain")
    return version


def validate_private_rustc(rustc: Path, required_version: str) -> str:
    _validate_private_path(rustc, "rustc")
    try:
        result = subprocess.run(
            [str(rustc), "--version"], check=True, capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise VerificationError("private rustc could not report its version") from exc
    version = result.stdout.strip()
    if not version.startswith(f"rustc {required_version} "):
        raise VerificationError("private rustc version does not match the required toolchain")
    return version


def _safe_extract_source(archive: Path, destination: Path, commit: str) -> Path:
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        expected_root = f"nautilus_trader-{commit}"
        if not members:
            raise VerificationError("Nautilus source archive is empty")
        for member in members:
            member_path = PurePosixPath(member.name)
            if (
                not member.name
                or member_path.is_absolute()
                or ".." in member_path.parts
                or member_path.parts[0] != expected_root
            ):
                raise VerificationError("Nautilus source archive has an unsafe entry")
            if member.issym():
                resolved: list[str] = []
                for part in (*member_path.parent.parts, *PurePosixPath(member.linkname).parts):
                    if part in {"", "."}:
                        continue
                    if part == "..":
                        if len(resolved) <= 1:
                            raise VerificationError("Nautilus source archive symlink escapes its root")
                        resolved.pop()
                    else:
                        resolved.append(part)
                if not resolved or resolved[0] != expected_root:
                    raise VerificationError("Nautilus source archive symlink escapes its root")
            elif not (member.isdir() or member.isfile()):
                raise VerificationError("Nautilus source archive has an unsafe entry")
        source.extractall(destination, filter="data")
    root = destination / expected_root
    _safe_directory(root, "extracted Nautilus source")
    return root


def _copy_derived_input(source: Path, target: Path, expected_sha256: object, label: str) -> None:
    _regular_file(source, label)
    if _sha256(source) != expected_sha256:
        raise VerificationError(f"{label} digest does not match the input cache policy")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    os.chmod(target, 0o600)


def _run_cargo_fetch(cargo: Path, rustc: Path, source: Path, cargo_home: Path) -> None:
    cargo_home.mkdir(mode=0o700)
    environment = os.environ.copy()
    environment["CARGO_HOME"] = str(cargo_home)
    environment.pop("CARGO_NET_OFFLINE", None)
    environment.pop("RUSTUP_HOME", None)
    environment["RUSTC"] = str(rustc)
    environment["PATH"] = "/usr/bin:/bin"
    try:
        subprocess.run(
            [str(cargo), "fetch", "--locked"], cwd=source, env=environment, check=True, timeout=900
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise VerificationError("private cargo could not fetch the locked dependency closure") from exc


def _collect_files(root: Path) -> list[str]:
    files: list[str] = []
    for current, directories, names in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        for directory in directories:
            path = current_path / directory
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode) or path.is_symlink():
                raise VerificationError("input cache contains a symlink or special directory")
        for name in names:
            path = current_path / name
            _regular_file(path, "input cache artifact")
            files.append(path.relative_to(root).as_posix())
    return sorted(files)


def _verify_immutable_directories(cache: Path, expected_files: set[str]) -> None:
    expected_directories: set[str] = set()
    for relative in expected_files:
        path = PurePosixPath(relative).parent
        while path != PurePosixPath("."):
            expected_directories.add(path.as_posix())
            path = path.parent
    for current, directories, _names in os.walk(cache, topdown=True, followlinks=False):
        current_path = Path(current)
        for directory in directories:
            path = current_path / directory
            relative = path.relative_to(cache).as_posix()
            if relative not in expected_directories:
                raise VerificationError("input cache has an unexpected directory")
            _directory(path, "input cache directory", 0o500)


def _artifact(path: str, kind: str, cache: Path) -> dict[str, str]:
    return {"path": path, "kind": kind, "sha256": _sha256(cache / path)}


def _freeze_cache(cache: Path) -> None:
    for current, directories, names in os.walk(cache, topdown=False, followlinks=False):
        current_path = Path(current)
        for name in names:
            path = current_path / name
            _regular_file(path, "input cache artifact")
            os.chmod(path, 0o400)
        for directory in directories:
            path = current_path / directory
            _safe_directory(path, "input cache directory")
            os.chmod(path, 0o500)
    os.chmod(cache, 0o500)


def acquire(cache: Path, policy: dict[str, object], cargo: Path) -> dict[str, object]:
    """Create one immutable external cache from the pinned source and Cargo lockfile."""
    policy = _validate_policy(policy)
    if cache.exists() or cache.is_symlink():
        raise VerificationError("input cache destination must not already exist")
    cargo_version = validate_private_cargo(cargo, str(policy["required_cargo_version"]))
    rustc_version = validate_private_rustc(cargo.parent / "rustc", str(policy["required_cargo_version"]))
    cache.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="nautilus-input-cache-", dir=cache.parent) as staging_text:
        staging = Path(staging_text)
        os.chmod(staging, 0o700)
        source_dir = staging / _SOURCE_PREFIX
        source_dir.mkdir(mode=0o700)
        source_archive = source_dir / f"nautilus_trader-{policy['upstream_commit']}.tar.gz"
        with urllib.request.urlopen(str(policy["source_url"]), timeout=120) as response, source_archive.open("wb") as target:
            shutil.copyfileobj(response, target)
        os.chmod(source_archive, 0o600)
        with tempfile.TemporaryDirectory(dir=staging) as extraction_text:
            extracted = _safe_extract_source(source_archive, Path(extraction_text), str(policy["upstream_commit"]))
            derived = staging / _DERIVED_PREFIX
            _copy_derived_input(extracted / "Cargo.lock", derived / "Cargo.lock", policy["cargo_lock_sha256"], "Cargo.lock")
            _copy_derived_input(extracted / "pyproject.toml", derived / "pyproject.toml", policy["pyproject_sha256"], "pyproject.toml")
            _run_cargo_fetch(cargo, cargo.parent / "rustc", extracted, staging / _CARGO_PREFIX)
        cargo_files = _collect_files(staging / _CARGO_PREFIX)
        if not cargo_files:
            raise VerificationError("private cargo did not cache a dependency closure")
        source_path = source_archive.relative_to(staging).as_posix()
        artifacts = [
            _artifact(source_path, "downloaded-source", staging),
            _artifact("derived/Cargo.lock", "derived-cargo-lock", staging),
            _artifact("derived/pyproject.toml", "derived-pyproject", staging),
            *[_artifact(f"{_CARGO_PREFIX}/{path}", "cargo-closure", staging) for path in cargo_files],
        ]
        manifest: dict[str, object] = {
            **{key: policy[key] for key in _POLICY_FIELDS - {"required_cargo_version"}},
            "cargo_version": cargo_version,
            "rustc_version": rustc_version,
            "artifacts": artifacts,
        }
        manifest_path = staging / _MANIFEST_NAME
        manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        _freeze_cache(staging)
        os.replace(staging, cache)
        return manifest


def _validate_manifest(document: object, policy: dict[str, object]) -> dict[str, object]:
    if not isinstance(document, dict) or set(document) != _MANIFEST_FIELDS:
        raise VerificationError("input cache manifest fields are missing or unknown")
    for field in _POLICY_FIELDS - {"required_cargo_version"}:
        if document.get(field) != policy[field]:
            raise VerificationError(f"input cache manifest {field} does not match policy")
    cargo_version = document.get("cargo_version")
    if not isinstance(cargo_version, str) or not cargo_version.startswith(f"cargo {policy['required_cargo_version']} "):
        raise VerificationError("input cache manifest has an unexpected Cargo version")
    rustc_version = document.get("rustc_version")
    if not isinstance(rustc_version, str) or not rustc_version.startswith(f"rustc {policy['required_cargo_version']} "):
        raise VerificationError("input cache manifest has an unexpected rustc version")
    artifacts = document.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise VerificationError("input cache manifest artifacts are invalid")
    observed: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict) or set(artifact) != _ARTIFACT_FIELDS:
            raise VerificationError("input cache manifest artifact fields are invalid")
        relative = _safe_relative(artifact["path"])
        if relative in observed:
            raise VerificationError("input cache manifest has duplicate artifacts")
        observed.add(relative)
        if artifact["kind"] not in {"downloaded-source", "derived-cargo-lock", "derived-pyproject", "cargo-closure"}:
            raise VerificationError("input cache manifest artifact kind is invalid")
        digest = artifact["sha256"]
        if not isinstance(digest, str) or len(digest) != _SHA256_LENGTH or any(character not in "0123456789abcdef" for character in digest):
            raise VerificationError("input cache manifest artifact digest is invalid")
    commit = str(policy["upstream_commit"])
    required = {
        f"source/nautilus_trader-{commit}.tar.gz": "downloaded-source",
        "derived/Cargo.lock": "derived-cargo-lock",
        "derived/pyproject.toml": "derived-pyproject",
    }
    entries = {str(artifact["path"]): str(artifact["kind"]) for artifact in artifacts}
    if any(entries.get(path) != kind for path, kind in required.items()):
        raise VerificationError("input cache manifest is missing a required input")
    if not any(path.startswith(f"{_CARGO_PREFIX}/") and kind == "cargo-closure" for path, kind in entries.items()):
        raise VerificationError("input cache manifest has no Cargo dependency closure")
    return document


def verify(cache: Path, policy: dict[str, object]) -> dict[str, object]:
    """Verify an immutable cache without invoking Cargo or using the network."""
    policy = _validate_policy(policy)
    _directory(cache, "input cache", 0o500)
    manifest_path = cache / _MANIFEST_NAME
    manifest_info = _regular_file(manifest_path, "input cache manifest")
    if stat.S_IMODE(manifest_info.st_mode) != 0o400:
        raise VerificationError("input cache manifest is mutable")
    try:
        manifest = _validate_manifest(json.loads(manifest_path.read_text(encoding="utf-8")), policy)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError("input cache manifest is invalid JSON") from exc
    artifacts = manifest["artifacts"]
    assert isinstance(artifacts, list)
    expected = {_MANIFEST_NAME, *[str(artifact["path"]) for artifact in artifacts]}
    _verify_immutable_directories(cache, expected)
    observed = set(_collect_files(cache))
    if observed != expected:
        raise VerificationError("input cache has missing or unexpected artifacts")
    for artifact in artifacts:
        assert isinstance(artifact, dict)
        path = cache / str(artifact["path"])
        info = _regular_file(path, "input cache artifact")
        if stat.S_IMODE(info.st_mode) != 0o400:
            raise VerificationError("input cache artifact is mutable")
        if _sha256(path) != artifact["sha256"]:
            raise VerificationError("input cache artifact digest drift")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--cargo", type=Path)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--acquire", action="store_true")
    actions.add_argument("--verify", action="store_true")
    args = parser.parse_args(argv)
    try:
        policy = load_policy(args.policy)
        if args.acquire:
            if args.cargo is None:
                raise VerificationError("--cargo is required when acquiring the input cache")
            acquire(args.cache, policy, args.cargo)
        verify(args.cache, policy)
    except (OSError, VerificationError) as exc:
        print(f"nautilus input cache verification failed: {exc}", file=sys.stderr)
        return 2
    print("nautilus input cache verification: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
