from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

import scripts.verify_nautilus_provenance as provenance
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


def test_self_consistent_wrong_upstream_metadata_is_rejected(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    upstream_path = root / "third_party/nautilus_trader/UPSTREAM.json"
    upstream = _read_json(upstream_path)
    upstream["upstream_tag"] = "v0.0.0"
    upstream["upstream_tag_object"] = "1" * 40
    upstream["upstream_commit"] = "2" * 40
    upstream["tag_commit"] = "2" * 40
    upstream["license_sha256"] = "3" * 64
    _write_json(upstream_path, upstream)

    with pytest.raises(VerificationError, match="unexpected Nautilus"):
        verify(root)


def test_any_vendored_source_is_rejected_until_a_trusted_manifest_exists(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    source = root / "third_party/nautilus_trader/source"
    source.mkdir()
    (source / "module.py").write_text("value = 2\n", encoding="utf-8")

    with pytest.raises(VerificationError, match="unexpected entry"):
        verify(root)


def test_unexpected_vendored_source_is_rejected(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    source = root / "third_party/nautilus_trader/source"
    source.mkdir()
    (source / "unexpected.py").write_text("value = 1\n", encoding="utf-8")

    with pytest.raises(VerificationError, match="unexpected entry"):
        verify(root)


def test_dangling_vendor_source_symlink_is_rejected(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    os.symlink(root / "does-not-exist", root / "third_party/nautilus_trader/source")

    with pytest.raises(VerificationError, match="unexpected entry"):
        verify(root)


def test_unmarked_extra_vendor_file_is_rejected(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    (root / "third_party/nautilus_trader/unlisted_vendor.py").write_text(
        "value = 1\n", encoding="utf-8"
    )

    with pytest.raises(VerificationError, match="unexpected entry"):
        verify(root)


def test_vendor_root_symlink_is_rejected(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    vendor = root / "third_party/nautilus_trader"
    backing = root / "third_party/vendor-backing"
    vendor.rename(backing)
    os.symlink(backing, vendor)

    with pytest.raises(VerificationError, match="vendor root is missing or unsafe"):
        verify(root)


@pytest.mark.parametrize("name", ["third_party", "legal"])
def test_provenance_parent_symlink_is_rejected(tmp_path: Path, name: str) -> None:
    root = _fixture_root(tmp_path)
    parent = root / name
    backing = root / f"{name}-backing"
    parent.rename(backing)
    os.symlink(backing, parent)

    with pytest.raises(VerificationError, match=f"{name} directory is missing or unsafe"):
        verify(root)


def test_nonautorised_derived_source_is_rejected(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    target = root / "packages/unapproved.py"
    target.parent.mkdir()
    target.write_text("# Derived from Nautilus" + "Trader\n", encoding="utf-8")

    with pytest.raises(VerificationError, match="outside approved path"):
        verify(root)


def test_remote_verification_rejects_wrong_tag_peel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _fixture_root(tmp_path)

    class Result:
        stdout = (
            "0ccb5b55879c072a6e07fc7cbe5297c53c378107\trefs/tags/v1.227.0\n"
            + "0" * 40
            + "\trefs/tags/v1.227.0^{}\n"
        )

    monkeypatch.setattr(provenance.subprocess, "run", lambda *_args, **_kwargs: Result())
    with pytest.raises(VerificationError, match="tag peel mismatch"):
        verify(root, verify_upstream=True)


def test_remote_verification_rejects_wrong_tag_object(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _fixture_root(tmp_path)

    class Result:
        stdout = (
            "0" * 40
            + "\trefs/tags/v1.227.0\n"
            + "280ae1762df51a492a4ce71506a40b5c8706def5\trefs/tags/v1.227.0^{}\n"
        )

    monkeypatch.setattr(provenance.subprocess, "run", lambda *_args, **_kwargs: Result())
    with pytest.raises(VerificationError, match="tag object mismatch"):
        verify(root, verify_upstream=True)
