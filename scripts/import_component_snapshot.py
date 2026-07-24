#!/usr/bin/env python3
"""Propose and atomically materialize fixed Git-object component snapshots."""

from __future__ import annotations

import argparse
import ctypes
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import stat
import subprocess
import sys
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.consolidation import (  # noqa: E402
    AuthorityError,
    ComponentManifest,
    ImportPolicy,
    ManifestEntry,
    ManifestError,
    canonical_manifest_bytes,
    load_source_authority,
    propose_manifest,
)


_GIT = "/usr/bin/git"
_GIT_TIMEOUT_SECONDS = 30
_MAX_MANIFEST_BYTES = 16 * 1024 * 1024
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_RENAME_NOREPLACE = 1
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
_MANIFEST_KEYS = frozenset({
    "schema_version", "component", "source_repository", "source_commit",
    "source_tree", "source_prefix", "destination_prefix", "policy", "entries",
    "aggregate_sha256",
})
_POLICY_KEYS = frozenset({
    "name", "include_patterns", "exclude_patterns", "forbidden_patterns",
})
_ENTRY_KEYS = frozenset({
    "source_path", "destination_path", "git_blob", "size", "mode", "sha256",
})


class CliError(RuntimeError):
    def __init__(self, code: str, path: str | None = None) -> None:
        self.code = code
        self.path = _safe_relative(path)
        super().__init__(code if self.path is None else f"{code}: {self.path}")


class CodeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise CliError("E_ARGUMENT")


@dataclass(frozen=True, slots=True)
class LoadedManifest:
    manifest: ComponentManifest
    raw: bytes


@dataclass(frozen=True, slots=True)
class CreatedDirectory:
    parts: tuple[str, ...]
    device: int
    inode: int


def _safe_relative(value: str | None) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        return None
    if path.as_posix() != value:
        return None
    return value


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _read_regular(path: Path) -> bytes:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | _NOFOLLOW | _CLOEXEC)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_MANIFEST_BYTES:
            raise OSError
        chunks: list[bytes] = []
        total = 0
        while chunk := os.read(descriptor, 65536):
            total += len(chunk)
            if total > _MAX_MANIFEST_BYTES:
                raise OSError
            chunks.append(chunk)
        return b"".join(chunks)
    except OSError:
        raise CliError("E_MANIFEST") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _strings(document: object) -> tuple[str, ...]:
    if not isinstance(document, list) or not all(isinstance(item, str) for item in document):
        raise ValueError
    return tuple(document)


def _load_manifest(path: Path) -> LoadedManifest:
    raw = _read_regular(path)
    try:
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
        if not isinstance(document, dict) or set(document) != _MANIFEST_KEYS:
            raise ValueError
        policy_document = document["policy"]
        entries_document = document["entries"]
        if not isinstance(policy_document, dict) or set(policy_document) != _POLICY_KEYS:
            raise ValueError
        if not isinstance(entries_document, list):
            raise ValueError
        entries: list[ManifestEntry] = []
        for entry in entries_document:
            if not isinstance(entry, dict) or set(entry) != _ENTRY_KEYS:
                raise ValueError
            entries.append(ManifestEntry(
                source_path=entry["source_path"],
                destination_path=entry["destination_path"],
                git_blob=entry["git_blob"],
                size=entry["size"],
                mode=entry["mode"],
                sha256=entry["sha256"],
            ))
        manifest = ComponentManifest(
            schema_version=document["schema_version"],
            component=document["component"],
            source_repository=Path(document["source_repository"]),
            source_commit=document["source_commit"],
            source_tree=document["source_tree"],
            source_prefix=PurePosixPath(document["source_prefix"]),
            destination_prefix=PurePosixPath(document["destination_prefix"]),
            policy=ImportPolicy(
                name=policy_document["name"],
                include_patterns=_strings(policy_document["include_patterns"]),
                exclude_patterns=_strings(policy_document["exclude_patterns"]),
                forbidden_patterns=_strings(policy_document["forbidden_patterns"]),
            ),
            entries=tuple(entries),
            aggregate_sha256=document["aggregate_sha256"],
        )
        canonical = canonical_manifest_bytes(manifest)
    except (KeyError, TypeError, ValueError, UnicodeError, ManifestError):
        raise CliError("E_MANIFEST") from None
    if not hmac.compare_digest(raw, canonical):
        raise CliError("E_MANIFEST")
    return LoadedManifest(manifest=manifest, raw=raw)


def component_policy(name: str) -> ImportPolicy:
    if name == "backend":
        return ImportPolicy(
            name="backend-source-v1",
            include_patterns=(
                "*.py", ".gitignore", "pyproject.toml", "uv.lock",
                "constraints-phase1.txt", "*.md", "exchange/*.py",
                "exchange/**/*.py", "db/*.py", "db/**/*.py", "tests/*", "tests/**",
            ),
            exclude_patterns=(
                ".keys.enc", ".env", ".env.*", ".mode", ".kill_switch",
                ".dexter/**", ".codegraph/**", ".venv/**", ".superpowers/**",
                "**/__pycache__/**", "**/*.pyc", "decisions/**", "memory/**",
                "models/**", "signals/**", "reports/**", "scratchpad/**",
                "**/scratchpad/**", "scratchpad*.json", "scratchpad*.jsonl",
                "**/scratchpad*.json", "**/scratchpad*.jsonl",
                "jobs/**", "job_artifacts/**", "run_status.json", "live_prices.json",
                "decisions_scored.jsonl", "strategy.json", "db/trading.db",
                "deploy/**", "scripts/**", "reference/ml4t", "reference/ml4t/**",
            ),
            forbidden_patterns=(),
        )
    if name == "dashboard":
        return ImportPolicy(
            name="dashboard-source-v1",
            include_patterns=("**",),
            exclude_patterns=(),
            forbidden_patterns=(
                ".env", ".env.*", "**/.env", "**/.env.*", ".next/**",
                "**/.next/**", "node_modules/**", "**/node_modules/**",
                "coverage/**", "**/coverage/**", "**/__pycache__/**", "**/*.pyc",
                "**/*.log", "credentials/**", "**/credentials/**",
                "**/*credential*", "**/*secret*", "runtime/**", "data/runtime/**",
            ),
        )
    raise CliError("E_POLICY")


def _authority_component(authority_path: Path, name: str):
    try:
        authority = load_source_authority(authority_path)
    except AuthorityError:
        raise CliError("E_AUTHORITY") from None
    try:
        return authority.components[name]
    except KeyError:
        raise CliError("E_POLICY") from None


def _map_manifest_error(error: ManifestError) -> CliError:
    if "GIT_OBJECT" in error.reason_code:
        return CliError("E_GIT_OBJECT", error.path)
    if "POLICY" in error.reason_code or "MODE_FORBIDDEN" in error.reason_code:
        return CliError("E_POLICY", error.path)
    if "SOURCE_MISMATCH" in error.reason_code:
        return CliError("E_TAMPER", error.path)
    return CliError("E_MANIFEST", error.path)


def _proposed(authority_path: Path, name: str) -> ComponentManifest:
    component = _authority_component(authority_path, name)
    try:
        return propose_manifest(component, component_policy(name))
    except ManifestError as error:
        raise _map_manifest_error(error) from None


def _write_proposal(path: Path, content: bytes) -> None:
    parent_descriptor: int | None = None
    file_descriptor: int | None = None
    created = False
    try:
        parent = path.parent if path.parent != Path("") else Path(".")
        parent_descriptor = os.open(parent, os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC)
        file_descriptor = os.open(
            path.name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC,
            0o600,
            dir_fd=parent_descriptor,
        )
        created = True
        os.fchmod(file_descriptor, 0o600)
        view = memoryview(content)
        while view:
            written = os.write(file_descriptor, view)
            if written <= 0:
                raise OSError
            view = view[written:]
        os.fsync(file_descriptor)
        os.close(file_descriptor)
        file_descriptor = None
        os.fsync(parent_descriptor)
    except FileExistsError:
        raise CliError("E_DESTINATION") from None
    except OSError:
        if created and parent_descriptor is not None:
            try:
                os.unlink(path.name, dir_fd=parent_descriptor)
            except OSError:
                pass
        raise CliError("E_DESTINATION") from None
    except BaseException:
        if created and parent_descriptor is not None:
            try:
                os.unlink(path.name, dir_fd=parent_descriptor)
            except OSError:
                pass
        raise CliError("E_DESTINATION") from None
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)
        if parent_descriptor is not None:
            os.close(parent_descriptor)


def _validated_root(path: Path) -> Path:
    text = os.fspath(path)
    try:
        if not path.is_absolute() or os.path.normpath(text) != text:
            raise OSError
        resolved = path.resolve(strict=True)
        metadata = path.lstat()
        if resolved != path or not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise OSError
        return path
    except OSError:
        raise CliError("E_DESTINATION") from None


def _open_directory(name: str, parent: int) -> int:
    try:
        descriptor = os.open(
            name, os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC, dir_fd=parent,
        )
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise OSError
        return descriptor
    except OSError:
        raise CliError("E_DESTINATION") from None


def _remove_created_parents(root_descriptor: int, created: tuple[CreatedDirectory, ...]) -> None:
    for directory in reversed(created):
        parent = os.dup(root_descriptor)
        try:
            for part in directory.parts[:-1]:
                child = os.open(
                    part,
                    os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC,
                    dir_fd=parent,
                )
                os.close(parent)
                parent = child
            try:
                metadata = os.stat(
                    directory.parts[-1], dir_fd=parent, follow_symlinks=False,
                )
                if (
                    not stat.S_ISDIR(metadata.st_mode)
                    or (metadata.st_dev, metadata.st_ino) != (directory.device, directory.inode)
                ):
                    continue
                os.rmdir(directory.parts[-1], dir_fd=parent)
                try:
                    os.fsync(parent)
                except OSError:
                    pass
            except OSError:
                pass
        except OSError:
            pass
        finally:
            os.close(parent)


def _destination_parent(
    root: Path, prefix: PurePosixPath,
) -> tuple[int, int, str, tuple[CreatedDirectory, ...]]:
    parts = prefix.parts
    if not parts or prefix.as_posix() == ".":
        raise CliError("E_DESTINATION")
    root_descriptor = os.open(root, os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC)
    current = os.dup(root_descriptor)
    created: list[CreatedDirectory] = []
    try:
        for index, part in enumerate(parts[:-1]):
            made = False
            try:
                os.mkdir(part, 0o755, dir_fd=current)
                made = True
            except FileExistsError:
                pass
            child = _open_directory(part, current)
            if made:
                metadata = os.fstat(child)
                created.append(CreatedDirectory(
                    parts=tuple(parts[:index + 1]),
                    device=metadata.st_dev,
                    inode=metadata.st_ino,
                ))
                try:
                    os.fsync(current)
                except BaseException:
                    os.close(child)
                    raise
            os.close(current)
            current = child
        try:
            os.stat(parts[-1], dir_fd=current, follow_symlinks=False)
        except FileNotFoundError:
            return root_descriptor, current, parts[-1], tuple(created)
        raise CliError("E_DESTINATION", prefix.as_posix())
    except BaseException:
        os.close(current)
        _remove_created_parents(root_descriptor, tuple(created))
        os.close(root_descriptor)
        raise


def _git_blob(repository: Path, object_id: str, safe_path: str) -> bytes:
    try:
        content = subprocess.run(
            [_GIT, "-C", os.fspath(repository), "cat-file", "blob", object_id],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=dict(_GIT_ENV),
            text=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        raise CliError("E_GIT_OBJECT", safe_path) from None
    header = f"blob {len(content)}\0".encode("ascii")
    if hashlib.sha1(header + content, usedforsecurity=False).hexdigest() != object_id:
        raise CliError("E_GIT_OBJECT", safe_path)
    return content


def _relative_entry(manifest: ComponentManifest, entry: ManifestEntry) -> PurePosixPath:
    prefix = manifest.destination_prefix.as_posix()
    marker = f"{prefix}/"
    if not entry.destination_path.startswith(marker):
        raise CliError("E_MANIFEST")
    return PurePosixPath(entry.destination_path[len(marker):])


def _child_directory(parent: int, name: str) -> int:
    try:
        os.mkdir(name, 0o700, dir_fd=parent)
    except FileExistsError:
        pass
    return _open_directory(name, parent)


def _write_entry(temp: int, manifest: ComponentManifest, entry: ManifestEntry) -> None:
    relative = _relative_entry(manifest, entry)
    current = os.dup(temp)
    try:
        for part in relative.parts[:-1]:
            child = _child_directory(current, part)
            os.close(current)
            current = child
        content = _git_blob(manifest.source_repository, entry.git_blob, relative.as_posix())
        if len(content) != entry.size or hashlib.sha256(content).hexdigest() != entry.sha256:
            raise CliError("E_TAMPER", relative.as_posix())
        descriptor = os.open(
            relative.parts[-1],
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC,
            0o600,
            dir_fd=current,
        )
        try:
            view = memoryview(content)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError
                view = view[written:]
            mode = 0o755 if entry.mode == "100755" else 0o644
            os.fchmod(descriptor, mode)
            os.fsync(descriptor)
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_size != entry.size
                or stat.S_IMODE(metadata.st_mode) != mode
            ):
                raise CliError("E_DESTINATION", entry.destination_path)
        finally:
            os.close(descriptor)
        os.fsync(current)
    except OSError:
        raise CliError("E_DESTINATION", entry.destination_path) from None
    finally:
        os.close(current)


def _identity_at(parent: int, name: str) -> tuple[int, int] | None:
    try:
        metadata = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except OSError:
        return None
    if not stat.S_ISDIR(metadata.st_mode):
        return None
    return metadata.st_dev, metadata.st_ino


def _remove_tree(
    parent: int, name: str, expected_identity: tuple[int, int] | None = None,
) -> None:
    try:
        descriptor = os.open(
            name, os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC, dir_fd=parent,
        )
    except OSError:
        return
    try:
        metadata = os.fstat(descriptor)
        identity = (metadata.st_dev, metadata.st_ino)
        if expected_identity is not None and identity != expected_identity:
            return
        with os.scandir(descriptor) as entries:
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        _remove_tree(descriptor, entry.name)
                    else:
                        os.unlink(entry.name, dir_fd=descriptor)
                except OSError:
                    pass
    finally:
        os.close(descriptor)
    try:
        if expected_identity is not None and _identity_at(parent, name) != expected_identity:
            return
        os.rmdir(name, dir_fd=parent)
        try:
            os.fsync(parent)
        except OSError:
            pass
    except OSError:
        pass


def _rename_noreplace(parent: int, source: str, destination: str) -> None:
    try:
        renameat2 = ctypes.CDLL(None, use_errno=True).renameat2
    except (AttributeError, OSError):
        raise CliError("E_DESTINATION") from None
    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    result = renameat2(
        parent,
        os.fsencode(source),
        parent,
        os.fsencode(destination),
        _RENAME_NOREPLACE,
    )
    if result != 0:
        ctypes.get_errno()
        raise CliError("E_DESTINATION")


def _verify_private_tree(descriptor: int, manifest: ComponentManifest) -> None:
    expected = {
        _relative_entry(manifest, entry).as_posix(): entry for entry in manifest.entries
    }
    expected_directories: set[str] = set()
    for relative in expected:
        parts = PurePosixPath(relative).parts
        expected_directories.update(
            PurePosixPath(*parts[:index]).as_posix() for index in range(1, len(parts))
        )
    observed: set[str] = set()

    def walk(directory_descriptor: int, prefix: PurePosixPath) -> None:
        try:
            entries = sorted(os.scandir(directory_descriptor), key=lambda item: item.name.encode("utf-8"))
        except (OSError, UnicodeError):
            raise CliError("E_DESTINATION", manifest.destination_prefix.as_posix()) from None
        for directory_entry in entries:
            relative_path = prefix / directory_entry.name
            relative = relative_path.as_posix()
            full_path = f"{manifest.destination_prefix.as_posix()}/{relative}"
            try:
                if directory_entry.is_dir(follow_symlinks=False):
                    if relative not in expected_directories:
                        raise CliError("E_DESTINATION", full_path)
                    child = os.open(
                        directory_entry.name,
                        os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC,
                        dir_fd=directory_descriptor,
                    )
                    try:
                        walk(child, relative_path)
                        os.fsync(child)
                    finally:
                        os.close(child)
                    continue
                if relative not in expected or not directory_entry.is_file(follow_symlinks=False):
                    raise CliError("E_DESTINATION", full_path)
                entry = expected[relative]
                file_descriptor = os.open(
                    directory_entry.name,
                    os.O_RDONLY | _NOFOLLOW | _CLOEXEC,
                    dir_fd=directory_descriptor,
                )
                try:
                    metadata = os.fstat(file_descriptor)
                    mode = 0o755 if entry.mode == "100755" else 0o644
                    digest = hashlib.sha256()
                    while chunk := os.read(file_descriptor, 65536):
                        digest.update(chunk)
                    if (
                        not stat.S_ISREG(metadata.st_mode)
                        or metadata.st_nlink != 1
                        or stat.S_IMODE(metadata.st_mode) != mode
                        or metadata.st_size != entry.size
                        or digest.hexdigest() != entry.sha256
                    ):
                        raise CliError("E_DESTINATION", full_path)
                finally:
                    os.close(file_descriptor)
                observed.add(relative)
            except CliError:
                raise
            except (OSError, UnicodeError):
                raise CliError("E_DESTINATION", full_path) from None

    walk(descriptor, PurePosixPath())
    missing = sorted(set(expected) - observed, key=lambda value: value.encode("utf-8"))
    if missing:
        raise CliError(
            "E_DESTINATION",
            f"{manifest.destination_prefix.as_posix()}/{missing[0]}",
        )


def _manifest_for_apply(authority_path: Path, loaded: LoadedManifest) -> ComponentManifest:
    manifest = loaded.manifest
    component = _authority_component(authority_path, manifest.component)
    policy = component_policy(manifest.component)
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
        raise _map_manifest_error(error) from None
    if not hmac.compare_digest(loaded.raw, expected):
        raise CliError("E_TAMPER")
    return manifest


def apply_snapshot(authority_path: Path, manifest_path: Path, root_path: Path) -> ComponentManifest:
    loaded = _load_manifest(manifest_path)
    manifest = _manifest_for_apply(authority_path, loaded)
    root = _validated_root(root_path)
    root_descriptor, parent_descriptor, destination_name, created_parents = _destination_parent(
        root, manifest.destination_prefix,
    )
    temporary_name = f".{manifest.component}-import-{secrets.token_hex(12)}"
    temporary_descriptor: int | None = None
    temporary_identity: tuple[int, int] | None = None
    held_identity: tuple[int, int] | None = None
    destination_identity: tuple[int, int] | None = None
    succeeded = False
    try:
        os.mkdir(temporary_name, 0o700, dir_fd=parent_descriptor)
        temporary_identity = _identity_at(parent_descriptor, temporary_name)
        if temporary_identity is None:
            raise CliError("E_DESTINATION", manifest.destination_prefix.as_posix())
        temporary_descriptor = _open_directory(temporary_name, parent_descriptor)
        temporary_metadata = os.fstat(temporary_descriptor)
        if temporary_identity != (temporary_metadata.st_dev, temporary_metadata.st_ino):
            raise CliError("E_DESTINATION", manifest.destination_prefix.as_posix())
        for entry in manifest.entries:
            _write_entry(temporary_descriptor, manifest, entry)
        _verify_private_tree(temporary_descriptor, manifest)
        os.fsync(temporary_descriptor)
        held_metadata = os.fstat(temporary_descriptor)
        held_identity = (held_metadata.st_dev, held_metadata.st_ino)
        if held_identity != temporary_identity:
            raise CliError("E_DESTINATION", manifest.destination_prefix.as_posix())
        _rename_noreplace(parent_descriptor, temporary_name, destination_name)
        destination_identity = held_identity
        observed_identity = _identity_at(parent_descriptor, destination_name)
        if observed_identity != destination_identity:
            raise CliError("E_DESTINATION", manifest.destination_prefix.as_posix())
        _verify_private_tree(temporary_descriptor, manifest)
        os.fsync(temporary_descriptor)
        os.fsync(parent_descriptor)
        succeeded = True
        return manifest
    except CliError:
        raise
    except (OSError, ValueError):
        raise CliError("E_DESTINATION", manifest.destination_prefix.as_posix()) from None
    except BaseException:
        raise CliError("E_DESTINATION", manifest.destination_prefix.as_posix()) from None
    finally:
        if not succeeded:
            if destination_identity is None and held_identity is not None:
                candidate = _identity_at(parent_descriptor, destination_name)
                if candidate == held_identity:
                    destination_identity = held_identity
            if destination_identity is not None:
                _remove_tree(parent_descriptor, destination_name, destination_identity)
            if temporary_identity is not None:
                _remove_tree(parent_descriptor, temporary_name, temporary_identity)
            _remove_created_parents(root_descriptor, created_parents)
        if temporary_descriptor is not None:
            os.close(temporary_descriptor)
        os.close(parent_descriptor)
        os.close(root_descriptor)


def _parser() -> argparse.ArgumentParser:
    parser = CodeArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    propose = commands.add_parser("propose", help="write a new canonical proposal")
    propose.add_argument("--authority", required=True, type=Path)
    propose.add_argument("--component", required=True)
    propose.add_argument("--output", required=True, type=Path)
    apply = commands.add_parser("apply", help="atomically apply an approved manifest")
    apply.add_argument("--authority", required=True, type=Path)
    apply.add_argument("--manifest", required=True, type=Path)
    apply.add_argument("--root", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        if arguments.command == "propose":
            manifest = _proposed(arguments.authority, arguments.component)
            _write_proposal(arguments.output, canonical_manifest_bytes(manifest))
            print(
                f"component={manifest.component} files={len(manifest.entries)} "
                f"tree={manifest.source_tree} aggregate={manifest.aggregate_sha256}"
            )
        else:
            manifest = apply_snapshot(arguments.authority, arguments.manifest, arguments.root)
            print(f"component={manifest.component} result=PASS")
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
