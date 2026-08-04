from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/prepare_nautilus_toolchain.py"


def _module():
    spec = importlib.util.spec_from_file_location("nautilus_toolchain", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_missing_cache_artifact_is_rejected(tmp_path: Path) -> None:
    module = _module()
    manifest = {
        "components": {"rustc": {"filename": "rustc.tar.xz", "sha256": "0" * 64}},
    }

    assert module.verify_cached_components(tmp_path, manifest) == ["rustc.tar.xz"]


def test_cached_artifact_requires_exact_hash(tmp_path: Path) -> None:
    module = _module()
    artifact = tmp_path / "rustc.tar.xz"
    artifact.write_bytes(b"trusted")
    manifest = {
        "components": {"rustc": {"filename": artifact.name, "sha256": "0" * 64}},
    }

    assert module.verify_cached_components(tmp_path, manifest) == [artifact.name]


def test_channel_manifest_is_also_hash_bound(tmp_path: Path) -> None:
    module = _module()
    manifest = {
        "components": {},
        "channel_manifest": {
            "filename": "channel-rust-1.95.0.toml",
            "sha256": "0" * 64,
        },
    }

    assert module.verify_cached_components(tmp_path, manifest) == ["channel-rust-1.95.0.toml"]


def test_installer_uses_equals_form_for_prefix(tmp_path: Path) -> None:
    module = _module()

    assert module.installer_argv(Path("/tmp/install.sh"), tmp_path) == [
        "sh", "/tmp/install.sh", f"--prefix={tmp_path}",
    ]
