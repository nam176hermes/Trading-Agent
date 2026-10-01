"""Sealed stage hashing remains bounded and detects replacement during reading."""

import hashlib
import os
from pathlib import Path
import tracemalloc

import pytest

from packages.runtime_release import staging_v2


def test_large_stage_file_is_hashed_without_retaining_its_bytes(tmp_path: Path):
    stage = tmp_path / "stage"
    stage.mkdir()
    path = stage / "large.bin"
    block = b"stage-file\0" * 65536
    with path.open("wb") as stream:
        for _ in range(24):
            stream.write(block)
    expected = hashlib.sha256(block * 24).hexdigest()
    path.chmod(0o444)
    stage.chmod(0o555)
    tracemalloc.start()
    try:
        entries = staging_v2.walk_sealed_stage(stage)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        stage.chmod(0o700)
    assert entries == [{"mode": "0444", "path": "large.bin", "sha256": expected,
                        "size": len(block) * 24, "type": "file"}]
    assert peak < 3 * 1024 * 1024, "stage hashing retained a complete file"


def test_stage_hash_rejects_path_replacement_during_read(tmp_path: Path, monkeypatch):
    stage = tmp_path / "stage"
    stage.mkdir()
    path = stage / "file"
    replacement = tmp_path / "replacement"
    path.write_bytes(b"old")
    replacement.write_bytes(b"new")
    path.chmod(0o444)
    replacement.chmod(0o444)
    stage.chmod(0o555)
    read = os.read
    replaced = False

    def replace_after_read(descriptor, size):
        nonlocal replaced
        raw = read(descriptor, size)
        if not replaced:
            stage.chmod(0o700)
            replacement.replace(path)
            stage.chmod(0o555)
            replaced = True
        return raw

    monkeypatch.setattr(os, "read", replace_after_read)
    try:
        with pytest.raises(ValueError):
            staging_v2.walk_sealed_stage(stage)
    finally:
        stage.chmod(0o700)
