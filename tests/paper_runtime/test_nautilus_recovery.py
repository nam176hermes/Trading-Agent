from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from packages.engine_contracts import canonical_json_bytes
from packages.safety_evidence import CanonicalKillSwitchState
from services.paper_runtime.nautilus_reconciliation import (
    NautilusChildState,
    NautilusRecoveryEvidence,
)
from services.paper_runtime.nautilus_recovery import (
    NAUTILUS_RECOVERY_RECEIPT_SCHEMA,
    NautilusRecoveryStore,
    load_nautilus_recovery_receipt,
    recover_nautilus_paper_session,
    write_nautilus_recovery_receipt,
)
from services.paper_runtime.nautilus_checkpoint import ZERO_CHECKPOINT_SHA256

from test_nautilus_reconciliation import _evidence


def _runtime_evidence(record: object, *, child_running: bool) -> NautilusRecoveryEvidence:
    checkpoint = record.checkpoint  # type: ignore[attr-defined]
    cursor = checkpoint.last_accepted_command - (
        2 if checkpoint.state.value == "STOPPING" else 1
    )
    return NautilusRecoveryEvidence(
        session_id=checkpoint.session_id,
        engine_version="1.231.0",
        expected_engine_version="1.231.0",
        closure_digest=checkpoint.closure_digest,
        expected_closure_digest=checkpoint.closure_digest,
        source_commit="0123456789abcdef0123456789abcdef01234567",
        expected_source_commit="0123456789abcdef0123456789abcdef01234567",
        config_digest="f" * 64,
        expected_config_digest="f" * 64,
        child_state=(
            NautilusChildState.RUNNING if child_running else NautilusChildState.GONE
        ),
        current_child_identity=checkpoint.child_identity if child_running else None,
        checkpoint=record,
        ledger_last_sequence=checkpoint.last_emitted_event,
        ledger_last_event_digest=checkpoint.last_event_digest,
        ledger_event_prefix_sha256=checkpoint.event_prefix_sha256,
        portfolio_state_hash=checkpoint.portfolio_state_hash,
        target_schedule_cursor=cursor,
        expected_target_schedule_cursor=cursor,
        final_engine_observation_sha256=checkpoint.semantic_state_hash,
        child_outcome_proven=not child_running,
        kill_switch_state=CanonicalKillSwitchState.INACTIVE,
    )


def test_recovery_receipt_is_canonical_generation_bound_and_no_clobber(
    tmp_path: Path,
) -> None:
    path = tmp_path / "recovery.json"
    receipt = write_nautilus_recovery_receipt(
        path, _evidence(), command_chain_sha256="c" * 64
    )
    raw = path.read_bytes()
    document = json.loads(raw)

    assert raw == canonical_json_bytes(document) + b"\n"
    assert document["schema"] == NAUTILUS_RECOVERY_RECEIPT_SCHEMA
    assert document["verdict"] == "RESUME_EXACT_PREFIX"
    assert document["engine_version"] == "1.231.0"
    assert document["closure_digest"] == _evidence().closure_digest
    assert document["command_chain_sha256"] == "c" * 64
    assert document["authority_limits"] == {
        "live_authorized": False,
        "network_query_allowed": False,
        "production_authorized": False,
    }
    assert receipt.receipt_sha256 == hashlib.sha256(raw).hexdigest()
    assert load_nautilus_recovery_receipt(path, receipt.receipt_sha256) == receipt

    with pytest.raises(FileExistsError):
        write_nautilus_recovery_receipt(
            path, _evidence(), command_chain_sha256="c" * 64
        )
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
    write_nautilus_recovery_receipt(
        path, _evidence(), command_chain_sha256="c" * 64
    )
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
        write_nautilus_recovery_receipt(
            tmp_path / "missing" / "receipt.json",
            _evidence(),
            command_chain_sha256="c" * 64,
        )

    target = tmp_path / "target.json"
    target.write_bytes(b"foreign")
    link = tmp_path / "receipt.json"
    link.symlink_to(target)
    with pytest.raises(FileExistsError):
        write_nautilus_recovery_receipt(
            link, _evidence(), command_chain_sha256="c" * 64
        )
    assert target.read_bytes() == b"foreign"


def test_receipt_is_sealed_and_loader_rejects_mutable_or_linked_authority(
    tmp_path: Path,
) -> None:
    path = tmp_path / "recovery.json"
    write_nautilus_recovery_receipt(
        path, _evidence(), command_chain_sha256="c" * 64
    )

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
        write_nautilus_recovery_receipt(
            path, _evidence(), command_chain_sha256="c" * 64
        )
    assert not path.exists()
    assert tuple(tmp_path.iterdir()) == ()

    monkeypatch.setattr(recovery.os, "write", real_write)
    write_nautilus_recovery_receipt(
        path, _evidence(), command_chain_sha256="c" * 64
    )


def test_durable_store_replays_exact_command_prefix_into_a_fresh_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test_nautilus_session import _Child, _commands, _request, _safety, _session

    root = tmp_path / "store"
    root.mkdir(mode=0o700)
    store = NautilusRecoveryStore(root)
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
    second = session.execute(
        target, expected_checkpoint_sha256=first.checkpoint.checkpoint_sha256
    )
    first_child.abort()
    receipt_path = tmp_path / "recovery.json"
    receipt = write_nautilus_recovery_receipt(
        receipt_path,
        _runtime_evidence(second.checkpoint, child_running=False),
        command_chain_sha256=store.chain_sha256,
    )

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
        prior_session=session,
        receipt_path=receipt_path,
        expected_receipt_sha256=receipt.receipt_sha256,
    )
    completed = recovered.session.execute(
        stop,
        expected_checkpoint_sha256=recovered.checkpoint.checkpoint_sha256,
    )

    assert recovered.disposition == "RESUME_EXACT_PREFIX"
    assert completed.state == "STOPPED"
    assert len(store.steps()) == 3
    assert all(path.stat().st_mode & 0o777 == 0o400 for path in root.iterdir())


def test_recovery_refuses_to_fork_a_still_running_prior_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test_nautilus_session import _Child, _commands, _request, _safety, _session

    root = tmp_path / "store"
    root.mkdir(mode=0o700)
    store = NautilusRecoveryStore(root)
    from services.job_worker.engine_profiles import P1_REAL_BACKTEST_POLICY

    closure = P1_REAL_BACKTEST_POLICY.closure_sha256
    old_child = _Child(closure=closure)
    old_session, _capability, _client = _session(
        old_child,
        [_safety()],
        monkeypatch,
        authority_closure=closure,
        recovery_recorder=store,
    )
    start, target, _stop = _commands(_request())
    first = old_session.execute(
        start, expected_checkpoint_sha256=ZERO_CHECKPOINT_SHA256
    )
    second = old_session.execute(
        target, expected_checkpoint_sha256=first.checkpoint.checkpoint_sha256
    )
    fresh_child = _Child(closure=closure, identity="f" * 64)
    receipt_path = tmp_path / "recovery.json"
    receipt = write_nautilus_recovery_receipt(
        receipt_path,
        _runtime_evidence(second.checkpoint, child_running=True),
        command_chain_sha256=store.chain_sha256,
    )

    with pytest.raises(ValueError, match="prior child is still running"):
        recover_nautilus_paper_session(
            store,
            lambda recorder: _session(
                fresh_child,
                [_safety()],
                monkeypatch,
                authority_closure=closure,
                authority_identity="f" * 64,
                recovery_recorder=recorder,
            )[0],
            prior_session=old_session,
            receipt_path=receipt_path,
            expected_receipt_sha256=receipt.receipt_sha256,
        )

    assert old_child.calls == 2
    assert fresh_child.calls == 0


def test_recovery_refuses_a_receipt_bound_to_another_command_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test_nautilus_session import _Child, _commands, _request, _safety, _session

    root = tmp_path / "store"
    root.mkdir(mode=0o700)
    store = NautilusRecoveryStore(root)
    from services.job_worker.engine_profiles import P1_REAL_BACKTEST_POLICY

    closure = P1_REAL_BACKTEST_POLICY.closure_sha256
    old_child = _Child(closure=closure)
    old_session, _capability, _client = _session(
        old_child,
        [_safety()],
        monkeypatch,
        authority_closure=closure,
        recovery_recorder=store,
    )
    start, _target, _stop = _commands(_request())
    first = old_session.execute(
        start, expected_checkpoint_sha256=ZERO_CHECKPOINT_SHA256
    )
    old_child.abort()
    receipt_path = tmp_path / "recovery.json"
    receipt = write_nautilus_recovery_receipt(
        receipt_path,
        _runtime_evidence(first.checkpoint, child_running=False),
        command_chain_sha256="d" * 64,
    )
    fresh_child = _Child(closure=closure, identity="f" * 64)

    with pytest.raises(ValueError, match="does not match prior session"):
        recover_nautilus_paper_session(
            store,
            lambda recorder: _session(
                fresh_child,
                [_safety()],
                monkeypatch,
                authority_closure=closure,
                authority_identity="f" * 64,
                recovery_recorder=recorder,
            )[0],
            prior_session=old_session,
            receipt_path=receipt_path,
            expected_receipt_sha256=receipt.receipt_sha256,
        )

    assert fresh_child.calls == 0


def test_failed_replay_aborts_the_fresh_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test_nautilus_session import _Child, _commands, _request, _safety, _session

    root = tmp_path / "store"
    root.mkdir(mode=0o700)
    store = NautilusRecoveryStore(root)
    from services.job_worker.engine_profiles import P1_REAL_BACKTEST_POLICY

    closure = P1_REAL_BACKTEST_POLICY.closure_sha256
    old_child = _Child(closure=closure)
    old_session, _capability, _client = _session(
        old_child,
        [_safety()],
        monkeypatch,
        authority_closure=closure,
        recovery_recorder=store,
    )
    start, target, _stop = _commands(_request())
    first = old_session.execute(start, expected_checkpoint_sha256=ZERO_CHECKPOINT_SHA256)
    second = old_session.execute(
        target, expected_checkpoint_sha256=first.checkpoint.checkpoint_sha256
    )
    old_child.abort()
    receipt_path = tmp_path / "recovery.json"
    receipt = write_nautilus_recovery_receipt(
        receipt_path,
        _runtime_evidence(second.checkpoint, child_running=False),
        command_chain_sha256=store.chain_sha256,
    )
    safety = [_safety()]
    fresh_child = _Child(closure=closure, identity="f" * 64)
    exchange = fresh_child.exchange

    def engage_after_start(raw: bytes) -> bytes:
        response = exchange(raw)
        if fresh_child.calls == 1:
            safety.append(_safety(kill_switch=CanonicalKillSwitchState.ACTIVE))
        return response

    fresh_child.exchange = engage_after_start  # type: ignore[method-assign]

    with pytest.raises(Exception, match="exit-only stop"):
        recover_nautilus_paper_session(
            store,
            lambda recorder: _session(
                fresh_child,
                safety,
                monkeypatch,
                authority_closure=closure,
                authority_identity="f" * 64,
                recovery_recorder=recorder,
            )[0],
            prior_session=old_session,
            receipt_path=receipt_path,
            expected_receipt_sha256=receipt.receipt_sha256,
        )

    assert fresh_child.calls == 1
    assert fresh_child.aborted is True


def test_in_flight_command_blocks_replay_after_outcome_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test_nautilus_session import _Child, _commands, _request, _safety, _session

    store = NautilusRecoveryStore(tmp_path)
    from services.job_worker.engine_profiles import P1_REAL_BACKTEST_POLICY

    child = _Child(closure=P1_REAL_BACKTEST_POLICY.closure_sha256)
    session, _capability, _client = _session(
        child,
        [_safety()],
        monkeypatch,
        authority_closure=P1_REAL_BACKTEST_POLICY.closure_sha256,
        recovery_recorder=store,
    )
    start, target, _stop = _commands(_request())
    first = session.execute(start, expected_checkpoint_sha256=ZERO_CHECKPOINT_SHA256)
    real_record = store.record

    def fail_target_outcome(*args: object, **kwargs: object) -> None:
        raise OSError("injected outcome write failure")

    monkeypatch.setattr(store, "record", fail_target_outcome)
    with pytest.raises(Exception, match="paper session authority is inconsistent"):
        session.execute(
            target,
            expected_checkpoint_sha256=first.checkpoint.checkpoint_sha256,
        )
    monkeypatch.setattr(store, "record", real_record)

    with pytest.raises(ValueError, match="outcome is uncertain"):
        store.begin_replay()
    assert child.calls == 2
    assert child.aborted is True


def test_store_keeps_the_original_private_directory_generation_pinned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test_nautilus_session import _Child, _commands, _request, _safety, _session

    root = tmp_path / "store"
    root.mkdir(mode=0o700)
    store = NautilusRecoveryStore(root)
    from services.job_worker.engine_profiles import P1_REAL_BACKTEST_POLICY

    child = _Child(closure=P1_REAL_BACKTEST_POLICY.closure_sha256)
    session, _capability, _client = _session(
        child,
        [_safety()],
        monkeypatch,
        authority_closure=P1_REAL_BACKTEST_POLICY.closure_sha256,
        recovery_recorder=store,
    )
    start, _target, _stop = _commands(_request())
    session.execute(start, expected_checkpoint_sha256=ZERO_CHECKPOINT_SHA256)
    moved = tmp_path / "moved"
    root.rename(moved)
    replacement = tmp_path / "replacement"
    replacement.mkdir(mode=0o700)
    os.symlink(replacement, root)

    assert len(store.steps()) == 1


def test_store_ignores_only_its_exact_crash_staging_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test_nautilus_session import _Child, _commands, _request, _safety, _session

    store = NautilusRecoveryStore(tmp_path)
    from services.job_worker.engine_profiles import P1_REAL_BACKTEST_POLICY

    child = _Child(closure=P1_REAL_BACKTEST_POLICY.closure_sha256)
    session, _capability, _client = _session(
        child,
        [_safety()],
        monkeypatch,
        authority_closure=P1_REAL_BACKTEST_POLICY.closure_sha256,
        recovery_recorder=store,
    )
    start, _target, _stop = _commands(_request())
    session.execute(start, expected_checkpoint_sha256=ZERO_CHECKPOINT_SHA256)
    staging = tmp_path / (".step-00000002.json." + "a" * 32 + ".tmp")
    staging.write_bytes(b"partial")
    staging.chmod(0o400)

    assert len(store.steps()) == 1
    (tmp_path / ".foreign.tmp").write_bytes(b"foreign")
    with pytest.raises(ValueError, match="inventory"):
        store.steps()


def test_replay_blocks_changed_command_prefix_without_starting_next_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test_nautilus_session import _Child, _commands, _request, _safety, _session

    root = tmp_path / "store"
    root.mkdir(mode=0o700)
    store = NautilusRecoveryStore(root)
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
    completed_prefix = session.execute(
        target, expected_checkpoint_sha256=first.checkpoint.checkpoint_sha256
    )
    child.abort()
    second = root / "step-00000002.json"
    second.chmod(0o600)
    document = json.loads(second.read_bytes())
    document["command_sha256"] = "f" * 64
    second.write_bytes(canonical_json_bytes(document) + b"\n")
    second.chmod(0o400)
    fresh = _Child(closure=closure, identity="e" * 64)
    receipt_path = tmp_path / "recovery.json"
    receipt = write_nautilus_recovery_receipt(
        receipt_path,
        _runtime_evidence(completed_prefix.checkpoint, child_running=False),
        command_chain_sha256="d" * 64,
    )

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
            prior_session=session,
            receipt_path=receipt_path,
            expected_receipt_sha256=receipt.receipt_sha256,
        )

    assert fresh.calls == 0
