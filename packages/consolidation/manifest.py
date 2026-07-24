"""Deterministic manifests built only from immutable Git objects."""

from __future__ import annotations

from dataclasses import dataclass
import fnmatch
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import unicodedata
from typing import Any

from .authority import (
    AuthorityError,
    ComponentAuthority,
    verify_component_authority,
)


_GIT = "/usr/bin/git"
_GIT_TIMEOUT_SECONDS = 30
_GIT_ENV = {
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_OPTIONAL_LOCKS": "0",
    "HOME": "/nonexistent",
    "LANG": "C",
    "LC_ALL": "C",
    "PATH": "/usr/bin:/bin",
}
_GIT_ID = re.compile(r"[0-9a-f]{40}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_POLICY_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
_COMPONENT_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
_REGULAR_MODES = frozenset({"100644", "100755"})


class ManifestError(RuntimeError):
    """A sanitized manifest failure with an optional safe relative path."""

    def __init__(self, reason_code: str, path: str | None = None) -> None:
        self.reason_code = reason_code
        self.path = _error_path(path)
        message = reason_code if self.path is None else f"{reason_code}: {self.path}"
        super().__init__(message)

    def __repr__(self) -> str:
        return f"ManifestError(reason_code={self.reason_code!r}, path={self.path!r})"


@dataclass(frozen=True, slots=True, repr=False)
class ImportPolicy:
    """Serializable path-selection policy evaluated against tree-relative paths."""

    name: str
    include_patterns: tuple[str, ...]
    exclude_patterns: tuple[str, ...] = ()
    forbidden_patterns: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, repr=False)
class ManifestEntry:
    source_path: str
    destination_path: str
    git_blob: str
    size: int
    mode: str
    sha256: str


@dataclass(frozen=True, slots=True, repr=False)
class ComponentManifest:
    schema_version: int
    component: str
    source_repository: Path
    source_commit: str
    source_tree: str
    source_prefix: PurePosixPath
    destination_prefix: PurePosixPath
    policy: ImportPolicy
    entries: tuple[ManifestEntry, ...]
    aggregate_sha256: str


def _error_path(value: str | None) -> str | None:
    if (
        not isinstance(value, str)
        or not value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        return None
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or ".." in path.parts
        or "." in path.parts
        or path.as_posix() != value
    ):
        return None
    return value


def _path(value: object) -> str:
    if not isinstance(value, str) or _error_path(value) is None:
        raise ManifestError("MANIFEST_PATH_INVALID")
    return value


def _prefix(value: PurePosixPath) -> str:
    if not isinstance(value, PurePosixPath):
        raise ManifestError("MANIFEST_PATH_INVALID")
    text = value.as_posix()
    if text == ".":
        return text
    return _path(text)


def _pattern(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ManifestError("MANIFEST_POLICY_INVALID")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or value != path.as_posix():
        raise ManifestError("MANIFEST_POLICY_INVALID")
    return value


def _validate_policy(policy: ImportPolicy) -> None:
    if (
        not isinstance(policy, ImportPolicy)
        or not isinstance(policy.name, str)
        or _POLICY_NAME.fullmatch(policy.name) is None
    ):
        raise ManifestError("MANIFEST_POLICY_INVALID")
    if not isinstance(policy.include_patterns, tuple) or not policy.include_patterns:
        raise ManifestError("MANIFEST_POLICY_INVALID")
    if not isinstance(policy.exclude_patterns, tuple) or not isinstance(
        policy.forbidden_patterns, tuple
    ):
        raise ManifestError("MANIFEST_POLICY_INVALID")
    for collection in (
        policy.include_patterns, policy.exclude_patterns, policy.forbidden_patterns,
    ):
        for pattern in collection:
            _pattern(pattern)
        if len(set(collection)) != len(collection):
            raise ManifestError("MANIFEST_POLICY_INVALID")


def _policy_document(policy: ImportPolicy) -> dict[str, Any]:
    _validate_policy(policy)
    return {
        "name": policy.name,
        "include_patterns": list(policy.include_patterns),
        "exclude_patterns": list(policy.exclude_patterns),
        "forbidden_patterns": list(policy.forbidden_patterns),
    }


def _matches(path: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def _selected(path: str, policy: ImportPolicy) -> bool:
    if _matches(path, policy.forbidden_patterns):
        raise ManifestError("MANIFEST_POLICY_FORBIDDEN", path)
    return _matches(path, policy.include_patterns) and not _matches(
        path, policy.exclude_patterns
    )


def _git_output(
    repository: Path,
    arguments: list[str],
    *,
    reason_code: str,
    path: str | None = None,
) -> bytes:
    try:
        return subprocess.run(
            [_GIT, "-C", os.fspath(repository), *arguments],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=dict(_GIT_ENV),
            text=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        ).stdout
    except subprocess.TimeoutExpired:
        raise ManifestError(reason_code, path) from None
    except (OSError, subprocess.SubprocessError):
        raise ManifestError(reason_code, path) from None


def _join(prefix: str, relative: str) -> str:
    return relative if prefix == "." else f"{prefix}/{relative}"


def _collision_key(path: str) -> tuple[str, str]:
    normalized = unicodedata.normalize("NFC", path)
    return normalized, normalized.casefold()


def _component_identity(path: str) -> tuple[str, ...]:
    return tuple(
        unicodedata.normalize("NFC", component).casefold()
        for component in PurePosixPath(path).parts
    )


def _is_ancestor(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    return len(left) < len(right) and right[:len(left)] == left


def _check_collisions(paths: list[str], *, destination: bool = False) -> None:
    exact: set[str] = set()
    normalized: dict[str, str] = {}
    folded: dict[str, str] = {}
    component_identities: dict[tuple[str, ...], str] = {}
    for path in paths:
        normalized_key, folded_key = _collision_key(path)
        component_identity = _component_identity(path)
        if (
            path in exact
            or (normalized_key in normalized and normalized[normalized_key] != path)
            or (folded_key in folded and folded[folded_key] != path)
            or any(
                _is_ancestor(component_identity, previous)
                or _is_ancestor(previous, component_identity)
                for previous in component_identities
            )
        ):
            raise ManifestError("MANIFEST_PATH_COLLISION", path)
        exact.add(path)
        normalized[normalized_key] = path
        folded[folded_key] = path
        component_identities[component_identity] = path


def _parse_tree(raw: bytes) -> list[tuple[str, str, str, str]]:
    records: list[tuple[str, str, str, str]] = []
    if raw and not raw.endswith(b"\x00"):
        raise ManifestError("MANIFEST_GIT_OBJECT_INVALID")
    for record in raw.split(b"\x00"):
        if not record:
            continue
        try:
            metadata, encoded_path = record.split(b"\t", 1)
            mode_bytes, type_bytes, object_bytes = metadata.split(b" ", 2)
            path = encoded_path.decode("utf-8", errors="strict")
            mode = mode_bytes.decode("ascii", errors="strict")
            object_type = type_bytes.decode("ascii", errors="strict")
            object_id = object_bytes.decode("ascii", errors="strict")
        except (ValueError, UnicodeDecodeError):
            raise ManifestError("MANIFEST_PATH_INVALID") from None
        _path(path)
        if _GIT_ID.fullmatch(object_id) is None:
            raise ManifestError("MANIFEST_GIT_OBJECT_INVALID", path)
        records.append((mode, object_type, object_id, path))
    _check_collisions([record[3] for record in records])
    return records


def _entry_document(entry: ManifestEntry) -> dict[str, Any]:
    return {
        "source_path": entry.source_path,
        "destination_path": entry.destination_path,
        "git_blob": entry.git_blob,
        "size": entry.size,
        "mode": entry.mode,
        "sha256": entry.sha256,
    }


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise ManifestError("MANIFEST_SCHEMA_INVALID") from None


def _entries_digest(entries: tuple[ManifestEntry, ...]) -> str:
    return hashlib.sha256(
        _canonical_json([_entry_document(entry) for entry in entries])
    ).hexdigest()


def _relative_to_prefix(path: str, prefix: str) -> str:
    if prefix == ".":
        return path
    marker = f"{prefix}/"
    if not path.startswith(marker):
        raise ManifestError("MANIFEST_PATH_INVALID")
    relative = path[len(marker):]
    return _path(relative)


def _validate_manifest(manifest: ComponentManifest) -> None:
    if not isinstance(manifest, ComponentManifest) or manifest.schema_version != 1:
        raise ManifestError("MANIFEST_SCHEMA_INVALID")
    if type(manifest.schema_version) is not int:
        raise ManifestError("MANIFEST_SCHEMA_INVALID")
    if not isinstance(manifest.component, str) or _COMPONENT_NAME.fullmatch(
        manifest.component
    ) is None:
        raise ManifestError("MANIFEST_SCHEMA_INVALID")
    try:
        repository_text = os.fspath(manifest.source_repository)
    except TypeError:
        raise ManifestError("MANIFEST_SCHEMA_INVALID") from None
    if not isinstance(repository_text, str):
        raise ManifestError("MANIFEST_SCHEMA_INVALID")
    repository = Path(repository_text)
    if (
        not repository.is_absolute()
        or ".." in repository.parts
        or str(repository) != repository_text
        or any(
            ord(character) < 32 or ord(character) == 127
            for character in repository_text
        )
    ):
        raise ManifestError("MANIFEST_SCHEMA_INVALID")
    if (
        not isinstance(manifest.source_commit, str)
        or not isinstance(manifest.source_tree, str)
        or _GIT_ID.fullmatch(manifest.source_commit) is None
        or _GIT_ID.fullmatch(manifest.source_tree) is None
        or not isinstance(manifest.aggregate_sha256, str)
        or _DIGEST.fullmatch(manifest.aggregate_sha256) is None
    ):
        raise ManifestError("MANIFEST_SCHEMA_INVALID")
    source_prefix = _prefix(manifest.source_prefix)
    destination_prefix = _prefix(manifest.destination_prefix)
    _validate_policy(manifest.policy)
    if not isinstance(manifest.entries, tuple):
        raise ManifestError("MANIFEST_SCHEMA_INVALID")
    source_paths: list[str] = []
    destination_paths: list[str] = []
    for entry in manifest.entries:
        if not isinstance(entry, ManifestEntry):
            raise ManifestError("MANIFEST_SCHEMA_INVALID")
        source_path = _path(entry.source_path)
        destination_path = _path(entry.destination_path)
        relative = _relative_to_prefix(source_path, source_prefix)
        if destination_path != _join(destination_prefix, relative):
            raise ManifestError("MANIFEST_PATH_INVALID")
        if (
            not isinstance(entry.git_blob, str)
            or _GIT_ID.fullmatch(entry.git_blob) is None
            or type(entry.size) is not int
            or entry.size < 0
            or entry.mode not in _REGULAR_MODES
            or not isinstance(entry.sha256, str)
            or _DIGEST.fullmatch(entry.sha256) is None
        ):
            raise ManifestError("MANIFEST_ENTRY_INVALID", relative)
        source_paths.append(source_path)
        destination_paths.append(destination_path)
    _check_collisions(source_paths)
    _check_collisions(destination_paths, destination=True)
    if list(manifest.entries) != sorted(
        manifest.entries, key=lambda entry: entry.destination_path.encode("utf-8")
    ):
        raise ManifestError("MANIFEST_ENTRIES_ORDER_INVALID")
    if _entries_digest(manifest.entries) != manifest.aggregate_sha256:
        raise ManifestError("MANIFEST_AGGREGATE_MISMATCH")


def propose_manifest(
    authority: ComponentAuthority,
    policy: ImportPolicy,
) -> ComponentManifest:
    """Propose a deterministic regular-file snapshot from one fixed Git tree."""

    _validate_policy(policy)
    try:
        verify_component_authority(authority)
    except AuthorityError as error:
        reason = (
            "MANIFEST_SOURCE_MISMATCH"
            if error.reason_code == "AUTHORITY_TREE_MISMATCH"
            else "MANIFEST_GIT_OBJECT_INVALID"
        )
        raise ManifestError(reason) from None
    tree_output = _git_output(
        authority.repository,
        [
            "ls-tree", "-rz", "-r", "--full-tree", "--no-abbrev",
            "--end-of-options", authority.tree,
        ],
        reason_code="MANIFEST_GIT_OBJECT_INVALID",
    )
    source_prefix = authority.source_prefix.as_posix()
    destination_prefix = authority.destination_prefix.as_posix()
    entries: list[ManifestEntry] = []
    for mode, object_type, object_id, relative in _parse_tree(tree_output):
        if not _selected(relative, policy):
            continue
        if mode not in _REGULAR_MODES or object_type != "blob":
            raise ManifestError("MANIFEST_MODE_FORBIDDEN", relative)
        content = _git_output(
            authority.repository,
            ["cat-file", "blob", object_id],
            reason_code="MANIFEST_GIT_OBJECT_INVALID",
            path=relative,
        )
        blob_header = f"blob {len(content)}\0".encode("ascii")
        if hashlib.sha1(blob_header + content, usedforsecurity=False).hexdigest() != object_id:
            raise ManifestError("MANIFEST_GIT_OBJECT_INVALID", relative)
        entries.append(ManifestEntry(
            source_path=_join(source_prefix, relative),
            destination_path=_join(destination_prefix, relative),
            git_blob=object_id,
            size=len(content),
            mode=mode,
            sha256=hashlib.sha256(content).hexdigest(),
        ))
    entries.sort(key=lambda entry: entry.destination_path.encode("utf-8"))
    entries_tuple = tuple(entries)
    manifest = ComponentManifest(
        schema_version=1,
        component=authority.name,
        source_repository=authority.repository,
        source_commit=authority.commit,
        source_tree=authority.tree,
        source_prefix=authority.source_prefix,
        destination_prefix=authority.destination_prefix,
        policy=policy,
        entries=entries_tuple,
        aggregate_sha256=_entries_digest(entries_tuple),
    )
    _validate_manifest(manifest)
    return manifest


def canonical_manifest_bytes(manifest: ComponentManifest) -> bytes:
    """Serialize one validated manifest as stable UTF-8 JSON plus one newline."""

    _validate_manifest(manifest)
    document = {
        "schema_version": manifest.schema_version,
        "component": manifest.component,
        "source_repository": os.fspath(manifest.source_repository),
        "source_commit": manifest.source_commit,
        "source_tree": manifest.source_tree,
        "source_prefix": manifest.source_prefix.as_posix(),
        "destination_prefix": manifest.destination_prefix.as_posix(),
        "policy": _policy_document(manifest.policy),
        "entries": [_entry_document(entry) for entry in manifest.entries],
        "aggregate_sha256": manifest.aggregate_sha256,
    }
    return _canonical_json(document) + b"\n"


def verify_manifest_source(manifest: ComponentManifest) -> None:
    """Recompute and compare a manifest against only its fixed source objects."""

    canonical_manifest_bytes(manifest)
    authority = ComponentAuthority(
        name=manifest.component,
        repository=manifest.source_repository,
        commit=manifest.source_commit,
        tree=manifest.source_tree,
        source_prefix=manifest.source_prefix,
        destination_prefix=manifest.destination_prefix,
    )
    expected = propose_manifest(authority, manifest.policy)
    expected_by_source = {entry.source_path: entry for entry in expected.entries}
    actual_by_source = {entry.source_path: entry for entry in manifest.entries}
    for path in sorted(set(expected_by_source) | set(actual_by_source), key=lambda value: value.encode("utf-8")):
        if expected_by_source.get(path) != actual_by_source.get(path):
            relative = _relative_to_prefix(path, manifest.source_prefix.as_posix())
            raise ManifestError("MANIFEST_SOURCE_MISMATCH", relative)
    if canonical_manifest_bytes(expected) != canonical_manifest_bytes(manifest):
        raise ManifestError("MANIFEST_SOURCE_MISMATCH")
