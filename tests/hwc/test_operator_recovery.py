from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from packages.operator_control.hashing import reason_sha256
from services.operator_control.journal import CommandJournal
from services.operator_control.state_store import (
    OperatorStateStore,
    RecoveryError,
    classify_recovery,
)
from tests.hwc.fixtures.operator_state import (
    intent_for,
    provision_operator_state,
    write_private,
)


def test_recovery_classifies_prior_desired_and_ambiguous_states(tmp_path: Path) -> None:
    paths = provision_operator_state(tmp_path)
    store = OperatorStateStore(paths)
    prior = store.read_state()
    desired_digest = hashlib.sha256(b"paper\n").hexdigest()
    intent = intent_for(prior, desired_file_sha256=desired_digest)
    assert classify_recovery(intent, prior, tombstone_sha256=None) == "RETRY"
    write_private(paths.mode_path, b"paper\n")
    assert (
        classify_recovery(intent, store.read_state(), tombstone_sha256=None)
        == "RECOVERED_MODE_REPLACEMENT"
    )
    write_private(paths.mode_path, b"live\n")
    with pytest.raises(RecoveryError, match="COMMAND_OUTCOME_UNKNOWN"):
        classify_recovery(intent, store.read_state(), tombstone_sha256=None)


def test_clear_recovery_is_bound_to_tombstone_and_reactivation_is_unknown(
    tmp_path: Path,
) -> None:
    paths = provision_operator_state(tmp_path)
    store = OperatorStateStore(paths)
    raw = b"2026-09-02T12:00:00Z: incident\n"
    write_private(paths.kill_switch_path, raw)
    active = store.read_state()
    intent = intent_for(
        active,
        desired_state="KILL_SWITCH_INACTIVE",
        desired_file_sha256=active.kill_switch_file_sha256,
    )
    cleared = store.clear_kill_switch(intent)
    assert (
        classify_recovery(
            intent, cleared.state, tombstone_sha256=cleared.tombstone_sha256
        )
        == "RECOVERED_KILL_SWITCH_CLEAR"
    )
    write_private(paths.kill_switch_path, raw)
    with pytest.raises(RecoveryError, match="COMMAND_OUTCOME_UNKNOWN"):
        classify_recovery(
            intent, store.read_state(), tombstone_sha256=cleared.tombstone_sha256
        )
    with pytest.raises(RecoveryError, match="COMMAND_OUTCOME_UNKNOWN"):
        classify_recovery(intent, cleared.state, tombstone_sha256="f" * 64)


@pytest.mark.parametrize(
    "failpoint",
    (
        "AFTER_INTENT_FSYNC",
        "BEFORE_STATE_APPLY",
        "AFTER_STATE_APPLY",
        "AFTER_APPLIED_FSYNC",
        "BEFORE_RECEIPT_FSYNC",
        "AFTER_RECEIPT_FSYNC",
    ),
)
def test_every_failpoint_leaves_durable_classifiable_evidence(
    tmp_path: Path, failpoint: str
) -> None:
    root = tmp_path / failpoint.lower()
    command = [
        sys.executable,
        "-m",
        "tests.hwc.fixtures.operator_state",
        "--crash-at",
        failpoint,
        "--root",
        str(root),
    ]
    result = subprocess.run(command, cwd=Path(__file__).parents[2], check=False)
    assert result.returncode == 77
    paths = provisioned_paths(root)
    snapshot = CommandJournal(paths).load(hashlib.sha256(b"idem.1").hexdigest())
    state = OperatorStateStore(paths).read_state()
    assert snapshot.intent is not None
    if failpoint in {"AFTER_INTENT_FSYNC", "BEFORE_STATE_APPLY"}:
        assert state.requested_mode == "UNKNOWN"
        assert snapshot.applied is None and snapshot.receipt is None
    elif failpoint == "AFTER_STATE_APPLY":
        assert state.requested_mode == "PAPER" and snapshot.applied is None
    elif failpoint in {"AFTER_APPLIED_FSYNC", "BEFORE_RECEIPT_FSYNC"}:
        assert snapshot.applied is not None and snapshot.receipt is None
    else:
        assert snapshot.receipt is not None


def provisioned_paths(parent: Path):
    from services.operator_control.state_store import OperatorStatePaths

    data = parent / "operator-data"
    command = data / ".operator-commands"
    return OperatorStatePaths(data, command, data / ".mode", data / ".kill_switch")
