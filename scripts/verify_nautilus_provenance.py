#!/usr/bin/env python3
"""Fail closed on NautilusTrader provenance, license and vendored-source drift."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_ALLOWED_MODES = {"external_pinned_upstream", "vendored_source"}
_UPSTREAM_FIELDS = {
    "schema_version", "distribution_mode", "upstream_repository", "upstream_tag",
    "upstream_tag_object", "upstream_commit", "tag_commit", "license_spdx",
    "license_sha256", "source_acquisition",
}
_MANIFEST_FIELDS = {"schema_version", "upstream_commit", "distribution_mode", "files"}
_APPROVED_DERIVED_PATHS = {
    "scripts/verify_nautilus_provenance.py",
}


class VerificationError(ValueError):
    pass


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError(f"invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise VerificationError(f"JSON object required: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regular_file(path: Path) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        raise VerificationError(f"expected one regular file: {path}") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise VerificationError(f"expected one regular file: {path}")
    return info


def _relative_file_set(root: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for directory, _, names in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        for name in names:
            path = directory_path / name
            if path.is_symlink():
                raise VerificationError(f"symbolic link is forbidden: {path}")
            _regular_file(path)
            relative = path.relative_to(root).as_posix()
            result[relative] = path
    return result


def _verify_upstream(document: dict[str, Any], license_path: Path) -> None:
    if set(document) != _UPSTREAM_FIELDS or document.get("schema_version") != 1:
        raise VerificationError("UPSTREAM.json fields are missing or unknown")
    if document["distribution_mode"] not in _ALLOWED_MODES:
        raise VerificationError("unsupported Nautilus distribution mode")
    if document["upstream_repository"] != "https://github.com/nautechsystems/nautilus_trader.git":
        raise VerificationError("unexpected Nautilus upstream repository")
    if not isinstance(document["upstream_tag"], str) or not document["upstream_tag"].startswith("v"):
        raise VerificationError("Nautilus upstream tag is not pinned")
    for key in ("upstream_tag_object", "upstream_commit", "tag_commit"):
        if not isinstance(document[key], str) or _COMMIT.fullmatch(document[key]) is None:
            raise VerificationError(f"invalid Nautilus {key}")
    if document["upstream_commit"] != document["tag_commit"]:
        raise VerificationError("Nautilus tag does not resolve to the pinned commit")
    if document["license_spdx"] != "LGPL-3.0-or-later":
        raise VerificationError("Nautilus license is not LGPL-3.0-or-later")
    if not isinstance(document["license_sha256"], str) or _SHA256.fullmatch(document["license_sha256"]) is None:
        raise VerificationError("invalid Nautilus license digest")
    _regular_file(license_path)
    if _sha256(license_path) != document["license_sha256"]:
        raise VerificationError("Nautilus license digest mismatch")


def _verify_manifest(document: dict[str, Any], upstream: dict[str, Any], source: Path) -> None:
    if set(document) != _MANIFEST_FIELDS or document.get("schema_version") != 1:
        raise VerificationError("FILE_MANIFEST.json fields are missing or unknown")
    if document.get("upstream_commit") != upstream["upstream_commit"]:
        raise VerificationError("file manifest upstream commit mismatch")
    if document.get("distribution_mode") != upstream["distribution_mode"]:
        raise VerificationError("file manifest distribution mode mismatch")
    records = document.get("files")
    if not isinstance(records, list):
        raise VerificationError("file manifest records must be a list")
    if upstream["distribution_mode"] == "external_pinned_upstream":
        if source.exists() or records:
            raise VerificationError("external upstream mode cannot contain vendored source")
        return
    if not source.is_dir() or source.is_symlink():
        raise VerificationError("vendored source directory is missing or unsafe")
    actual = _relative_file_set(source)
    expected: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict) or set(record) != {"path", "size", "sha256"}:
            raise VerificationError("invalid vendored source manifest record")
        relative = record["path"]
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise VerificationError("unsafe vendored source manifest path")
        if relative in expected or not isinstance(record["size"], int) or record["size"] < 0:
            raise VerificationError("duplicate or invalid vendored source manifest record")
        if not isinstance(record["sha256"], str) or _SHA256.fullmatch(record["sha256"]) is None:
            raise VerificationError("invalid vendored source digest")
        expected[relative] = record
    if set(actual) != set(expected):
        raise VerificationError("vendored source manifest has missing or unexpected file")
    for relative, path in actual.items():
        record = expected[relative]
        if path.stat().st_size != record["size"] or _sha256(path) != record["sha256"]:
            raise VerificationError(f"vendored source drift: {relative}")


def _verify_no_unapproved_derived_source(root: Path) -> None:
    ignored = {".git", ".venv", "node_modules", ".next", "__pycache__"}
    for path in root.rglob("*"):
        if ignored.intersection(path.relative_to(root).parts) or not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root).as_posix()
        if relative.startswith("third_party/nautilus_trader/source/") or relative in _APPROVED_DERIVED_PATHS:
            continue
        if path.suffix not in {".py", ".pyx", ".pxd", ".pxi", ".rs"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if "Derived from NautilusTrader" in text:
            raise VerificationError(f"Nautilus-derived source outside approved path: {relative}")


def verify(root: Path) -> None:
    root = root.resolve(strict=True)
    vendor = root / "third_party/nautilus_trader"
    upstream_path = vendor / "UPSTREAM.json"
    manifest_path = vendor / "FILE_MANIFEST.json"
    modifications = vendor / "MODIFICATIONS.md"
    vendor_license = vendor / "LICENSE"
    legal_license = root / "legal/LGPL-3.0-or-later.txt"
    notices = root / "legal/THIRD_PARTY_NOTICES.md"
    for path in (upstream_path, manifest_path, modifications, vendor_license, legal_license, notices):
        _regular_file(path)
    upstream = _load_json(upstream_path)
    _verify_upstream(upstream, vendor_license)
    if vendor_license.read_bytes() != legal_license.read_bytes():
        raise VerificationError("Nautilus vendor and legal license copies differ")
    if upstream["upstream_commit"] not in modifications.read_text(encoding="utf-8"):
        raise VerificationError("modification log does not identify the pinned upstream commit")
    if upstream["upstream_commit"] not in notices.read_text(encoding="utf-8"):
        raise VerificationError("third-party notice does not identify the pinned upstream commit")
    _verify_manifest(_load_json(manifest_path), upstream, vendor / "source")
    _verify_no_unapproved_derived_source(root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        verify(args.root)
    except (OSError, VerificationError) as exc:
        print(f"nautilus provenance verification failed: {exc}", file=sys.stderr)
        return 2
    print("nautilus provenance verification: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
