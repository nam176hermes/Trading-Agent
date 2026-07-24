from __future__ import annotations

from email.parser import Parser
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tomllib
from typing import Any, NoReturn
from urllib.parse import urlsplit
import zipfile


MANIFEST_NAME = "wheelhouse-manifest.json"
_FAILURE = "offline wheelhouse verification failed"
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_IDENTITY = re.compile(r"CPython 3\.11\.\d+\Z")
_DOWNLOADER = re.compile(r"pip \d+\.\d+(?:\.\d+)?\Z")


def _fail() -> NoReturn:
    raise ValueError(_FAILURE)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            _fail()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            stat.S_IMODE(before.st_mode),
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            stat.S_IMODE(after.st_mode),
        ):
            _fail()
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def _safe_regular_file(path: Path, *, sealed: bool) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError:
        _fail()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        _fail()
    mode = stat.S_IMODE(metadata.st_mode)
    if metadata.st_uid != os.getuid() or mode & (0o022 | 0o7000):
        _fail()
    if sealed and mode & 0o200:
        _fail()
    return metadata


def _safe_wheelhouse(path: Path, *, sealed: bool) -> None:
    if not path.is_absolute():
        _fail()
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError:
        _fail()
    if resolved != path or not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
        _fail()
    mode = stat.S_IMODE(metadata.st_mode)
    if mode & (0o022 | 0o7000):
        _fail()
    if sealed and mode & 0o200:
        _fail()


def _locked_wheels(lock_path: Path) -> dict[str, dict[str, str | int]]:
    _safe_regular_file(lock_path, sealed=False)
    try:
        document = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        _fail()
    locked: dict[str, dict[str, str | int]] = {}
    for package in document.get("package", []):
        if not isinstance(package, dict):
            _fail()
        name = package.get("name")
        version = package.get("version")
        wheels = package.get("wheels", [])
        if not isinstance(name, str) or not isinstance(version, str) or not isinstance(wheels, list):
            _fail()
        for wheel in wheels:
            if not isinstance(wheel, dict):
                _fail()
            value = wheel.get("hash")
            url = wheel.get("url")
            size = wheel.get("size")
            if not isinstance(value, str) or not value.startswith("sha256:"):
                _fail()
            digest = value.removeprefix("sha256:")
            if _DIGEST.fullmatch(digest) is None or not isinstance(url, str) or not isinstance(size, int):
                _fail()
            parsed = urlsplit(url)
            if (
                parsed.scheme != "https"
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
            ):
                _fail()
            if digest in locked:
                _fail()
            locked[digest] = {
                "package": name,
                "version": version,
                "source_url": url,
                "size": size,
            }
    if not locked:
        _fail()
    return locked


def _wheel_metadata(path: Path) -> tuple[str, str, str]:
    try:
        with zipfile.ZipFile(path) as archive:
            metadata_names = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
            if len(metadata_names) != 1:
                _fail()
            info = archive.getinfo(metadata_names[0])
            if info.file_size > 1024 * 1024:
                _fail()
            message = Parser().parsestr(archive.read(info).decode("utf-8"))
    except (OSError, KeyError, UnicodeError, zipfile.BadZipFile):
        _fail()
    name = message.get("Name")
    version = message.get("Version")
    license_value = message.get("License-Expression") or message.get("License") or "UNKNOWN"
    if not isinstance(name, str) or not isinstance(version, str) or not isinstance(license_value, str):
        _fail()
    license_value = " ".join(license_value.split())
    if not license_value or len(license_value) > 512:
        _fail()
    return name, version, license_value


def _wheel_tags(filename: str) -> tuple[str, str, str]:
    if not filename.endswith(".whl"):
        _fail()
    parts = filename[:-4].rsplit("-", 3)
    if len(parts) != 4 or not all(parts):
        _fail()
    return parts[1], parts[2], parts[3]


def build_wheelhouse_manifest(
    wheelhouse: Path | str,
    lock_path: Path | str,
    *,
    python_identity: str,
    downloader: str,
) -> dict[str, Any]:
    root = Path(wheelhouse)
    lock = Path(lock_path)
    _safe_wheelhouse(root, sealed=False)
    if _IDENTITY.fullmatch(python_identity) is None or _DOWNLOADER.fullmatch(downloader) is None:
        _fail()
    locked = _locked_wheels(lock)
    artifacts: list[dict[str, Any]] = []
    try:
        children = sorted(root.iterdir(), key=lambda item: os.fsencode(item.name))
    except OSError:
        _fail()
    for child in children:
        if child.name == MANIFEST_NAME:
            continue
        metadata = _safe_regular_file(child, sealed=False)
        if child.suffix != ".whl":
            _fail()
        digest = _sha256(child)
        locked_artifact = locked.get(digest)
        if locked_artifact is None or metadata.st_size != locked_artifact["size"]:
            _fail()
        name, version, license_value = _wheel_metadata(child)
        if name.lower().replace("_", "-") != str(locked_artifact["package"]).lower().replace("_", "-"):
            _fail()
        if version != locked_artifact["version"]:
            _fail()
        python_tag, abi_tag, platform_tag = _wheel_tags(child.name)
        artifacts.append(
            {
                "artifact_type": "wheel",
                "filename": child.name,
                "license": license_value,
                "package": locked_artifact["package"],
                "platform_tag": platform_tag,
                "python_tag": python_tag,
                "abi_tag": abi_tag,
                "sha256": digest,
                "size": metadata.st_size,
                "source_url": locked_artifact["source_url"],
                "version": locked_artifact["version"],
            }
        )
    if not artifacts:
        _fail()
    base = {
        "manifest_version": 1,
        "lock_sha256": _sha256(lock),
        "python_identity": python_identity,
        "downloader": downloader,
        "artifacts": artifacts,
    }
    return {**base, "aggregate_sha256": hashlib.sha256(_canonical(base)).hexdigest()}


def write_wheelhouse_manifest(
    wheelhouse: Path | str,
    lock_path: Path | str,
    *,
    python_identity: str,
    downloader: str,
) -> Path:
    root = Path(wheelhouse)
    manifest = root / MANIFEST_NAME
    if manifest.exists() or manifest.is_symlink():
        _fail()
    try:
        children = tuple(root.iterdir())
    except OSError:
        _fail()
    if not children:
        _fail()
    for child in children:
        if child.suffix != ".whl":
            _fail()
        descriptor: int | None = None
        try:
            descriptor = os.open(child, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) & 0o7000
            ):
                _fail()
            os.fchmod(descriptor, 0o444)
        except OSError:
            _fail()
        finally:
            if descriptor is not None:
                os.close(descriptor)
    document = build_wheelhouse_manifest(
        root,
        lock_path,
        python_identity=python_identity,
        downloader=downloader,
    )
    try:
        descriptor = os.open(
            manifest,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(descriptor, "wb") as output:
            output.write(_canonical(document) + b"\n")
        for child in root.iterdir():
            if child.is_symlink() or not child.is_file():
                _fail()
            child.chmod(0o444)
        root.chmod(0o555)
    except OSError:
        _fail()
    return manifest


def verify_offline_wheelhouse(wheelhouse: Path | str, lock_path: Path | str) -> str:
    root = Path(wheelhouse)
    lock = Path(lock_path)
    _safe_wheelhouse(root, sealed=True)
    manifest = root / MANIFEST_NAME
    _safe_regular_file(manifest, sealed=True)
    try:
        raw = manifest.read_bytes()
        document = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        _fail()
    if not isinstance(document, dict) or set(document) != {
        "manifest_version",
        "lock_sha256",
        "python_identity",
        "downloader",
        "artifacts",
        "aggregate_sha256",
    }:
        _fail()
    try:
        children = tuple(root.iterdir())
    except OSError:
        _fail()
    for child in children:
        _safe_regular_file(child, sealed=True)
    expected = build_wheelhouse_manifest(
        root,
        lock,
        python_identity=document.get("python_identity", ""),
        downloader=document.get("downloader", ""),
    )
    if raw != _canonical(expected) + b"\n" or document != expected:
        _fail()
    digest = document.get("aggregate_sha256")
    if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
        _fail()
    return digest
