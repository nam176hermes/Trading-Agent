from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from scripts.verify_nautilus_provenance import VerificationError, verify


ROOT = Path(__file__).resolve().parents[2]
REQUIRED = (
    "third_party/nautilus_trader/UPSTREAM.json",
    "third_party/nautilus_trader/FILE_MANIFEST.json",
    "third_party/nautilus_trader/MODIFICATIONS.md",
    "third_party/nautilus_trader/LICENSE",
    "legal/LGPL-3.0-or-later.txt",
    "legal/THIRD_PARTY_NOTICES.md",
)


def _fixture_root(tmp_path: Path) -> Path:
    for relative in REQUIRED:
        source = ROOT / relative
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    return tmp_path


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, document: dict[str, object]) -> None:
    path.write_text(json.dumps(document, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _enable_vendored_mode(root: Path) -> Path:
    upstream_path = root / "third_party/nautilus_trader/UPSTREAM.json"
    manifest_path = root / "third_party/nautilus_trader/FILE_MANIFEST.json"
    upstream = _read_json(upstream_path)
    upstream["distribution_mode"] = "vendored_source"
    _write_json(upstream_path, upstream)
    source = root / "third_party/nautilus_trader/source"
    source.mkdir()
    entry = source / "module.py"
    entry.write_text("value = 1\n", encoding="utf-8")
    manifest = _read_json(manifest_path)
    manifest["distribution_mode"] = "vendored_source"
    manifest["files"] = [{
        "path": "module.py",
        "size": entry.stat().st_size,
        "sha256": hashlib.sha256(entry.read_bytes()).hexdigest(),
    }]
    _write_json(manifest_path, manifest)
    return entry


def test_current_external_pinned_upstream_is_valid() -> None:
    verify(ROOT)


def test_missing_provenance_is_rejected(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    (root / "third_party/nautilus_trader/UPSTREAM.json").unlink()

    with pytest.raises(VerificationError, match="regular file"):
        verify(root)


def test_missing_license_is_rejected(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    (root / "legal/LGPL-3.0-or-later.txt").unlink()

    with pytest.raises(VerificationError, match="regular file"):
        verify(root)


def test_wrong_upstream_commit_is_rejected(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    upstream_path = root / "third_party/nautilus_trader/UPSTREAM.json"
    upstream = _read_json(upstream_path)
    upstream["tag_commit"] = "0" * 40
    _write_json(upstream_path, upstream)

    with pytest.raises(VerificationError, match="does not resolve"):
        verify(root)


def test_changed_vendored_file_is_rejected(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    entry = _enable_vendored_mode(root)
    verify(root)
    entry.write_text("value = 2\n", encoding="utf-8")

    with pytest.raises(VerificationError, match="vendored source drift"):
        verify(root)


def test_unexpected_vendored_source_is_rejected(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    source = root / "third_party/nautilus_trader/source"
    source.mkdir()
    (source / "unexpected.py").write_text("value = 1\n", encoding="utf-8")

    with pytest.raises(VerificationError, match="external upstream mode"):
        verify(root)


def test_nonautorised_derived_source_is_rejected(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    target = root / "packages/unapproved.py"
    target.parent.mkdir()
    target.write_text("# Derived from Nautilus" + "Trader\n", encoding="utf-8")

    with pytest.raises(VerificationError, match="outside approved path"):
        verify(root)
