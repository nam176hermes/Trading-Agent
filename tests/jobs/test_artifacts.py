from __future__ import annotations

import hashlib
import io
import os
import stat
import tempfile
from pathlib import Path

import pytest

from services.job_worker.artifacts import MAX_STREAM_BYTES, ArtifactWriter


def test_artifact_writer_enforces_permissions_and_relative_metadata() -> None:
    # The repository's pytest temp root is DrvFS, which does not implement
    # Unix permission bits. Artifacts are Linux-hosted, so exercise a real
    # POSIX filesystem for this permission invariant.
    with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
        tmp_path = Path(temporary)
        previous = os.umask(0)
        try:
            writer = ArtifactWriter(tmp_path / "artifacts")
            metadata = writer.capture_stream("job-1", "attempt-1", "stdout", io.BytesIO(b"safe"))
        finally:
            os.umask(previous)

        target = tmp_path / "artifacts" / metadata.relative_ref
        assert stat.S_IMODE((tmp_path / "artifacts").stat().st_mode) == 0o700
        assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        assert not metadata.relative_ref.startswith("/")
        assert target.read_bytes() == b"safe"


def test_artifact_writer_caps_storage_but_drains_and_hashes_every_observed_byte(tmp_path) -> None:
    payload = b"x" * (MAX_STREAM_BYTES + 8193)
    stream = io.BytesIO(payload)

    metadata = ArtifactWriter(tmp_path / "artifacts").capture_stream(
        "job-1", "attempt-1", "stderr", stream
    )

    target = tmp_path / "artifacts" / metadata.relative_ref
    assert stream.tell() == len(payload)
    assert target.stat().st_size == MAX_STREAM_BYTES
    assert metadata.size_bytes == len(payload)
    assert metadata.sha256 == hashlib.sha256(payload).hexdigest()
    assert metadata.truncated is True
    assert "x" not in repr(metadata)


@pytest.mark.parametrize("identifier", ["../escape", "/absolute", "a/b", "", "."])
def test_artifact_writer_rejects_unsafe_identifiers(tmp_path, identifier) -> None:
    writer = ArtifactWriter(tmp_path / "artifacts")
    with pytest.raises(ValueError):
        writer.capture_stream(identifier, "attempt", "stdout", io.BytesIO())


def test_artifact_writer_rejects_symlinked_root(tmp_path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)

    with pytest.raises(ValueError):
        ArtifactWriter(linked)


def test_artifact_writer_rejects_symlinked_job_component(tmp_path) -> None:
    root = tmp_path / "artifacts"
    writer = ArtifactWriter(root)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "job-1").symlink_to(outside, target_is_directory=True)

    with pytest.raises(OSError):
        writer.capture_stream("job-1", "attempt-1", "stdout", io.BytesIO(b"unsafe"))

    assert list(outside.iterdir()) == []


def test_artifact_writer_rechecks_root_chain_after_constructor(tmp_path) -> None:
    root = tmp_path / "artifacts"
    writer = ArtifactWriter(root)
    original = tmp_path / "original"
    root.rename(original)
    root.symlink_to(original, target_is_directory=True)

    with pytest.raises(OSError):
        writer.capture_stream("job-1", "attempt-1", "stdout", io.BytesIO(b"unsafe"))
