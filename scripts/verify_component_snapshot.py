#!/usr/bin/env python3
"""Independently verify a component snapshot in a worktree or Git revision."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.consolidation import (  # noqa: E402
    AuthorityError,
    ComponentManifest,
    ManifestError,
    canonical_manifest_bytes,
    load_source_authority,
    propose_manifest,
)
from import_component_snapshot import (  # noqa: E402
    CliError,
    CodeArgumentParser,
    _load_manifest,
    component_policy,
)


_GIT = "/usr/bin/git"
_GIT_TIMEOUT_SECONDS = 30
_GIT_EXECUTION_GUARDS = (
    "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
)
_GIT_ID = re.compile(r"[0-9a-f]{40}\Z")
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
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


def _git(repository: Path, arguments: list[str], code: str, path: str | None = None) -> bytes:
    try:
        return subprocess.run(
            [
                _GIT, *_GIT_EXECUTION_GUARDS, "-C", os.fspath(repository),
                *arguments,
            ],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=dict(_GIT_ENV),
            text=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        raise CliError(code, path) from None


def _validated_root(path: Path) -> Path:
    text = os.fspath(path)
    try:
        if not path.is_absolute() or os.path.normpath(text) != text:
            raise OSError
        if path.resolve(strict=True) != path:
            raise OSError
        metadata = path.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise OSError
        return path
    except OSError:
        raise CliError("E_DESTINATION") from None


def _validate_source(authority_path: Path, manifest: ComponentManifest, raw: bytes) -> None:
    try:
        authority = load_source_authority(authority_path)
        component = authority.components[manifest.component]
    except (AuthorityError, KeyError):
        raise CliError("E_AUTHORITY") from None
    try:
        policy = component_policy(manifest.component)
    except CliError:
        raise CliError("E_POLICY") from None
    if (
        manifest.source_repository != component.repository
        or manifest.source_commit != component.commit
        or manifest.source_tree != component.tree
        or manifest.source_prefix != component.source_prefix
        or manifest.destination_prefix != component.destination_prefix
        or manifest.policy != policy
    ):
        raise CliError("E_TAMPER")
    try:
        expected = canonical_manifest_bytes(propose_manifest(component, policy))
    except ManifestError as error:
        if "GIT_OBJECT" in error.reason_code:
            raise CliError("E_GIT_OBJECT", error.path) from None
        if "POLICY" in error.reason_code or "MODE_FORBIDDEN" in error.reason_code:
            raise CliError("E_POLICY", error.path) from None
        raise CliError("E_MANIFEST", error.path) from None
    if not hmac.compare_digest(raw, expected):
        raise CliError("E_TAMPER")


def _relative(manifest: ComponentManifest, destination: str) -> str:
    marker = f"{manifest.destination_prefix.as_posix()}/"
    if not destination.startswith(marker):
        raise CliError("E_MANIFEST")
    relative = destination[len(marker):]
    path = PurePosixPath(relative)
    if not relative or path.is_absolute() or ".." in path.parts or path.as_posix() != relative:
        raise CliError("E_MANIFEST")
    return relative


def _working_files(destination: Path, prefix: str) -> dict[str, Path]:
    try:
        metadata = destination.lstat()
    except OSError:
        raise CliError("E_TAMPER", prefix) from None
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise CliError("E_TAMPER", prefix)
    files: dict[str, Path] = {}
    try:
        for directory, names, filenames in os.walk(destination, followlinks=False):
            directory_path = Path(directory)
            relative_directory = directory_path.relative_to(destination)
            for name in list(names):
                path = directory_path / name
                relative = (relative_directory / name).as_posix()
                if path.is_symlink():
                    files[relative] = path
                    names.remove(name)
            for name in filenames:
                path = directory_path / name
                relative = (relative_directory / name).as_posix()
                files[relative] = path
    except OSError:
        raise CliError("E_TAMPER", prefix) from None
    return files


def _verify_working(root: Path, manifest: ComponentManifest) -> None:
    prefix = manifest.destination_prefix.as_posix()
    destination = root.joinpath(*manifest.destination_prefix.parts)
    actual = _working_files(destination, prefix)
    expected = {_relative(manifest, entry.destination_path): entry for entry in manifest.entries}
    for relative in sorted(set(actual) | set(expected), key=lambda value: value.encode("utf-8")):
        full_path = f"{prefix}/{relative}"
        if relative not in actual or relative not in expected:
            raise CliError("E_TAMPER", full_path)
        path = actual[relative]
        descriptor: int | None = None
        try:
            descriptor = os.open(path, os.O_RDONLY | _NOFOLLOW | _CLOEXEC)
            metadata = os.fstat(descriptor)
            entry = expected[relative]
            executable = bool(metadata.st_mode & 0o111)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_mode & 0o022
                or executable != (entry.mode == "100755")
                or metadata.st_size != entry.size
            ):
                raise CliError("E_TAMPER", full_path)
            digest = hashlib.sha256()
            while chunk := os.read(descriptor, 65536):
                digest.update(chunk)
            if digest.hexdigest() != entry.sha256:
                raise CliError("E_TAMPER", full_path)
        except CliError:
            raise
        except OSError:
            raise CliError("E_TAMPER", full_path) from None
        finally:
            if descriptor is not None:
                os.close(descriptor)


def _revision_entries(root: Path, revision: str, prefix: str) -> dict[str, tuple[str, str]]:
    if _GIT_ID.fullmatch(revision) is None:
        raise CliError("E_ARGUMENT")
    resolved = _git(
        root,
        ["rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}"],
        "E_GIT_OBJECT",
    ).strip()
    try:
        if resolved.decode("ascii") != revision:
            raise ValueError
    except (UnicodeDecodeError, ValueError):
        raise CliError("E_GIT_OBJECT") from None
    tree_spec = f"{revision}:{prefix}"
    tree = _git(
        root, ["rev-parse", "--verify", "--end-of-options", tree_spec], "E_TAMPER", prefix,
    ).strip()
    try:
        tree_id = tree.decode("ascii")
    except UnicodeDecodeError:
        raise CliError("E_GIT_OBJECT", prefix) from None
    if _GIT_ID.fullmatch(tree_id) is None:
        raise CliError("E_GIT_OBJECT", prefix)
    listing = _git(
        root,
        ["ls-tree", "-rz", "-r", "--full-tree", "--no-abbrev", "--end-of-options", tree_id],
        "E_GIT_OBJECT",
    )
    if listing and not listing.endswith(b"\0"):
        raise CliError("E_GIT_OBJECT")
    result: dict[str, tuple[str, str]] = {}
    for record in listing.split(b"\0"):
        if not record:
            continue
        try:
            metadata, encoded_path = record.split(b"\t", 1)
            mode, object_type, object_id = metadata.decode("ascii").split(" ", 2)
            path = encoded_path.decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            raise CliError("E_GIT_OBJECT") from None
        if object_type != "blob" or mode not in {"100644", "100755"}:
            raise CliError("E_TAMPER", f"{prefix}/{path}")
        if path in result:
            raise CliError("E_TAMPER", f"{prefix}/{path}")
        result[path] = (mode, object_id)
    return result


def _verify_revision(root: Path, revision: str, manifest: ComponentManifest) -> None:
    prefix = manifest.destination_prefix.as_posix()
    actual = _revision_entries(root, revision, prefix)
    expected = {_relative(manifest, entry.destination_path): entry for entry in manifest.entries}
    for relative in sorted(set(actual) | set(expected), key=lambda value: value.encode("utf-8")):
        full_path = f"{prefix}/{relative}"
        if relative not in actual or relative not in expected:
            raise CliError("E_TAMPER", full_path)
        mode, object_id = actual[relative]
        entry = expected[relative]
        if mode != entry.mode or object_id != entry.git_blob:
            raise CliError("E_TAMPER", full_path)
        content = _git(root, ["cat-file", "blob", object_id], "E_GIT_OBJECT", full_path)
        header = f"blob {len(content)}\0".encode("ascii")
        if (
            hashlib.sha1(header + content, usedforsecurity=False).hexdigest() != object_id
            or len(content) != entry.size
            or hashlib.sha256(content).hexdigest() != entry.sha256
        ):
            raise CliError("E_TAMPER", full_path)


def verify_snapshot(
    authority_path: Path,
    manifest_path: Path,
    root_path: Path,
    revision: str | None = None,
) -> ComponentManifest:
    loaded = _load_manifest(manifest_path)
    _validate_source(authority_path, loaded.manifest, loaded.raw)
    root = _validated_root(root_path)
    if revision is None:
        _verify_working(root, loaded.manifest)
    else:
        _verify_revision(root, revision, loaded.manifest)
    return loaded.manifest


def _parser() -> argparse.ArgumentParser:
    parser = CodeArgumentParser(description=__doc__)
    parser.add_argument("--authority", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--revision")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        manifest = verify_snapshot(
            arguments.authority, arguments.manifest, arguments.root, arguments.revision,
        )
        revision = "" if arguments.revision is None else f" revision={arguments.revision}"
        print(f"component={manifest.component}{revision} result=PASS")
        return 0
    except CliError as error:
        print(str(error), file=sys.stderr)
        return 1
    except SystemExit:
        raise
    except BaseException:
        print("E_ARGUMENT", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
