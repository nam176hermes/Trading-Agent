from __future__ import annotations

import hashlib
import os
import signal
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from datetime import UTC, datetime, timedelta

import pytest

from services.job_worker.artifacts import ArtifactWriter
from services.job_worker.command_registry import BuiltCommand, CommandLineage
from services.job_worker.errors import SafetyBlockedError
from services.job_worker.engine_spawn import (
    EngineBuiltSpawn,
    EngineSpawnLineage,
    PreparedEngineSpawn,
)
from services.job_worker.process_runner import (
    HeartbeatDecision,
    HeartbeatInstruction,
    ProcessRunner as _ProcessRunner,
    SessionMember,
)
from services.job_worker.recovery import ProcessIdentity
from services.job_worker.safety import KillSwitchState, SafetyMode
from services.job_worker.safety_state import SafetyEvidence
from tests.jobs.backend_contract_fixtures import ATTEMPT_ID, BACKEND_COMMIT, JOB_ID


@dataclass
class Prepared:
    value: BuiltCommand


class FakeProcess:
    pid = 417

    def __init__(self, polls, *, stdout=b"out", stderr=b"err", hold_pipes=False):
        stdout_read, stdout_write = os.pipe()
        stderr_read, stderr_write = os.pipe()
        os.write(stdout_write, stdout)
        os.write(stderr_write, stderr)
        self.held_writes = [stdout_write, stderr_write] if hold_pipes else []
        if not hold_pipes:
            os.close(stdout_write)
            os.close(stderr_write)
        self.stdout = os.fdopen(stdout_read, "rb", buffering=0)
        self.stderr = os.fdopen(stderr_read, "rb", buffering=0)
        self._polls = iter(polls)
        self._last = None

    def leader_exited(self, _pid):
        try:
            self._last = next(self._polls)
        except StopIteration:
            pass
        return self._last is not None

    def wait(self, timeout=None):
        self.wait_timeout = timeout
        if self._last is None:
            raise TimeoutError
        return self._last

    def send_signal(self, sig):
        self.sent_signal = sig

    def close_descendant_pipes(self):
        for descriptor in self.held_writes:
            os.close(descriptor)
        self.held_writes.clear()


class Inspector:
    def __init__(self, identities):
        self.identities = iter(identities)
        self.last = None

    def inspect(self, pid):
        try:
            self.last = next(self.identities)
        except StopIteration:
            pass
        return self.last


class PidfdHarness:
    def __init__(self, members, on_signal=lambda _pid, _sig: None):
        self.members = members
        self.on_signal = on_signal
        self.opened = {}
        self.leader_seen = False

    def open(self, pid, _flags):
        descriptor = os.open("/dev/null", os.O_RDONLY)
        self.opened[descriptor] = pid
        return descriptor

    def inspect(self, pid):
        member = next((member for member in self.members() if member.pid == pid), None)
        if member is not None:
            if pid == 417:
                self.leader_seen = True
            return member
        if pid == 417 and not self.leader_seen:
            self.leader_seen = True
            return SessionMember(417, 417, 417, 99)
        return None

    def send(self, descriptor, sig, _info, _flags):
        self.on_signal(self.opened[descriptor], sig)

    def options(self):
        return {
            "member_inspector": self.inspect,
            "leader_inspector": self.inspect,
            "pidfd_open": self.open,
            "pidfd_send_signal": self.send,
        }


def command() -> BuiltCommand:
    return BuiltCommand(
        executable=Path("/fixed/python"),
        cwd=Path("/fixed/release"),
        argv=("/fixed/python", "-I", "-B", "paper_main.py"),
        timeout_seconds=10,
        max_attempts=1,
        result_validator_id="legacy-report-v1",
        capability_fingerprint="a" * 64,
        backend_revision=BACKEND_COMMIT,
        lineage=CommandLineage(
            authority_document_sha256="c" * 64,
            backend_manifest_sha256="d" * 64,
            semantic_policy_sha256="e" * 64,
            semantic_active_authority_sha256="f" * 64,
            semantic_version_manifest_sha256="1" * 64,
            semantic_input_fingerprint="2" * 64,
            semantic_manifest_version="semantic-v1",
            semantic_generated_at="2026-07-16T12:00:00+00:00",
            semantic_expires_at="2026-07-16T12:30:00+00:00",
        ),
        shell=False,
    )


def safety_evidence(digest: str) -> SafetyEvidence:
    generated = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
    return SafetyEvidence(
        requested_mode=SafetyMode.PAPER,
        effective_mode=SafetyMode.PAPER,
        live_execution_enabled=False,
        live_trading_approved=False,
        kill_switch_state=KillSwitchState.INACTIVE,
        snapshot_sha256=digest,
        generated_at=generated,
        expires_at=generated + timedelta(seconds=6),
    )


class ProcessRunner(_ProcessRunner):
    """Bind routine lifecycle tests to typed current-safety evidence."""

    def __init__(self, *args, safety_clock=None, **kwargs):
        super().__init__(
            *args,
            safety_clock=safety_clock or (
                lambda: datetime(2026, 7, 16, 12, 0, 1, tzinfo=UTC)
            ),
            **kwargs,
        )

    def run(self, *args, preflight=None, **kwargs):
        return super().run(
            *args,
            preflight=preflight or (lambda: safety_evidence("3" * 64)),
            **kwargs,
        )


def identity() -> ProcessIdentity:
    return ProcessIdentity(417, 417, 99, hashlib.sha256(b"cmdline\0").hexdigest())


def runner(tmp_path, process, inspector, calls, clock_values=None):
    values = iter(clock_values or [0, 0.1, 0.2, 0.3, 0.4, 1, 2, 3, 4, 5])
    last = [0.0]

    def clock():
        try:
            last[0] = next(values)
        except StopIteration:
            last[0] += 0.1
        return last[0]

    def popen(argv, **kwargs):
        calls.append((argv, kwargs))
        return process

    members = lambda: (SessionMember(417, 417, 417, 99),) if process._last is None else ()
    pidfds = PidfdHarness(members, lambda pid, sig: calls.append((pid, sig)))
    return ProcessRunner(
        ArtifactWriter(tmp_path / "artifacts"),
        popen=popen,
        inspector=inspector,
        leader_exited=process.leader_exited,
        session_members=lambda _identity: members(),
        **pidfds.options(),
        monotonic=clock,
        sleep=lambda _: None,
        poll_interval=0.01,
        terminate_grace_seconds=0.5,
    )


def engine_spawn(*descriptors: int) -> EngineBuiltSpawn:
    return EngineBuiltSpawn(
        argv=(
            "/sealed/sandbox",
            "--unshare-net",
            "--clearenv",
            "/engine/bin/engine",
            "run-backtest",
            "/inputs/request.json",
            "/inputs/request.sha256",
        ),
        cwd=Path("/"),
        environment={},
        pass_fds=descriptors,
        close_after_spawn_fds=descriptors,
        timeout_seconds=10,
        result_validator_id="engine-event-v1",
        capability_fingerprint="9" * 64,
        source_revision=BACKEND_COMMIT,
        lineage=EngineSpawnLineage("a" * 64, "b" * 64, "c" * 64),
    )


def prepared_engine_spawn() -> PreparedEngineSpawn:
    return object.__new__(PreparedEngineSpawn)


def test_runner_consumes_engine_authority_once_and_remains_popen_owner(
    monkeypatch, tmp_path
) -> None:
    calls = []
    consumed = []
    request_read, request_write = os.pipe()
    sidecar_read, sidecar_write = os.pipe()
    os.close(request_write)
    os.close(sidecar_write)
    built = engine_spawn(request_read, sidecar_read)
    prepared = prepared_engine_spawn()
    monkeypatch.setattr(
        "services.job_worker.process_runner.consume_prepared_engine_spawn",
        lambda item: consumed.append(item) or built,
    )
    monkeypatch.setattr(
        "services.job_worker.process_runner.build_child_environment",
        lambda _settings: pytest.fail("engine spawn must not use legacy environment"),
    )

    outcome = runner(
        tmp_path, FakeProcess([None, 0]), Inspector([identity()]), calls
    ).run(
        lambda: prepared,
        object(),
        10,
        lambda _: HeartbeatDecision.CONTINUE,
        job_id=JOB_ID,
        attempt_id=ATTEMPT_ID,
    )

    assert consumed == [prepared]
    argv, kwargs = calls[0]
    assert argv == list(built.argv)
    assert kwargs == {
        "cwd": "/",
        "env": {},
        "shell": False,
        "start_new_session": True,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "pass_fds": (request_read, sidecar_read),
    }
    for descriptor in (request_read, sidecar_read):
        with pytest.raises(OSError):
            os.fstat(descriptor)
    assert outcome.capability_fingerprint == "9" * 64
    assert outcome.result_validator_id == "engine-event-v1"
    assert outcome.backend_revision == BACKEND_COMMIT
    assert outcome.lineage.command == {
        "engine_closure_sha256": "a" * 64,
        "os_sandbox_profile_sha256": "b" * 64,
        "engine_request_sha256": "c" * 64,
    }


@pytest.mark.parametrize(
    ("decision", "expected_reason"),
    (
        (HeartbeatDecision.CANCEL, "CANCELLED"),
        (HeartbeatDecision.STALE_LEASE, "STALE_LEASE"),
        (HeartbeatDecision.SAFETY_DRIFT, "SAFETY_DRIFT"),
    ),
)
def test_engine_spawn_preserves_cancel_lease_and_safety_termination(
    monkeypatch, tmp_path, decision, expected_reason
) -> None:
    calls = []
    consumed = []
    read_descriptor, write_descriptor = os.pipe()
    os.close(write_descriptor)
    built = engine_spawn(read_descriptor)
    monkeypatch.setattr(
        "services.job_worker.process_runner.consume_prepared_engine_spawn",
        lambda item: consumed.append(item) or built,
    )
    monkeypatch.setattr(
        "services.job_worker.process_runner.build_child_environment",
        lambda _settings: pytest.fail("engine spawn must not use legacy environment"),
    )
    process = FakeProcess([None] * 3 + [-signal.SIGKILL])
    observed = identity()

    outcome = runner(
        tmp_path, process, Inspector([observed, observed]), calls
    ).run(
        lambda: prepared_engine_spawn(),
        object(),
        10,
        lambda _: decision,
        job_id=JOB_ID,
        attempt_id=ATTEMPT_ID,
    )

    assert len(consumed) == 1
    assert outcome.termination_reason == expected_reason
    assert (417, signal.SIGTERM) in calls


def test_runner_consumes_once_at_spawn_and_uses_hardened_popen(monkeypatch, tmp_path) -> None:
    calls = []
    consumed = []
    prepared = Prepared(command())
    monkeypatch.setattr("services.job_worker.process_runner.consume_prepared_spawn", lambda item: consumed.append(item) or item.value)
    monkeypatch.setattr(
        "services.job_worker.process_runner.consume_prepared_engine_spawn",
        lambda _item: pytest.fail("legacy spawn must retain its legacy consumer"),
    )
    monkeypatch.setattr(
        "services.job_worker.process_runner.build_child_environment",
        lambda settings: {
            "SAFE": "1",
            "HOME": "/home/thenam176/.local/run/trading-agent/research-home",
        },
    )
    process = FakeProcess([None, 0])

    outcome = runner(tmp_path, process, Inspector([identity()]), calls).run(
        lambda: prepared, object(), 10, lambda _: HeartbeatDecision.CONTINUE,
        job_id=JOB_ID, attempt_id=ATTEMPT_ID,
    )

    assert consumed == [prepared]
    argv, kwargs = calls[0]
    assert argv == list(command().argv)
    assert kwargs == {
        "cwd": str(command().cwd), "env": {
            "SAFE": "1",
            "HOME": "/home/thenam176/.local/run/trading-agent/research-home",
            "TRADING_JOB_ID": JOB_ID,
            "TRADING_JOB_ATTEMPT_ID": ATTEMPT_ID,
            "TRADING_RESEARCH_BACKEND_COMMIT": BACKEND_COMMIT,
            "TRADING_RESEARCH_SCRATCHPAD_ROOT": "/home/thenam176/.local/run/trading-agent/research-home/scratchpad",
        }, "shell": False,
        "start_new_session": True, "stdin": -3, "stdout": -1, "stderr": -1,
    }
    assert not any("/home/thenam176/.hermes/crypto-research" in value for value in kwargs["env"].values())
    assert outcome.exit_code == 0
    assert outcome.identity == identity()
    assert outcome.stdout.size_bytes == 3
    assert outcome.stderr.size_bytes == 3


def test_runner_derives_staging_scratchpad_from_issued_child_environment(
    monkeypatch, tmp_path
) -> None:
    calls = []
    scratch_home = tmp_path / "runtime" / "scratch"
    monkeypatch.setattr(
        "services.job_worker.process_runner.consume_prepared_spawn",
        lambda item: item.value,
    )
    monkeypatch.setattr(
        "services.job_worker.process_runner.build_child_environment",
        lambda settings: {
            "HOME": str(scratch_home),
            "TRADING_SEMANTIC_AUTHORITY_PATH": str(
                tmp_path / "semantic" / "active.json"
            ),
        },
    )

    runner(
        tmp_path,
        FakeProcess([None, 0]),
        Inspector([identity()]),
        calls,
    ).run(
        lambda: Prepared(command()),
        object(),
        10,
        lambda _: HeartbeatDecision.CONTINUE,
        job_id=JOB_ID,
        attempt_id=ATTEMPT_ID,
    )

    child = calls[0][1]["env"]
    assert child["TRADING_RESEARCH_SCRATCHPAD_ROOT"] == str(
        scratch_home / "scratchpad"
    )
    assert child["TRADING_SEMANTIC_AUTHORITY_PATH"] == str(
        tmp_path / "semantic" / "active.json"
    )


def test_runner_rejects_legacy_multi_mode_command_before_spawn(
    monkeypatch, tmp_path
) -> None:
    calls = []
    legacy = replace(
        command(),
        argv=(
            "/fixed/python", "-I", "-B", "main.py", "--mode", "snapshot",
            "--research-only",
        ),
    )
    monkeypatch.setattr(
        "services.job_worker.process_runner.consume_prepared_spawn",
        lambda item: item.value,
    )
    monkeypatch.setattr(
        "services.job_worker.process_runner.build_child_environment",
        lambda _settings: {"SAFE": "1"},
    )

    with pytest.raises(ValueError, match="attested command shape is unsafe"):
        runner(
            tmp_path,
            FakeProcess([0]),
            Inspector([identity()]),
            calls,
        ).run(
            lambda: Prepared(legacy),
            object(),
            10,
            lambda _: HeartbeatDecision.CONTINUE,
            job_id=JOB_ID,
            attempt_id=ATTEMPT_ID,
        )

    assert calls == []


def test_runner_requires_current_safety_preflight_before_preparation(
    monkeypatch, tmp_path
) -> None:
    calls = []
    prepared_calls = []
    prepared = Prepared(command())
    monkeypatch.setattr(
        "services.job_worker.process_runner.consume_prepared_spawn",
        lambda item: item.value,
    )
    monkeypatch.setattr(
        "services.job_worker.process_runner.build_child_environment",
        lambda settings: {},
    )

    process = FakeProcess([0])
    members = lambda: ()
    pidfds = PidfdHarness(members)
    instance = _ProcessRunner(
        ArtifactWriter(tmp_path / "artifacts"),
        popen=lambda *args, **kwargs: calls.append((args, kwargs)) or process,
        inspector=Inspector([identity()]),
        leader_exited=process.leader_exited,
        session_members=lambda _identity: (),
        **pidfds.options(),
    )

    with pytest.raises(TypeError, match="preflight"):
        instance.run(
            lambda: prepared_calls.append(True) or prepared,
            object(),
            10,
            lambda _: HeartbeatDecision.CONTINUE,
            job_id=JOB_ID,
            attempt_id=ATTEMPT_ID,
        )

    assert prepared_calls == []
    assert calls == []


def test_exact_isolated_backend_argv_can_import_only_its_attested_sibling_root(
    tmp_path,
) -> None:
    backend_main = (
        Path(__file__).resolve().parents[2] / "legacy/research-backend/main.py"
    )
    source = backend_main.read_text(encoding="utf-8")
    begin = "# BEGIN ISOLATED SEALED BACKEND IMPORT BOOTSTRAP"
    end = "# END ISOLATED SEALED BACKEND IMPORT BOOTSTRAP"
    assert begin in source and end in source
    bootstrap = source.split(begin, 1)[1].split(end, 1)[0]
    assert source.index(end) < source.index("from job_attribution import")

    release = tmp_path / "sealed-backend"
    release.mkdir(mode=0o700)
    (release / "sibling_probe.py").write_text(
        "VALUE = 'sealed-local'\n", encoding="utf-8"
    )
    (release / "main.py").write_text(
        "from pathlib import Path\nimport sys\n"
        f"{bootstrap}\n"
        "from sibling_probe import VALUE\nprint(VALUE)\n",
        encoding="utf-8",
    )
    untrusted = tmp_path / "untrusted-pythonpath"
    untrusted.mkdir()
    (untrusted / "sibling_probe.py").write_text(
        "raise RuntimeError('untrusted PYTHONPATH imported')\n", encoding="utf-8"
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "main.py",
            "--mode",
            "snapshot",
            "--research-only",
        ],
        cwd=release,
        env={"PYTHONPATH": str(untrusted)},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "sealed-local"
    assert not list(release.rglob("__pycache__"))


@pytest.mark.parametrize(
    ("job_id", "attempt_id"),
    [
        ("job-1", ATTEMPT_ID),
        (JOB_ID, "attempt-1"),
        ("job_0123456789abcdef0123456789abcdeg", ATTEMPT_ID),
        (JOB_ID, "attempt_fedcba9876543210fedcba987654321"),
    ],
)
def test_runner_rejects_non_backend_attribution_ids_before_spawn(
    monkeypatch, tmp_path, job_id, attempt_id,
) -> None:
    calls = []
    monkeypatch.setattr(
        "services.job_worker.process_runner.build_child_environment",
        lambda settings: {"SAFE": "1"},
    )
    process = FakeProcess([None, 0])

    with pytest.raises(ValueError, match="attribution"):
        runner(tmp_path, process, Inspector([identity()]), calls).run(
            lambda: Prepared(command()), object(), 10,
            lambda _: HeartbeatDecision.CONTINUE,
            job_id=job_id, attempt_id=attempt_id,
        )

    assert calls == []


def test_runner_rejects_invalid_attested_backend_revision_before_spawn(
    monkeypatch, tmp_path,
) -> None:
    calls = []
    built = command()
    object.__setattr__(built, "backend_revision", "unreviewed")
    monkeypatch.setattr(
        "services.job_worker.process_runner.consume_prepared_spawn",
        lambda item: built,
    )
    monkeypatch.setattr(
        "services.job_worker.process_runner.build_child_environment",
        lambda settings: {"SAFE": "1"},
    )

    with pytest.raises(ValueError, match="backend revision"):
        runner(
            tmp_path, FakeProcess([None, 0]), Inspector([identity()]), calls,
        ).run(
            lambda: Prepared(built), object(), 10,
            lambda _: HeartbeatDecision.CONTINUE,
            job_id=JOB_ID, attempt_id=ATTEMPT_ID,
        )

    assert calls == []


@pytest.mark.parametrize(
    ("decision", "clock_values", "expected_reason"),
    [
        (HeartbeatDecision.CANCEL, None, "CANCELLED"),
        (HeartbeatDecision.SAFETY_DRIFT, None, "SAFETY_DRIFT"),
        (HeartbeatDecision.STALE_LEASE, None, "STALE_LEASE"),
        (HeartbeatDecision.CONTINUE, [0, 0.1, 11, 11.1, 11.3], "TIMEOUT"),
    ],
)
def test_runner_terminates_exact_process_group_for_cancel_drift_stale_or_timeout(
    monkeypatch, tmp_path, decision, clock_values, expected_reason
) -> None:
    calls = []
    prepared = Prepared(command())
    monkeypatch.setattr("services.job_worker.process_runner.consume_prepared_spawn", lambda item: item.value)
    monkeypatch.setattr("services.job_worker.process_runner.build_child_environment", lambda settings: {})
    process = FakeProcess([None] * 3 + [-signal.SIGKILL])
    observed = identity()
    outcomes = iter([decision, HeartbeatDecision.CONTINUE])

    result = runner(
        tmp_path, process, Inspector([observed, observed]), calls, clock_values
    ).run(
        lambda: prepared, object(), 10, lambda _: next(outcomes, HeartbeatDecision.CONTINUE),
        job_id=JOB_ID, attempt_id=ATTEMPT_ID,
    )

    assert result.termination_reason == expected_reason, calls
    assert (417, signal.SIGTERM) in calls


def test_runner_preserves_typed_safety_reason_through_process_cleanup(
    monkeypatch, tmp_path,
) -> None:
    calls = []
    prepared = Prepared(command())
    reason_code = "SAFETY_KILL_SWITCH_ACTIVE"
    monkeypatch.setattr(
        "services.job_worker.process_runner.consume_prepared_spawn",
        lambda item: item.value,
    )
    monkeypatch.setattr(
        "services.job_worker.process_runner.build_child_environment",
        lambda settings: {},
    )

    result = runner(
        tmp_path, FakeProcess([None] * 3 + [-signal.SIGKILL]),
        Inspector([identity(), identity()]), calls,
    ).run(
        lambda: prepared, object(), 10,
        lambda _: HeartbeatInstruction.safety_drift(reason_code),
        job_id=JOB_ID, attempt_id=ATTEMPT_ID,
    )

    assert result.termination_reason == "SAFETY_DRIFT"
    assert result.safety_reason_code == reason_code
    assert (417, signal.SIGTERM) in calls


def test_runner_freezes_first_safety_reason_during_cleanup_heartbeats(
    monkeypatch, tmp_path,
) -> None:
    calls = []
    prepared = Prepared(command())
    first_reason = "SAFETY_KILL_SWITCH_ACTIVE"
    later_reason = "SAFETY_STATE_STALE"
    instructions = iter((
        HeartbeatInstruction.safety_drift(first_reason),
        HeartbeatInstruction.safety_drift(later_reason),
    ))
    observed = []
    monkeypatch.setattr(
        "services.job_worker.process_runner.consume_prepared_spawn",
        lambda item: item.value,
    )
    monkeypatch.setattr(
        "services.job_worker.process_runner.build_child_environment",
        lambda settings: {},
    )

    def heartbeat(_identity):
        instruction = next(
            instructions, HeartbeatInstruction.safety_drift(later_reason),
        )
        observed.append(instruction.reason_code)
        return instruction

    result = runner(
        tmp_path, FakeProcess([None] * 3 + [-signal.SIGKILL]),
        Inspector([identity(), identity()]), calls,
    ).run(
        lambda: prepared, object(), 10, heartbeat,
        job_id=JOB_ID, attempt_id=ATTEMPT_ID,
    )

    assert observed[:2] == [first_reason, later_reason]
    assert result.termination_reason == "SAFETY_DRIFT"
    assert result.safety_reason_code == first_reason


def test_runner_refuses_to_signal_when_exact_identity_drifted(monkeypatch, tmp_path) -> None:
    calls = []
    prepared = Prepared(command())
    monkeypatch.setattr("services.job_worker.process_runner.consume_prepared_spawn", lambda item: item.value)
    monkeypatch.setattr("services.job_worker.process_runner.build_child_environment", lambda settings: {})
    changed = ProcessIdentity(417, 417, 100, "b" * 64)

    result = runner(tmp_path, FakeProcess([None]), Inspector([identity(), changed]), calls).run(
        lambda: prepared, object(), 10, lambda _: HeartbeatDecision.CANCEL,
        job_id=JOB_ID, attempt_id=ATTEMPT_ID,
    )

    assert result.termination_reason == "PROCESS_GROUP_CLEANUP_UNPROVEN"
    assert (417, signal.SIGTERM) in calls


def test_runner_records_start_even_when_child_exits_before_first_poll(monkeypatch, tmp_path) -> None:
    calls = []
    beats = []
    prepared = Prepared(command())
    monkeypatch.setattr("services.job_worker.process_runner.consume_prepared_spawn", lambda item: item.value)
    monkeypatch.setattr("services.job_worker.process_runner.build_child_environment", lambda settings: {})

    runner(tmp_path, FakeProcess([0]), Inspector([identity()]), calls).run(
        lambda: prepared, object(), 10,
        lambda observed: beats.append(observed) or HeartbeatDecision.CONTINUE,
        job_id=JOB_ID, attempt_id=ATTEMPT_ID,
    )

    assert beats and all(observed == identity() for observed in beats)


def test_spawn_order_builds_environment_after_attested_command_at_last_moment(monkeypatch, tmp_path) -> None:
    events = []
    prepared = Prepared(command())
    monkeypatch.setattr(
        "services.job_worker.process_runner.consume_prepared_spawn",
        lambda item: events.append("consume") or item.value,
    )
    monkeypatch.setattr("services.job_worker.process_runner.build_child_environment", lambda settings: events.append("environment") or {})

    process_runner = runner(tmp_path, FakeProcess([0]), Inspector([identity()]), events)
    process_runner.run(
        lambda: events.append("prepare") or prepared, object(), 10, lambda _: HeartbeatDecision.CONTINUE,
        preflight=lambda: events.append("preflight") or safety_evidence("3" * 64),
        job_id=JOB_ID, attempt_id=ATTEMPT_ID,
    )

    assert events[:5] == ["preflight", "prepare", "environment", "consume", "preflight"]
    assert events[5][0] == list(command().argv)


def test_authority_rotation_after_prepare_blocks_before_consume_environment_or_popen(monkeypatch, tmp_path) -> None:
    events = []
    prepared = Prepared(command())
    checks = iter((
        safety_evidence("3" * 64),
        SafetyBlockedError("SAFETY_AUTHORITY_CHANGED", "rotated"),
    ))

    def preflight():
        events.append("preflight")
        result = next(checks)
        if isinstance(result, BaseException):
            raise result
        return result

    monkeypatch.setattr(
        "services.job_worker.process_runner.consume_prepared_spawn",
        lambda item: events.append("consume") or item.value,
    )
    monkeypatch.setattr(
        "services.job_worker.process_runner.build_child_environment",
        lambda settings: events.append("environment") or {},
    )

    with pytest.raises(SafetyBlockedError) as raised:
        runner(tmp_path, FakeProcess([0]), Inspector([identity()]), events).run(
            lambda: events.append("prepare") or prepared,
            object(), 10, lambda _: HeartbeatDecision.CONTINUE,
            preflight=preflight, job_id=JOB_ID, attempt_id=ATTEMPT_ID,
        )

    assert raised.value.reason_code == "SAFETY_AUTHORITY_CHANGED"
    assert events == ["preflight", "prepare", "environment", "consume", "preflight"]


@pytest.mark.parametrize(
    "unsafe",
    [
        replace(
            safety_evidence("5" * 64),
            requested_mode=SafetyMode.LIVE,
            effective_mode=SafetyMode.LIVE,
            live_execution_enabled=True,
            live_trading_approved=True,
        ),
        replace(
            safety_evidence("6" * 64),
            generated_at=datetime(2026, 7, 16, 11, 59, tzinfo=UTC),
            expires_at=datetime(2026, 7, 16, 11, 59, 6, tzinfo=UTC),
        ),
        replace(safety_evidence("7" * 64), snapshot_sha256="not-a-digest"),
    ],
)
def test_final_typed_but_unsafe_or_invalid_safety_evidence_never_reaches_popen(
    monkeypatch, tmp_path, unsafe
) -> None:
    calls = []
    snapshots = iter((safety_evidence("4" * 64), unsafe))
    prepared = Prepared(command())
    monkeypatch.setattr(
        "services.job_worker.process_runner.consume_prepared_spawn",
        lambda item: item.value,
    )
    monkeypatch.setattr(
        "services.job_worker.process_runner.build_child_environment", lambda settings: {},
    )

    with pytest.raises(SafetyBlockedError):
        runner(tmp_path, FakeProcess([0]), Inspector([identity()]), calls).run(
            lambda: prepared,
            object(),
            10,
            lambda _: HeartbeatDecision.CONTINUE,
            preflight=lambda: next(snapshots),
            job_id=JOB_ID,
            attempt_id=ATTEMPT_ID,
        )

    assert calls == []


def test_runner_persists_exact_sanitized_authority_semantic_and_safety_chain(
    monkeypatch, tmp_path,
) -> None:
    calls = []
    prepared = Prepared(command())
    snapshots = iter((safety_evidence("3" * 64), safety_evidence("4" * 64)))
    monkeypatch.setattr(
        "services.job_worker.process_runner.consume_prepared_spawn",
        lambda item: item.value,
    )
    monkeypatch.setattr(
        "services.job_worker.process_runner.build_child_environment", lambda settings: {},
    )

    result = runner(
        tmp_path, FakeProcess([0]), Inspector([identity()]), calls,
    ).run(
        lambda: prepared,
        object(),
        10,
        lambda _: HeartbeatDecision.CONTINUE,
        preflight=lambda: next(snapshots),
        job_id=JOB_ID,
        attempt_id=ATTEMPT_ID,
    )

    assert result.lineage.command == command().lineage.as_metadata()
    assert result.lineage.safety_initial["snapshot_sha256"] == "3" * 64
    assert result.lineage.safety_final["snapshot_sha256"] == "4" * 64
    assert result.lineage.command["semantic_input_fingerprint"] == "2" * 64


def test_unproven_post_spawn_identity_is_killed_before_worker_can_continue(monkeypatch, tmp_path) -> None:
    calls = []
    prepared = Prepared(command())
    monkeypatch.setattr("services.job_worker.process_runner.consume_prepared_spawn", lambda item: item.value)
    monkeypatch.setattr("services.job_worker.process_runner.build_child_environment", lambda settings: {})

    process = FakeProcess([None])
    with pytest.raises(RuntimeError, match="identity could not be proven"):
        runner(tmp_path, process, Inspector([None]), calls).run(
            lambda: prepared, object(), 10, lambda _: HeartbeatDecision.CONTINUE,
            job_id=JOB_ID, attempt_id=ATTEMPT_ID,
        )

    assert not any(call == (417, signal.SIGKILL) for call in calls)
    assert process.sent_signal == signal.SIGKILL
    assert process.wait_timeout is not None


def test_inspector_exception_after_popen_uses_only_exact_child_handle_and_reaps(monkeypatch, tmp_path) -> None:
    calls = []
    prepared = Prepared(command())
    process = FakeProcess([None])

    class BrokenInspector:
        def inspect(self, pid):
            raise OSError("procfs unavailable")

    monkeypatch.setattr("services.job_worker.process_runner.consume_prepared_spawn", lambda item: item.value)
    monkeypatch.setattr("services.job_worker.process_runner.build_child_environment", lambda settings: {})

    with pytest.raises(OSError, match="procfs unavailable"):
        runner(tmp_path, process, BrokenInspector(), calls).run(
            lambda: prepared, object(), 10, lambda _: HeartbeatDecision.CONTINUE,
            job_id=JOB_ID, attempt_id=ATTEMPT_ID,
        )

    assert process.sent_signal == signal.SIGKILL
    assert process.wait_timeout is not None
    assert not any(call == (417, signal.SIGKILL) for call in calls)


def test_leader_zombie_anchors_pgid_until_descendant_pipe_cancel_cleanup(monkeypatch, tmp_path) -> None:
    events = []
    process = FakeProcess([0], hold_pipes=True)
    prepared = Prepared(command())
    monkeypatch.setattr("services.job_worker.process_runner.consume_prepared_spawn", lambda item: item.value)
    monkeypatch.setattr("services.job_worker.process_runner.build_child_environment", lambda settings: {})

    def killpg(pgid, sig):
        events.append(("signal", pgid, sig))
        if sig == signal.SIGKILL:
            process.close_descendant_pipes()

    original_wait = process.wait
    process.wait = lambda timeout=None: events.append(("wait", timeout)) or original_wait(timeout)
    decisions = iter([HeartbeatDecision.CONTINUE, HeartbeatDecision.CANCEL])
    ticks = iter(number / 100 for number in range(1000))
    members = lambda: (SessionMember(500, 417, 417, 1),) if process.held_writes else ()
    pidfds = PidfdHarness(members, lambda pid, sig: killpg(pid, sig))
    instance = ProcessRunner(
        ArtifactWriter(tmp_path / "artifacts"), popen=lambda *args, **kwargs: process,
        inspector=Inspector([identity()]),
        leader_exited=process.leader_exited, monotonic=lambda: next(ticks),
        session_members=lambda _identity: members(), **pidfds.options(), sleep=lambda _: None,
        poll_interval=0.01, terminate_grace_seconds=0.2,
    )

    result = instance.run(
        lambda: prepared, object(), 10,
        lambda _: next(decisions, HeartbeatDecision.CANCEL),
        job_id=JOB_ID, attempt_id=ATTEMPT_ID,
    )

    assert result.termination_reason == "CANCELLED"
    assert ("signal", 500, signal.SIGTERM) in events
    assert ("signal", 500, signal.SIGKILL) in events
    assert events[-1][0] == "wait"


def test_leader_exit_with_descendant_held_pipes_forces_anchored_group_cleanup(monkeypatch, tmp_path) -> None:
    signals = []
    process = FakeProcess([0], hold_pipes=True)
    prepared = Prepared(command())
    monkeypatch.setattr("services.job_worker.process_runner.consume_prepared_spawn", lambda item: item.value)
    monkeypatch.setattr("services.job_worker.process_runner.build_child_environment", lambda settings: {})
    ticks = iter(number / 100 for number in range(1000))

    def killpg(pgid, sig):
        signals.append((pgid, sig))
        if sig == signal.SIGKILL:
            process.close_descendant_pipes()

    members = lambda: (SessionMember(500, 417, 417, 1),) if process.held_writes else ()
    pidfds = PidfdHarness(members, killpg)

    result = ProcessRunner(
        ArtifactWriter(tmp_path / "artifacts"), popen=lambda *args, **kwargs: process,
        inspector=Inspector([identity()]),
        leader_exited=process.leader_exited, monotonic=lambda: next(ticks),
        session_members=lambda _identity: members(), **pidfds.options(), sleep=lambda _: None,
        poll_interval=0.01, terminate_grace_seconds=0.1,
    ).run(
        lambda: prepared, object(), 10, lambda _: HeartbeatDecision.CONTINUE,
        job_id=JOB_ID, attempt_id=ATTEMPT_ID,
    )

    assert result.termination_reason is None, signals
    assert signals == [(500, signal.SIGTERM), (500, signal.SIGKILL)]


def test_cleanup_signal_error_blocks_but_still_kills_drains_closes_and_reaps(monkeypatch, tmp_path) -> None:
    events = []
    process = FakeProcess([0], hold_pipes=True)
    prepared = Prepared(command())
    monkeypatch.setattr("services.job_worker.process_runner.consume_prepared_spawn", lambda item: item.value)
    monkeypatch.setattr("services.job_worker.process_runner.build_child_environment", lambda settings: {})

    def killpg(_pgid, sig):
        events.append(sig)
        if sig == signal.SIGTERM:
            raise PermissionError("term denied")
        process.close_descendant_pipes()

    members = lambda: (SessionMember(500, 417, 417, 1),) if process.held_writes else ()
    pidfds = PidfdHarness(members, killpg)

    instance = ProcessRunner(
        ArtifactWriter(tmp_path / "artifacts"), popen=lambda *args, **kwargs: process,
        inspector=Inspector([identity()]),
        leader_exited=process.leader_exited, poll_interval=0.01,
        session_members=lambda _identity: members(), **pidfds.options(), sleep=lambda _: None,
        terminate_grace_seconds=0.05,
    )
    result = instance.run(
        lambda: prepared, object(), 10, lambda _: HeartbeatDecision.CANCEL,
        job_id=JOB_ID, attempt_id=ATTEMPT_ID,
    )

    assert result.termination_reason == "PROCESS_GROUP_CLEANUP_UNPROVEN"
    assert events == [signal.SIGTERM, signal.SIGKILL]
    assert process.wait_timeout is not None


def test_default_leader_probe_uses_waitid_wnowait(monkeypatch) -> None:
    from services.job_worker import process_runner

    observed = []
    monkeypatch.setattr(process_runner.os, "waitid", lambda idtype, pid, flags: observed.append((idtype, pid, flags)) or object())

    assert process_runner._leader_exited_wnowait(417)
    assert observed[0][0] == os.P_PID
    assert observed[0][1] == 417
    assert observed[0][2] & os.WNOWAIT
    assert observed[0][2] & os.WNOHANG


def test_default_group_inspector_checks_session_pgrp_and_ignores_zombies(monkeypatch) -> None:
    from services.job_worker import process_runner

    class Entry:
        def __init__(self, name): self.name = name
    class Scan:
        def __enter__(self): return iter([Entry("417"), Entry("418"), Entry("419")])
        def __exit__(self, *args): return False

    def stat_line(pid, state, pgrp, session, start):
        tail = [state, "1", str(pgrp), str(session), *("0" for _ in range(15)), str(start)]
        return f"{pid} (worker) {' '.join(tail)}".encode()

    values = {
        10: stat_line(417, "Z", 417, 417, 123),
        11: stat_line(418, "S", 600, 417, 124),
        12: stat_line(419, "S", 417, 999, 125),
    }
    descriptors = iter(values)
    monkeypatch.setattr(process_runner.os, "scandir", lambda _path: Scan())
    monkeypatch.setattr(process_runner.os, "open", lambda *args, **kwargs: next(descriptors))
    monkeypatch.setattr(process_runner.os, "read", lambda fd, _size: values[fd])
    monkeypatch.setattr(process_runner.os, "close", lambda _fd: None)

    assert process_runner._session_members_proc(identity()) == (SessionMember(418, 600, 417, 124),)


def test_selector_close_and_wait_errors_do_not_skip_pipe_cleanup(monkeypatch, tmp_path) -> None:
    import selectors

    process = FakeProcess([0])
    prepared = Prepared(command())
    monkeypatch.setattr("services.job_worker.process_runner.consume_prepared_spawn", lambda item: item.value)
    monkeypatch.setattr("services.job_worker.process_runner.build_child_environment", lambda settings: {})

    class BrokenCloseSelector:
        def __init__(self): self.inner = selectors.DefaultSelector()
        def register(self, *args): return self.inner.register(*args)
        def unregister(self, *args): return self.inner.unregister(*args)
        def select(self, *args): return self.inner.select(*args)
        def close(self):
            self.inner.close()
            raise OSError("selector close failed")

    process.wait = lambda timeout=None: (_ for _ in ()).throw(OSError("wait failed"))
    pidfds = PidfdHarness(lambda: ())
    result = ProcessRunner(
        ArtifactWriter(tmp_path / "artifacts"), popen=lambda *args, **kwargs: process,
        inspector=Inspector([identity()]), leader_exited=process.leader_exited,
        session_members=lambda _identity: (), **pidfds.options(), sleep=lambda _: None,
        selector_factory=BrokenCloseSelector, poll_interval=0.01,
        terminate_grace_seconds=0.05,
    ).run(
        lambda: prepared, object(), 10, lambda _: HeartbeatDecision.CONTINUE,
        job_id=JOB_ID, attempt_id=ATTEMPT_ID,
    )

    assert result.termination_reason == "PROCESS_GROUP_CLEANUP_UNPROVEN"
    assert process.stdout.closed and process.stderr.closed


def test_descendant_ignores_term_and_closes_pipes_but_still_receives_mandatory_kill(monkeypatch, tmp_path) -> None:
    process = FakeProcess([0], hold_pipes=True)
    prepared = Prepared(command())
    signals = []
    alive = [True]
    ticks = iter(number / 100 for number in range(1000))
    monkeypatch.setattr("services.job_worker.process_runner.consume_prepared_spawn", lambda item: item.value)
    monkeypatch.setattr("services.job_worker.process_runner.build_child_environment", lambda settings: {})

    def killpg(_pgid, sig):
        signals.append(sig)
        if sig == signal.SIGTERM:
            process.close_descendant_pipes()
        else:
            alive[0] = False

    members = lambda: (SessionMember(500, 417, 417, 1),) if alive[0] else ()
    pidfds = PidfdHarness(members, killpg)

    result = ProcessRunner(
        ArtifactWriter(tmp_path / "artifacts"), popen=lambda *args, **kwargs: process,
        inspector=Inspector([identity()]),
        leader_exited=process.leader_exited,
        session_members=lambda _identity: members(), **pidfds.options(), sleep=lambda _: None,
        monotonic=lambda: next(ticks), poll_interval=0.01,
        terminate_grace_seconds=0.1,
    ).run(
        lambda: prepared, object(), 10, lambda _: HeartbeatDecision.CANCEL,
        job_id=JOB_ID, attempt_id=ATTEMPT_ID,
    )

    assert result.termination_reason == "CANCELLED"
    assert signals == [signal.SIGTERM, signal.SIGKILL]


def test_normal_leader_exit_cleans_background_descendant_even_after_pipe_eof(monkeypatch, tmp_path) -> None:
    process = FakeProcess([0])
    prepared = Prepared(command())
    signals = []
    alive = [True]
    ticks = iter(number / 100 for number in range(1000))
    monkeypatch.setattr("services.job_worker.process_runner.consume_prepared_spawn", lambda item: item.value)
    monkeypatch.setattr("services.job_worker.process_runner.build_child_environment", lambda settings: {})

    def killpg(_pgid, sig):
        signals.append(sig)
        if sig == signal.SIGKILL:
            alive[0] = False

    members = lambda: (SessionMember(500, 417, 417, 1),) if alive[0] else ()
    pidfds = PidfdHarness(members, killpg)

    result = ProcessRunner(
        ArtifactWriter(tmp_path / "artifacts"), popen=lambda *args, **kwargs: process,
        inspector=Inspector([identity()]),
        leader_exited=process.leader_exited,
        session_members=lambda _identity: members(), **pidfds.options(), sleep=lambda _: None,
        monotonic=lambda: next(ticks), poll_interval=0.01,
        terminate_grace_seconds=0.1,
    ).run(
        lambda: prepared, object(), 10, lambda _: HeartbeatDecision.CONTINUE,
        job_id=JOB_ID, attempt_id=ATTEMPT_ID,
    )

    assert result.termination_reason is None
    assert signals == [signal.SIGTERM, signal.SIGKILL]


def test_pipe_setup_failure_closes_both_original_streams_and_reaps(monkeypatch, tmp_path) -> None:
    class BrokenStream:
        def __init__(self, broken=False): self.broken, self.closed = broken, False
        def fileno(self):
            if self.broken: raise OSError("fileno failed")
            return 999
        def close(self): self.closed = True

    process = FakeProcess([0])
    process.stdout.close()
    process.stderr.close()
    process.stdout = BrokenStream(broken=True)
    process.stderr = BrokenStream()
    prepared = Prepared(command())
    monkeypatch.setattr("services.job_worker.process_runner.consume_prepared_spawn", lambda item: item.value)
    monkeypatch.setattr("services.job_worker.process_runner.build_child_environment", lambda settings: {})

    with pytest.raises(OSError, match="fileno failed"):
        pidfds = PidfdHarness(lambda: ())
        ProcessRunner(
            ArtifactWriter(tmp_path / "artifacts"), popen=lambda *args, **kwargs: process,
            inspector=Inspector([identity()]), leader_exited=process.leader_exited,
            session_members=lambda _identity: (), **pidfds.options(), sleep=lambda _: None,
        ).run(
            lambda: prepared, object(), 10, lambda _: HeartbeatDecision.CONTINUE,
            job_id=JOB_ID, attempt_id=ATTEMPT_ID,
        )

    assert process.stdout.closed and process.stderr.closed
    assert process.wait_timeout is not None


def test_set_blocking_failure_still_closes_both_original_pipes(monkeypatch, tmp_path) -> None:
    process = FakeProcess([0])
    prepared = Prepared(command())
    monkeypatch.setattr("services.job_worker.process_runner.consume_prepared_spawn", lambda item: item.value)
    monkeypatch.setattr("services.job_worker.process_runner.build_child_environment", lambda settings: {})
    monkeypatch.setattr("services.job_worker.process_runner.os.set_blocking", lambda *args: (_ for _ in ()).throw(OSError("blocking failed")))

    with pytest.raises(OSError, match="blocking failed"):
        pidfds = PidfdHarness(lambda: ())
        ProcessRunner(
            ArtifactWriter(tmp_path / "artifacts"), popen=lambda *args, **kwargs: process,
            inspector=Inspector([identity()]), leader_exited=process.leader_exited,
            session_members=lambda _identity: (), **pidfds.options(), sleep=lambda _: None,
        ).run(
            lambda: prepared, object(), 10, lambda _: HeartbeatDecision.CONTINUE,
            job_id=JOB_ID, attempt_id=ATTEMPT_ID,
        )

    assert process.stdout.closed and process.stderr.closed


def test_group_still_live_after_kill_is_cleanup_unproven(monkeypatch, tmp_path) -> None:
    process = FakeProcess([0])
    prepared = Prepared(command())
    ticks = iter(number / 100 for number in range(1000))
    monkeypatch.setattr("services.job_worker.process_runner.consume_prepared_spawn", lambda item: item.value)
    monkeypatch.setattr("services.job_worker.process_runner.build_child_environment", lambda settings: {})

    members = lambda: (SessionMember(500, 417, 417, 1),)
    pidfds = PidfdHarness(members)
    result = ProcessRunner(
        ArtifactWriter(tmp_path / "artifacts"), popen=lambda *args, **kwargs: process,
        inspector=Inspector([identity()]),
        leader_exited=process.leader_exited,
        session_members=lambda _identity: members(), **pidfds.options(), sleep=lambda _: None,
        monotonic=lambda: next(ticks), poll_interval=0.01,
        terminate_grace_seconds=0.05,
    ).run(
        lambda: prepared, object(), 10, lambda _: HeartbeatDecision.CANCEL,
        job_id=JOB_ID, attempt_id=ATTEMPT_ID,
    )

    assert result.termination_reason == "PROCESS_GROUP_CLEANUP_UNPROVEN"


def test_group_empty_but_pipe_held_is_bounded_and_blocked(monkeypatch, tmp_path) -> None:
    process = FakeProcess([0], hold_pipes=True)
    prepared = Prepared(command())
    ticks = iter(number / 100 for number in range(1000))
    monkeypatch.setattr("services.job_worker.process_runner.consume_prepared_spawn", lambda item: item.value)
    monkeypatch.setattr("services.job_worker.process_runner.build_child_environment", lambda settings: {})

    pidfds = PidfdHarness(lambda: ())
    result = ProcessRunner(
        ArtifactWriter(tmp_path / "artifacts"), popen=lambda *args, **kwargs: process,
        inspector=Inspector([identity()]), leader_exited=process.leader_exited,
        session_members=lambda _identity: (), **pidfds.options(), sleep=lambda _: None,
        monotonic=lambda: next(ticks), poll_interval=0.01,
        terminate_grace_seconds=0.05,
    ).run(
        lambda: prepared, object(), 10, lambda _: HeartbeatDecision.CONTINUE,
        job_id=JOB_ID, attempt_id=ATTEMPT_ID,
    )

    assert result.termination_reason == "OUTPUT_DRAIN_TRUNCATED"
    process.close_descendant_pipes()


def test_alternate_pgrp_descendant_closes_pipes_ignores_term_and_is_killed(monkeypatch, tmp_path) -> None:
    process = FakeProcess([0], hold_pipes=True)
    prepared = Prepared(command())
    live = {SessionMember(501, 600, 417, 1)}
    signals = []
    ticks = iter(number / 100 for number in range(1000))
    monkeypatch.setattr("services.job_worker.process_runner.consume_prepared_spawn", lambda item: item.value)
    monkeypatch.setattr("services.job_worker.process_runner.build_child_environment", lambda settings: {})

    def killpg(pgid, sig):
        signals.append((pgid, sig))
        if sig == signal.SIGTERM:
            process.close_descendant_pipes()
        if sig == signal.SIGKILL:
            live.clear()

    members = lambda: tuple(live)
    pidfds = PidfdHarness(members, killpg)

    result = ProcessRunner(
        ArtifactWriter(tmp_path / "artifacts"), popen=lambda *args, **kwargs: process,
        inspector=Inspector([identity()]),
        leader_exited=process.leader_exited,
        session_members=lambda _identity: members(), **pidfds.options(), sleep=lambda _: None,
        monotonic=lambda: next(ticks), poll_interval=0.01,
        terminate_grace_seconds=0.05,
    ).run(
        lambda: prepared, object(), 10, lambda _: HeartbeatDecision.CANCEL,
        job_id=JOB_ID, attempt_id=ATTEMPT_ID,
    )

    assert result.termination_reason == "CANCELLED"
    assert signals == [(501, signal.SIGTERM), (501, signal.SIGKILL)]


def test_session_membership_churn_requires_consecutive_empty_proof(monkeypatch, tmp_path) -> None:
    process = FakeProcess([0])
    prepared = Prepared(command())
    scans = [(), (SessionMember(502, 700, 417, 1),)]
    live = {SessionMember(502, 700, 417, 1)}
    signals = []
    ticks = iter(number / 100 for number in range(1000))
    monkeypatch.setattr("services.job_worker.process_runner.consume_prepared_spawn", lambda item: item.value)
    monkeypatch.setattr("services.job_worker.process_runner.build_child_environment", lambda settings: {})

    def members(_identity):
        return scans.pop(0) if scans else tuple(live)

    def killpg(pgid, sig):
        signals.append((pgid, sig))
        if sig == signal.SIGKILL:
            live.clear()

    provider = lambda: tuple(live)
    pidfds = PidfdHarness(provider, killpg)

    result = ProcessRunner(
        ArtifactWriter(tmp_path / "artifacts"), popen=lambda *args, **kwargs: process,
        inspector=Inspector([identity()]),
        leader_exited=process.leader_exited, session_members=members,
        **pidfds.options(), sleep=lambda _: None,
        monotonic=lambda: next(ticks), poll_interval=0.01,
        terminate_grace_seconds=0.05,
    ).run(
        lambda: prepared, object(), 10, lambda _: HeartbeatDecision.CONTINUE,
        job_id=JOB_ID, attempt_id=ATTEMPT_ID,
    )

    assert result.termination_reason is None, signals
    assert signals == [(502, signal.SIGTERM), (502, signal.SIGKILL)]


def test_signal_error_on_one_session_pgrp_still_attempts_all_groups_and_blocks(monkeypatch, tmp_path) -> None:
    process = FakeProcess([0])
    prepared = Prepared(command())
    live = {SessionMember(503, 700, 417, 1), SessionMember(504, 800, 417, 1)}
    signals = []
    ticks = iter(number / 100 for number in range(1000))
    monkeypatch.setattr("services.job_worker.process_runner.consume_prepared_spawn", lambda item: item.value)
    monkeypatch.setattr("services.job_worker.process_runner.build_child_environment", lambda settings: {})

    def killpg(pid, sig):
        signals.append((pid, sig))
        if pid == 503 and sig == signal.SIGTERM:
            raise PermissionError("denied")
        if sig == signal.SIGKILL:
            live.difference_update(member for member in tuple(live) if member.pid == pid)

    members = lambda: tuple(live)
    pidfds = PidfdHarness(members, killpg)

    result = ProcessRunner(
        ArtifactWriter(tmp_path / "artifacts"), popen=lambda *args, **kwargs: process,
        inspector=Inspector([identity()]),
        leader_exited=process.leader_exited,
        session_members=lambda _identity: members(), **pidfds.options(), sleep=lambda _: None,
        monotonic=lambda: next(ticks), poll_interval=0.01,
        terminate_grace_seconds=0.05,
    ).run(
        lambda: prepared, object(), 10, lambda _: HeartbeatDecision.CANCEL,
        job_id=JOB_ID, attempt_id=ATTEMPT_ID,
    )

    assert result.termination_reason == "PROCESS_GROUP_CLEANUP_UNPROVEN"
    assert (503, signal.SIGTERM) in signals and (504, signal.SIGTERM) in signals
    assert (503, signal.SIGKILL) in signals and (504, signal.SIGKILL) in signals


def test_pid_reuse_or_session_escape_during_pidfd_open_never_signals_wrong_process(monkeypatch, tmp_path) -> None:
    process = FakeProcess([0])
    prepared = Prepared(command())
    member = SessionMember(505, 900, 417, 111)
    scans = [(member,), (), ()]
    signals = []
    pidfds = PidfdHarness(lambda: (member,), lambda pid, sig: signals.append((pid, sig)))
    original_inspect = pidfds.inspect
    pidfds.inspect = lambda pid: SessionMember(505, 900, 999, 222) if pid == 505 else original_inspect(pid)
    monkeypatch.setattr("services.job_worker.process_runner.consume_prepared_spawn", lambda item: item.value)
    monkeypatch.setattr("services.job_worker.process_runner.build_child_environment", lambda settings: {})

    result = ProcessRunner(
        ArtifactWriter(tmp_path / "artifacts"), popen=lambda *args, **kwargs: process,
        inspector=Inspector([identity()]), leader_exited=process.leader_exited,
        session_members=lambda _identity: scans.pop(0) if scans else (),
        **pidfds.options(), sleep=lambda _: None, poll_interval=0.01,
        terminate_grace_seconds=0.05,
    ).run(
        lambda: prepared, object(), 10, lambda _: HeartbeatDecision.CANCEL,
        job_id=JOB_ID, attempt_id=ATTEMPT_ID,
    )

    assert result.termination_reason == "PROCESS_GROUP_CLEANUP_UNPROVEN"
    assert signals == []


def test_exception_path_uses_same_pidfd_cleanup_for_alternate_member(monkeypatch, tmp_path) -> None:
    process = FakeProcess([0])
    prepared = Prepared(command())
    live = {SessionMember(506, 901, 417, 333)}
    signals = []

    def on_signal(pid, sig):
        signals.append((pid, sig))
        if sig == signal.SIGKILL:
            live.clear()

    pidfds = PidfdHarness(lambda: tuple(live), on_signal)
    ticks = iter(number / 100 for number in range(1000))
    monkeypatch.setattr("services.job_worker.process_runner.consume_prepared_spawn", lambda item: item.value)
    monkeypatch.setattr("services.job_worker.process_runner.build_child_environment", lambda settings: {})

    with pytest.raises(RuntimeError, match="heartbeat exploded"):
        ProcessRunner(
            ArtifactWriter(tmp_path / "artifacts"), popen=lambda *args, **kwargs: process,
            inspector=Inspector([identity()]), leader_exited=process.leader_exited,
            session_members=lambda _identity: tuple(live), **pidfds.options(),
            monotonic=lambda: next(ticks), sleep=lambda _: None,
            poll_interval=0.01, terminate_grace_seconds=0.05,
        ).run(
            lambda: prepared, object(), 10,
            lambda _: (_ for _ in ()).throw(RuntimeError("heartbeat exploded")),
            job_id=JOB_ID, attempt_id=ATTEMPT_ID,
        )

    assert signals == [(506, signal.SIGTERM), (506, signal.SIGKILL)]
    assert process.wait_timeout is not None


@pytest.mark.parametrize("failure", ["open", "send"])
def test_pidfd_open_or_signal_failure_blocks_but_reaps(monkeypatch, tmp_path, failure) -> None:
    process = FakeProcess([0])
    prepared = Prepared(command())
    member = SessionMember(507, 902, 417, 444)
    scans = [(member,), (), ()]
    pidfds = PidfdHarness(lambda: (member,))
    if failure == "open":
        original_open = pidfds.open
        pidfds.open = lambda pid, flags: (
            (_ for _ in ()).throw(OSError("pidfd open failed"))
            if pid == 507 else original_open(pid, flags)
        )
    else:
        pidfds.send = lambda *_: (_ for _ in ()).throw(OSError("pidfd send failed"))
    monkeypatch.setattr("services.job_worker.process_runner.consume_prepared_spawn", lambda item: item.value)
    monkeypatch.setattr("services.job_worker.process_runner.build_child_environment", lambda settings: {})

    result = ProcessRunner(
        ArtifactWriter(tmp_path / "artifacts"), popen=lambda *args, **kwargs: process,
        inspector=Inspector([identity()]), leader_exited=process.leader_exited,
        session_members=lambda _identity: scans.pop(0) if scans else (),
        **pidfds.options(), sleep=lambda _: None,
        poll_interval=0.01, terminate_grace_seconds=0.05,
    ).run(
        lambda: prepared, object(), 10, lambda _: HeartbeatDecision.CANCEL,
        job_id=JOB_ID, attempt_id=ATTEMPT_ID,
    )

    assert result.termination_reason == "PROCESS_GROUP_CLEANUP_UNPROVEN"
    assert process.wait_timeout is not None


def test_unsupported_pidfd_platform_fails_closed_at_runner_start(monkeypatch, tmp_path) -> None:
    from services.job_worker import process_runner

    monkeypatch.setattr(process_runner, "_syscall_numbers", lambda: (_ for _ in ()).throw(RuntimeError("unsupported")))
    monkeypatch.delattr(process_runner.os, "pidfd_open", raising=False)
    monkeypatch.delattr(process_runner.signal, "pidfd_send_signal", raising=False)

    with pytest.raises(RuntimeError, match="unsupported"):
        ProcessRunner(ArtifactWriter(tmp_path / "artifacts"))


def test_repeated_session_scan_failure_still_term_kills_retained_leader(monkeypatch, tmp_path) -> None:
    process = FakeProcess([None])
    prepared = Prepared(command())
    leader = SessionMember(417, 417, 417, 99)
    signals = []
    ticks = iter(number / 100 for number in range(1000))

    def on_signal(pid, sig):
        signals.append((pid, sig))
        if sig == signal.SIGKILL:
            process._last = -signal.SIGKILL

    pidfds = PidfdHarness(lambda: (leader,), on_signal)
    monkeypatch.setattr("services.job_worker.process_runner.consume_prepared_spawn", lambda item: item.value)
    monkeypatch.setattr("services.job_worker.process_runner.build_child_environment", lambda settings: {})

    result = ProcessRunner(
        ArtifactWriter(tmp_path / "artifacts"), popen=lambda *args, **kwargs: process,
        inspector=Inspector([identity()]), leader_exited=process.leader_exited,
        session_members=lambda _identity: (_ for _ in ()).throw(OSError("scan failed")),
        **pidfds.options(), monotonic=lambda: next(ticks), sleep=lambda _: None,
        poll_interval=0.01, terminate_grace_seconds=0.05,
    ).run(
        lambda: prepared, object(), 10, lambda _: HeartbeatDecision.CANCEL,
        job_id=JOB_ID, attempt_id=ATTEMPT_ID,
    )

    assert result.termination_reason == "PROCESS_GROUP_CLEANUP_UNPROVEN"
    assert (417, signal.SIGTERM) in signals
    assert (417, signal.SIGKILL) in signals
    assert process.wait_timeout is not None


def test_snapshot_omission_of_same_live_leader_never_proves_empty_and_signals(monkeypatch, tmp_path) -> None:
    process = FakeProcess([None])
    prepared = Prepared(command())
    leader = SessionMember(417, 417, 417, 99)
    alive = [True]
    signals = []
    ticks = iter(number / 100 for number in range(1000))

    def provider():
        return (leader,) if alive[0] else ()

    def on_signal(pid, sig):
        signals.append((pid, sig))
        if sig == signal.SIGKILL:
            alive[0] = False
            process._last = -signal.SIGKILL

    pidfds = PidfdHarness(provider, on_signal)
    monkeypatch.setattr("services.job_worker.process_runner.consume_prepared_spawn", lambda item: item.value)
    monkeypatch.setattr("services.job_worker.process_runner.build_child_environment", lambda settings: {})

    result = ProcessRunner(
        ArtifactWriter(tmp_path / "artifacts"), popen=lambda *args, **kwargs: process,
        inspector=Inspector([identity()]), leader_exited=process.leader_exited,
        session_members=lambda _identity: (), **pidfds.options(),
        monotonic=lambda: next(ticks), sleep=lambda _: None,
        poll_interval=0.01, terminate_grace_seconds=0.05,
    ).run(
        lambda: prepared, object(), 10, lambda _: HeartbeatDecision.CANCEL,
        job_id=JOB_ID, attempt_id=ATTEMPT_ID,
    )

    assert result.termination_reason == "PROCESS_GROUP_CLEANUP_UNPROVEN"
    assert signals == [(417, signal.SIGTERM), (417, signal.SIGKILL)]


def test_leader_pidfd_open_failure_has_no_pid_fallback_before_heartbeat(monkeypatch, tmp_path) -> None:
    process = FakeProcess([0])
    prepared = Prepared(command())
    heartbeats = []
    pidfds = PidfdHarness(lambda: (SessionMember(417, 417, 417, 99),))
    pidfds.open = lambda *_: (_ for _ in ()).throw(OSError("leader pidfd failed"))
    monkeypatch.setattr("services.job_worker.process_runner.consume_prepared_spawn", lambda item: item.value)
    monkeypatch.setattr("services.job_worker.process_runner.build_child_environment", lambda settings: {})

    with pytest.raises(RuntimeError, match="leader pidfd identity"):
        ProcessRunner(
            ArtifactWriter(tmp_path / "artifacts"), popen=lambda *args, **kwargs: process,
            inspector=Inspector([identity()]), leader_exited=process.leader_exited,
            session_members=lambda _identity: (), **pidfds.options(), sleep=lambda _: None,
        ).run(
            lambda: prepared, object(), 10,
            lambda observed: heartbeats.append(observed) or HeartbeatDecision.CONTINUE,
            job_id=JOB_ID, attempt_id=ATTEMPT_ID,
        )

    assert heartbeats == []
    assert not hasattr(process, "sent_signal")
    assert process.wait_timeout is not None


def test_leader_proc_reader_accepts_matching_zombie_evidence(monkeypatch) -> None:
    from services.job_worker import process_runner

    tail = ["Z", "1", "417", "417", *("0" for _ in range(15)), "99"]
    raw = f"417 (fast exit) {' '.join(tail)}".encode()
    read_fd, write_fd = os.pipe()
    os.write(write_fd, raw)
    os.close(write_fd)
    monkeypatch.setattr(process_runner.os, "open", lambda *args, **kwargs: read_fd)

    assert process_runner._read_leader_proc(417) == SessionMember(417, 417, 417, 99)


def test_mocked_fast_exit_keeps_zombie_for_leader_but_not_session_scan(monkeypatch) -> None:
    from services.job_worker import process_runner

    class Entry:
        name = "417"

    class Scan:
        def __enter__(self): return iter([Entry()])
        def __exit__(self, *args): return False

    tail = ["Z", "1", "417", "417", *("0" for _ in range(15)), "99"]
    raw = f"417 (fast exit) {' '.join(tail)}".encode()
    observed = []

    def open_stat(*_args, **_kwargs):
        read_fd, write_fd = os.pipe()
        os.write(write_fd, raw)
        os.close(write_fd)
        return read_fd

    monkeypatch.setattr(
        process_runner.os, "waitid",
        lambda idtype, pid, flags: observed.append((idtype, pid, flags)) or object(),
    )
    monkeypatch.setattr(process_runner.os, "open", open_stat)
    monkeypatch.setattr(process_runner.os, "scandir", lambda _path: Scan())

    assert process_runner._leader_exited_wnowait(417)
    assert process_runner._read_leader_proc(417) == SessionMember(417, 417, 417, 99)
    assert process_runner._session_members_proc(identity()) == ()
    assert observed == [(os.P_PID, 417, os.WEXITED | os.WNOHANG | os.WNOWAIT)]


def test_fast_exit_uses_zombie_leader_revalidation_and_completes(monkeypatch, tmp_path) -> None:
    process = FakeProcess([0])
    prepared = Prepared(command())
    leader = SessionMember(417, 417, 417, 99)
    pidfds = PidfdHarness(lambda: ())
    options = pidfds.options()
    options["member_inspector"] = lambda _pid: None
    options["leader_inspector"] = lambda _pid: leader
    monkeypatch.setattr("services.job_worker.process_runner.consume_prepared_spawn", lambda item: item.value)
    monkeypatch.setattr("services.job_worker.process_runner.build_child_environment", lambda settings: {})

    outcome = ProcessRunner(
        ArtifactWriter(tmp_path / "artifacts"), popen=lambda *args, **kwargs: process,
        inspector=Inspector([identity()]), leader_exited=process.leader_exited,
        session_members=lambda _identity: (), **options, sleep=lambda _: None,
    ).run(
        lambda: prepared, object(), 10, lambda _: HeartbeatDecision.CONTINUE,
        job_id=JOB_ID, attempt_id=ATTEMPT_ID,
    )

    assert outcome.exit_code == 0
    assert outcome.termination_reason is None


def test_opened_leader_pidfd_mismatch_signals_only_exact_pidfd(monkeypatch, tmp_path) -> None:
    process = FakeProcess([0])
    prepared = Prepared(command())
    signals = []
    pidfds = PidfdHarness(lambda: (), lambda pid, sig: signals.append((pid, sig)))
    options = pidfds.options()
    options["leader_inspector"] = lambda _pid: SessionMember(417, 417, 417, 100)
    monkeypatch.setattr("services.job_worker.process_runner.consume_prepared_spawn", lambda item: item.value)
    monkeypatch.setattr("services.job_worker.process_runner.build_child_environment", lambda settings: {})

    with pytest.raises(RuntimeError, match="PROCESS_GROUP_CLEANUP_UNPROVEN"):
        ProcessRunner(
            ArtifactWriter(tmp_path / "artifacts"), popen=lambda *args, **kwargs: process,
            inspector=Inspector([identity()]), leader_exited=process.leader_exited,
            session_members=lambda _identity: (), **options, sleep=lambda _: None,
        ).run(
            lambda: prepared, object(), 10, lambda _: HeartbeatDecision.CONTINUE,
            job_id=JOB_ID, attempt_id=ATTEMPT_ID,
        )

    assert signals == [(417, signal.SIGKILL)]
    assert not hasattr(process, "sent_signal")
    assert process.wait_timeout is not None
