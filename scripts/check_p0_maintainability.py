#!/usr/bin/env python3
"""Validate the narrowly scoped P0 maintainability hotspot policy."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
from typing import Any


SCHEMA_VERSION = "p0-maintainability-hotspots/v1"
MANIFEST_KEYS = {"schema_version", "baseline_sha", "hotspots"}
HOTSPOT_KEYS = {
    "path",
    "status",
    "baseline_bytes",
    "responsibility_id",
    "baseline_first_party_imports",
}
FROZEN_KEYS = HOTSPOT_KEYS | {"max_net_growth_bytes"}
STATUSES = {"FROZEN_FOR_GROWTH", "MONITOR"}
MINIMUM_FIRST_PARTY_ROOTS = {
    "apps",
    "services",
    "packages",
    "engines",
    "legacy",
    "native",
    "ops",
}


class PolicyError(Exception):
    """A reviewed maintainability policy or its checked source is invalid."""


def fail(message: str) -> None:
    raise PolicyError(message)


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, text=True, capture_output=True, check=False
    )
    if result.returncode:
        fail(f"baseline git check failed: {' '.join(args)}")
    return result.stdout.strip()


def checked_path(root: Path, value: Any) -> tuple[str, Path]:
    if not isinstance(value, str) or not value:
        fail("hotspot path must be a non-empty string")
    path = PurePosixPath(value)
    if path.is_absolute():
        fail(f"absolute hotspot path is forbidden: {value}")
    if any(part in {"", ".", ".."} for part in path.parts):
        fail(f"path traversal is forbidden: {value}")
    candidate = root.joinpath(*path.parts)
    component = root
    for part in path.parts:
        component /= part
        if component.is_symlink():
            fail(f"symlink hotspot path component is forbidden: {value}")
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        fail(f"path traversal is forbidden: {value}")
    return path.as_posix(), candidate


def baseline_bytes(root: Path, baseline_sha: str, path: str) -> tuple[int, bytes]:
    object_name = f"{baseline_sha}:{path}"
    try:
        size = int(git(root, "cat-file", "-s", object_name))
        contents = subprocess.run(
            ["git", "show", object_name],
            cwd=root,
            capture_output=True,
            check=False,
        )
    except ValueError:
        fail(f"baseline object is not a regular source blob: {object_name}")
    if contents.returncode:
        fail(f"baseline object/path absent: {object_name}")
    return size, contents.stdout


def repository_import_roots(root: Path) -> set[str]:
    """Return import roots exposed by this checkout's directories and packages."""
    roots = set(MINIMUM_FIRST_PARTY_ROOTS)
    roots.update(
        child.name
        for child in root.iterdir()
        if child.is_dir() and not child.name.startswith(".")
    )
    for package_init in root.rglob("__init__.py"):
        relative = package_init.relative_to(root)
        if any(part.startswith(".") for part in relative.parts):
            continue
        roots.add(package_init.parent.name)
    return roots


def is_repository_module(root: Path, name: str) -> bool:
    """Whether a dotted import name resolves to a source module in this checkout."""
    candidate = root.joinpath(*name.split("."))
    return candidate.with_suffix(".py").is_file() or (candidate / "__init__.py").is_file()


def first_party_imports(root: Path, source: bytes) -> set[str]:
    try:
        tree = ast.parse(source.decode("utf-8"))
    except (SyntaxError, UnicodeDecodeError) as error:
        fail(f"cannot parse hotspot imports: {error}")
    imports: set[str] = set()
    import_roots = repository_import_roots(root)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module.split(".", 1)[0] not in import_roots:
                continue
            names = (
                f"{node.module}.{alias.name}"
                if alias.name != "*"
                and is_repository_module(root, f"{node.module}.{alias.name}")
                else node.module
                for alias in node.names
            )
        else:
            continue
        for name in names:
            import_root = name.split(".", 1)[0]
            if import_root in import_roots:
                imports.add(name)
    return imports


def validate_manifest(root: Path, manifest: Path) -> list[dict[str, Any]]:
    if not manifest.is_file():
        fail(f"manifest is missing or not a regular file: {manifest}")
    try:
        document = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"manifest cannot be parsed: {error}")
    if not isinstance(document, dict) or set(document) != MANIFEST_KEYS:
        fail("manifest has unknown or missing keys")
    if document["schema_version"] != SCHEMA_VERSION:
        fail("manifest schema_version is unsupported")
    baseline_sha = document["baseline_sha"]
    if not isinstance(baseline_sha, str) or not baseline_sha:
        fail("baseline SHA must be a non-empty string")
    git(root, "cat-file", "-e", f"{baseline_sha}^{{commit}}")
    hotspots = document["hotspots"]
    if not isinstance(hotspots, list) or not hotspots:
        fail("manifest hotspots must be a non-empty list")

    checked: list[dict[str, Any]] = []
    paths: set[str] = set()
    for hotspot in hotspots:
        if not isinstance(hotspot, dict):
            fail("hotspot must be an object")
        status = hotspot.get("status")
        keys = FROZEN_KEYS if status == "FROZEN_FOR_GROWTH" else HOTSPOT_KEYS
        if set(hotspot) != keys:
            fail("hotspot has unknown or missing keys")
        if status not in STATUSES:
            fail("hotspot status is unsupported")
        path, candidate = checked_path(root, hotspot["path"])
        if path in paths:
            fail(f"duplicate hotspot path: {path}")
        paths.add(path)
        if candidate.is_symlink():
            fail(f"symlink hotspot is forbidden: {path}")
        if not candidate.exists() or not stat.S_ISREG(candidate.stat().st_mode):
            fail(f"hotspot must be a regular file: {path}")
        if type(hotspot["baseline_bytes"]) is not int or hotspot["baseline_bytes"] <= 0:
            fail(f"baseline bytes must be a positive integer: {path}")
        if not isinstance(hotspot["responsibility_id"], str) or not hotspot["responsibility_id"]:
            fail(f"responsibility ID must be a non-empty string: {path}")
        declared_imports = hotspot["baseline_first_party_imports"]
        if (
            not isinstance(declared_imports, list)
            or any(not isinstance(name, str) or not name for name in declared_imports)
            or declared_imports != sorted(set(declared_imports))
        ):
            fail(f"baseline first-party imports must be a sorted unique string list: {path}")
        if status == "FROZEN_FOR_GROWTH" and (
            type(hotspot["max_net_growth_bytes"]) is not int
            or hotspot["max_net_growth_bytes"] < 0
        ):
            fail(f"frozen growth ceiling must be a non-negative integer: {path}")
        actual_baseline, source = baseline_bytes(root, baseline_sha, path)
        if actual_baseline != hotspot["baseline_bytes"]:
            fail(f"baseline bytes do not match baseline object: {path}")
        parsed_baseline_imports = sorted(first_party_imports(root, source))
        if declared_imports != parsed_baseline_imports:
            fail(f"baseline first-party imports do not match baseline object: {path}")
        checked.append({**hotspot, "_path": candidate, "_root": root})
    return checked


def check_hotspots(hotspots: list[dict[str, Any]]) -> None:
    for hotspot in hotspots:
        path = hotspot["_path"]
        current_bytes = path.stat().st_size
        baseline = hotspot["baseline_bytes"]
        delta = current_bytes - baseline
        status = hotspot["status"]
        print(
            f"path={hotspot['path']} current_bytes={current_bytes} "
            f"baseline_bytes={baseline} delta_bytes={delta} status={status}",
            file=sys.stderr,
        )
        if status == "FROZEN_FOR_GROWTH":
            if delta > hotspot["max_net_growth_bytes"]:
                fail(f"frozen hotspot growth exceeds approved ceiling: {hotspot['path']}")
        current_imports = first_party_imports(hotspot["_root"], path.read_bytes())
        drift = sorted(current_imports - set(hotspot["baseline_first_party_imports"]))
        if drift:
            fail(f"first-party import drift: {hotspot['path']}: {', '.join(drift)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.root.is_symlink():
            fail(f"root must be a non-symlink directory: {args.root}")
        root = args.root.resolve(strict=True)
        if not root.is_dir():
            fail(f"root must be a non-symlink directory: {args.root}")
        hotspots = validate_manifest(root, args.manifest)
        check_hotspots(hotspots)
    except PolicyError as error:
        print(f"P0_MAINTAINABILITY_GUARD_FAIL: {error}", file=sys.stderr)
        return 1
    print("P0_MAINTAINABILITY_GUARD_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
