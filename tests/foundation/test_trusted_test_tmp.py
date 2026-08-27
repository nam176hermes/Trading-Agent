from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

import pytest

from scripts.trusted_test_tmp import TrustedTestTmpError, prepare_trusted_test_tmp


def _private_test_root() -> Path:
    root = Path.home() / ".cache" / "trading-agent" / "trusted-test-tmp-tests"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    return root


def test_explicit_trusted_root_creates_isolated_component_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(tempfile.mkdtemp(prefix="explicit-", dir=_private_test_root()))
    root.chmod(0o700)
    monkeypatch.setenv("TRADING_TEST_TMP_ROOT", str(root))

    session = prepare_trusted_test_tmp("root-pytest")
    try:
        assert session.root == root.resolve()
        assert session.path.parent == root.resolve()
        assert stat.S_IMODE(session.path.stat().st_mode) == 0o700
        assert {os.environ[name] for name in ("TMPDIR", "TEMP", "TMP")} == {
            str(session.path)
        }
    finally:
        session.cleanup()
        root.rmdir()


def test_explicit_root_rejects_world_writable_ancestor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRADING_TEST_TMP_ROOT", "/tmp")

    with pytest.raises(TrustedTestTmpError, match="writable ancestor"):
        prepare_trusted_test_tmp("root-pytest")


def test_unsafe_ambient_tmpdir_falls_back_to_private_user_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TRADING_TEST_TMP_ROOT", raising=False)
    monkeypatch.setenv("TMPDIR", "/tmp")

    session = prepare_trusted_test_tmp("root-pytest")
    try:
        assert session.root == (
            Path.home() / ".cache" / "trading-agent" / "test-tmp"
        ).resolve()
        assert session.path.parent == session.root
    finally:
        session.cleanup()


def test_cleanup_refuses_replaced_session_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(tempfile.mkdtemp(prefix="replacement-", dir=_private_test_root()))
    root.chmod(0o700)
    monkeypatch.setenv("TRADING_TEST_TMP_ROOT", str(root))
    session = prepare_trusted_test_tmp("root-pytest")
    replacement = root / "replacement"
    replacement.mkdir(mode=0o700)
    session.path.rmdir()
    replacement.rename(session.path)

    with pytest.raises(TrustedTestTmpError, match="identity changed"):
        session.cleanup()

    session.path.rmdir()
    root.rmdir()


def test_cleanup_removes_read_only_nested_directories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(tempfile.mkdtemp(prefix="readonly-", dir=_private_test_root()))
    root.chmod(0o700)
    monkeypatch.setenv("TRADING_TEST_TMP_ROOT", str(root))
    session = prepare_trusted_test_tmp("root-pytest")
    nested = session.path / "locked" / "nested"
    nested.mkdir(parents=True)
    artifact = nested / "runtime.json"
    artifact.write_text("{}", encoding="utf-8")
    artifact.chmod(0o400)
    nested.chmod(0o500)
    nested.parent.chmod(0o500)

    session.cleanup()

    assert not session.path.exists()
    root.rmdir()
