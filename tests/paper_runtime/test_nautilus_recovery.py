from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from packages.engine_contracts import canonical_json_bytes
from packages.nautilus_runtime_contracts.paper import parse_paper_command_frame
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
from services.paper_runtime.nautilus_session import NautilusSessionRejected

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
    stale_path = tmp_path / "stale-recovery.json"
    stale_receipt = write_nautilus_recovery_receipt(
        stale_path,
        _runtime_evidence(first.checkpoint, child_running=False),
        command_chain_sha256=store.chain_sha256,
    )
    stale_child = _Child(closure=closure, identity="e" * 64)
    with pytest.raises(ValueError, match="does not match prior session"):
        recover_nautilus_paper_session(
            store,
            lambda recorder: _session(
                stale_child,
                [_safety()],
                monkeypatch,
                authority_closure=closure,
                authority_identity="e" * 64,
                recovery_recorder=recorder,
            )[0],
            prior_session=session,
            receipt_path=stale_path,
            expected_receipt_sha256=stale_receipt.receipt_sha256,
        )
    assert stale_child.calls == 0
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


@pytest.mark.parametrize(
    "boundary",
    (
        "target_acceptance",
        "order_submit",
        "fill",
        "checkpoint_persist",
        "event_persist",
        "stop",
    ),
)
def test_every_execution_crash_boundary_blocks_duplicate_exposure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    from test_nautilus_session import _Child, _commands, _request, _safety, _session

    root = tmp_path / "store"
    root.mkdir(mode=0o700)
    store = NautilusRecoveryStore(root)
    from services.job_worker.engine_profiles import P1_REAL_BACKTEST_POLICY

    closure = P1_REAL_BACKTEST_POLICY.closure_sha256
    child = _Child(closure=closure)
    counts = {"targets": 0, "orders": 0, "fills": 0}
    exchange = child.exchange

    def fail_at_boundary(raw: bytes) -> bytes:
        command = parse_paper_command_frame(raw).command.command_type
        if command == "SubmitTargetPortfolio":
            counts["targets"] += 1
            if boundary == "target_acceptance":
                raise RuntimeError("crash after target acceptance")
            counts["orders"] += 1
            if boundary == "order_submit":
                raise RuntimeError("crash after order submit")
            counts["fills"] += 1
            if boundary == "fill":
                raise RuntimeError("crash after fill")
        return exchange(raw)

    child.exchange = fail_at_boundary  # type: ignore[method-assign]
    if boundary == "stop":
        child.close = lambda: 1  # type: ignore[method-assign]
    session, _capability, _client = _session(
        child,
        [_safety()],
        monkeypatch,
        authority_closure=closure,
        recovery_recorder=store,
    )
    start, target, stop = _commands(_request())
    first = session.execute(start, expected_checkpoint_sha256=ZERO_CHECKPOINT_SHA256)
    command = target
    prior_sha256 = first.checkpoint.checkpoint_sha256
    if boundary == "checkpoint_persist":
        advance = session._checkpoints.advance

        def fail_checkpoint(*args: object, **kwargs: object) -> object:
            raise OSError("crash before checkpoint persist")

        monkeypatch.setattr(session._checkpoints, "advance", fail_checkpoint)
    elif boundary in {"event_persist", "stop"}:
        second = session.execute(target, expected_checkpoint_sha256=prior_sha256)
        command = stop
        prior_sha256 = second.checkpoint.checkpoint_sha256
        if boundary == "event_persist":
            monkeypatch.setattr(
                session._ledger,
                "ingest",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    OSError("crash before event persist")
                ),
            )
    with pytest.raises(NautilusSessionRejected, match="authority is inconsistent"):
        session.execute(command, expected_checkpoint_sha256=prior_sha256)
    if boundary == "event_persist":
        child.abort()
    if boundary == "checkpoint_persist":
        monkeypatch.setattr(session._checkpoints, "advance", advance)

    terminal = store.steps()[-1]
    receipt_path = tmp_path / "recovery.json"
    receipt = write_nautilus_recovery_receipt(
        receipt_path,
        _runtime_evidence(terminal.checkpoint, child_running=False),
        command_chain_sha256=terminal.step_sha256,
    )
    fresh = _Child(closure=closure, identity="f" * 64)
    before = dict(counts)
    with pytest.raises(ValueError, match="outcome is uncertain"):
        recover_nautilus_paper_session(
            store,
            lambda recorder: _session(
                fresh,
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

    assert counts == before
    assert fresh.calls == 0
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


# PR02: replay bookkeeping is always released when recovery fails, without losing the primary failure.
def _prepared_recovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from test_nautilus_session import _Child, _commands, _request, _safety, _session

    root = tmp_path / "store"
    root.mkdir(mode=0o700)
    store = NautilusRecoveryStore(root)
    from services.job_worker.engine_profiles import P1_REAL_BACKTEST_POLICY

    closure = P1_REAL_BACKTEST_POLICY.closure_sha256
    old_child = _Child(closure=closure)
    old_session, _capability, _client = _session(
        old_child, [_safety()], monkeypatch, authority_closure=closure, recovery_recorder=store,
    )
    start, target, _stop = _commands(_request())
    first = old_session.execute(start, expected_checkpoint_sha256=ZERO_CHECKPOINT_SHA256)
    second = old_session.execute(target, expected_checkpoint_sha256=first.checkpoint.checkpoint_sha256)
    old_child.abort()                                        # the prior child is proven dead
    receipt_path = tmp_path / "recovery.json"
    receipt = write_nautilus_recovery_receipt(
        receipt_path, _runtime_evidence(second.checkpoint, child_running=False),
        command_chain_sha256=store.chain_sha256,
    )

    def fresh(identity: str = "f" * 64, safety=None):
        child = _Child(closure=closure, identity=identity)
        return child, (lambda recorder: _session(
            child, safety if safety is not None else [_safety()], monkeypatch, authority_closure=closure,
            authority_identity=identity, recovery_recorder=recorder)[0])

    def recover(factory, on_store=None):
        return recover_nautilus_paper_session(
            on_store or store, factory, prior_session=old_session, receipt_path=receipt_path,
            expected_receipt_sha256=receipt.receipt_sha256,
        )

    return store, root, fresh, recover


def _prefix_hashes(root: Path) -> dict[str, str]:
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(root.iterdir())
            if path.name.startswith(("intent-", "step-"))}


def test_factory_error_releases_replay_and_holds_unknown_outcome(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, _root, fresh, recover = _prepared_recovery(tmp_path, monkeypatch)

    def broken_factory(_recorder):
        raise OSError("child could not be created")

    with pytest.raises(OSError, match="could not be created"):
        recover(broken_factory)
    assert store.begin_replay()                              # bookkeeping released: no "already active"
    store.cancel_replay()
    child, factory = fresh()
    with pytest.raises(ValueError, match="outcome is unknown"):   # a raising factory is no proof of no child
        recover(factory)
    assert child.calls == 0


def test_invalid_recovery_factory_result_releases_replay_and_holds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, _root, fresh, recover = _prepared_recovery(tmp_path, monkeypatch)

    with pytest.raises(TypeError, match="invalid paper session"):
        recover(lambda _recorder: object())
    assert store.begin_replay()
    store.cancel_replay()
    child, factory = fresh()
    with pytest.raises(ValueError, match="outcome is unknown"):
        recover(factory)
    assert child.calls == 0


def test_abort_error_still_releases_replay_bookkeeping(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from test_nautilus_session import _safety

    store, _root, fresh, recover = _prepared_recovery(tmp_path, monkeypatch)
    safety = [_safety()]
    child, factory = fresh(safety=safety)
    exchange = child.exchange

    def engage_after_start(raw: bytes) -> bytes:
        response = exchange(raw)
        safety.append(_safety(kill_switch=CanonicalKillSwitchState.ACTIVE))
        return response

    def failing_abort() -> None:
        raise OSError("abort failed")                        # the child is NOT stopped

    child.exchange = engage_after_start  # type: ignore[method-assign]
    child.abort = failing_abort  # type: ignore[method-assign]

    with pytest.raises(BaseExceptionGroup) as raised:
        recover(factory)

    messages = [str(error) for error in raised.value.exceptions]
    assert any("exit-only stop" in message for message in messages)          # primary failure kept
    assert any("abort failed" in message for message in messages)           # cleanup failure kept
    assert any("could not be terminated" in message for message in messages)  # termination unproven
    assert store.begin_replay()                              # bookkeeping released: no "already active"
    store.cancel_replay()


def test_failed_replay_preserves_durable_prefix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, root, fresh, recover = _prepared_recovery(tmp_path, monkeypatch)
    before = _prefix_hashes(root)

    def broken_factory(_recorder):
        raise OSError("child could not be created")

    with pytest.raises(OSError):
        recover(broken_factory)
    with pytest.raises(ValueError, match="outcome is unknown"):
        recover(lambda _recorder: object())

    assert _prefix_hashes(root) == before


# R3 REVISE: a failed recovery child that may still run blocks every further recovery; liveness failures count
# as unproven termination and never leave the replay bookkeeping stuck.
def _failing_recovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, liveness_error: bool = False):
    from test_nautilus_session import _safety

    store, _root, fresh, recover = _prepared_recovery(tmp_path, monkeypatch)
    safety = [_safety()]
    child, factory = fresh(safety=safety)
    exchange = child.exchange

    def engage_after_start(raw: bytes) -> bytes:
        response = exchange(raw)
        safety.append(_safety(kill_switch=CanonicalKillSwitchState.ACTIVE))
        return response

    def failing_abort() -> None:
        raise OSError("abort failed")

    def failing_liveness() -> bool:
        raise ValueError("paper child liveness cannot be proven")

    child.exchange = engage_after_start  # type: ignore[method-assign]
    child.abort = failing_abort  # type: ignore[method-assign]
    if liveness_error:
        child.is_running = failing_liveness  # type: ignore[method-assign]
    ports = []

    def capturing(recorder):
        session = factory(recorder)
        ports.append(session._child)          # the exact child port recovery recorded
        return session

    with pytest.raises(BaseExceptionGroup) as raised:
        recover(capturing)
    child.port = ports[0]
    return store, fresh, recover, child, raised.value


def test_retry_is_refused_while_a_failed_recovery_child_may_still_run(tmp_path, monkeypatch) -> None:
    store, fresh, recover, failed_child, _group = _failing_recovery(tmp_path, monkeypatch)
    assert failed_child.is_running()                              # abort failed; the child still runs

    second_child, second_factory = fresh()
    with pytest.raises(ValueError, match="not proven terminated"):
        recover(second_factory)
    assert second_child.calls == 0                                # no second child was driven

    failed_child.aborted = True                                   # termination is now proven
    assert recover(second_factory).disposition == "RESUME_EXACT_PREFIX"


def test_liveness_error_keeps_every_failure_and_releases_bookkeeping(tmp_path, monkeypatch) -> None:
    store, fresh, recover, _child, group = _failing_recovery(tmp_path, monkeypatch, liveness_error=True)

    messages = [f"{type(error).__name__}: {error}" for error in group.exceptions]
    assert any("exit-only stop" in message for message in messages)                   # primary
    assert any(message == "OSError: abort failed" for message in messages)             # abort failure
    assert any("liveness cannot be proven" in message for message in messages)        # liveness failure
    assert any("could not be terminated" in message for message in messages)          # unproven termination
    assert store.begin_replay()                                                        # not "already active"
    store.cancel_replay()
    second_child, second_factory = fresh()
    with pytest.raises(ValueError, match="not proven terminated"):                     # liveness unknown: blocked
        recover(second_factory)
    assert second_child.calls == 0



# R3 REVISE-02: the unproven-termination state is durable in the store directory, bound to the attempt and the
# child identity, written before the factory runs; a reopened store keeps blocking until termination is proven.
def test_reopened_store_blocks_while_the_failed_child_still_runs(tmp_path, monkeypatch) -> None:
    store, fresh, recover, failed_child, _group = _failing_recovery(tmp_path, monkeypatch)
    reopened = NautilusRecoveryStore(tmp_path / "store")

    second_child, second_factory = fresh()
    with pytest.raises(ValueError, match="not proven terminated"):
        recover(second_factory, on_store=reopened)
    assert second_child.calls == 0


def test_reopened_store_blocks_while_liveness_is_unknown(tmp_path, monkeypatch) -> None:
    store, fresh, recover, failed_child, _group = _failing_recovery(tmp_path, monkeypatch, liveness_error=True)
    reopened = NautilusRecoveryStore(tmp_path / "store")

    with pytest.raises(ValueError, match="not proven terminated"):
        reopened.prove_recovery_child_terminated(failed_child.port)  # liveness raises: no proof
    second_child, second_factory = fresh()
    with pytest.raises(ValueError, match="not proven terminated"):
        recover(second_factory, on_store=reopened)
    assert second_child.calls == 0


def test_reopened_store_resumes_after_termination_is_proven(tmp_path, monkeypatch) -> None:
    store, fresh, recover, failed_child, _group = _failing_recovery(tmp_path, monkeypatch)
    reopened = NautilusRecoveryStore(tmp_path / "store")
    from types import SimpleNamespace

    impostor = SimpleNamespace(process_authority_sha256="9" * 64, is_running=lambda: False)
    with pytest.raises(ValueError, match="not proven terminated"):
        reopened.prove_recovery_child_terminated(impostor)          # a dead child with another identity is no proof

    failed_child.aborted = True                                     # the failed child really ended
    reopened.prove_recovery_child_terminated(failed_child.port)
    second_child, second_factory = fresh()
    assert recover(second_factory, on_store=NautilusRecoveryStore(tmp_path / "store")).disposition == "RESUME_EXACT_PREFIX"


def test_crash_between_attempt_marker_and_child_identity_blocks(tmp_path, monkeypatch) -> None:
    store, root, fresh, recover = _prepared_recovery(tmp_path, monkeypatch)
    store._open_recovery_attempt()          # crash simulation: the marker exists, the child identity never landed
    before = _prefix_hashes(root)

    second_child, second_factory = fresh()
    with pytest.raises(ValueError, match="recovery attempt outcome is unknown"):
        recover(second_factory, on_store=NautilusRecoveryStore(root))
    assert second_child.calls == 0 and _prefix_hashes(root) == before


def test_corrupt_recovery_attempt_marker_blocks_before_the_factory(tmp_path, monkeypatch) -> None:
    store, root, fresh, recover = _prepared_recovery(tmp_path, monkeypatch)
    marker = root / "recovery-attempt-00000001.json"
    marker.write_bytes(b"not json")
    marker.chmod(0o400)

    second_child, second_factory = fresh()
    with pytest.raises(ValueError, match="recovery attempt"):
        recover(second_factory, on_store=NautilusRecoveryStore(root))
    assert second_child.calls == 0


def test_proven_attempt_does_not_block_a_later_recovery(tmp_path, monkeypatch) -> None:
    store, fresh, recover, failed_child, _group = _failing_recovery(tmp_path, monkeypatch)
    root = tmp_path / "store"
    failed_child.aborted = True
    NautilusRecoveryStore(root).prove_recovery_child_terminated(failed_child.port)

    _child, factory = fresh()
    assert recover(factory, on_store=NautilusRecoveryStore(root)).disposition == "RESUME_EXACT_PREFIX"
    names = sorted(path.name for path in root.iterdir() if path.name.startswith("recovery-attempt-"))
    assert names == ["recovery-attempt-00000001-child.json", "recovery-attempt-00000001-closed.json",
                     "recovery-attempt-00000001.json", "recovery-attempt-00000002-child.json",
                     "recovery-attempt-00000002.json"]                       # attempt 2's child still runs: open


# R3 REVISE-03: no path closes an attempt without proof that its recorded child ended.
def test_factory_that_raises_after_creating_a_child_holds_the_attempt(tmp_path, monkeypatch) -> None:
    store, root, fresh, recover = _prepared_recovery(tmp_path, monkeypatch)
    created, factory = fresh()

    def raises_after_spawn(recorder):
        factory(recorder)                                          # a live child now exists
        raise OSError("factory failed after spawning")

    with pytest.raises(OSError, match="after spawning"):
        recover(raises_after_spawn)
    assert created.is_running()
    second, second_factory = fresh("e" * 64)
    with pytest.raises(ValueError, match="outcome is unknown"):
        recover(second_factory, on_store=NautilusRecoveryStore(root))
    assert second.calls == 0


def test_termination_proof_requires_the_issued_port(tmp_path, monkeypatch) -> None:
    from types import SimpleNamespace

    store, fresh, recover, failed_child, _group = _failing_recovery(tmp_path, monkeypatch)
    root = tmp_path / "store"
    copy = SimpleNamespace(process_authority_sha256=failed_child.port.process_authority_sha256,
                           is_running=lambda: False)
    with pytest.raises(ValueError, match="not proven terminated"):
        NautilusRecoveryStore(root).prove_recovery_child_terminated(copy)   # right identity, not the issued port
    assert failed_child.is_running()
    second, second_factory = fresh()
    with pytest.raises(ValueError, match="not proven terminated"):
        recover(second_factory, on_store=NautilusRecoveryStore(root))
    assert second.calls == 0


def test_successful_recovery_blocks_another_child_while_the_recovered_child_runs(tmp_path, monkeypatch) -> None:
    store, root, fresh, recover = _prepared_recovery(tmp_path, monkeypatch)
    first, first_factory = fresh()
    recovered = recover(first_factory)
    assert first.is_running()

    second, second_factory = fresh("e" * 64)
    with pytest.raises(ValueError, match="not proven terminated"):
        recover(second_factory, on_store=NautilusRecoveryStore(root))       # same prior session and receipt
    assert second.calls == 0

    first.aborted = True                                                    # the recovered child ended
    NautilusRecoveryStore(root).prove_recovery_child_terminated(recovered.session._child)
    assert recover(second_factory, on_store=NautilusRecoveryStore(root)).disposition == "RESUME_EXACT_PREFIX"


def _marker(root: Path, name: str, document: dict[str, object]) -> None:
    path = root / f"recovery-attempt-{name}.json"
    path.write_bytes(canonical_json_bytes(document) + b"\n")
    path.chmod(0o400)


_OPENER = {"schema_version": "nautilus-recovery-attempt-v1", "attempt": 1, "chain_sha256": "a" * 64}
_CHILD = {"schema_version": "nautilus-recovery-attempt-v1", "attempt": 1, "child_identity": "b" * 64}


@pytest.mark.parametrize("markers", [
    {"00000001": {k: v for k, v in _OPENER.items() if k != "chain_sha256"},                      # Codex probe
     "00000001-closed": {"schema_version": "nautilus-recovery-attempt-v1", "attempt": 1, "reason": "TERMINATED"}},
    {"00000001": {k: v for k, v in _OPENER.items() if k != "chain_sha256"}},                     # opener lacks chain
    {"00000001": _OPENER, "00000001-closed": _CHILD},                                            # closed, no child
    {"00000001": _OPENER, "00000001-child": _CHILD, "00000001-closed": {**_CHILD, "child_identity": "c" * 64}},
    {"00000001": _OPENER, "00000001-child": _CHILD, "00000001-closed": {k: _CHILD[k] for k in ("schema_version", "attempt")}},
    {"00000001": {**_OPENER, "extra": True}},
    {"00000001": {**_OPENER, "attempt": True}},
    {"00000001": _OPENER, "00000001-child": _CHILD, "00000002": {**_OPENER, "attempt": 2}},      # earlier left open
])
def test_structurally_invalid_attempt_markers_fail_closed(tmp_path, monkeypatch, markers) -> None:
    store, root, fresh, recover = _prepared_recovery(tmp_path, monkeypatch)
    for name, document in markers.items():
        _marker(root, name, document)

    with pytest.raises(ValueError, match="recovery attempt"):
        NautilusRecoveryStore(root)
    second, second_factory = fresh()
    with pytest.raises(ValueError, match="recovery attempt"):
        recover(second_factory)
    assert second.calls == 0


def test_non_canonical_attempt_marker_fails_closed(tmp_path, monkeypatch) -> None:
    store, root, fresh, recover = _prepared_recovery(tmp_path, monkeypatch)
    path = root / "recovery-attempt-00000001.json"
    path.write_bytes(json.dumps(_OPENER, indent=1).encode() + b"\n")
    path.chmod(0o400)

    with pytest.raises(ValueError, match="recovery attempt"):
        NautilusRecoveryStore(root)


# R3 REVISE-04: opening an attempt re-checks the inventory it numbers from; the guard before it may be stale.
def _interleaved(tmp_path, monkeypatch, hook: str):
    """Store B passes the guard; A recovers on another store at B's `hook`; then B continues."""
    store, root, fresh, recover = _prepared_recovery(tmp_path, monkeypatch)
    a_child, a_factory = fresh()
    b_child, b_factory = fresh("e" * 64)
    b_store = NautilusRecoveryStore(root)
    original = getattr(b_store, hook)
    results = []

    def a_runs_first(*args):
        results.append(recover(a_factory, on_store=NautilusRecoveryStore(root)))
        return original(*args)

    monkeypatch.setattr(b_store, hook, a_runs_first)
    b_calls = []

    def b_counting(recorder):
        b_calls.append(recorder)
        return b_factory(recorder)

    return root, b_store, a_child, b_child, results, lambda: recover(b_counting, on_store=b_store), b_calls


def test_attempt_opened_after_a_concurrent_recovery_is_refused(tmp_path, monkeypatch) -> None:
    root, b_store, a_child, b_child, results, recover_b, b_calls = _interleaved(
        tmp_path, monkeypatch, "_open_recovery_attempt")

    with pytest.raises(ValueError, match="recovery attempt"):
        recover_b()
    assert results[0].disposition == "RESUME_EXACT_PREFIX" and a_child.is_running()
    assert b_calls == [] and b_child.calls == 0                 # the blocked side never reached the factory
    assert b_store.begin_replay()                               # bookkeeping released
    b_store.cancel_replay()
    NautilusRecoveryStore(root)                                 # inventory still valid
    assert sorted(path.name for path in root.iterdir() if path.name.startswith("recovery-attempt-")) == [
        "recovery-attempt-00000001-child.json", "recovery-attempt-00000001.json"]


def test_same_attempt_number_race_is_refused_by_the_create_only_writer(tmp_path, monkeypatch) -> None:
    root, b_store, a_child, b_child, results, recover_b, b_calls = _interleaved(
        tmp_path, monkeypatch, "_write_attempt_marker")          # B already numbered attempt 1

    with pytest.raises(FileExistsError):
        recover_b()
    assert results[0].disposition == "RESUME_EXACT_PREFIX"
    assert b_calls == [] and b_child.calls == 0
    assert b_store.begin_replay()
    b_store.cancel_replay()
    NautilusRecoveryStore(root)
