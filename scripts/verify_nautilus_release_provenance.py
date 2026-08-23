#!/usr/bin/env python3
"""Verify the exact Nautilus 1.231 release provenance from an offline cache."""
from __future__ import annotations

import argparse
from email.parser import BytesParser
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
from typing import Any
import unicodedata
import zipfile


_ROOT = Path(__file__).resolve().parents[1]
_EXPECTED_POLICY_SHA256 = "a8bdaf64020fde99f6accc9d8e60e517b4f1388737cc2e3075ba1fd1d10a6fe0"
_POLICY_FIELDS = {
    "schema_version",
    "engine_name",
    "engine_version",
    "runtime_family",
    "candidate_closure_schema",
    "upstream",
    "cache_layout",
    "cache_trust_model",
    "source_authority",
    "release_assets",
    "build_input_manifest",
    "attestation_disposition",
    "activation_status",
}
_EXPECTED_UPSTREAM = {
    "repository": "https://github.com/nautechsystems/nautilus_trader.git",
    "tag": "v1.231.0",
    "tag_object": "d3e1685e979925d7b0ffacd1b3f442547686e18f",
    "peeled_commit": "27a8e54e7ac3c57d6cbf8891f0283dfbaee97317",
    "git_object_format": "sha1",
}
_SHA1 = re.compile(r"[0-9a-f]{40}")
_SOURCE_BINARY_SUFFIXES = (
    ".so",
    ".dll",
    ".dylib",
    ".pyd",
    ".exe",
    ".o",
    ".a",
    ".whl",
    ".pyc",
    ".pyo",
    ".class",
    ".jar",
)
_WHEEL_NATIVE_SUFFIXES = (".so", ".dll", ".dylib", ".pyd")
_MAX_ARCHIVE_MEMBERS = 20_000
_MAX_MEMBER_BYTES = 512 * 1024 * 1024
_MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024


class VerificationError(ValueError):
    """Raised when supplied provenance is present but cannot be trusted."""


def _closed_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise VerificationError("policy contains a duplicate JSON field")
        document[key] = value
    return document


def _canonical_sha256(value: object) -> str:
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _regular_file(path: Path, label: str, *, mode: int | None = None) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        raise VerificationError(f"{label} is missing") from exc
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != os.geteuid()
    ):
        raise VerificationError(f"{label} is not one task-UID-owned regular file")
    if mode is not None and stat.S_IMODE(info.st_mode) != mode:
        raise VerificationError(f"{label} has an unsafe or mutable mode")
    return info


def _directory(path: Path, label: str, *, mode: int | None = None) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        raise VerificationError(f"{label} is missing") from exc
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.geteuid()
    ):
        raise VerificationError(f"{label} is not a task-UID-owned directory")
    if mode is not None and stat.S_IMODE(info.st_mode) != mode:
        raise VerificationError(f"{label} has an unsafe or mutable mode")
    return info


def _reject_symlinked_ancestors(path: Path, label: str) -> None:
    if (
        not path.is_absolute()
        or path == Path("/")
        or any(part in {"", ".", ".."} for part in path.parts[1:])
    ):
        raise VerificationError(f"{label} must be an absolute non-root path")
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


def _require_sha1(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA1.fullmatch(value) is None:
        raise VerificationError(f"{label} is not an exact SHA-1 object identity")
    return value


def load_policy(path: Path) -> dict[str, Any]:
    _regular_file(path, "release provenance policy")
    try:
        document = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_closed_json_object
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError("release provenance policy is invalid JSON") from exc
    if (
        not isinstance(document, dict)
        or set(document) != _POLICY_FIELDS
        or document.get("schema_version") != 7
        or document.get("candidate_closure_schema") != 7
        or document.get("engine_name") != "nautilus_trader"
        or document.get("engine_version") != "1.231.0"
        or document.get("runtime_family") != "cython-v1"
        or document.get("upstream") != _EXPECTED_UPSTREAM
        or _canonical_sha256(document) != _EXPECTED_POLICY_SHA256
    ):
        raise VerificationError("policy does not match the reviewed schema-7 policy")
    return document


def _archive_path(name: str, *, expected_root: str, label: str) -> str:
    raw = name.rstrip("/")
    if (
        not raw
        or "\\" in raw
        or raw.startswith("/")
        or re.match(r"^[A-Za-z]:", raw)
        or "//" in raw
        or "\x00" in raw
    ):
        raise VerificationError(f"{label} archive has an unsafe member path")
    parts = raw.split("/")
    if any(part in {"", ".", ".."} for part in parts) or parts[0] != expected_root:
        raise VerificationError(f"{label} archive has an unsafe member path")
    relative = "/".join(parts[1:])
    if not relative:
        raise VerificationError(f"{label} archive has a root-only member")
    if re.match(r"^[A-Za-z]:", relative):
        raise VerificationError(f"{label} archive has an unsafe member path")
    return relative


def _register_regular_path(
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


def _scan_source_archive(path: Path, expected_root: str) -> dict[str, Any]:
    records: list[dict[str, object]] = []
    files: dict[str, dict[str, object]] = {}
    path_nodes: dict[str, tuple[str, str]] = {}
    total = 0
    try:
        with tarfile.open(path, "r:gz") as archive:
            members = archive.getmembers()
            if not members or len(members) > _MAX_ARCHIVE_MEMBERS:
                raise VerificationError("source archive member count is invalid")
            for member in members:
                relative = _archive_path(
                    member.name, expected_root=expected_root, label="source"
                )
                _register_regular_path(
                    relative,
                    path_nodes,
                    "source archive",
                )
                if not member.isfile():
                    raise VerificationError("source archive contains a non-file entry")
                if relative.casefold().endswith(_SOURCE_BINARY_SUFFIXES):
                    raise VerificationError(
                        "source archive contains an unexpected generated binary"
                    )
                if member.size < 0 or member.size > _MAX_MEMBER_BYTES:
                    raise VerificationError("source archive member size is invalid")
                total += member.size
                if total > _MAX_TOTAL_BYTES:
                    raise VerificationError("source archive expanded size is invalid")
                source = archive.extractfile(member)
                if source is None:
                    raise VerificationError("source archive file cannot be read")
                digest = hashlib.sha256()
                size = 0
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
                    size += len(block)
                if size != member.size:
                    raise VerificationError("source archive member is truncated")
                record = {
                    "mode": f"{member.mode & 0o7777:04o}",
                    "path": relative,
                    "sha256": digest.hexdigest(),
                    "size": size,
                    "type": "file",
                }
                records.append(record)
                files[relative] = record
    except (OSError, tarfile.TarError) as exc:
        raise VerificationError("source archive is unreadable") from exc
    records.sort(key=lambda item: str(item["path"]))
    return {
        "directory_count": 0,
        "file_count": len(files),
        "files": files,
        "layout_sha256": _canonical_sha256(records),
        "member_count": len(records),
    }


def _wheel_path(name: str) -> str:
    raw = name.rstrip("/")
    if (
        not raw
        or "\\" in raw
        or raw.startswith("/")
        or re.match(r"^[A-Za-z]:", raw)
        or "//" in raw
        or "\x00" in raw
        or any(part in {"", ".", ".."} for part in raw.split("/"))
    ):
        raise VerificationError("wheel archive has an unsafe member path")
    return PurePosixPath(raw).as_posix()


def _scan_wheel_archive(path: Path) -> dict[str, Any]:
    names: list[str] = []
    path_nodes: dict[str, tuple[str, str]] = {}
    roots: set[str] = set()
    native: list[str] = []
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if not infos or len(infos) > _MAX_ARCHIVE_MEMBERS:
                raise VerificationError("wheel archive member count is invalid")
            total = 0
            for info in infos:
                name = _wheel_path(info.filename)
                _register_regular_path(
                    name,
                    path_nodes,
                    "wheel archive",
                )
                roots.add(PurePosixPath(name).parts[0])
                mode = (info.external_attr >> 16) & 0xFFFF
                file_type = stat.S_IFMT(mode)
                if info.is_dir() or stat.S_ISLNK(mode) or file_type not in {
                    0,
                    stat.S_IFREG,
                }:
                    raise VerificationError("wheel archive contains a non-file entry")
                if info.flag_bits & 0x1:
                    raise VerificationError("wheel archive contains an encrypted entry")
                if info.file_size < 0 or info.file_size > _MAX_MEMBER_BYTES:
                    raise VerificationError("wheel archive member size is invalid")
                total += info.file_size
                if total > _MAX_TOTAL_BYTES:
                    raise VerificationError("wheel archive expanded size is invalid")
                if not info.is_dir() and name.casefold().endswith(
                    _SOURCE_BINARY_SUFFIXES
                ):
                    if not name.casefold().endswith(_WHEEL_NATIVE_SUFFIXES) or not name.startswith(
                        "nautilus_trader/"
                    ):
                        raise VerificationError(
                            "wheel archive contains an unexpected generated binary"
                        )
                    native.append(name)
                names.append(name)
            metadata_paths = [
                name for name in names if name.endswith(".dist-info/METADATA")
            ]
            wheel_paths = [name for name in names if name.endswith(".dist-info/WHEEL")]
            if len(metadata_paths) != 1 or len(wheel_paths) != 1:
                raise VerificationError("wheel archive metadata layout is invalid")
            metadata = BytesParser().parsebytes(archive.read(metadata_paths[0]))
            wheel_text = archive.read(wheel_paths[0]).decode("utf-8")
    except (OSError, UnicodeDecodeError, zipfile.BadZipFile) as exc:
        raise VerificationError("wheel archive is unreadable") from exc
    return {
        "file_count": len(infos),
        "member_count": len(infos),
        "metadata_name": metadata.get("Name"),
        "metadata_version": metadata.get("Version"),
        "name_layout_sha256": _canonical_sha256(sorted(names)),
        "native_count": len(native),
        "roots": sorted(roots),
        "tags": sorted(
            line.removeprefix("Tag: ").strip()
            for line in wheel_text.splitlines()
            if line.startswith("Tag: ")
        ),
    }


def _offline_git_env() -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in {"LANG", "LC_ALL", "PATH", "TEMP", "TMP", "TMPDIR"}
    }
    environment.update(
        {
            "GIT_ASKPASS": "/bin/false",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "SSH_ASKPASS": "/bin/false",
        }
    )
    return environment


def _git_command(git_dir: Path, *args: str) -> list[str]:
    return [
        "git",
        "--no-replace-objects",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "protocol.allow=never",
        f"--git-dir={git_dir}",
        *args,
    ]


def _git_output(git_dir: Path, *args: str) -> bytes:
    try:
        return subprocess.run(
            _git_command(git_dir, *args),
            check=True,
            capture_output=True,
            env=_offline_git_env(),
            timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise VerificationError("offline Git object verification failed") from exc


def _assert_git_cache_guard(git_dir: Path) -> None:
    _directory(git_dir, "offline Git cache")
    administrative_escapes = (
        "commondir",
        "config.worktree",
        "gitdir",
        "worktrees",
    )
    if any(os.path.lexists(git_dir / name) for name in administrative_escapes):
        raise VerificationError("offline Git cache contains worktree/common-dir authority")
    config = _git_output(git_dir, "config", "--local", "--null", "--list")
    keys = {
        entry.split(b"\n", 1)[0].decode("utf-8").strip().casefold()
        for entry in config.split(b"\0")
        if entry
    }
    includes = sorted(
        key
        for key in keys
        if key == "include"
        or key.startswith("include.")
        or key == "includeif"
        or key.startswith("includeif.")
    )
    if includes:
        raise VerificationError("offline Git cache contains include authority")
    if any(
        key.startswith(("fsck.", "fetch.fsck.", "receive.fsck.")) for key in keys
    ):
        raise VerificationError("offline Git cache contains fsck policy overrides")
    promisors = sorted(
        key
        for key in keys
        if key == "extensions.partialclone"
        or (key.startswith("remote.") and key.endswith(".promisor"))
        or (key.startswith("remote.") and key.endswith(".partialclonefilter"))
    )
    if promisors or list((git_dir / "objects/pack").glob("*.promisor")):
        raise VerificationError("offline Git cache contains promisor authority")
    if any(key.startswith("extensions.") for key in keys):
        raise VerificationError("offline Git cache contains extension authority")
    if any("alternate" in key for key in keys):
        raise VerificationError("offline Git cache contains alternate configuration")
    for name in ("alternates", "http-alternates"):
        if os.path.lexists(git_dir / "objects/info" / name):
            raise VerificationError("offline Git cache contains alternate authority")
    expected_common = os.fsencode(git_dir.absolute())
    common = _git_output(
        git_dir, "rev-parse", "--path-format=absolute", "--git-common-dir"
    ).strip()
    objects = _git_output(
        git_dir, "rev-parse", "--path-format=absolute", "--git-path", "objects"
    ).strip()
    if common != expected_common or objects != expected_common + b"/objects":
        raise VerificationError("offline Git cache escapes its private object directory")
    object_format = _git_output(
        git_dir, "rev-parse", "--show-object-format"
    ).decode("ascii").strip()
    if object_format != "sha1":
        raise VerificationError("offline Git cache uses an unexpected object format")


def _verify_git_authority(
    git_dir: Path, *, tag: str, tag_object: str, peeled_commit: str
) -> dict[str, str]:
    tag_object = _require_sha1(tag_object, "tag object")
    peeled_commit = _require_sha1(peeled_commit, "peeled commit")
    _assert_git_cache_guard(git_dir)
    if _git_output(git_dir, "rev-parse", "--is-bare-repository").strip() != b"true":
        raise VerificationError("offline Git cache is not bare")
    _git_output(git_dir, "fsck", "--full", "--strict", "--no-dangling")
    observed_tag = _git_output(git_dir, "rev-parse", f"refs/tags/{tag}").decode(
        "ascii"
    ).strip()
    if observed_tag != tag_object:
        raise VerificationError("offline Git tag object does not match policy")
    if _git_output(git_dir, "cat-file", "-t", tag_object).strip() != b"tag":
        raise VerificationError("offline Git tag object is not annotated")
    observed_commit = _git_output(
        git_dir, "rev-parse", f"{tag_object}^{{commit}}"
    ).decode("ascii").strip()
    if observed_commit != peeled_commit:
        raise VerificationError("offline Git peeled commit does not match policy")
    tag_raw = _git_output(git_dir, "cat-file", "tag", tag_object)
    expected_headers = (
        f"object {peeled_commit}\n" f"type commit\n" f"tag {tag}\n"
    ).encode("ascii")
    if not tag_raw.startswith(expected_headers):
        raise VerificationError("offline Git tag payload does not bind the expected commit")
    if b"-----BEGIN PGP SIGNATURE-----" in tag_raw or b"-----BEGIN SSH SIGNATURE-----" in tag_raw:
        raise VerificationError("tag signature disposition drifted from policy")
    return {"tag_object": observed_tag, "peeled_commit": observed_commit}


class _BatchReader:
    def __init__(self, git_dir: Path) -> None:
        try:
            self._process = subprocess.Popen(
                _git_command(git_dir, "cat-file", "--batch"),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=_offline_git_env(),
            )
        except OSError as exc:
            raise VerificationError("offline Git batch reader is unavailable") from exc
        if self._process.stdin is None or self._process.stdout is None:
            raise VerificationError("offline Git batch reader is incomplete")

    def blob(self, oid: str) -> bytes:
        _require_sha1(oid, "Git blob")
        assert self._process.stdin is not None
        assert self._process.stdout is not None
        try:
            self._process.stdin.write((oid + "\n").encode("ascii"))
            self._process.stdin.flush()
            header = self._process.stdout.readline().decode("ascii").strip().split()
            if len(header) != 3 or header[:2] != [oid, "blob"]:
                raise VerificationError("offline Git object is missing or not a blob")
            size = int(header[2])
            if size < 0 or size > _MAX_MEMBER_BYTES:
                raise VerificationError("offline Git blob size is invalid")
            raw = self._process.stdout.read(size)
            if len(raw) != size or self._process.stdout.read(1) != b"\n":
                raise VerificationError("offline Git blob is truncated")
            return raw
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise VerificationError("offline Git blob read failed") from exc

    def close(self) -> None:
        assert self._process.stdin is not None
        self._process.stdin.close()
        if self._process.stdout is not None:
            self._process.stdout.close()
        try:
            return_code = self._process.wait(timeout=30)
        except subprocess.TimeoutExpired as exc:
            self._process.kill()
            self._process.wait()
            raise VerificationError("offline Git batch reader timed out") from exc
        stderr = self._process.stderr.read() if self._process.stderr is not None else b""
        if self._process.stderr is not None:
            self._process.stderr.close()
        if return_code != 0:
            raise VerificationError(
                f"offline Git batch reader failed: {stderr.decode('utf-8', 'replace').strip()}"
            )


def _git_tree(git_dir: Path, commit: str) -> dict[str, tuple[str, str]]:
    raw = _git_output(git_dir, "ls-tree", "-rz", "--full-tree", commit)
    entries: dict[str, tuple[str, str]] = {}
    path_nodes: dict[str, tuple[str, str]] = {}
    for row in raw.split(b"\0"):
        if not row:
            continue
        try:
            header, path_raw = row.split(b"\t", 1)
            mode, kind, oid = header.decode("ascii").split()
            path = path_raw.decode("utf-8", "strict")
        except (UnicodeDecodeError, ValueError) as exc:
            raise VerificationError("Git tree entry is malformed") from exc
        pure = PurePosixPath(path)
        if (
            not path
            or "\\" in path
            or pure.is_absolute()
            or re.match(r"^[A-Za-z]:", path)
            or any(part in {"", ".", ".."} for part in pure.parts)
        ):
            raise VerificationError("Git tree contains an unsafe path")
        if kind != "blob" or mode not in {"100644", "100755", "120000"}:
            raise VerificationError("Git tree contains a non-blob or unsupported mode")
        if path.casefold().endswith(_SOURCE_BINARY_SUFFIXES):
            raise VerificationError("Git tree contains an unexpected generated binary")
        _register_regular_path(
            path,
            path_nodes,
            "Git tree",
        )
        entries[path] = (mode, _require_sha1(oid, "Git tree blob"))
    if not entries or len(entries) > _MAX_ARCHIVE_MEMBERS:
        raise VerificationError("Git tree entry count is invalid")
    return entries


def _resolved_symlink_path(path: str, target: str, entries: dict[str, tuple[str, str]]) -> str:
    if (
        not target
        or "\\" in target
        or PurePosixPath(target).is_absolute()
        or re.match(r"^[A-Za-z]:", target)
    ):
        raise VerificationError("Git symlink target is absolute or ambiguous")
    resolved: list[str] = []
    for part in (*PurePosixPath(path).parent.parts, *PurePosixPath(target).parts):
        if part in {"", "."}:
            continue
        if part == "..":
            if not resolved:
                raise VerificationError("Git symlink target escapes the source tree")
            resolved.pop()
        else:
            resolved.append(part)
    result = "/".join(resolved)
    if not result or result not in entries:
        raise VerificationError("Git symlink target is outside the source tree")
    return result


def materialize_git_source(git_dir: Path, commit: str, output: Path) -> dict[str, Any]:
    """Create a deterministic, symlink-free source tarball from one exact Git tree."""
    _assert_git_cache_guard(git_dir)
    commit = _require_sha1(commit, "materialized commit")
    resolved = _git_output(git_dir, "rev-parse", f"{commit}^{{commit}}").decode(
        "ascii"
    ).strip()
    if resolved != commit:
        raise VerificationError("materialized commit does not resolve exactly")
    if output.exists() or output.is_symlink():
        raise VerificationError("materialized source destination must be absent")
    _reject_symlinked_ancestors(output.parent.absolute(), "materialization directory")
    _directory(output.parent, "materialization directory")
    entries = _git_tree(git_dir, commit)
    reader = _BatchReader(git_dir)
    blob_cache: dict[str, bytes] = {}

    def blob(oid: str) -> bytes:
        if oid not in blob_cache:
            blob_cache[oid] = reader.blob(oid)
        return blob_cache[oid]

    records: list[dict[str, object]] = []
    payloads: dict[str, bytes] = {}
    try:
        for path in sorted(entries):
            source_mode, source_blob = entries[path]
            current = path
            seen: set[str] = set()
            while entries[current][0] == "120000":
                if current in seen:
                    raise VerificationError("Git symlink cycle is not permitted")
                seen.add(current)
                try:
                    target = blob(entries[current][1]).decode("utf-8", "strict")
                except UnicodeDecodeError as exc:
                    raise VerificationError("Git symlink target is not UTF-8") from exc
                current = _resolved_symlink_path(current, target, entries)
            resolved_mode, resolved_blob = entries[current]
            if resolved_mode not in {"100644", "100755"}:
                raise VerificationError("Git symlink resolves to a non-regular source")
            payload = blob(resolved_blob)
            output_mode = "0755" if resolved_mode == "100755" else "0644"
            records.append(
                {
                    "output_mode": output_mode,
                    "output_sha256": hashlib.sha256(payload).hexdigest(),
                    "path": path,
                    "resolved_blob": resolved_blob,
                    "resolved_path": current,
                    "size": len(payload),
                    "source_blob": source_blob,
                    "source_mode": source_mode,
                }
            )
            payloads[path] = payload
        reader.close()
    except BaseException:
        try:
            reader.close()
        except BaseException:
            pass
        raise
    root = f"nautilus_trader-{commit}"
    try:
        with output.open("xb") as raw_output:
            with gzip.GzipFile(
                filename="", mode="wb", fileobj=raw_output, compresslevel=9, mtime=0
            ) as compressed:
                with tarfile.open(
                    fileobj=compressed,
                    mode="w",
                    format=tarfile.PAX_FORMAT,
                    pax_headers={},
                ) as archive:
                    for record in records:
                        path = str(record["path"])
                        payload = payloads[path]
                        member = tarfile.TarInfo(f"{root}/{path}")
                        member.type = tarfile.REGTYPE
                        member.mode = int(str(record["output_mode"]), 8)
                        member.uid = member.gid = member.mtime = 0
                        member.uname = member.gname = ""
                        member.size = len(payload)
                        archive.addfile(member, io.BytesIO(payload))
        os.chmod(output, 0o400)
    except BaseException:
        try:
            output.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    symlink_records = [record for record in records if record["source_mode"] == "120000"]
    return {
        "archive_sha256": _sha256(output),
        "archive_size": output.stat().st_size,
        "entry_count": len(records),
        "manifest_sha256": _canonical_sha256(records),
        "member_count": len(records),
        "records": records,
        "symlink_count": len(symlink_records),
        "symlink_records": symlink_records,
    }


def _is_build_input(path: str) -> bool:
    name = PurePosixPath(path).name
    return path in {".cargo/config.toml", "build.py", "rust-toolchain.toml"} or name in {
        "Cargo.toml",
        "Cargo.lock",
        "build.rs",
        "pyproject.toml",
    }


def _verify_build_inputs(
    materialization: dict[str, Any], sdist: dict[str, Any], policy: dict[str, Any]
) -> None:
    sdist_files = sdist["files"]
    observed: list[dict[str, object]] = []
    for record in materialization["records"]:
        path = str(record["path"])
        if not _is_build_input(path):
            continue
        cross_check = "OMITTED_FROM_SDIST"
        if path in sdist_files:
            source = sdist_files[path]
            if (
                source["sha256"] != record["output_sha256"]
                or source["size"] != record["size"]
            ):
                raise VerificationError(
                    "primary and sdist cross-check build input bytes disagree"
                )
            cross_check = "BYTE_IDENTICAL"
        observed.append(
            {
                "blob_oid": record["source_blob"],
                "git_mode": record["source_mode"],
                "path": path,
                "sdist_cross_check": cross_check,
                "sha256": record["output_sha256"],
                "size": record["size"],
            }
        )
    manifest = policy["build_input_manifest"]
    if (
        observed != manifest["records"]
        or len(observed) != manifest["record_count"]
        or _canonical_sha256(observed) != manifest["sha256"]
    ):
        raise VerificationError("exact build input manifest drifted")


def _validate_cache(cache: Path, policy: dict[str, Any]) -> None:
    _reject_symlinked_ancestors(cache, "release provenance cache")
    try:
        cache.relative_to(_ROOT)
    except ValueError:
        pass
    else:
        raise VerificationError("release provenance cache must remain outside Git")
    _directory(cache, "release provenance cache", mode=0o500)
    expected = set(policy["cache_layout"]["entries"])
    observed = {entry.name for entry in cache.iterdir()}
    if observed != expected:
        raise VerificationError("release provenance cache has missing or unexpected entries")
    for current, directories, names in os.walk(cache, followlinks=False):
        current_path = Path(current)
        for directory in directories:
            _directory(current_path / directory, "release provenance cache directory", mode=0o500)
        for name in names:
            _regular_file(current_path / name, "release provenance cache file", mode=0o400)


def _verify_artifact(path: Path, record: dict[str, Any], label: str) -> None:
    info = _regular_file(path, label, mode=0o400)
    if info.st_size != record["size"] or _sha256(path) != record["sha256"]:
        raise VerificationError(f"{label} digest or size drifted")


def verify(policy_path: Path, cache: Path | None) -> dict[str, Any]:
    policy = load_policy(policy_path)
    if cache is None:
        return {"reason": "cache-not-supplied", "status": "DEFERRED"}
    _validate_cache(cache, policy)
    upstream = policy["upstream"]
    git_dir = cache / policy["cache_layout"]["git_directory"]
    git_receipt = _verify_git_authority(
        git_dir,
        tag=upstream["tag"],
        tag_object=upstream["tag_object"],
        peeled_commit=upstream["peeled_commit"],
    )
    primary = policy["source_authority"]["primary"]
    cross_check = policy["source_authority"]["cross_check"]
    wheel_record = policy["release_assets"]["cpython312_linux_wheel"]
    primary_path = cache / primary["filename"]
    sdist_path = cache / cross_check["filename"]
    wheel_path = cache / wheel_record["filename"]
    _verify_artifact(primary_path, primary, "primary materialized source")
    _verify_artifact(sdist_path, cross_check, "official sdist cross-check")
    _verify_artifact(wheel_path, wheel_record, "official CPython 3.12 Linux wheel")
    with tempfile.TemporaryDirectory(prefix="p1-u02-provenance-") as temporary:
        reproduced_path = Path(temporary) / primary["filename"]
        reproduced = materialize_git_source(
            git_dir, upstream["peeled_commit"], reproduced_path
        )
    expected_materialization = {
        "archive_sha256": primary["sha256"],
        "archive_size": primary["size"],
        "entry_count": primary["entry_count"],
        "manifest_sha256": primary["materialization_manifest_sha256"],
        "member_count": primary["member_count"],
        "symlink_count": primary["symlink_count"],
        "symlink_records": primary["symlink_records"],
    }
    observed_materialization = {
        key: reproduced[key] for key in expected_materialization
    }
    if observed_materialization != expected_materialization:
        raise VerificationError("primary source materialization drifted from Git authority")
    sdist = _scan_source_archive(sdist_path, cross_check["top_level_root"])
    if any(
        sdist[key] != cross_check[key]
        for key in ("member_count", "file_count", "directory_count", "layout_sha256")
    ):
        raise VerificationError("official sdist cross-check layout drifted")
    _verify_build_inputs(reproduced, sdist, policy)
    wheel = _scan_wheel_archive(wheel_path)
    if (
        wheel["member_count"] != wheel_record["member_count"]
        or wheel["file_count"] != wheel_record["file_count"]
        or wheel["roots"] != wheel_record["top_level_roots"]
        or wheel["native_count"] != wheel_record["native_member_count"]
        or wheel["name_layout_sha256"] != wheel_record["name_layout_sha256"]
        or wheel["metadata_name"] != "nautilus_trader"
        or wheel["metadata_version"] != "1.231.0"
        or wheel["tags"] != ["cp312-cp312-manylinux_2_35_x86_64"]
    ):
        raise VerificationError("official wheel identity or layout drifted")
    return {
        "network": "DISABLED_BY_CONSTRUCTION",
        "peeled_commit": git_receipt["peeled_commit"],
        "primary_sha256": primary["sha256"],
        "status": "PASS",
        "tag_object": git_receipt["tag_object"],
        "wheel_sha256": wheel_record["sha256"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--cache", type=Path)
    arguments = parser.parse_args(argv)
    try:
        receipt = verify(arguments.policy, arguments.cache)
    except (OSError, VerificationError) as exc:
        reason = "-".join(str(exc).split())
        print(f"NAUTILUS_RELEASE_PROVENANCE=FAIL reason={reason}", file=sys.stderr)
        return 2
    if receipt["status"] == "DEFERRED":
        print("NAUTILUS_RELEASE_PROVENANCE=DEFERRED reason=cache-not-supplied")
        return 3
    print(
        "NAUTILUS_RELEASE_PROVENANCE=PASS "
        f"tag_object={receipt['tag_object']} "
        f"peeled_commit={receipt['peeled_commit']} "
        f"primary_sha256={receipt['primary_sha256']} "
        f"wheel_sha256={receipt['wheel_sha256']} "
        "network=DISABLED_BY_CONSTRUCTION"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
