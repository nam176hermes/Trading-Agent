#!/usr/bin/env python3
"""Acquire and materialize a hash-bound Rust toolchain without rustup."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifacts(manifest: dict[str, object]) -> list[dict[str, object]]:
    components = manifest["components"]
    assert isinstance(components, dict)
    artifacts = list(components.values())
    channel_manifest = manifest.get("channel_manifest")
    if channel_manifest is not None:
        artifacts.append(channel_manifest)
    if not all(isinstance(item, dict) for item in artifacts):
        raise ValueError("invalid toolchain input manifest")
    return artifacts


def verify_cached_components(cache: Path, manifest: dict[str, object]) -> list[str]:
    invalid: list[str] = []
    for item in _artifacts(manifest):
        filename, expected = item["filename"], item["sha256"]
        assert isinstance(filename, str) and isinstance(expected, str)
        artifact = cache / filename
        if not artifact.is_file() or artifact.is_symlink() or _sha256(artifact) != expected:
            invalid.append(filename)
    return invalid


def _load_manifest(path: Path) -> dict[str, object]:
    document = json.loads(path.read_text(encoding="utf-8"))
    rust = document["rust"]
    assert isinstance(rust, dict) and isinstance(rust["components"], dict)
    channel_url = rust["channel_manifest_url"]
    channel_sha256 = rust["channel_manifest_sha256"]
    assert isinstance(channel_url, str) and isinstance(channel_sha256, str)
    return {
        "channel_manifest": {
            "filename": Path(channel_url).name,
            "sha256": channel_sha256,
            "url": channel_url,
        },
        "components": {
            name: {**item, "filename": Path(item["url"]).name}
            for name, item in rust["components"].items()
        },
    }


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError("toolchain cache must be a private directory")


def installer_argv(installer: Path, destination: Path) -> list[str]:
    """Return the Rust installer invocation accepted by component install.sh."""
    return ["sh", str(installer), f"--prefix={destination}"]


def acquire(cache: Path, manifest: dict[str, object]) -> None:
    _private_directory(cache)
    for item in _artifacts(manifest):
        destination = cache / item["filename"]
        if destination.is_file() and not destination.is_symlink() and _sha256(destination) == item["sha256"]:
            continue
        with tempfile.NamedTemporaryFile(dir=cache, delete=False) as temporary:
            temporary_path = Path(temporary.name)
            with urllib.request.urlopen(item["url"], timeout=60) as response:
                shutil.copyfileobj(response, temporary)
        try:
            if _sha256(temporary_path) != item["sha256"]:
                raise ValueError(f"toolchain digest mismatch: {item['filename']}")
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, destination)
        finally:
            temporary_path.unlink(missing_ok=True)


def materialize(cache: Path, destination: Path, manifest: dict[str, object]) -> None:
    if verify_cached_components(cache, manifest):
        raise ValueError("toolchain cache is incomplete or has digest drift")
    _private_directory(destination)
    components = manifest["components"]
    assert isinstance(components, dict)
    for name in ("rustc", "rust-std", "cargo"):
        item = components[name]
        assert isinstance(item, dict)
        with tempfile.TemporaryDirectory(dir=cache) as temporary:
            root = Path(temporary)
            with tarfile.open(cache / item["filename"], "r:xz") as archive:
                for member in archive.getmembers():
                    if member.issym() or member.islnk() or member.isdev() or Path(member.name).is_absolute() or ".." in Path(member.name).parts:
                        raise ValueError("unsafe Rust component archive")
                archive.extractall(root, filter="data")
            installers = list(root.rglob("install.sh"))
            if len(installers) != 1:
                raise ValueError(f"invalid Rust component layout: {name}")
            subprocess.run(installer_argv(installers[0], destination), check=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--acquire", action="store_true")
    parser.add_argument("--materialize", action="store_true")
    args = parser.parse_args(argv)
    manifest = _load_manifest(args.manifest)
    if args.acquire:
        acquire(args.cache, manifest)
    if args.materialize:
        materialize(args.cache, args.destination, manifest)
    invalid = verify_cached_components(args.cache, manifest)
    if invalid:
        print("invalid toolchain cache: " + ", ".join(invalid), file=sys.stderr)
        return 2
    print("nautilus Rust toolchain cache: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
