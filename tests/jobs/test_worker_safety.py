from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

from services.job_worker.errors import SafetyBlockedError
from services.job_worker.safety import (
    APPROVED_DATA_ROOT,
    KillSwitchState,
    SafetyMode,
    SafetyProvider,
    SafetySnapshot,
    assert_safe,
    validate_data_root,
)


@pytest.fixture
def tmp_path():
    path = Path(tempfile.mkdtemp(prefix="task7-safety-", dir="/home/thenam176/.cache"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _snapshot(**changes) -> SafetySnapshot:
    values = dict(requested_mode=SafetyMode.PAPER, effective_mode=SafetyMode.PAPER, live_execution_enabled=False, live_trading_approved=False, kill_switch_state=KillSwitchState.INACTIVE)
    values.update(changes)
    return SafetySnapshot(**values)


def _provider(tmp_path: Path, monkeypatch, source: dict[str, str], *, mode="paper", kill="2026-07-12T00:00:00Z: test"):
    root = tmp_path / "data"
    root.mkdir(mode=0o700)
    if mode is not None:
        (root / ".mode").write_text(mode, encoding="utf-8")
        (root / ".mode").chmod(0o600)
    if kill is not None:
        (root / ".kill_switch").write_text(kill, encoding="utf-8")
        (root / ".kill_switch").chmod(0o600)
    monkeypatch.setattr("services.job_worker.safety.APPROVED_DATA_ROOT", root)
    return SafetyProvider(validate_data_root(), source=source)


def test_phase1_canonical_data_and_kill_switch_paths_are_fixed():
    assert APPROVED_DATA_ROOT == Path("/home/thenam176/.hermes/crypto-research")


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"requested_mode": SafetyMode.UNKNOWN}, "SAFETY_REQUESTED_MODE_UNKNOWN"),
        ({"requested_mode": SafetyMode.LIVE}, "SAFETY_REQUESTED_MODE_NOT_PAPER"),
        ({"effective_mode": SafetyMode.UNKNOWN}, "SAFETY_EFFECTIVE_MODE_UNKNOWN"),
        ({"live_execution_enabled": None}, "SAFETY_LIVE_EXECUTION_GATE_UNKNOWN"),
        ({"live_execution_enabled": True}, "SAFETY_LIVE_EXECUTION_GATE_ENABLED"),
        ({"live_trading_approved": None}, "SAFETY_LIVE_APPROVAL_GATE_UNKNOWN"),
        ({"live_trading_approved": True}, "SAFETY_LIVE_APPROVAL_GATE_ENABLED"),
        ({"kill_switch_state": KillSwitchState.UNKNOWN}, "SAFETY_KILL_SWITCH_UNKNOWN"),
        ({"kill_switch_state": KillSwitchState.ACTIVE}, "SAFETY_KILL_SWITCH_ACTIVE"),
    ],
)
def test_every_missing_or_noncanonical_safety_evidence_blocks(changes, reason):
    with pytest.raises(SafetyBlockedError) as raised:
        assert_safe(_snapshot(**changes))
    assert raised.value.reason_code == reason


@pytest.mark.parametrize(
    ("missing", "field", "reason"),
    [
        ("mode", "requested_mode", "SAFETY_REQUESTED_MODE_UNKNOWN"),
        ("execution", "live_execution_enabled", "SAFETY_LIVE_EXECUTION_GATE_UNKNOWN"),
        ("approval", "live_trading_approved", "SAFETY_LIVE_APPROVAL_GATE_UNKNOWN"),
    ],
)
def test_provider_never_defaults_missing_evidence_to_safe(tmp_path, monkeypatch, missing, field, reason):
    source = {"LIVE_EXECUTION_ENABLED": "false", "LIVE_TRADING_APPROVED": "false"}
    if missing == "execution": source.pop("LIVE_EXECUTION_ENABLED")
    if missing == "approval": source.pop("LIVE_TRADING_APPROVED")
    provider = _provider(tmp_path, monkeypatch, source, mode=None if missing == "mode" else "paper")
    snapshot = provider.snapshot()
    expected = SafetyMode.UNKNOWN if field == "requested_mode" else None
    assert getattr(snapshot, field) is expected
    with pytest.raises(SafetyBlockedError) as raised:
        assert_safe(snapshot)
    assert raised.value.reason_code == reason


def test_only_complete_explicit_paper_evidence_passes(tmp_path, monkeypatch):
    provider = _provider(tmp_path, monkeypatch, {"LIVE_EXECUTION_ENABLED": "false", "LIVE_TRADING_APPROVED": "false"}, kill="2026-07-12T00:00:00Z: drill active")
    snapshot = provider.snapshot()
    assert snapshot.kill_switch_state is KillSwitchState.ACTIVE
    with pytest.raises(SafetyBlockedError): assert_safe(snapshot)
    (tmp_path / "data/.kill_switch").unlink()
    snapshot = provider.snapshot()
    assert snapshot.kill_switch_state is KillSwitchState.INACTIVE
    assert_safe(snapshot)


@pytest.mark.parametrize("content", ["INACTIVE", "2026-07-12T00:00:00Z: cleared\nINACTIVE", "bad: reason", "2026-07-12T00:00:00Z:"])
def test_worker_uses_canonical_kill_switch_format_only(tmp_path, monkeypatch, content):
    provider = _provider(tmp_path, monkeypatch, {"LIVE_EXECUTION_ENABLED": "false", "LIVE_TRADING_APPROVED": "false"}, kill=content)
    assert provider.snapshot().kill_switch_state is KillSwitchState.UNKNOWN


def test_present_kill_switch_with_unsafe_mode_is_unknown(tmp_path, monkeypatch):
    provider = _provider(tmp_path, monkeypatch, {"LIVE_EXECUTION_ENABLED": "false", "LIVE_TRADING_APPROVED": "false"})
    (tmp_path / "data/.kill_switch").chmod(0o644)
    assert provider.snapshot().kill_switch_state is KillSwitchState.UNKNOWN


def test_kill_switch_path_override_cannot_redirect_evidence(tmp_path, monkeypatch):
    provider = _provider(tmp_path, monkeypatch, {"LIVE_EXECUTION_ENABLED": "false", "LIVE_TRADING_APPROVED": "false", "TRADING_KILL_SWITCH_PATH": str(tmp_path / "attacker")})
    with pytest.raises(SafetyBlockedError) as raised:
        provider.snapshot()
    assert raised.value.reason_code == "SAFETY_KILL_SWITCH_PATH_OVERRIDE"


@pytest.mark.parametrize("target", ["root", "mode", "kill"])
def test_symlinked_safety_evidence_is_rejected(tmp_path, monkeypatch, target):
    provider = _provider(tmp_path, monkeypatch, {"LIVE_EXECUTION_ENABLED": "false", "LIVE_TRADING_APPROVED": "false"})
    root = tmp_path / "data"
    if target == "root":
        real = tmp_path / "real"
        root.rename(real)
        root.symlink_to(real, target_is_directory=True)
        with pytest.raises(SafetyBlockedError) as raised:
            validate_data_root()
        assert raised.value.reason_code == "SAFETY_DATA_ROOT_SYMLINK"
        return
    path = root / (".mode" if target == "mode" else ".kill_switch")
    real = root / (path.name + ".real")
    path.rename(real)
    path.symlink_to(real)
    snapshot = provider.snapshot()
    field = snapshot.requested_mode if target == "mode" else snapshot.kill_switch_state
    assert field in {SafetyMode.UNKNOWN, KillSwitchState.UNKNOWN}


def test_data_root_rejects_override_traversal_wrong_owner_and_unsafe_mode(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir(mode=0o700)
    monkeypatch.setattr("services.job_worker.safety.APPROVED_DATA_ROOT", root)
    with pytest.raises(SafetyBlockedError): validate_data_root(root / ".." / "data")
    root.chmod(0o777)
    with pytest.raises(SafetyBlockedError) as raised: validate_data_root()
    assert raised.value.reason_code == "SAFETY_DATA_ROOT_MODE_UNSAFE"
    root.chmod(0o700)
    uid = os.geteuid()
    monkeypatch.setattr("services.job_worker.safety.os.geteuid", lambda: uid + 1)
    with pytest.raises(SafetyBlockedError) as raised: validate_data_root()
    assert raised.value.reason_code == "SAFETY_DATA_ROOT_OWNER_UNSAFE"
