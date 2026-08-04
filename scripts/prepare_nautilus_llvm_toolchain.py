#!/usr/bin/env python3
"""Acquire, verify, and materialize a private LLVM build toolchain."""
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
import urllib.request
from pathlib import Path, PurePosixPath


REPO_ROOT = Path(__file__).resolve().parents[1]
CACHE_MANIFEST = "llvm-cache-manifest.json"
TOOLCHAIN_MANIFEST = "llvm-toolchain-manifest.json"
REQUIRED_TOOLS = ("clang", "clang++", "ld.lld")


class VerificationError(ValueError):
    """Raised when a cache or materialized compiler violates policy."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_bytes(document: dict[str, object]) -> bytes:
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _policy_digest(policy: dict[str, object]) -> str:
    return hashlib.sha256(_canonical_bytes(policy)).hexdigest()


def load_policy(path: Path) -> dict[str, object]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VerificationError(f"invalid LLVM policy: {error}") from error
    if not isinstance(document, dict):
        raise VerificationError("invalid LLVM policy document")
    _validate_policy(document)
    return document


def _validate_policy(policy: dict[str, object]) -> None:
    if set(policy) != {
        "schema_version",
        "version",
        "release_tag",
        "platform",
        "asset",
        "tools",
        "resource_headers",
    }:
        raise VerificationError("invalid LLVM policy fields")
    if policy["schema_version"] != 2 or policy["platform"] != "linux-x86_64":
        raise VerificationError("unsupported LLVM policy")
    version = policy["version"]
    tag = policy["release_tag"]
    asset = policy["asset"]
    tools = policy["tools"]
    if not isinstance(version, str) or not isinstance(tag, str):
        raise VerificationError("invalid LLVM release identity")
    if tag != f"llvmorg-{version}" or not isinstance(asset, dict) or not isinstance(tools, dict):
        raise VerificationError("inconsistent LLVM release identity")
    if set(asset) != {"filename", "url", "sha256", "size", "archive_root"}:
        raise VerificationError("invalid LLVM asset policy")
    expected_url_prefix = f"https://github.com/llvm/llvm-project/releases/download/{tag}/"
    if not isinstance(asset["url"], str) or not asset["url"].startswith(expected_url_prefix):
        raise VerificationError("LLVM asset is not an official release URL")
    if not isinstance(asset["filename"], str) or Path(asset["filename"]).name != asset["filename"]:
        raise VerificationError("invalid LLVM asset filename")
    if not isinstance(asset["archive_root"], str) or "/" in asset["archive_root"]:
        raise VerificationError("invalid LLVM archive root")
    _validate_digest_size(asset, "LLVM asset")
    if set(tools) != set(REQUIRED_TOOLS):
        raise VerificationError("LLVM policy must bind clang, clang++, and ld.lld")
    for name in REQUIRED_TOOLS:
        record = tools[name]
        if not isinstance(record, dict) or set(record) != {
            "archive_path",
            "sha256",
            "size",
            "identity_prefix",
        }:
            raise VerificationError(f"invalid LLVM tool policy: {name}")
        if record["archive_path"] != f"bin/{name}":
            raise VerificationError(f"invalid LLVM archive path: {name}")
        if not isinstance(record["identity_prefix"], str) or not record["identity_prefix"]:
            raise VerificationError(f"invalid LLVM identity: {name}")
        _validate_digest_size(record, f"LLVM tool {name}")
    resource_headers = policy["resource_headers"]
    if not isinstance(resource_headers, dict) or set(resource_headers) != {
        "archive_root",
        "files",
    }:
        raise VerificationError("invalid LLVM resource header policy")
    resource_root = resource_headers["archive_root"]
    expected_resource_root = f"lib/clang/{version.split('.')[0]}/include"
    if resource_root != expected_resource_root:
        raise VerificationError("invalid LLVM resource header archive root")
    files = resource_headers["files"]
    if not isinstance(files, dict) or not files:
        raise VerificationError("LLVM resource header policy must bind files")
    for relative, record in files.items():
        _safe_relative_path(relative, "LLVM resource header")
        if not isinstance(record, dict) or set(record) != {"sha256", "size"}:
            raise VerificationError(f"invalid LLVM resource header policy: {relative}")
        _validate_digest_size(record, f"LLVM resource header {relative}")


def _validate_digest_size(record: dict[str, object], label: str) -> None:
    digest = record.get("sha256")
    size = record.get("size")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
        or not isinstance(size, int)
        or isinstance(size, bool)
        or size <= 0
    ):
        raise VerificationError(f"invalid {label} digest or size")


def _safe_relative_path(value: object, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or value.startswith("/") or "\\" in value:
        raise VerificationError(f"invalid {label} path")
    path = PurePosixPath(value)
    if str(path) != value or any(part in ("", ".", "..") for part in path.parts):
        raise VerificationError(f"invalid {label} path")
    return path


def _ensure_external(path: Path, label: str) -> Path:
    if not path.is_absolute():
        raise VerificationError(f"{label} must be absolute")
    lexical = Path(os.path.abspath(path))
    if lexical == REPO_ROOT or REPO_ROOT in lexical.parents:
        raise VerificationError(f"{label} must remain external to the checkout")
    return lexical


def _reject_symlink_ancestors(path: Path, label: str, *, include_leaf: bool = True) -> None:
    current = Path(path.anchor)
    parts = path.parts[1:] if path.is_absolute() else path.parts
    for index, part in enumerate(parts):
        current /= part
        if not include_leaf and index == len(parts) - 1:
            break
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            raise VerificationError(f"{label} has a symlinked ancestor")


def _open_private_parent(destination: Path, label: str) -> tuple[Path, int]:
    destination = _ensure_external(destination, label)
    _reject_symlink_ancestors(destination.parent, label)
    try:
        info = destination.parent.stat()
    except OSError as error:
        raise VerificationError(f"{label} parent is unavailable") from error
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise VerificationError(f"{label} parent must be owner-controlled mode 0700")
    flags = os.O_RDONLY | os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        parent_fd = os.open(destination.parent, flags)
    except OSError as error:
        raise VerificationError(f"{label} parent cannot be opened safely") from error
    return destination, parent_fd


def _regular_file(path: Path, mode: int, label: str) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as error:
        raise VerificationError(f"{label} is missing") from error
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != mode
    ):
        raise VerificationError(f"{label} is not a sealed direct regular file")
    return info


def _directory(path: Path, mode: int, label: str) -> None:
    try:
        info = path.lstat()
    except OSError as error:
        raise VerificationError(f"{label} is missing") from error
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != mode
    ):
        raise VerificationError(f"{label} is not a sealed direct directory")


def _safe_member_path(name: str, root: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if not name or name.startswith("/") or "\\" in name or ".." in path.parts:
        raise VerificationError(f"unsafe archive path: {name}")
    if not path.parts or path.parts[0] != root:
        raise VerificationError(f"unsafe archive path outside reviewed root: {name}")
    return path


def _resolve_link(member: tarfile.TarInfo, root: str) -> PurePosixPath:
    target = PurePosixPath(member.linkname)
    if member.linkname.startswith("/") or "\\" in member.linkname:
        raise VerificationError(f"unsafe archive link: {member.name}")
    base = PurePosixPath(member.name).parent if member.issym() else PurePosixPath()
    parts: list[str] = []
    for part in (base / target).parts:
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                raise VerificationError(f"unsafe archive link: {member.name}")
            parts.pop()
        else:
            parts.append(part)
    resolved = PurePosixPath(*parts)
    if not resolved.parts or resolved.parts[0] != root:
        raise VerificationError(f"unsafe archive link outside reviewed root: {member.name}")
    return resolved


def _archive_members(archive: tarfile.TarFile, root: str) -> dict[str, tarfile.TarInfo]:
    members: dict[str, tarfile.TarInfo] = {}
    for member in archive.getmembers():
        path = _safe_member_path(member.name, root)
        normalized = str(path)
        if normalized in members:
            raise VerificationError(f"duplicate archive member: {normalized}")
        if member.isdev() or member.isfifo():
            raise VerificationError(f"unsafe special archive member: {normalized}")
        if member.issym() or member.islnk():
            _resolve_link(member, root)
        members[normalized] = member
    return members


def _tool_member(
    members: dict[str, tarfile.TarInfo], name: str, root: str
) -> tarfile.TarInfo:
    current = PurePosixPath(root) / "bin" / name
    visited: set[str] = set()
    for _ in range(16):
        key = str(current)
        if key in visited:
            raise VerificationError(f"archive link cycle for {name}")
        visited.add(key)
        member = members.get(key)
        if member is None:
            raise VerificationError(f"required LLVM tool missing from archive: {name}")
        if member.isfile():
            return member
        if member.issym() or member.islnk():
            current = _resolve_link(member, root)
            continue
        raise VerificationError(f"required LLVM tool is not a regular file: {name}")
    raise VerificationError(f"archive link chain too deep for {name}")


def _resource_member(
    members: dict[str, tarfile.TarInfo], root: str, archive_root: str, relative: str
) -> tarfile.TarInfo:
    member = members.get(str(PurePosixPath(root) / archive_root / relative))
    if member is None:
        raise VerificationError(f"required LLVM resource header missing: {relative}")
    if not member.isfile() or member.issym() or member.islnk():
        raise VerificationError(
            f"required LLVM resource header is not a direct regular file: {relative}"
        )
    return member


def _verify_member_content(
    archive: tarfile.TarFile,
    member: tarfile.TarInfo,
    record: dict[str, object],
    label: str,
) -> None:
    extracted = archive.extractfile(member)
    if extracted is None:
        raise VerificationError(f"cannot read {label}")
    digest = hashlib.sha256()
    size = 0
    for chunk in iter(lambda: extracted.read(1024 * 1024), b""):
        digest.update(chunk)
        size += len(chunk)
    if size != record["size"] or digest.hexdigest() != record["sha256"]:
        raise VerificationError(f"{label} digest mismatch in archive")


def _verify_archive(
    path: Path, policy: dict[str, object], *, reject_ancestors: bool = True
) -> None:
    _validate_policy(policy)
    if reject_ancestors:
        _reject_symlink_ancestors(path, "LLVM archive")
    asset = policy["asset"]
    assert isinstance(asset, dict)
    try:
        info = path.lstat()
    except OSError as error:
        raise VerificationError("LLVM archive is missing") from error
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise VerificationError("LLVM archive must be a direct single-link regular file")
    if info.st_size != asset["size"] or _sha256(path) != asset["sha256"]:
        raise VerificationError("LLVM archive digest or size mismatch")
    root = str(asset["archive_root"])
    try:
        with tarfile.open(path, "r:xz") as archive:
            members = _archive_members(archive, root)
            tools = policy["tools"]
            assert isinstance(tools, dict)
            verified_members: dict[str, tuple[int, str]] = {}
            for name in REQUIRED_TOOLS:
                member = _tool_member(members, name, root)
                if member.name not in verified_members:
                    extracted = archive.extractfile(member)
                    if extracted is None:
                        raise VerificationError(f"cannot read LLVM tool: {name}")
                    digest = hashlib.sha256()
                    size = 0
                    for chunk in iter(lambda: extracted.read(1024 * 1024), b""):
                        digest.update(chunk)
                        size += len(chunk)
                    verified_members[member.name] = (size, digest.hexdigest())
                size, observed_digest = verified_members[member.name]
                record = tools[name]
                assert isinstance(record, dict)
                if size != record["size"] or observed_digest != record["sha256"]:
                    raise VerificationError(f"LLVM tool digest mismatch in archive: {name}")
            resource_headers = policy["resource_headers"]
            assert isinstance(resource_headers, dict)
            resource_files = resource_headers["files"]
            assert isinstance(resource_files, dict)
            verified_resources: list[
                tuple[tarfile.TarInfo, str, dict[str, object]]
            ] = []
            for relative, record in resource_files.items():
                assert isinstance(relative, str) and isinstance(record, dict)
                member = _resource_member(
                    members,
                    root,
                    str(resource_headers["archive_root"]),
                    relative,
                )
                verified_resources.append((member, relative, record))
            for member, relative, record in sorted(
                verified_resources, key=lambda item: item[0].offset_data
            ):
                _verify_member_content(
                    archive,
                    member,
                    record,
                    f"LLVM resource header {relative}",
                )
    except (tarfile.TarError, OSError) as error:
        raise VerificationError(f"invalid LLVM release archive: {error}") from error


def _cache_manifest(policy: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "policy_sha256": _policy_digest(policy),
        "asset": policy["asset"],
        "tools": policy["tools"],
        "resource_headers": policy["resource_headers"],
    }


def _discard(parent_fd: int, name: str) -> None:
    path = Path(f"/proc/self/fd/{parent_fd}") / name
    if path.exists():
        for child in path.rglob("*"):
            try:
                os.chmod(child, 0o700 if child.is_dir() else 0o600, follow_symlinks=False)
            except OSError:
                pass
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass
        shutil.rmtree(path)


def publish_verified_archive(
    source_archive: Path,
    cache: Path,
    policy: dict[str, object],
    *,
    _source_is_descriptor_bound: bool = False,
) -> None:
    """Publish a locally acquired, already policy-bound archive atomically."""
    _validate_policy(policy)
    if not _source_is_descriptor_bound:
        _reject_symlink_ancestors(source_archive, "LLVM source archive")
    try:
        source_info = source_archive.lstat()
    except OSError as error:
        raise VerificationError("LLVM source archive is missing") from error
    if (
        not stat.S_ISREG(source_info.st_mode)
        or source_info.st_nlink != 1
        or source_info.st_uid != os.geteuid()
        or stat.S_IMODE(source_info.st_mode) & 0o022
    ):
        raise VerificationError("LLVM source archive is not an owner-controlled direct file")
    cache, parent_fd = _open_private_parent(cache, "LLVM cache")
    stage_name = f".llvm-cache-{os.getpid()}-{os.urandom(8).hex()}"
    try:
        try:
            os.stat(cache.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise VerificationError("LLVM cache destination already exists")
        os.mkdir(stage_name, 0o700, dir_fd=parent_fd)
        stage = Path(f"/proc/self/fd/{parent_fd}") / stage_name
        asset = policy["asset"]
        assert isinstance(asset, dict)
        cached_archive = stage / str(asset["filename"])
        with source_archive.open("rb") as source, cached_archive.open("xb") as target:
            shutil.copyfileobj(source, target, length=1024 * 1024)
        os.chmod(cached_archive, 0o400)
        _verify_archive(cached_archive, policy, reject_ancestors=False)
        manifest = stage / CACHE_MANIFEST
        with manifest.open("xb") as handle:
            handle.write(_canonical_bytes(_cache_manifest(policy)))
        os.chmod(manifest, 0o400)
        os.chmod(stage, 0o500)
        os.replace(stage_name, cache.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
    except BaseException:
        _discard(parent_fd, stage_name)
        raise
    finally:
        os.close(parent_fd)


def acquire(cache: Path, policy: dict[str, object]) -> None:
    """Download only the exact policy URL, then verify before publication."""
    cache, parent_fd = _open_private_parent(cache, "LLVM cache")
    temporary_name = f".llvm-download-{os.getpid()}-{os.urandom(8).hex()}"
    temporary = Path(f"/proc/self/fd/{parent_fd}") / temporary_name
    try:
        asset = policy["asset"]
        assert isinstance(asset, dict)
        request = urllib.request.Request(str(asset["url"]), headers={"User-Agent": "trading-agent-llvm-cache/1"})
        with urllib.request.urlopen(request, timeout=60) as response, temporary.open("xb") as target:
            shutil.copyfileobj(response, target, length=1024 * 1024)
        os.chmod(temporary, 0o600)
        publish_verified_archive(
            temporary, cache, policy, _source_is_descriptor_bound=True
        )
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        os.close(parent_fd)


def verify_cache(cache: Path, policy: dict[str, object]) -> None:
    """Verify a sealed cache without network access or subprocesses."""
    _validate_policy(policy)
    cache = _ensure_external(cache, "LLVM cache")
    _reject_symlink_ancestors(cache, "LLVM cache")
    _directory(cache, 0o500, "LLVM cache")
    asset = policy["asset"]
    assert isinstance(asset, dict)
    expected = {str(asset["filename"]), CACHE_MANIFEST}
    if {entry.name for entry in cache.iterdir()} != expected:
        raise VerificationError("LLVM cache file set mismatch")
    archive = cache / str(asset["filename"])
    archive_info = _regular_file(archive, 0o400, "LLVM cached archive")
    _regular_file(cache / CACHE_MANIFEST, 0o400, "LLVM cache manifest")
    if archive_info.st_size != asset["size"] or _sha256(archive) != asset["sha256"]:
        raise VerificationError("LLVM cached archive digest or size mismatch")
    try:
        manifest_bytes = (cache / CACHE_MANIFEST).read_bytes()
        manifest = json.loads(manifest_bytes)
    except (OSError, json.JSONDecodeError) as error:
        raise VerificationError("invalid LLVM cache manifest") from error
    expected_manifest = _cache_manifest(policy)
    if manifest != expected_manifest or manifest_bytes != _canonical_bytes(expected_manifest):
        raise VerificationError("LLVM cache manifest is not policy-bound")


def _write_tool(
    archive: tarfile.TarFile,
    member: tarfile.TarInfo,
    destination: Path,
    record: dict[str, object],
    name: str,
) -> None:
    source = archive.extractfile(member)
    if source is None:
        raise VerificationError(f"cannot read LLVM tool: {name}")
    digest = hashlib.sha256()
    size = 0
    with destination.open("xb") as output:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            output.write(chunk)
            digest.update(chunk)
            size += len(chunk)
    if size != record["size"] or digest.hexdigest() != record["sha256"]:
        raise VerificationError(f"LLVM tool digest mismatch while materializing: {name}")
    os.chmod(destination, 0o500)


def _write_resource_header(
    archive: tarfile.TarFile,
    member: tarfile.TarInfo,
    destination: Path,
    record: dict[str, object],
    relative: str,
) -> None:
    source = archive.extractfile(member)
    if source is None:
        raise VerificationError(f"cannot read LLVM resource header: {relative}")
    digest = hashlib.sha256()
    size = 0
    with destination.open("xb") as output:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            output.write(chunk)
            digest.update(chunk)
            size += len(chunk)
    if size != record["size"] or digest.hexdigest() != record["sha256"]:
        raise VerificationError(
            f"LLVM resource header digest mismatch while materializing: {relative}"
        )
    os.chmod(destination, 0o400)


def materialize(
    cache: Path, destination: Path, policy: dict[str, object]
) -> dict[str, str]:
    """Materialize direct compiler binaries into an atomic sealed directory."""
    _validate_policy(policy)
    verify_cache(cache, policy)
    destination, parent_fd = _open_private_parent(destination, "LLVM toolchain")
    stage_name = f".llvm-toolchain-{os.getpid()}-{os.urandom(8).hex()}"
    try:
        try:
            os.stat(destination.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise VerificationError("LLVM toolchain destination already exists")
        os.mkdir(stage_name, 0o700, dir_fd=parent_fd)
        stage = Path(f"/proc/self/fd/{parent_fd}") / stage_name
        binary_dir = stage / "bin"
        binary_dir.mkdir(mode=0o700)
        asset = policy["asset"]
        tools = policy["tools"]
        resource_headers = policy["resource_headers"]
        assert (
            isinstance(asset, dict)
            and isinstance(tools, dict)
            and isinstance(resource_headers, dict)
        )
        with tarfile.open(cache / str(asset["filename"]), "r:xz") as archive:
            members = _archive_members(archive, str(asset["archive_root"]))
            materialized_members: dict[str, Path] = {}
            for name in REQUIRED_TOOLS:
                record = tools[name]
                assert isinstance(record, dict)
                member = _tool_member(members, name, str(asset["archive_root"]))
                destination_tool = binary_dir / name
                if member.name in materialized_members:
                    shutil.copyfile(materialized_members[member.name], destination_tool)
                    os.chmod(destination_tool, 0o500)
                else:
                    _write_tool(archive, member, destination_tool, record, name)
                    materialized_members[member.name] = destination_tool
            resource_root = stage / str(resource_headers["archive_root"])
            resource_root.mkdir(parents=True, mode=0o700)
            resource_files = resource_headers["files"]
            assert isinstance(resource_files, dict)
            materialized_resources: list[
                tuple[tarfile.TarInfo, str, dict[str, object]]
            ] = []
            for relative, record in resource_files.items():
                assert isinstance(relative, str) and isinstance(record, dict)
                member = _resource_member(
                    members,
                    str(asset["archive_root"]),
                    str(resource_headers["archive_root"]),
                    relative,
                )
                materialized_resources.append((member, relative, record))
            for member, relative, record in sorted(
                materialized_resources, key=lambda item: item[0].offset_data
            ):
                destination_header = resource_root.joinpath(*PurePosixPath(relative).parts)
                destination_header.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                _write_resource_header(
                    archive, member, destination_header, record, relative
                )
        manifest_document = {
            "schema_version": 2,
            "policy_sha256": _policy_digest(policy),
            "tools": tools,
            "resource_headers": resource_headers,
        }
        manifest = stage / TOOLCHAIN_MANIFEST
        with manifest.open("xb") as handle:
            handle.write(_canonical_bytes(manifest_document))
        os.chmod(manifest, 0o400)
        os.chmod(binary_dir, 0o500)
        for current, directories, _files in os.walk(stage / "lib", topdown=False):
            for directory in directories:
                os.chmod(Path(current) / directory, 0o500)
            os.chmod(current, 0o500)
        os.chmod(stage, 0o500)
        os.replace(stage_name, destination.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
    except BaseException:
        _discard(parent_fd, stage_name)
        raise
    finally:
        os.close(parent_fd)
    return verify_materialized(destination, policy)


def verify_materialized(toolchain: Path, policy: dict[str, object]) -> dict[str, str]:
    _validate_policy(policy)
    toolchain = _ensure_external(toolchain, "LLVM toolchain")
    _reject_symlink_ancestors(toolchain, "LLVM toolchain")
    _directory(toolchain, 0o500, "LLVM toolchain")
    binary_dir = toolchain / "bin"
    tools = policy["tools"]
    resource_headers = policy["resource_headers"]
    assert isinstance(tools, dict) and isinstance(resource_headers, dict)
    resource_files = resource_headers["files"]
    assert isinstance(resource_files, dict)
    expected_files = {
        TOOLCHAIN_MANIFEST,
        *(f"bin/{name}" for name in REQUIRED_TOOLS),
        *(
            f"{resource_headers['archive_root']}/{relative}"
            for relative in resource_files
        ),
    }
    expected_directories = {".", "bin"}
    for relative in expected_files:
        parent = PurePosixPath(relative).parent
        while str(parent) != ".":
            expected_directories.add(str(parent))
            parent = parent.parent
    observed_files: set[str] = set()
    observed_directories = {"."}
    for current, directories, files in os.walk(toolchain, followlinks=False):
        current_path = Path(current)
        relative_current = current_path.relative_to(toolchain)
        for directory in directories:
            relative = (relative_current / directory).as_posix()
            observed_directories.add(relative)
            _directory(current_path / directory, 0o500, f"LLVM toolchain directory {relative}")
        for filename in files:
            observed_files.add((relative_current / filename).as_posix())
    if observed_files != expected_files or observed_directories != expected_directories:
        missing = sorted(expected_files - observed_files)
        unexpected = sorted(observed_files - expected_files)
        missing_directories = sorted(expected_directories - observed_directories)
        unexpected_directories = sorted(observed_directories - expected_directories)
        raise VerificationError(
            "LLVM toolchain file set mismatch: "
            f"missing={missing}, unexpected={unexpected}, "
            f"missing_directories={missing_directories}, "
            f"unexpected_directories={unexpected_directories}"
        )
    _regular_file(toolchain / TOOLCHAIN_MANIFEST, 0o400, "LLVM toolchain manifest")
    expected_manifest = {
        "schema_version": 2,
        "policy_sha256": _policy_digest(policy),
        "tools": tools,
        "resource_headers": resource_headers,
    }
    manifest_path = toolchain / TOOLCHAIN_MANIFEST
    try:
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes)
    except (OSError, json.JSONDecodeError) as error:
        raise VerificationError("invalid LLVM toolchain manifest") from error
    if manifest != expected_manifest or manifest_bytes != _canonical_bytes(expected_manifest):
        raise VerificationError("LLVM toolchain manifest is not policy-bound")
    identities: dict[str, str] = {}
    for name in REQUIRED_TOOLS:
        record = tools[name]
        assert isinstance(record, dict)
        binary = binary_dir / name
        info = _regular_file(binary, 0o500, f"LLVM tool {name}")
        if info.st_size != record["size"] or _sha256(binary) != record["sha256"]:
            raise VerificationError(f"LLVM tool digest mismatch: {name}")
        try:
            completed = subprocess.run(
                [str(binary), "--version"],
                check=True,
                capture_output=True,
                text=True,
                env={"PATH": str(binary_dir)},
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise VerificationError(f"LLVM tool identity failed: {name}") from error
        identity = (completed.stdout or completed.stderr).splitlines()[0].strip()
        if not identity.startswith(str(record["identity_prefix"])):
            raise VerificationError(f"LLVM tool identity mismatch: {name}")
        identities[name] = identity
    resource_root = toolchain / str(resource_headers["archive_root"])
    for relative, record in resource_files.items():
        assert isinstance(relative, str) and isinstance(record, dict)
        header = resource_root.joinpath(*PurePosixPath(relative).parts)
        info = _regular_file(header, 0o400, f"LLVM resource header {relative}")
        if info.st_size != record["size"] or _sha256(header) != record["sha256"]:
            raise VerificationError(f"LLVM resource header digest mismatch: {relative}")
    return identities


def compiler_environment(llvm_bin: Path) -> dict[str, str]:
    llvm_bin = _ensure_external(llvm_bin, "LLVM bin")
    return {
        "PATH": str(llvm_bin),
        "CC": str(llvm_bin / "clang"),
        "CXX": str(llvm_bin / "clang++"),
        "LD": str(llvm_bin / "ld.lld"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--destination", type=Path)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--acquire", action="store_true")
    action.add_argument("--verify-cache", action="store_true")
    action.add_argument("--materialize", action="store_true")
    action.add_argument("--verify-toolchain", action="store_true")
    action.add_argument("--print-compiler-env", action="store_true")
    args = parser.parse_args(argv)
    try:
        policy = load_policy(args.policy)
        if args.acquire:
            if args.cache is None:
                parser.error("--acquire requires --cache")
            acquire(args.cache, policy)
            verify_cache(args.cache, policy)
            print("nautilus LLVM cache acquisition: PASS")
        elif args.verify_cache:
            if args.cache is None:
                parser.error("--verify-cache requires --cache")
            verify_cache(args.cache, policy)
            print("nautilus LLVM cache offline verification: PASS")
        elif args.materialize:
            if args.cache is None or args.destination is None:
                parser.error("--materialize requires --cache and --destination")
            identities = materialize(args.cache, args.destination, policy)
            for name in REQUIRED_TOOLS:
                print(f"{name}: {identities[name]}")
        elif args.verify_toolchain:
            if args.destination is None:
                parser.error("--verify-toolchain requires --destination")
            identities = verify_materialized(args.destination, policy)
            for name in REQUIRED_TOOLS:
                print(f"{name}: {identities[name]}")
            print("nautilus LLVM toolchain offline verification: PASS")
        else:
            if args.destination is None:
                parser.error("--print-compiler-env requires --destination")
            verify_materialized(args.destination, policy)
            for name, value in compiler_environment(args.destination / "bin").items():
                print(f"{name}={value}")
    except VerificationError as error:
        print(f"nautilus LLVM verification failed: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
