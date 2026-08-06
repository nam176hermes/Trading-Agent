from __future__ import annotations

from collections.abc import Iterator
import importlib.util
import json
from pathlib import Path
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/prepare_nautilus_toolchain.py"


@pytest.fixture
def tmp_path() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="nautilus-rust-test-", dir="/tmp") as directory:
        yield Path(directory)


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


def _sealed_materialized_toolchain(tmp_path: Path):
    module = _module()
    toolchain = tmp_path / "toolchain"
    binary = toolchain / "bin" / "cargo"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"trusted-cargo")
    binary.chmod(0o500)
    library = toolchain / "lib" / "libstd.so"
    library.parent.mkdir()
    library.write_bytes(b"trusted-runtime")
    library.chmod(0o400)
    for directory in (binary.parent, library.parent):
        directory.chmod(0o500)
    records = module._tree_records(toolchain)
    policy = {
        "rust_version": "1.95.0",
        "materialized_toolchain": {
            "file_count": len(records),
            "tree_sha256": module._tree_sha256(records),
        },
    }
    (toolchain / "materialized-toolchain-manifest.json").write_text(
        json.dumps({"schema_version": 1, "files": records}) + "\n", encoding="utf-8"
    )
    (toolchain / "materialized-toolchain-manifest.json").chmod(0o400)
    toolchain.chmod(0o500)
    return module, toolchain, binary, policy


def test_materialized_toolchain_is_hash_bound_and_sealed(tmp_path: Path) -> None:
    module, toolchain, binary, policy = _sealed_materialized_toolchain(tmp_path)

    module.verify_materialized_toolchain(toolchain, policy)

    toolchain.chmod(0o700)
    binary.chmod(0o700)
    binary.write_bytes(b"replacement reporting cargo 1.95.0")
    binary.chmod(0o500)
    toolchain.chmod(0o500)
    try:
        module.verify_materialized_toolchain(toolchain, policy)
    except ValueError as exc:
        assert "hash drift" in str(exc)
    else:
        raise AssertionError("replacement cargo binary was accepted")


def test_materialized_toolchain_rejects_mutable_directories(tmp_path: Path) -> None:
    module, toolchain, _binary, policy = _sealed_materialized_toolchain(tmp_path)
    toolchain.chmod(0o700)

    try:
        module.verify_materialized_toolchain(toolchain, policy)
    except ValueError as exc:
        assert "not sealed" in str(exc)
    else:
        raise AssertionError("mutable toolchain root was accepted")


def test_cli_read_only_materialized_verification_checks_the_sealed_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, toolchain, _binary, policy = _sealed_materialized_toolchain(tmp_path)
    checked: list[Path] = []

    monkeypatch.setattr(module, "load_manifest", lambda _path: policy)
    monkeypatch.setattr(module, "verify_cached_components", lambda _cache, _manifest: [])
    monkeypatch.setattr(
        module,
        "verify_materialized_toolchain",
        lambda destination, _manifest: checked.append(destination),
    )

    assert module.main(
        [
            "--manifest",
            str(tmp_path / "policy.json"),
            "--cache",
            str(tmp_path / "cache"),
            "--destination",
            str(toolchain),
            "--verify-materialized",
        ]
    ) == 0

    assert checked == [toolchain]
