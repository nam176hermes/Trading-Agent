from __future__ import annotations

import json
import hashlib
import importlib.util
import os
from pathlib import Path
import stat
import tarfile
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "engines/nautilus/input-cache-policy.json"
SCRIPT = ROOT / "scripts/prepare_nautilus_input_cache.py"


def test_committed_input_cache_policy_binds_the_01b_upstream_and_lockfile() -> None:
    assert POLICY.is_file()
    document = json.loads(POLICY.read_text(encoding="utf-8"))

    assert document == {
        "schema_version": 1,
        "upstream_repository": "https://github.com/nautechsystems/nautilus_trader.git",
        "upstream_tag": "v1.227.0",
        "upstream_tag_object": "0ccb5b55879c072a6e07fc7cbe5297c53c378107",
        "upstream_commit": "280ae1762df51a492a4ce71506a40b5c8706def5",
        "source_url": (
            "https://github.com/nautechsystems/nautilus_trader/archive/"
            "280ae1762df51a492a4ce71506a40b5c8706def5.tar.gz"
        ),
        "source_sha256": "a00d3ab0c5b2ba1e4a4ac4c9af70f5b3fe30717d9b42a328e51696e3894a45e2",
        "cargo_lock_sha256": "083652294183947a352d1443ed0245311bf7ee5a716b66ccc21e814be25851ed",
        "pyproject_sha256": "f707cbe27b183ba598c31f1b3b6ec67e36f36e878c4228d3fef80741efb81b28",
        "required_cargo_version": "1.95.0",
    }


def test_input_cache_tool_is_committed() -> None:
    assert SCRIPT.is_file()


def _module():
    spec = importlib.util.spec_from_file_location("nautilus_input_cache", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _policy(source_url: str, source_sha256: str, cargo_lock: bytes, pyproject: bytes) -> dict[str, object]:
    return {
        "schema_version": 1,
        "upstream_repository": "https://github.com/nautechsystems/nautilus_trader.git",
        "upstream_tag": "v1.227.0",
        "upstream_tag_object": "0ccb5b55879c072a6e07fc7cbe5297c53c378107",
        "upstream_commit": "280ae1762df51a492a4ce71506a40b5c8706def5",
        "source_url": source_url,
        "source_sha256": source_sha256,
        "cargo_lock_sha256": _sha256(cargo_lock),
        "pyproject_sha256": _sha256(pyproject),
        "required_cargo_version": "1.95.0",
    }


def _source_archive(
    tmp_path: Path, cargo_lock: bytes, pyproject: bytes, *, symlink_target: str | None = None
) -> Path:
    archive = tmp_path / "source.tar.gz"
    root = "nautilus_trader-280ae1762df51a492a4ce71506a40b5c8706def5"
    source = tmp_path / "source-files"
    source.mkdir()
    (source / "Cargo.lock").write_bytes(cargo_lock)
    (source / "pyproject.toml").write_bytes(pyproject)
    (source / "Cargo.toml").write_text("[package]\nname = 'fixture'\nversion = '0.0.0'\n", encoding="utf-8")
    with tarfile.open(archive, "w:gz") as output:
        for name in ("Cargo.lock", "pyproject.toml", "Cargo.toml"):
            output.add(source / name, arcname=f"{root}/{name}")
        if symlink_target is not None:
            symlink = tarfile.TarInfo(f"{root}/unsafe-link")
            symlink.type = tarfile.SYMTYPE
            symlink.linkname = symlink_target
            output.addfile(symlink)
    return archive


def _private_cargo(tmp_path: Path, version: str = "1.95.0") -> Path:
    cargo = tmp_path / "private-toolchain/bin/cargo"
    cargo.parent.mkdir(parents=True, exist_ok=True)
    cargo.write_text(
        "#!/bin/sh\n"
        f"if [ \"$1\" = \"--version\" ]; then echo 'cargo {version} (fixture)'; exit 0; fi\n"
        "test \"$1\" = \"fetch\" && test \"$2\" = \"--locked\" || exit 9\n"
        "mkdir -p \"$CARGO_HOME/registry/cache/fixture\"\n"
        "printf fixture > \"$CARGO_HOME/registry/cache/fixture/fixture-1.0.0.crate\"\n",
        encoding="utf-8",
    )
    rustc = cargo.with_name("rustc")
    rustc.write_text(
        "#!/bin/sh\n"
        f"echo 'rustc {version} (fixture)'\n",
        encoding="utf-8",
    )
    cargo.chmod(0o500)
    rustc.chmod(0o500)
    cargo.parent.chmod(0o700)
    cargo.parent.parent.chmod(0o700)
    return cargo


def _make_artifact_parent_mutable(cache: Path, artifact: Path) -> None:
    directory = artifact.parent
    while directory != cache.parent:
        directory.chmod(0o700)
        directory = directory.parent


def _freeze_artifact_parent(cache: Path, artifact: Path) -> None:
    directories: list[Path] = []
    directory = artifact.parent
    while directory != cache.parent:
        directories.append(directory)
        directory = directory.parent
    for frozen in reversed(directories):
        frozen.chmod(0o500)


@pytest.fixture
def private_cache_root() -> Path:
    with tempfile.TemporaryDirectory(dir="/tmp", prefix="nautilus-input-cache-test-") as directory:
        yield Path(directory)


def test_acquire_binds_source_derived_inputs_and_cargo_closure(tmp_path: Path, private_cache_root: Path) -> None:
    module = _module()
    cargo_lock = b"version = 3\n"
    pyproject = b"[project]\nname = 'fixture'\n"
    archive = _source_archive(tmp_path, cargo_lock, pyproject)
    cache = private_cache_root / "external-cache"
    policy = _policy(archive.as_uri(), _sha256(archive.read_bytes()), cargo_lock, pyproject)

    manifest = module.acquire(cache, policy, _private_cargo(tmp_path))

    assert manifest["upstream_commit"] == policy["upstream_commit"]
    assert manifest["cargo_lock_sha256"] == _sha256(cargo_lock)
    assert {entry["path"] for entry in manifest["artifacts"]} == {
        "cargo-home/registry/cache/fixture/fixture-1.0.0.crate",
        "derived/Cargo.lock",
        "derived/pyproject.toml",
        "source/nautilus_trader-280ae1762df51a492a4ce71506a40b5c8706def5.tar.gz",
    }
    assert module.verify(cache, policy) == manifest
    for entry in manifest["artifacts"]:
        artifact = cache / entry["path"]
        assert stat.S_IMODE(artifact.stat().st_mode) == 0o400
        assert artifact.stat().st_nlink == 1


def test_acquisition_rejects_source_digest_drift(tmp_path: Path, private_cache_root: Path) -> None:
    module = _module()
    cargo_lock = b"version = 3\n"
    pyproject = b"[project]\nname = 'fixture'\n"
    archive = _source_archive(tmp_path, cargo_lock, pyproject)
    policy = _policy(archive.as_uri(), "0" * 64, cargo_lock, pyproject)

    with pytest.raises(module.VerificationError, match="source archive digest"):
        module.acquire(private_cache_root / "digest-drift-cache", policy, _private_cargo(tmp_path))


def test_acquisition_preserves_primary_error_and_closes_descriptors_when_discard_fails(
    tmp_path: Path, private_cache_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    cargo_lock = b"version = 3\n"
    pyproject = b"[project]\nname = 'fixture'\n"
    archive = _source_archive(tmp_path, cargo_lock, pyproject)
    policy = _policy(archive.as_uri(), "0" * 64, cargo_lock, pyproject)
    descriptors: list[int] = []
    prepare_parent = module._prepare_private_cache_parent
    create_staging = module._create_private_staging

    def capture_parent(path: Path) -> int:
        descriptor = prepare_parent(path)
        descriptors.append(descriptor)
        return descriptor

    def capture_staging(parent_fd: int) -> tuple[str, int]:
        name, descriptor = create_staging(parent_fd)
        descriptors.append(descriptor)
        return name, descriptor

    def fail_discard(_parent_fd: int, _name: str, _staging: Path) -> None:
        raise OSError("discard failed")

    monkeypatch.setattr(module, "_prepare_private_cache_parent", capture_parent)
    monkeypatch.setattr(module, "_create_private_staging", capture_staging)
    monkeypatch.setattr(module, "_discard_private_staging", fail_discard)

    with pytest.raises(module.VerificationError, match="source archive digest"):
        module.acquire(private_cache_root / "failed-cleanup-cache", policy, _private_cargo(tmp_path))

    assert len(descriptors) == 2
    for descriptor in descriptors:
        with pytest.raises(OSError, match="Bad file descriptor"):
            os.fstat(descriptor)


def test_acquisition_creates_a_missing_private_cache_parent(tmp_path: Path, private_cache_root: Path) -> None:
    module = _module()
    cargo_lock = b"version = 3\n"
    pyproject = b"[project]\nname = 'fixture'\n"
    archive = _source_archive(tmp_path, cargo_lock, pyproject)
    policy = _policy(archive.as_uri(), _sha256(archive.read_bytes()), cargo_lock, pyproject)
    cache = private_cache_root / "new-parent" / "nested" / "cache"

    module.acquire(cache, policy, _private_cargo(tmp_path))

    assert stat.S_IMODE(cache.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(cache.parent.parent.stat().st_mode) == 0o700
    assert module.verify(cache, policy)["schema_version"] == 1


def test_acquisition_uses_the_verified_parent_after_a_path_swap(
    tmp_path: Path, private_cache_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    cargo_lock = b"version = 3\n"
    pyproject = b"[project]\nname = 'fixture'\n"
    archive = _source_archive(tmp_path, cargo_lock, pyproject)
    policy = _policy(archive.as_uri(), _sha256(archive.read_bytes()), cargo_lock, pyproject)
    parent = private_cache_root / "parent"
    parent.mkdir()
    cache = parent / "cache"
    backing = private_cache_root / "parent-backing"
    substituted = private_cache_root / "substituted"
    substituted.mkdir()
    original = module._prepare_private_cache_parent

    def swap_after_prepare(path: Path):
        parent_fd = original(path)
        parent.rename(backing)
        os.symlink(substituted, parent)
        return parent_fd

    monkeypatch.setattr(module, "_prepare_private_cache_parent", swap_after_prepare)
    module.acquire(cache, policy, _private_cargo(tmp_path))

    assert (backing / "cache").is_dir()
    assert not (substituted / "cache").exists()


def test_source_extraction_rejects_an_absolute_symlink_target(tmp_path: Path) -> None:
    module = _module()
    cargo_lock = b"version = 3\n"
    pyproject = b"[project]\nname = 'fixture'\n"
    archive = _source_archive(tmp_path, cargo_lock, pyproject, symlink_target="/tmp/escape")

    with pytest.raises(module.VerificationError, match="symlink escapes"):
        module._safe_extract_source(archive, tmp_path / "extracted", "280ae1762df51a492a4ce71506a40b5c8706def5")


def test_verifier_rejects_missing_hash_drifted_mutable_and_symlinked_inputs(
    tmp_path: Path, private_cache_root: Path
) -> None:
    module = _module()
    cargo_lock = b"version = 3\n"
    pyproject = b"[project]\nname = 'fixture'\n"
    archive = _source_archive(tmp_path, cargo_lock, pyproject)
    cache = private_cache_root / "missing-cache"
    policy = _policy(archive.as_uri(), _sha256(archive.read_bytes()), cargo_lock, pyproject)
    cargo = _private_cargo(tmp_path)
    module.acquire(cache, policy, cargo)

    cargo_artifact = cache / "cargo-home/registry/cache/fixture/fixture-1.0.0.crate"
    _make_artifact_parent_mutable(cache, cargo_artifact)
    cargo_artifact.unlink()
    _freeze_artifact_parent(cache, cargo_artifact)
    with pytest.raises(module.VerificationError, match="missing"):
        module.verify(cache, policy)

    cache = private_cache_root / "drifted-cache"
    module.acquire(cache, policy, cargo)
    cargo_artifact = cache / "cargo-home/registry/cache/fixture/fixture-1.0.0.crate"
    _make_artifact_parent_mutable(cache, cargo_artifact)
    cargo_artifact.chmod(0o600)
    cargo_artifact.write_bytes(b"drift")
    with pytest.raises(module.VerificationError, match="mutable|digest"):
        module.verify(cache, policy)

    cache = private_cache_root / "symlinked-cache"
    module.acquire(cache, policy, cargo)
    cargo_artifact = cache / "cargo-home/registry/cache/fixture/fixture-1.0.0.crate"
    _make_artifact_parent_mutable(cache, cargo_artifact)
    cargo_artifact.unlink()
    os.symlink(cache / "derived/Cargo.lock", cargo_artifact)
    _freeze_artifact_parent(cache, cargo_artifact)
    with pytest.raises(module.VerificationError, match="regular|symlink"):
        module.verify(cache, policy)


def test_verification_is_offline_and_does_not_invoke_cargo(
    tmp_path: Path, private_cache_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    cargo_lock = b"version = 3\n"
    pyproject = b"[project]\nname = 'fixture'\n"
    archive = _source_archive(tmp_path, cargo_lock, pyproject)
    cache = private_cache_root / "external-cache"
    policy = _policy(archive.as_uri(), _sha256(archive.read_bytes()), cargo_lock, pyproject)
    module.acquire(cache, policy, _private_cargo(tmp_path))

    monkeypatch.setattr(module.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("cargo invoked"))
    assert module.verify(cache, policy)["schema_version"] == 1


def test_acquisition_rejects_a_non_private_or_wrong_version_cargo(tmp_path: Path) -> None:
    module = _module()
    cargo = _private_cargo(tmp_path)
    backing = cargo.with_name("cargo-backing")
    cargo.rename(backing)
    os.symlink(backing, cargo)

    with pytest.raises(module.VerificationError, match="symlinked ancestor|regular non-symlink"):
        module.validate_private_cargo(cargo, "1.95.0")


@pytest.mark.parametrize("tool, validator", [("cargo", "validate_private_cargo"), ("rustc", "validate_private_rustc")])
def test_private_toolchain_rejects_a_symlinked_ancestor(
    tmp_path: Path, tool: str, validator: str
) -> None:
    module = _module()
    cargo = _private_cargo(tmp_path / "backing")
    alias = tmp_path / "alias"
    os.symlink(cargo.parent.parent, alias)

    with pytest.raises(module.VerificationError, match="symlinked ancestor"):
        getattr(module, validator)(alias / "bin" / tool, "1.95.0")


def test_cache_verification_rejects_a_symlinked_ancestor(
    tmp_path: Path, private_cache_root: Path
) -> None:
    module = _module()
    cargo_lock = b"version = 3\n"
    pyproject = b"[project]\nname = 'fixture'\n"
    archive = _source_archive(tmp_path, cargo_lock, pyproject)
    policy = _policy(archive.as_uri(), _sha256(archive.read_bytes()), cargo_lock, pyproject)
    backing = private_cache_root / "backing"
    backing.mkdir()
    alias = private_cache_root / "alias"
    os.symlink(backing, alias)
    cargo = _private_cargo(tmp_path)

    with pytest.raises(module.VerificationError, match="symlinked ancestor"):
        module.acquire(alias / "new-cache", policy, cargo)

    cache = backing / "cache"
    module.acquire(cache, policy, cargo)

    with pytest.raises(module.VerificationError, match="symlinked ancestor"):
        module.verify(alias / "cache", policy)


def test_acquisition_rejects_a_wrong_cargo_version(tmp_path: Path) -> None:
    module = _module()

    with pytest.raises(module.VerificationError, match="version"):
        module.validate_private_cargo(_private_cargo(tmp_path, "1.94.0"), "1.95.0")
