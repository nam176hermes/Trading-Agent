from __future__ import annotations

from collections.abc import Iterator
import hashlib
import importlib.util
import io
import json
import os
import stat
import tarfile
import tempfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "scripts" / "prepare_nautilus_llvm_toolchain.py"
POLICY = ROOT / "engines" / "nautilus" / "llvm-toolchain-policy.json"


@pytest.fixture
def tmp_path() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="nautilus-llvm-test-", dir="/tmp") as directory:
        yield Path(directory)


def _load_tool():
    spec = importlib.util.spec_from_file_location("prepare_nautilus_llvm_toolchain", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _add_file(archive: tarfile.TarFile, name: str, content: bytes, mode: int = 0o755) -> None:
    member = tarfile.TarInfo(name)
    member.size = len(content)
    member.mode = mode
    archive.addfile(member, io.BytesIO(content))


def _fixture_archive(
    path: Path,
    *,
    unsafe_name: str | None = None,
    symlinked_header: bool = False,
) -> tuple[dict[str, bytes], dict[str, bytes]]:
    clang = b"#!/bin/sh\necho 'clang version 18.1.8 (private fixture)'\n"
    tools = {
        "clang": clang,
        "clang++": clang,
        "ld.lld": b"#!/bin/sh\necho 'LLD 18.1.8 (private fixture)'\n",
    }
    headers = {
        "__stddef_size_t.h": b"typedef __SIZE_TYPE__ size_t;\n",
        "stddef.h": b'#include "__stddef_size_t.h"\n',
    }
    root = "clang+llvm-18.1.8-x86_64-linux-gnu-ubuntu-18.04"
    with tarfile.open(path, "w:xz") as archive:
        _add_file(archive, f"{root}/bin/clang-18", tools["clang"])
        clang = tarfile.TarInfo(f"{root}/bin/clang")
        clang.type = tarfile.SYMTYPE
        clang.linkname = "clang-18"
        archive.addfile(clang)
        clangxx = tarfile.TarInfo(f"{root}/bin/clang++")
        clangxx.type = tarfile.SYMTYPE
        clangxx.linkname = "clang"
        archive.addfile(clangxx)
        _add_file(archive, f"{root}/bin/lld", tools["ld.lld"])
        linker = tarfile.TarInfo(f"{root}/bin/ld.lld")
        linker.type = tarfile.SYMTYPE
        linker.linkname = "lld"
        archive.addfile(linker)
        for name, content in headers.items():
            archive_name = f"{root}/lib/clang/18/include/{name}"
            if symlinked_header and name == "stddef.h":
                header = tarfile.TarInfo(archive_name)
                header.type = tarfile.SYMTYPE
                header.linkname = "__stddef_size_t.h"
                archive.addfile(header)
            else:
                _add_file(archive, archive_name, content, mode=0o644)
        if unsafe_name is not None:
            _add_file(archive, unsafe_name, b"escape")
    return tools, headers


def _fixture_policy(
    archive: Path, tools: dict[str, bytes], headers: dict[str, bytes]
) -> dict[str, object]:
    return {
        "schema_version": 2,
        "version": "18.1.8",
        "release_tag": "llvmorg-18.1.8",
        "platform": "linux-x86_64",
        "asset": {
            "filename": archive.name,
            "url": (
                "https://github.com/llvm/llvm-project/releases/download/"
                "llvmorg-18.1.8/clang%2Bllvm-18.1.8-x86_64-linux-gnu-ubuntu-18.04.tar.xz"
            ),
            "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "size": archive.stat().st_size,
            "archive_root": "clang+llvm-18.1.8-x86_64-linux-gnu-ubuntu-18.04",
        },
        "tools": {
            name: {
                "archive_path": f"bin/{name}",
                "sha256": _sha256_bytes(content),
                "size": len(content),
                "identity_prefix": "clang version 18.1.8"
                if name != "ld.lld"
                else "LLD 18.1.8",
            }
            for name, content in tools.items()
        },
        "resource_headers": {
            "archive_root": "lib/clang/18/include",
            "files": {
                name: {
                    "sha256": _sha256_bytes(content),
                    "size": len(content),
                }
                for name, content in headers.items()
            },
        },
    }


def _private_parent(tmp_path: Path) -> Path:
    parent = tmp_path / "private"
    parent.mkdir(mode=0o700)
    return parent


def test_committed_policy_pins_one_official_linux_x86_64_release() -> None:
    document = json.loads(POLICY.read_text(encoding="utf-8"))

    assert document["schema_version"] == 2
    assert document["platform"] == "linux-x86_64"
    assert document["asset"]["url"].startswith(
        "https://github.com/llvm/llvm-project/releases/download/llvmorg-"
    )
    assert document["asset"]["filename"].endswith(".tar.xz")
    assert len(document["asset"]["sha256"]) == 64
    assert document["asset"]["size"] > 0
    assert set(document["tools"]) == {"clang", "clang++", "ld.lld"}
    for record in document["tools"].values():
        assert len(record["sha256"]) == 64
        assert record["size"] > 0
    resource_headers = document["resource_headers"]
    assert resource_headers["archive_root"] == "lib/clang/22/include"
    assert "stddef.h" in resource_headers["files"]
    for path, record in resource_headers["files"].items():
        assert path and not path.startswith("/") and ".." not in Path(path).parts
        assert len(record["sha256"]) == 64
        assert record["size"] > 0


def test_cache_and_materialization_are_sealed_and_direct(tmp_path: Path) -> None:
    tool = _load_tool()
    archive = tmp_path / "fixture.tar.xz"
    contents, headers = _fixture_archive(archive)
    policy = _fixture_policy(archive, contents, headers)
    parent = _private_parent(tmp_path)
    cache = parent / "cache"
    destination = parent / "toolchain"

    tool.publish_verified_archive(archive, cache, policy)
    tool.verify_cache(cache, policy)
    identities = tool.materialize(cache, destination, policy)

    assert set(identities) == {"clang", "clang++", "ld.lld"}
    assert stat.S_IMODE(cache.stat().st_mode) == 0o500
    assert stat.S_IMODE((cache / archive.name).stat().st_mode) == 0o400
    assert stat.S_IMODE(destination.stat().st_mode) == 0o500
    assert stat.S_IMODE((destination / "bin").stat().st_mode) == 0o500
    for name, content in contents.items():
        binary = destination / "bin" / name
        assert binary.is_file() and not binary.is_symlink()
        assert stat.S_ISREG(binary.lstat().st_mode)
        assert stat.S_IMODE(binary.stat().st_mode) == 0o500
        assert binary.read_bytes() == content
    include = destination / "lib" / "clang" / "18" / "include"
    assert stat.S_IMODE((destination / "lib").stat().st_mode) == 0o500
    assert stat.S_IMODE((destination / "lib" / "clang").stat().st_mode) == 0o500
    assert stat.S_IMODE((destination / "lib" / "clang" / "18").stat().st_mode) == 0o500
    assert stat.S_IMODE(include.stat().st_mode) == 0o500
    assert {path.name for path in include.iterdir()} == set(headers)
    for name, content in headers.items():
        header = include / name
        assert header.is_file() and not header.is_symlink()
        assert stat.S_IMODE(header.stat().st_mode) == 0o400
        assert header.read_bytes() == content


def test_acquisition_downloads_into_descriptor_bound_private_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = _load_tool()
    archive = tmp_path / "fixture.tar.xz"
    contents, headers = _fixture_archive(archive)
    policy = _fixture_policy(archive, contents, headers)
    cache = _private_parent(tmp_path) / "cache"
    payload = archive.read_bytes()
    monkeypatch.setattr(tool.urllib.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(payload))

    tool.acquire(cache, policy)

    tool.verify_cache(cache, policy)


@pytest.mark.parametrize(
    "unsafe_name",
    ["../escape", "/absolute", "root/../../escape"],
)
def test_unsafe_archive_paths_never_publish_a_cache(tmp_path: Path, unsafe_name: str) -> None:
    tool = _load_tool()
    archive = tmp_path / "unsafe.tar.xz"
    contents, headers = _fixture_archive(archive, unsafe_name=unsafe_name)
    policy = _fixture_policy(archive, contents, headers)
    cache = _private_parent(tmp_path) / "cache"

    with pytest.raises(tool.VerificationError, match="unsafe archive path"):
        tool.publish_verified_archive(archive, cache, policy)

    assert not cache.exists()


def test_resource_headers_must_be_direct_archive_files(tmp_path: Path) -> None:
    tool = _load_tool()
    archive = tmp_path / "symlinked-header.tar.xz"
    contents, headers = _fixture_archive(archive, symlinked_header=True)
    policy = _fixture_policy(archive, contents, headers)
    cache = _private_parent(tmp_path) / "cache"

    with pytest.raises(tool.VerificationError, match="direct regular file"):
        tool.publish_verified_archive(archive, cache, policy)

    assert not cache.exists()


@pytest.mark.parametrize("mutation", ["mutable", "digest", "extra", "symlink"])
def test_offline_cache_verification_rejects_substitution(tmp_path: Path, mutation: str) -> None:
    tool = _load_tool()
    archive = tmp_path / "fixture.tar.xz"
    contents, headers = _fixture_archive(archive)
    policy = _fixture_policy(archive, contents, headers)
    cache = _private_parent(tmp_path) / "cache"
    tool.publish_verified_archive(archive, cache, policy)
    cached_archive = cache / archive.name

    if mutation == "mutable":
        os.chmod(cached_archive, 0o600)
    elif mutation == "digest":
        os.chmod(cached_archive, 0o600)
        cached_archive.write_bytes(b"substituted")
        os.chmod(cached_archive, 0o400)
    elif mutation == "extra":
        os.chmod(cache, 0o700)
        (cache / "ambient-clang").write_text("unexpected", encoding="utf-8")
        os.chmod(cache / "ambient-clang", 0o400)
        os.chmod(cache, 0o500)
    else:
        os.chmod(cache, 0o700)
        cached_archive.unlink()
        cached_archive.symlink_to(archive)
        os.chmod(cache, 0o500)

    with pytest.raises(tool.VerificationError):
        tool.verify_cache(cache, policy)


def test_identity_check_never_falls_back_to_ambient_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = _load_tool()
    archive = tmp_path / "fixture.tar.xz"
    contents, headers = _fixture_archive(archive)
    policy = _fixture_policy(archive, contents, headers)
    parent = _private_parent(tmp_path)
    cache = parent / "cache"
    destination = parent / "toolchain"
    tool.publish_verified_archive(archive, cache, policy)
    tool.materialize(cache, destination, policy)
    ambient = tmp_path / "ambient"
    ambient.mkdir()
    marker = tmp_path / "ambient-ran"
    for name in ("clang", "clang++", "ld.lld"):
        fake = ambient / name
        fake.write_text(f"#!/bin/sh\ntouch '{marker}'\n", encoding="utf-8")
        fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(ambient))

    identities = tool.verify_materialized(destination, policy)

    assert identities["clang"].startswith("clang version 18.1.8")
    assert not marker.exists()
    os.chmod(destination, 0o700)
    os.chmod(destination / "bin", 0o700)
    (destination / "bin" / "clang").unlink()
    os.chmod(destination / "bin", 0o500)
    os.chmod(destination, 0o500)
    with pytest.raises(tool.VerificationError, match="clang"):
        tool.verify_materialized(destination, policy)
    assert not marker.exists()


@pytest.mark.parametrize("mutation", ["digest", "extra", "symlink"])
def test_materialized_verifier_rejects_resource_header_substitution(
    tmp_path: Path, mutation: str
) -> None:
    tool = _load_tool()
    archive = tmp_path / "fixture.tar.xz"
    contents, headers = _fixture_archive(archive)
    policy = _fixture_policy(archive, contents, headers)
    parent = _private_parent(tmp_path)
    cache = parent / "cache"
    destination = parent / "toolchain"
    tool.publish_verified_archive(archive, cache, policy)
    tool.materialize(cache, destination, policy)
    include = destination / "lib" / "clang" / "18" / "include"
    header = include / "stddef.h"
    os.chmod(destination, 0o700)
    os.chmod(destination / "lib", 0o700)
    os.chmod(destination / "lib" / "clang", 0o700)
    os.chmod(destination / "lib" / "clang" / "18", 0o700)
    os.chmod(include, 0o700)
    if mutation == "digest":
        os.chmod(header, 0o600)
        header.write_bytes(b"substituted")
        os.chmod(header, 0o400)
    elif mutation == "extra":
        extra = include / "ambient.h"
        extra.write_bytes(b"unexpected")
        os.chmod(extra, 0o400)
    else:
        header.unlink()
        header.symlink_to(include / "__stddef_size_t.h")
    os.chmod(include, 0o500)
    os.chmod(destination / "lib" / "clang" / "18", 0o500)
    os.chmod(destination / "lib" / "clang", 0o500)
    os.chmod(destination / "lib", 0o500)
    os.chmod(destination, 0o500)

    with pytest.raises(tool.VerificationError):
        tool.verify_materialized(destination, policy)


def test_compiler_environment_contains_only_absolute_private_tools(tmp_path: Path) -> None:
    tool = _load_tool()
    llvm_bin = (tmp_path / "toolchain" / "bin").resolve()

    environment = tool.compiler_environment(llvm_bin)

    assert environment == {
        "PATH": str(llvm_bin),
        "CC": str(llvm_bin / "clang"),
        "CXX": str(llvm_bin / "clang++"),
        "LD": str(llvm_bin / "ld.lld"),
    }
