from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from packages.engine_contracts import canonical_json_bytes
from services.paper_runtime.nautilus_recovery import (
    NAUTILUS_RECOVERY_RECEIPT_SCHEMA,
    NautilusRecoveryStore,
    load_nautilus_recovery_receipt,
    recover_nautilus_paper_session,
    write_nautilus_recovery_receipt,
)
from services.paper_runtime.nautilus_checkpoint import ZERO_CHECKPOINT_SHA256

from test_nautilus_reconciliation import _evidence


def test_recovery_receipt_is_canonical_generation_bound_and_no_clobber(
    tmp_path: Path,
) -> None:
    path = tmp_path / "recovery.json"
    receipt = write_nautilus_recovery_receipt(path, _evidence())
    raw = path.read_bytes()
    document = json.loads(raw)

    assert raw == canonical_json_bytes(document) + b"\n"
    assert document["schema"] == NAUTILUS_RECOVERY_RECEIPT_SCHEMA
    assert document["verdict"] == "RECONCILIATION_REQUIRED"
    assert document["engine_version"] == "1.231.0"
    assert document["closure_digest"] == _evidence().closure_digest
    assert document["authority_limits"] == {
        "live_authorized": False,
        "network_query_allowed": False,
        "production_authorized": False,
    }
    assert receipt.receipt_sha256 == hashlib.sha256(raw).hexdigest()
    assert load_nautilus_recovery_receipt(path, receipt.receipt_sha256) == receipt

    with pytest.raises(FileExistsError):
        write_nautilus_recovery_receipt(path, _evidence())
    assert path.read_bytes() == raw


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(schema="wrong"),
        lambda value: value.update(verdict="START_NEW"),
        lambda value: value.update(engine_version="1.227.0"),
        lambda value: value.update(closure_digest="f" * 64),
        lambda value: value.update(evidence_sha256="F" * 64),
        lambda value: value.update(extra=True),
        lambda value: value["authority_limits"].update(network_query_allowed=True),
    ],
)
def test_loader_rejects_changed_or_moving_recovery_authority(
    tmp_path: Path, mutation: object
) -> None:
    path = tmp_path / "recovery.json"
    write_nautilus_recovery_receipt(path, _evidence())
    document = json.loads(path.read_bytes())
    mutation(document)  # type: ignore[operator]
    path.chmod(0o600)
    path.write_bytes(canonical_json_bytes(document) + b"\n")
    path.chmod(0o400)

    with pytest.raises(ValueError):
        load_nautilus_recovery_receipt(
            path, hashlib.sha256(path.read_bytes()).hexdigest()
        )


def test_receipt_path_must_be_a_new_regular_file_in_an_existing_directory(
    tmp_path: Path,
) -> None:
    with pytest.raises(FileNotFoundError):
        write_nautilus_recovery_receipt(tmp_path / "missing" / "receipt.json", _evidence())

    target = tmp_path / "target.json"
    target.write_bytes(b"foreign")
    link = tmp_path / "receipt.json"
    link.symlink_to(target)
    with pytest.raises(FileExistsError):
        write_nautilus_recovery_receipt(link, _evidence())
    assert target.read_bytes() == b"foreign"


def test_receipt_is_sealed_and_loader_rejects_mutable_or_linked_authority(
    tmp_path: Path,
) -> None:
    path = tmp_path / "recovery.json"
    write_nautilus_recovery_receipt(path, _evidence())

    assert path.stat().st_mode & 0o777 == 0o400
    path.chmod(0o600)
    with pytest.raises(ValueError, match="sealed"):
        load_nautilus_recovery_receipt(path, "0" * 64)


def test_failed_receipt_write_never_poisons_final_no_clobber_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import services.paper_runtime.nautilus_recovery as recovery

    path = tmp_path / "recovery.json"
    real_write = recovery.os.write
    calls = 0

    def fail_after_short_write(descriptor: int, raw: object) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            return real_write(descriptor, bytes(raw)[:17])
        raise OSError("injected write failure")

    monkeypatch.setattr(recovery.os, "write", fail_after_short_write)
    with pytest.raises(OSError, match="injected"):
        write_nautilus_recovery_receipt(path, _evidence())
    assert not path.exists()
    assert tuple(tmp_path.iterdir()) == ()

    monkeypatch.setattr(recovery.os, "write", real_write)
    write_nautilus_recovery_receipt(path, _evidence())


def test_durable_store_replays_exact_command_prefix_into_a_fresh_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test_nautilus_session import _Child, _commands, _request, _safety, _session

    store = NautilusRecoveryStore(tmp_path)
    from services.job_worker.engine_profiles import P1_REAL_BACKTEST_POLICY

    closure = P1_REAL_BACKTEST_POLICY.closure_sha256
    first_child = _Child(closure=closure)
    session, _capability, _client = _session(
        first_child,
        [_safety()],
        monkeypatch,
        authority_closure=closure,
        recovery_recorder=store,
    )
    start, target, stop = _commands(_request())
    first = session.execute(start, expected_checkpoint_sha256=ZERO_CHECKPOINT_SHA256)
    session.execute(target, expected_checkpoint_sha256=first.checkpoint.checkpoint_sha256)
    first_child.abort()

    fresh_child = _Child(closure=closure, identity="f" * 64)
    recovered = recover_nautilus_paper_session(
        store,
        lambda recorder: _session(
            fresh_child,
            [_safety()],
            monkeypatch,
            authority_closure=closure,
            authority_identity="f" * 64,
            recovery_recorder=recorder,
        )[0],
    )
    completed = recovered.session.execute(
        stop,
        expected_checkpoint_sha256=recovered.checkpoint.checkpoint_sha256,
    )

    assert recovered.disposition == "RESUME_EXACT_PREFIX"
    assert completed.state == "STOPPED"
    assert len(store.steps()) == 3
    assert all(path.stat().st_mode & 0o777 == 0o400 for path in tmp_path.iterdir())


def test_replay_blocks_changed_command_prefix_without_starting_next_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test_nautilus_session import _Child, _commands, _request, _safety, _session

    store = NautilusRecoveryStore(tmp_path)
    from services.job_worker.engine_profiles import P1_REAL_BACKTEST_POLICY

    closure = P1_REAL_BACKTEST_POLICY.closure_sha256
    child = _Child(closure=closure)
    session, _capability, _client = _session(
        child,
        [_safety()],
        monkeypatch,
        authority_closure=closure,
        recovery_recorder=store,
    )
    start, target, _stop = _commands(_request())
    first = session.execute(start, expected_checkpoint_sha256=ZERO_CHECKPOINT_SHA256)
    session.execute(target, expected_checkpoint_sha256=first.checkpoint.checkpoint_sha256)
    child.abort()
    second = sorted(tmp_path.iterdir())[1]
    second.chmod(0o600)
    document = json.loads(second.read_bytes())
    document["command_sha256"] = "f" * 64
    second.write_bytes(canonical_json_bytes(document) + b"\n")
    second.chmod(0o400)
    fresh = _Child(closure=closure, identity="e" * 64)

    with pytest.raises(ValueError, match="durable recovery"):
        recover_nautilus_paper_session(
            store,
            lambda recorder: _session(
                fresh,
                [_safety()],
                monkeypatch,
                authority_closure=closure,
                authority_identity="e" * 64,
                recovery_recorder=recorder,
            )[0],
        )

    assert fresh.calls == 0
