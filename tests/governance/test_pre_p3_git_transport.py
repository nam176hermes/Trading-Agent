"""Fresh Git source reads preserve binary bytes without pipe wakeup amplification."""
from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from packages.pre_p3_provenance import ProvenanceError, _git
from tests.governance.test_pre_p3_provenance import _repo


def test_large_git_blob_preserves_bytes_with_bounded_pipe_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _repo(tmp_path)
    blob = bytes(range(256)) * 8192
    oid = _git(root, 'hash-object', '-w', '--stdin', input_bytes=blob).strip()
    readiness_calls = []

    class CountSelector(subprocess._PopenSelector):
        def select(self, timeout=None):
            readiness_calls.append(True)
            return super().select(timeout)

    monkeypatch.setattr(subprocess, '_PopenSelector', CountSelector)
    result = _git(root, 'cat-file', '--batch', input_bytes=oid+b'\n')

    assert result == oid+b' blob '+str(len(blob)).encode()+b'\n'+blob+b'\n'
    # A pipe requires at least 64 reads for this blob (at most 32KiB/read).
    # Readiness dispatch must remain bounded by the tiny stdin/stderr exchange.
    assert len(readiness_calls) <= 16


def test_git_batch_preserves_repeated_binary_blobs_and_large_stdin(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    blob = bytes(range(256)) * 32
    oid = _git(root, 'hash-object', '-w', '--stdin', input_bytes=blob).strip()
    copies = 512
    expected = oid+b' blob '+str(len(blob)).encode()+b'\n'+blob+b'\n'

    assert _git(root, 'cat-file', '--batch', input_bytes=(oid+b'\n')*copies) == expected*copies


def test_git_transport_still_rejects_command_failure(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    with pytest.raises(ProvenanceError, match='git source authority is unavailable'):
        _git(root, 'rev-parse', 'missing-fixture-revision^{commit}')
