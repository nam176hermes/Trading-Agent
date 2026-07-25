from __future__ import annotations

import base64
import csv
from email.parser import Parser
import hashlib
from io import StringIO
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tomllib
from typing import Any, NoReturn
from urllib.parse import urlsplit
import zipfile


MANIFEST_NAME = "wheelhouse-manifest.json"
DEPENDENCY_MANIFEST_VERSION = 1
_FAILURE = "offline wheelhouse verification failed"
_DEPENDENCY_FAILURE = "installed dependency manifest verification failed"
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


def _dependency_fail() -> NoReturn:
    raise ValueError(_DEPENDENCY_FAILURE)


def _safe_zip_name(name: str) -> tuple[str, ...]:
    if not name or "\\" in name or "\x00" in name:
        _dependency_fail()
    stripped = name[:-1] if name.endswith("/") else name
    pure = PurePosixPath(stripped)
    if (
        not stripped
        or pure.is_absolute()
        or pure.as_posix() != stripped
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        _dependency_fail()
    return pure.parts


def _installed_wheel_path(name: str) -> str | None:
    parts = _safe_zip_name(name)
    if parts[0].endswith(".data"):
        if len(parts) < 3 or parts[1] not in {"purelib", "platlib"}:
            return None
        parts = parts[2:]
    value = PurePosixPath(*parts).as_posix()
    if not value or value.startswith("."):
        _dependency_fail()
    return value


def _record_digest(value: str) -> bytes:
    try:
        algorithm, encoded = value.split("=", 1)
        if algorithm != "sha256" or not encoded:
            _dependency_fail()
        return base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    except (ValueError, TypeError):
        _dependency_fail()


def _wheel_installation_entries(
    wheel: Path,
    wheel_sha256: str,
) -> tuple[list[dict[str, Any]], str, bytes]:
    try:
        with zipfile.ZipFile(wheel) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)) or not infos:
                _dependency_fail()
            record_names = [name for name in names if name.endswith(".dist-info/RECORD")]
            if len(record_names) != 1:
                _dependency_fail()
            record_name = record_names[0]
            record_raw = archive.read(record_name)
            rows: dict[str, tuple[str, str]] = {}
            for row in csv.reader(StringIO(record_raw.decode("utf-8"), newline="")):
                if len(row) != 3 or row[0] in rows:
                    _dependency_fail()
                _safe_zip_name(row[0])
                rows[row[0]] = (row[1], row[2])

            archive_files: set[str] = set()
            entries: list[dict[str, Any]] = []
            for info in infos:
                _safe_zip_name(info.filename)
                unix_mode = (info.external_attr >> 16) & 0xFFFF
                kind = stat.S_IFMT(unix_mode)
                if info.flag_bits & 0x1 or kind not in {0, stat.S_IFREG, stat.S_IFDIR}:
                    _dependency_fail()
                if info.is_dir() or info.filename.endswith("/"):
                    continue
                if info.file_size > 256 * 1024 * 1024:
                    _dependency_fail()
                archive_files.add(info.filename)
                raw = archive.read(info)
                if len(raw) != info.file_size:
                    _dependency_fail()
                recorded_hash, recorded_size = rows.get(info.filename, ("", ""))
                if info.filename == record_name:
                    if recorded_hash or recorded_size:
                        _dependency_fail()
                elif (
                    _record_digest(recorded_hash) != hashlib.sha256(raw).digest()
                    or recorded_size != str(len(raw))
                ):
                    _dependency_fail()
                installed_path = _installed_wheel_path(info.filename)
                if installed_path is None:
                    continue
                entries.append(
                    {
                        "path": installed_path,
                        "sha256": hashlib.sha256(raw).hexdigest(),
                        "size": len(raw),
                        "wheel_sha256": wheel_sha256,
                    }
                )
            if set(rows) != archive_files:
                _dependency_fail()
            record_path = _installed_wheel_path(record_name)
            if record_path is None:
                _dependency_fail()
            return entries, record_path, record_raw
    except (OSError, KeyError, UnicodeError, csv.Error, zipfile.BadZipFile):
        _dependency_fail()


def build_site_packages_manifest(
    wheelhouse: Path | str,
    lock_path: Path | str,
    *,
    uv_identity: str,
    uv_sha256: str,
) -> dict[str, Any]:
    root = Path(wheelhouse)
    lock = Path(lock_path)
    aggregate = verify_offline_wheelhouse(root, lock)
    if (
        not isinstance(uv_identity, str)
        or not uv_identity
        or len(uv_identity) > 128
        or _DIGEST.fullmatch(uv_sha256) is None
    ):
        _dependency_fail()
    try:
        wheelhouse_document = json.loads((root / MANIFEST_NAME).read_bytes())
        artifacts = wheelhouse_document["artifacts"]
        if not isinstance(artifacts, list) or not artifacts:
            _dependency_fail()
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        _dependency_fail()

    wheels: list[dict[str, object]] = []
    files: list[dict[str, object]] = []
    seen: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            _dependency_fail()
        filename = artifact.get("filename")
        wheel_sha256 = artifact.get("sha256")
        if (
            not isinstance(filename, str)
            or PurePosixPath(filename).name != filename
            or not isinstance(wheel_sha256, str)
            or _DIGEST.fullmatch(wheel_sha256) is None
        ):
            _dependency_fail()
        wheel_entries, _, _ = _wheel_installation_entries(root / filename, wheel_sha256)
        for entry in wheel_entries:
            path = str(entry["path"])
            if path in seen:
                _dependency_fail()
            seen.add(path)
            files.append(entry)
        wheels.append({"filename": filename, "sha256": wheel_sha256})

    wheels.sort(key=lambda item: os.fsencode(str(item["filename"])))
    files.sort(key=lambda item: os.fsencode(str(item["path"])))
    installed_files = [
        {key: item[key] for key in ("path", "sha256", "size")} for item in files
    ]
    return {
        "files": files,
        "installed_file_set_sha256": hashlib.sha256(_canonical(installed_files)).hexdigest(),
        "lock_sha256": _sha256(lock),
        "provenance_file_set_sha256": hashlib.sha256(_canonical(files)).hexdigest(),
        "schema_version": DEPENDENCY_MANIFEST_VERSION,
        "uv": {"identity": uv_identity, "sha256": uv_sha256},
        "wheelhouse_aggregate_sha256": aggregate,
        "wheels": wheels,
    }


def write_site_packages_manifest(
    output: Path | str,
    wheelhouse: Path | str,
    lock_path: Path | str,
    *,
    uv_identity: str,
    uv_sha256: str,
) -> Path:
    path = Path(output)
    if path.exists() or path.is_symlink():
        _dependency_fail()
    document = build_site_packages_manifest(
        wheelhouse,
        lock_path,
        uv_identity=uv_identity,
        uv_sha256=uv_sha256,
    )
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o444,
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_canonical(document) + b"\n")
    except OSError:
        _dependency_fail()
    return path


def _load_site_packages_manifest(path: Path) -> tuple[bytes, dict[str, Any]]:
    _safe_regular_file(path, sealed=False)
    try:
        raw = path.read_bytes()
        document = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        _dependency_fail()
    if not isinstance(document, dict) or raw != _canonical(document) + b"\n":
        _dependency_fail()
    return raw, document


def _rewrite_regular_file(path: Path, raw: bytes) -> None:
    descriptor: int | None = None
    try:
        before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) & (0o022 | 0o7000)
        ):
            _dependency_fail()
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_TRUNC | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            _dependency_fail()
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                _dependency_fail()
            view = view[written:]
        os.fsync(descriptor)
    except OSError:
        _dependency_fail()
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _remove_uv_metadata(path: Path, expected: bytes | None) -> None:
    try:
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & (0o022 | 0o7000)
            or os.listxattr(path, follow_symlinks=False)
        ):
            _dependency_fail()
        raw = path.read_bytes()
        if expected is not None and raw != expected:
            _dependency_fail()
        if expected is None and not isinstance(json.loads(raw), dict):
            _dependency_fail()
        path.unlink()
    except (OSError, UnicodeError, json.JSONDecodeError):
        _dependency_fail()


def canonicalize_installed_site_packages(
    wheelhouse: Path | str,
    lock_path: Path | str,
    site_packages: Path | str,
    dependency_manifest: Path | str,
) -> dict[str, object]:
    root = Path(wheelhouse)
    site = Path(site_packages)
    raw, expected = _load_site_packages_manifest(Path(dependency_manifest))
    try:
        uv = expected["uv"]
        generated = build_site_packages_manifest(
            root,
            lock_path,
            uv_identity=uv["identity"],
            uv_sha256=uv["sha256"],
        )
    except (KeyError, TypeError):
        _dependency_fail()
    if generated != expected:
        _dependency_fail()
    try:
        site_info = site.lstat()
        if (
            not site.is_absolute()
            or not stat.S_ISDIR(site_info.st_mode)
            or site_info.st_uid != os.getuid()
            or stat.S_IMODE(site_info.st_mode) & (0o022 | 0o7000)
        ):
            _dependency_fail()
    except OSError:
        _dependency_fail()

    try:
        wheel_map = {
            str(item["filename"]): str(item["sha256"]) for item in expected["wheels"]
        }
    except (KeyError, TypeError):
        _dependency_fail()
    for filename, wheel_sha256 in wheel_map.items():
        _, record_path, record_raw = _wheel_installation_entries(root / filename, wheel_sha256)
        dist_info = (site / record_path).parent
        _remove_uv_metadata(dist_info / "INSTALLER", b"uv")
        _remove_uv_metadata(dist_info / "REQUESTED", b"")
        _remove_uv_metadata(dist_info / "uv_cache.json", None)
        _rewrite_regular_file(site / record_path, record_raw)

    actual: list[dict[str, object]] = []
    try:
        candidates = sorted(
            site.rglob("*"),
            key=lambda item: os.fsencode(item.relative_to(site).as_posix()),
        )
        for path in candidates:
            relative = path.relative_to(site).as_posix()
            info = path.lstat()
            mode = stat.S_IMODE(info.st_mode)
            if (
                info.st_uid != os.getuid()
                or mode & (0o022 | 0o7000)
                or os.listxattr(path, follow_symlinks=False)
            ):
                _dependency_fail()
            if stat.S_ISDIR(info.st_mode):
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                _dependency_fail()
            actual.append({"path": relative, "sha256": _sha256(path), "size": info.st_size})
    except OSError:
        _dependency_fail()
    try:
        installed_expected = [
            {key: item[key] for key in ("path", "sha256", "size")}
            for item in expected["files"]
        ]
        installed_digest = expected["installed_file_set_sha256"]
    except (KeyError, TypeError):
        _dependency_fail()
    if actual != installed_expected or hashlib.sha256(_canonical(actual)).hexdigest() != installed_digest:
        _dependency_fail()
    return {
        "file_count": len(actual),
        "installed_file_set_sha256": installed_digest,
        "lock_sha256": expected["lock_sha256"],
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "provenance_file_set_sha256": expected["provenance_file_set_sha256"],
        "schema_version": expected["schema_version"],
        "uv_sha256": expected["uv"]["sha256"],
        "wheelhouse_aggregate_sha256": expected["wheelhouse_aggregate_sha256"],
    }
