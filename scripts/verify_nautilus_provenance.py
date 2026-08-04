#!/usr/bin/env python3
"""Fail closed on NautilusTrader provenance, license and vendored-source drift."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_EXTERNAL_MODE = "external_pinned_upstream"
_UPSTREAM_FIELDS = {
    "schema_version", "distribution_mode", "upstream_repository", "upstream_tag",
    "upstream_tag_object", "upstream_commit", "tag_commit", "license_spdx",
    "license_sha256", "source_acquisition",
}
_MANIFEST_FIELDS = {"schema_version", "upstream_commit", "distribution_mode", "files"}
_APPROVED_DERIVED_PATHS = {
    "scripts/verify_nautilus_provenance.py",
}
_EXPECTED_UPSTREAM = {
    "upstream_repository": "https://github.com/nautechsystems/nautilus_trader.git",
    "upstream_tag": "v1.227.0",
    "upstream_tag_object": "0ccb5b55879c072a6e07fc7cbe5297c53c378107",
    "upstream_commit": "280ae1762df51a492a4ce71506a40b5c8706def5",
    "tag_commit": "280ae1762df51a492a4ce71506a40b5c8706def5",
    "license_spdx": "LGPL-3.0-or-later",
    "license_sha256": "ee907919ec88c9c017b1f8b608db20960b6598aefcc4fe58820bde955d65ed3c",
}
_EXTERNAL_VENDOR_FILES = {
    "UPSTREAM.json", "FILE_MANIFEST.json", "MODIFICATIONS.md", "LICENSE",
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


def _verify_upstream(document: dict[str, Any], license_path: Path) -> None:
    if set(document) != _UPSTREAM_FIELDS or document.get("schema_version") != 1:
        raise VerificationError("UPSTREAM.json fields are missing or unknown")
    if document["distribution_mode"] != _EXTERNAL_MODE:
        raise VerificationError("vendored Nautilus source is not permitted in packet 01B")
    for key, expected in _EXPECTED_UPSTREAM.items():
        if document.get(key) != expected:
            raise VerificationError(f"unexpected Nautilus {key}")
    for key in ("upstream_tag_object", "upstream_commit", "tag_commit"):
        if not isinstance(document[key], str) or _COMMIT.fullmatch(document[key]) is None:
            raise VerificationError(f"invalid Nautilus {key}")
    if not isinstance(document["license_sha256"], str) or _SHA256.fullmatch(document["license_sha256"]) is None:
        raise VerificationError("invalid Nautilus license digest")
    _regular_file(license_path)
    if _sha256(license_path) != document["license_sha256"]:
        raise VerificationError("Nautilus license digest mismatch")


def _verify_manifest(document: dict[str, Any], upstream: dict[str, Any]) -> None:
    if set(document) != _MANIFEST_FIELDS or document.get("schema_version") != 1:
        raise VerificationError("FILE_MANIFEST.json fields are missing or unknown")
    if document.get("upstream_commit") != upstream["upstream_commit"]:
        raise VerificationError("file manifest upstream commit mismatch")
    if document.get("distribution_mode") != upstream["distribution_mode"]:
        raise VerificationError("file manifest distribution mode mismatch")
    records = document.get("files")
    if not isinstance(records, list):
        raise VerificationError("file manifest records must be a list")
    if records:
        raise VerificationError("packet 01B cannot contain a vendored source manifest")


def _verify_external_vendor_layout(vendor: Path) -> None:
    info = vendor.lstat()
    if not stat.S_ISDIR(info.st_mode) or vendor.is_symlink():
        raise VerificationError("Nautilus vendor root is missing or unsafe")
    observed = {child.name for child in vendor.iterdir()}
    if observed != _EXTERNAL_VENDOR_FILES:
        raise VerificationError("external Nautilus vendor tree has unexpected entry")
    for name in observed:
        _regular_file(vendor / name)


def _verify_remote_tag(upstream: dict[str, Any]) -> None:
    tag = upstream["upstream_tag"]
    try:
        result = subprocess.run(
            ["git", "ls-remote", upstream["upstream_repository"], f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"],
            check=True, capture_output=True, text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise VerificationError("unable to resolve Nautilus upstream tag") from exc
    refs = {line.split("\t", 1)[1]: line.split("\t", 1)[0] for line in result.stdout.splitlines() if "\t" in line}
    if refs.get(f"refs/tags/{tag}") != upstream["upstream_tag_object"]:
        raise VerificationError("Nautilus upstream tag object mismatch")
    if refs.get(f"refs/tags/{tag}^{{}}") != upstream["upstream_commit"]:
        raise VerificationError("Nautilus upstream tag peel mismatch")


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


def verify(root: Path, *, verify_upstream: bool = False) -> None:
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
    _verify_external_vendor_layout(vendor)
    source = vendor / "source"
    try:
        source.lstat()
    except FileNotFoundError:
        pass
    else:
        raise VerificationError("external upstream mode cannot contain vendored source")
    upstream = _load_json(upstream_path)
    _verify_upstream(upstream, vendor_license)
    if vendor_license.read_bytes() != legal_license.read_bytes():
        raise VerificationError("Nautilus vendor and legal license copies differ")
    if upstream["upstream_commit"] not in modifications.read_text(encoding="utf-8"):
        raise VerificationError("modification log does not identify the pinned upstream commit")
    if upstream["upstream_commit"] not in notices.read_text(encoding="utf-8"):
        raise VerificationError("third-party notice does not identify the pinned upstream commit")
    _verify_manifest(_load_json(manifest_path), upstream)
    if verify_upstream:
        _verify_remote_tag(upstream)
    _verify_no_unapproved_derived_source(root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--verify-upstream", action="store_true")
    args = parser.parse_args(argv)
    try:
        verify(args.root, verify_upstream=args.verify_upstream)
    except (OSError, VerificationError) as exc:
        print(f"nautilus provenance verification failed: {exc}", file=sys.stderr)
        return 2
    print("nautilus provenance verification: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
