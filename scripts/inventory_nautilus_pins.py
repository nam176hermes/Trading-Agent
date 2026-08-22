#!/usr/bin/env python3
"""Generate or verify a Nautilus pin inventory bound to exact Git commits."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from pathlib import PurePosixPath
import stat
import subprocess
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.nautilus_pin_inventory.engine import INVENTORY_PATH, PinInventoryEngine, PinInventoryError
from scripts.nautilus_pin_inventory.git_source import GitAuthorityError, GitTreeSnapshot


class _UsageError(ValueError):
    pass


class _StaleError(ValueError):
    pass


def _git(root: Path, *arguments: str, text: bool = True) -> str | bytes:
    result = subprocess.run(
        ("git", "-C", str(root), "--no-replace-objects", *arguments),
        capture_output=True, text=text, check=False,
        env={"PATH": os.environ.get("PATH", ""), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_NO_REPLACE_OBJECTS": "1", "GIT_NO_LAZY_FETCH": "1", "GIT_OPTIONAL_LOCKS": "0", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
    )
    if result.returncode:
        stderr = result.stderr if text else result.stderr.decode("utf-8", errors="replace")
        raise _UsageError(stderr.strip() or "Git command failed")
    return result.stdout


def _root(value: str) -> Path:
    try:
        root = Path(value).resolve(strict=True)
    except OSError as exc:
        raise _UsageError("repository root is absent") from exc
    if not root.is_dir():
        raise _UsageError("repository root is not a directory")
    return root


def _object_format(root: Path) -> str:
    value = _git(root, "rev-parse", "--show-object-format")
    if value not in ("sha1\n", "sha256\n"):
        raise _UsageError("repository object format is invalid")
    return value.strip()


def _commit_oid(root: Path, value: str) -> str:
    object_format = _object_format(root)
    width = 40 if object_format == "sha1" else 64
    if len(value) != width or any(character not in "0123456789abcdef" for character in value):
        raise _UsageError("commit must be a full lowercase object-format OID")
    object_type = _git(root, "cat-file", "-t", value)
    if object_type != "commit\n":
        raise _UsageError("object is not a commit")
    return value


def _inventory_path(value: str) -> str:
    if not value or "\\" in value or "\x00" in value:
        raise _UsageError("inventory path is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part == ".." for part in path.parts):
        raise _UsageError("inventory path must be a relative POSIX Git path")
    parts = tuple(part for part in path.parts if part != ".")
    if not parts:
        raise _UsageError("inventory path is invalid")
    return "/".join(parts)


def _required_inventory_path(value: str) -> str:
    path = _inventory_path(value)
    if path != INVENTORY_PATH:
        raise _UsageError("inventory path must be the required inventory path")
    return path


def _output_parent(root: Path, path: str) -> Path:
    parent = root
    for part in path.split("/")[:-1]:
        parent = parent / part
        try:
            mode = os.lstat(parent).st_mode
        except OSError as exc:
            raise _UsageError("output parent is inaccessible") from exc
        if not stat.S_ISDIR(mode):
            raise _UsageError("output parent is not a regular directory")
    return parent


def _tree_entry(root: Path, commit: str, path: str) -> tuple[str, str, str] | None:
    output = _git(root, "ls-tree", "--full-tree", commit, "--", path)
    if not output:
        return None
    try:
        prefix, found = output.rstrip("\n").split("\t", 1)
        mode, kind, oid = prefix.split(" ")
    except ValueError as exc:
        raise _UsageError("Git tree entry is invalid") from exc
    if found != path:
        raise _UsageError("Git tree entry path is invalid")
    return mode, kind, oid


def _require_absent(root: Path, commit: str, path: str) -> None:
    if _tree_entry(root, commit, path) is not None:
        raise _StaleError("inventory is present in source commit")


def _generate(root: Path, source_commit: str, output: Path) -> int:
    path = _required_inventory_path(str(output))
    requested_output = _output_parent(root, path) / path.rsplit("/", 1)[-1]
    source = _commit_oid(root, source_commit)
    if os.path.lexists(requested_output):
        try:
            requested_output.resolve(strict=False)
        except RuntimeError as exc:
            raise _UsageError("output path is inaccessible") from exc
        raise _StaleError("output already exists")
    _require_absent(root, source, INVENTORY_PATH)
    snapshot = GitTreeSnapshot.from_commit(root, source)
    try:
        payload = PinInventoryEngine().serialize(PinInventoryEngine().generate(snapshot))
    except PinInventoryError as exc:
        if str(exc).startswith(("unregistered governed identity:", "required identity is missing:")):
            raise _StaleError(str(exc)) from exc
        raise
    try:
        with requested_output.open("xb") as stream:
            stream.write(payload)
    except OSError as exc:
        raise _UsageError("could not create requested output") from exc
    return 0


def _verify(root: Path, source_commit: str, inventory_commit: str, inventory_path: str) -> int:
    path = _required_inventory_path(inventory_path)
    source = _commit_oid(root, source_commit)
    inventory = _commit_oid(root, inventory_commit)
    parents = _git(root, "show", "-s", "--format=%P", inventory).strip().split()
    if parents != [source]:
        raise _StaleError("inventory commit must have exactly source as its sole parent")
    _require_absent(root, source, path)
    entry = _tree_entry(root, inventory, path)
    if entry is None:
        raise _StaleError("inventory is absent from inventory commit")
    if entry[:2] != ("100644", "blob"):
        raise _StaleError("inventory is not a regular mode-100644 blob")
    changes = _git(root, "diff-tree", "--no-commit-id", "--no-renames", "-r", "--name-status", source, inventory)
    if changes != f"A\t{path}\n":
        raise _StaleError("source to inventory range is not exactly one inventory addition")
    payload = _git(root, "show", f"{inventory}:{path}", text=False)
    snapshot = GitTreeSnapshot.from_commit(root, source)
    try:
        PinInventoryEngine().verify(snapshot, payload)
    except PinInventoryError as exc:
        if str(exc) == "inventory schema is invalid":
            raise _UsageError(str(exc)) from exc
        raise _StaleError(str(exc)) from exc
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="inventory_nautilus_pins.py")
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate")
    generate.add_argument("--root", required=True)
    generate.add_argument("--source-commit", required=True)
    generate.add_argument("--output", required=True, type=Path)
    verify = commands.add_parser("verify")
    verify.add_argument("--root", required=True)
    verify.add_argument("--source-commit", required=True)
    verify.add_argument("--inventory-commit", required=True)
    verify.add_argument("--inventory-path", required=True)
    return parser


def main(arguments: list[str] | None = None) -> int:
    try:
        namespace = _parser().parse_args(arguments)
        if namespace.command == "generate":
            _required_inventory_path(str(namespace.output))
        else:
            _required_inventory_path(namespace.inventory_path)
        root = _root(namespace.root)
        if namespace.command == "generate":
            return _generate(root, namespace.source_commit, namespace.output)
        return _verify(root, namespace.source_commit, namespace.inventory_commit, namespace.inventory_path)
    except _StaleError as exc:
        print(f"PIN_INVENTORY_STALE: {exc}", file=sys.stderr)
        return 1
    except (_UsageError, GitAuthorityError, PinInventoryError, OSError, ValueError) as exc:
        print(f"PIN_INVENTORY_USAGE: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
