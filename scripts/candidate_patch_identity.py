#!/usr/bin/env python3
"""Produce a deterministic identity for a candidate patch.

The identity is derived from a base tree and the bytes of tracked changes plus
an explicit allow-list of untracked regular files.  Git-ignored files are not
inventoried, so local build outputs cannot accidentally enter a candidate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
from typing import Any


class CandidateError(RuntimeError):
    """A candidate cannot be safely identified."""


def _git(root: Path, *args: str) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode:
        message = completed.stderr.decode("utf-8", errors="replace").strip()
        raise CandidateError(message or f"git {' '.join(args)} failed")
    return completed.stdout


def _decode_path(value: bytes) -> str:
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError as error:
        raise CandidateError("Git reported a path that is not valid UTF-8") from error


def _validate_relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or value == ".":
        raise CandidateError(f"path must be a non-empty relative path: {value!r}")
    return path.as_posix()


def _changed_tracked_paths(root: Path, base: str) -> dict[str, str]:
    fields = _git(root, "diff", "--name-status", "-z", "--no-renames", base, "--").split(b"\0")
    changed: dict[str, str] = {}
    if fields and fields[-1] == b"":
        fields.pop()
    if len(fields) % 2:
        raise CandidateError("unexpected Git name-status output")
    for index in range(0, len(fields), 2):
        status, raw_path = fields[index : index + 2]
        path = _validate_relative_path(_decode_path(raw_path))
        status_text = status.decode("ascii", errors="strict")
        if status_text == "D":
            changed[path] = "deleted"
        elif status_text and status_text[0] in {"A", "M", "T"}:
            changed[path] = "present"
        else:
            raise CandidateError(f"unsupported Git change status {status_text!r} for {path}")
    return changed


def _untracked_paths(root: Path) -> list[str]:
    output = _git(root, "ls-files", "--others", "--exclude-standard", "-z")
    return sorted(_validate_relative_path(_decode_path(item)) for item in output.split(b"\0") if item)


def _is_allowed(path: str, allowed: set[str]) -> bool:
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in allowed)


def _present_entry(root: Path, relative_path: str) -> dict[str, str]:
    location = root / relative_path
    try:
        metadata = location.lstat()
    except FileNotFoundError as error:
        raise CandidateError(f"changed path is missing: {relative_path}") from error
    if not stat.S_ISREG(metadata.st_mode):
        raise CandidateError(f"candidate path must be a regular file: {relative_path}")
    contents = location.read_bytes()
    return {
        "path": relative_path,
        "mode": "100755" if metadata.st_mode & stat.S_IXUSR else "100644",
        "sha256": hashlib.sha256(contents).hexdigest(),
    }


def _canonical_json(document: dict[str, Any]) -> bytes:
    return json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def candidate_report(root: Path, base: str, allowed_untracked: set[str]) -> dict[str, Any]:
    top_level = Path(_git(root, "rev-parse", "--show-toplevel").decode("utf-8").strip()).resolve()
    if root.resolve() != top_level:
        raise CandidateError("--root must name the top level of a Git worktree")

    base_commit = _git(root, "rev-parse", "--verify", "--end-of-options", f"{base}^{{commit}}").decode("ascii").strip()
    base_tree = _git(root, "rev-parse", "--verify", "--end-of-options", f"{base}^{{tree}}").decode("ascii").strip()
    changed = _changed_tracked_paths(root, base)
    untracked = _untracked_paths(root)
    unapproved = [path for path in untracked if not _is_allowed(path, allowed_untracked)]

    for path in untracked:
        if _is_allowed(path, allowed_untracked):
            changed.setdefault(path, "present")

    entries: list[dict[str, str]] = []
    for path, state in sorted(changed.items()):
        if state == "deleted":
            entries.append({"path": path, "state": "deleted"})
        else:
            entries.append(_present_entry(root, path))

    identity_document = {"base_tree": base_tree, "entries": entries, "schema_version": 1}
    return {
        "base_commit": base_commit,
        "base_tree": base_tree,
        "candidate_sha256": hashlib.sha256(_canonical_json(identity_document)).hexdigest(),
        "complete": not unapproved,
        "entries": entries,
        "schema_version": 1,
        "unapproved_untracked": unapproved,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Git worktree root (default: current directory)")
    parser.add_argument("--base", default="HEAD", help="base revision (default: HEAD)")
    parser.add_argument(
        "--allow-untracked",
        action="append",
        default=[],
        metavar="PATH",
        help="untracked relative file or directory allowed in the candidate; repeatable",
    )
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="exit non-zero if a non-ignored untracked path has not been allowed",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        allowed = {_validate_relative_path(item) for item in args.allow_untracked}
        report = candidate_report(args.root.resolve(), args.base, allowed)
    except CandidateError as error:
        print(f"candidate_patch_identity: {error}", file=sys.stderr)
        return 2

    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 2 if args.require_complete and not report["complete"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
