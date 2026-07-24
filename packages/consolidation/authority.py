"""Strict loader for the fixed source repositories and Git objects."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
from types import MappingProxyType
from typing import Any, Mapping


_GIT = "/usr/bin/git"
_GIT_TIMEOUT_SECONDS = 30
_MAX_DOCUMENT_BYTES = 1024 * 1024
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_ROOT_KEYS = frozenset({
    "schema_version", "sealed_phase4b_metadata_sha256", "components",
})
_COMPONENT_KEYS = frozenset({
    "repository", "commit", "tree", "source_prefix", "destination_prefix",
})
_COMPONENT_PREFIXES = MappingProxyType({
    "core": (".", "."),
    "backend": (".", "legacy/research-backend"),
    "dashboard": ("trading-agent", "apps/dashboard"),
})
_GIT_ENV = MappingProxyType({
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_OPTIONAL_LOCKS": "0",
    "HOME": "/nonexistent",
    "LANG": "C",
    "LC_ALL": "C",
    "PATH": "/usr/bin:/bin",
})


class AuthorityError(RuntimeError):
    """A sanitized authority failure with a stable machine reason."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)

    def __repr__(self) -> str:
        return f"AuthorityError(reason_code={self.reason_code!r})"


@dataclass(frozen=True, slots=True, repr=False)
class ComponentAuthority:
    name: str
    repository: Path
    commit: str
    tree: str
    source_prefix: PurePosixPath
    destination_prefix: PurePosixPath


@dataclass(frozen=True, slots=True, repr=False)
class SourceAuthority:
    schema_version: int
    sealed_phase4b_metadata_sha256: str
    components: Mapping[str, ComponentAuthority]


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _read_document(path: Path) -> bytes:
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_DOCUMENT_BYTES:
            raise ValueError
        chunks: list[bytes] = []
        total = 0
        while chunk := os.read(descriptor, 65536):
            total += len(chunk)
            if total > _MAX_DOCUMENT_BYTES:
                raise ValueError
            chunks.append(chunk)
        return b"".join(chunks)
    except (OSError, ValueError):
        raise AuthorityError("AUTHORITY_DOCUMENT_INVALID") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _git_output(repository: Path, arguments: list[str], reason_code: str) -> bytes:
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
        raise AuthorityError(reason_code) from None
    except (OSError, subprocess.SubprocessError):
        raise AuthorityError(reason_code) from None


def _valid_prefix(value: str) -> PurePosixPath:
    if (
        not isinstance(value, str)
        or not value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise AuthorityError("AUTHORITY_PREFIX_INVALID")
    prefix = PurePosixPath(value)
    if (
        prefix.is_absolute()
        or ".." in prefix.parts
        or value != prefix.as_posix()
        or (value != "." and "." in prefix.parts)
    ):
        raise AuthorityError("AUTHORITY_PREFIX_INVALID")
    return prefix


def _valid_repository(value: object) -> Path:
    if not isinstance(value, str) or any(
        ord(character) < 32 or ord(character) == 127 for character in value
    ):
        raise AuthorityError("AUTHORITY_REPOSITORY_INVALID")
    repository = Path(value)
    if not repository.is_absolute() or ".." in repository.parts or str(repository) != value:
        raise AuthorityError("AUTHORITY_REPOSITORY_INVALID")
    return repository


def _resolve_component_tree(component: ComponentAuthority) -> str:
    resolved_commit = _git_output(
        component.repository,
        ["rev-parse", "--verify", "--end-of-options", f"{component.commit}^{{commit}}"],
        "AUTHORITY_GIT_OBJECT_INVALID",
    ).strip()
    try:
        commit = resolved_commit.decode("ascii")
    except UnicodeDecodeError:
        raise AuthorityError("AUTHORITY_GIT_OBJECT_INVALID") from None
    if commit != component.commit:
        raise AuthorityError("AUTHORITY_GIT_OBJECT_INVALID")

    object_spec = f"{component.commit}^{{tree}}"
    if component.source_prefix.as_posix() != ".":
        object_spec = f"{component.commit}:{component.source_prefix.as_posix()}"
    resolved_tree = _git_output(
        component.repository,
        ["rev-parse", "--verify", "--end-of-options", object_spec],
        "AUTHORITY_GIT_OBJECT_INVALID",
    ).strip()
    try:
        return resolved_tree.decode("ascii")
    except UnicodeDecodeError:
        raise AuthorityError("AUTHORITY_GIT_OBJECT_INVALID") from None


def verify_component_authority(component: ComponentAuthority) -> None:
    """Re-resolve one immutable Git identity without reading its worktree."""

    if (
        not isinstance(component, ComponentAuthority)
        or not isinstance(component.name, str)
        or not component.name
        or not isinstance(component.repository, Path)
        or not isinstance(component.commit, str)
        or not isinstance(component.tree, str)
        or not isinstance(component.source_prefix, PurePosixPath)
        or not isinstance(component.destination_prefix, PurePosixPath)
        or _COMMIT.fullmatch(component.commit) is None
        or _COMMIT.fullmatch(component.tree) is None
    ):
        raise AuthorityError("AUTHORITY_GIT_ID_INVALID")
    _valid_repository(os.fspath(component.repository))
    _valid_prefix(component.source_prefix.as_posix())
    _valid_prefix(component.destination_prefix.as_posix())
    if _resolve_component_tree(component) != component.tree:
        raise AuthorityError("AUTHORITY_TREE_MISMATCH")


def _component(
    name: str,
    document: object,
    *,
    resolve_git_objects: bool,
) -> ComponentAuthority:
    if not isinstance(document, dict) or set(document) != _COMPONENT_KEYS:
        raise AuthorityError("AUTHORITY_SCHEMA_INVALID")
    commit = document["commit"]
    tree = document["tree"]
    if (
        not isinstance(commit, str)
        or not isinstance(tree, str)
        or _COMMIT.fullmatch(commit) is None
        or _COMMIT.fullmatch(tree) is None
    ):
        raise AuthorityError("AUTHORITY_GIT_ID_INVALID")
    source = _valid_prefix(document["source_prefix"])
    destination = _valid_prefix(document["destination_prefix"])
    expected_source, expected_destination = _COMPONENT_PREFIXES[name]
    if source.as_posix() != expected_source or destination.as_posix() != expected_destination:
        raise AuthorityError("AUTHORITY_PREFIX_INVALID")
    component = ComponentAuthority(
        name=name,
        repository=_valid_repository(document["repository"]),
        commit=commit,
        tree=tree,
        source_prefix=source,
        destination_prefix=destination,
    )
    if resolve_git_objects:
        verify_component_authority(component)
    return component


def _load_source_authority(path: Path, *, resolve_git_objects: bool) -> SourceAuthority:
    try:
        document = json.loads(
            _read_document(Path(path)).decode("utf-8"),
            object_pairs_hook=_pairs,
        )
    except AuthorityError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
        raise AuthorityError("AUTHORITY_JSON_INVALID") from None
    if not isinstance(document, dict) or set(document) != _ROOT_KEYS:
        raise AuthorityError("AUTHORITY_SCHEMA_INVALID")
    if type(document["schema_version"]) is not int:
        raise AuthorityError("AUTHORITY_SCHEMA_INVALID")
    if document["schema_version"] != 1:
        raise AuthorityError("AUTHORITY_SCHEMA_UNSUPPORTED")
    digest = document["sealed_phase4b_metadata_sha256"]
    if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
        raise AuthorityError("AUTHORITY_DIGEST_INVALID")
    components_document = document["components"]
    if (
        not isinstance(components_document, dict)
        or set(components_document) != set(_COMPONENT_PREFIXES)
    ):
        raise AuthorityError("AUTHORITY_COMPONENTS_INVALID")
    components = {
        name: _component(
            name,
            components_document[name],
            resolve_git_objects=resolve_git_objects,
        )
        for name in _COMPONENT_PREFIXES
    }
    return SourceAuthority(
        schema_version=1,
        sealed_phase4b_metadata_sha256=digest,
        components=MappingProxyType(components),
    )


def parse_source_authority(path: Path) -> SourceAuthority:
    """Parse exact authority metadata without requiring external Git repositories."""

    return _load_source_authority(Path(path), resolve_git_objects=False)


def load_source_authority(path: Path) -> SourceAuthority:
    """Load and resolve the exact version-one consolidation authority."""

    return _load_source_authority(Path(path), resolve_git_objects=True)
