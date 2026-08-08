from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid5

import pytest

from packages.engine_contracts import (
    EngineCommandEnvelope,
    EngineEvent,
    EngineEventEnvelope,
    EventAttribute,
    EventFamily,
    canonical_json_bytes,
    payload_digest,
)
from packages.nautilus_backtest import (
    SCENARIO_IDS,
    BacktestScenarioV1,
    build_canonical_simulation_fixture,
    calculate_reference_outcome,
)
from services.job_worker.engine_spawn_interface import EngineSpawnError
from scripts import verify_nautilus_v12_r3_parity as verifier
from packages.research_validation import materialize_phase4_campaign


def _input_digest(envelope: EngineCommandEnvelope) -> str:
    names = (
        "engine_configuration",
        "instrument_catalog",
        "strategy_configuration",
        "market_data",
        "simulation_scenario",
    )
    return hashlib.sha256(
        canonical_json_bytes(
            {name: getattr(envelope.payload, name).sha256 for name in names}
        )
    ).hexdigest()


def _event_bytes(
    envelope: EngineCommandEnvelope,
    *,
    mismatch: bool = False,
    reverse_attributes: bool = False,
) -> bytes:
    scenario_id = next(
        candidate
        for candidate in SCENARIO_IDS
        if hashlib.sha256(
            build_canonical_simulation_fixture(candidate).simulation_scenario
        ).hexdigest()
        == envelope.payload.simulation_scenario.sha256
    )
    fixture = build_canonical_simulation_fixture(scenario_id)
    scenario = BacktestScenarioV1.from_mounted_artifacts(
        scenario_bytes=fixture.simulation_scenario,
        catalog_bytes=fixture.instrument_catalog,
        strategy_bytes=fixture.strategy_configuration,
        market_data_bytes=fixture.market_data,
        start_time=envelope.payload.start_time,
        end_time=envelope.payload.end_time,
    )
    expected = calculate_reference_outcome(scenario)
    values: list[tuple[str, str | int]] = [
        ("input_artifacts_sha256", _input_digest(envelope)),
        ("scenario_digest", expected.scenario_digest),
        ("scenario_id", expected.scenario_id),
        ("event_digest", "0" * 64 if mismatch else expected.event_digest),
        ("iterations", expected.iterations),
        ("total_events", expected.total_events),
        ("total_orders", expected.total_orders),
        ("total_fills", expected.total_fills),
        ("total_positions", expected.total_positions),
        ("filled_quantity", str(expected.filled_quantity)),
        ("remaining_quantity", str(expected.remaining_quantity)),
        ("position_quantity", str(expected.position_quantity)),
        ("average_entry_price", str(expected.average_entry_price)),
        ("fees", str(expected.fees)),
        ("realized_pnl", str(expected.realized_pnl)),
        ("unrealized_pnl", str(expected.unrealized_pnl)),
        (
            "stop_take_profit_precedence",
            expected.stop_take_profit_precedence,
        ),
    ]
    if reverse_attributes:
        values.reverse()
    payload = EngineEvent(
        event_type="NautilusBacktestSimulationCompleted",
        family=EventFamily.ENGINE_LIFECYCLE,
        attributes=tuple(
            EventAttribute(name=name, value=value) for name, value in values
        ),
    )
    event = EngineEventEnvelope(
        message_id=uuid5(
            envelope.message_id, "NautilusBacktestSimulationCompleted"
        ),
        correlation_id=envelope.correlation_id,
        causation_id=envelope.message_id,
        engine_run_id=envelope.engine_run_id,
        stream_sequence=envelope.stream_sequence + 1,
        event_time=envelope.event_time,
        initialization_time=envelope.initialization_time,
        schema_version=envelope.schema_version,
        producer_identity=envelope.producer_identity,
        source_commit=envelope.source_commit,
        config_digest=envelope.config_digest,
        payload_digest=payload_digest(payload),
        payload=payload,
    )
    return canonical_json_bytes(event)


class _Harness:
    def __init__(
        self,
        *,
        candidate_schema: int = 6,
        rollback_schema: int = 3,
        stderr: bytes = b"",
        stdout_transform: Callable[[bytes, int], bytes] | None = None,
        consume_error: EngineSpawnError | None = None,
        candidate_drift_after_initial: bool = False,
        prepare_error_after_transport: EngineSpawnError | None = None,
    ) -> None:
        self.candidate_schema = candidate_schema
        self.rollback_schema = rollback_schema
        self.stderr = stderr
        self.stdout_transform = stdout_transform or (lambda value, _run: value + b"\n")
        self.consume_error = consume_error
        self.candidate_drift_after_initial = candidate_drift_after_initial
        self.prepare_error_after_transport = prepare_error_after_transport
        self.candidate_attest_calls = 0
        self.attest_calls: list[tuple[str, Path]] = []
        self.prepare_calls: list[EngineCommandEnvelope] = []
        self.consume_calls = 0
        self.popen_calls: list[dict[str, object]] = []
        self.provider_kwargs: list[dict[str, object]] = []
        self.input_sources: list[Path] = []
        self._run_by_scenario: dict[str, int] = {}

    def attest(self, config, *, expected_profile: str):
        assert stat.S_IMODE(config.artifact_directory.stat().st_mode) == 0o500
        self.attest_calls.append((expected_profile, config.artifact_directory))
        if expected_profile == "zero-order":
            return SimpleNamespace(
                manifest_schema_version=self.rollback_schema,
                closure_sha256="a" * 64,
                closure_manifest=None,
                profile="zero-order",
            )
        self.candidate_attest_calls += 1
        return SimpleNamespace(
            manifest_schema_version=self.candidate_schema,
            closure_sha256=(
                "d" * 64
                if self.candidate_drift_after_initial
                and self.candidate_attest_calls > 1
                else "b" * 64
            ),
            closure_manifest=SimpleNamespace(sha256="c" * 64),
            profile="execution-simulation",
        )

    def provider_factory(self, **kwargs):
        harness = self
        self.provider_kwargs.append(kwargs)

        class Provider:
            def prepare(self, envelope: EngineCommandEnvelope):
                kwargs["attest_closure"]()
                inputs = kwargs["attest_inputs"](envelope.payload)
                harness.input_sources.extend(item.source for item in inputs)
                run_directory = (
                    kwargs["transport_root"]
                    / f"run-{envelope.engine_run_id.hex}"
                )
                run_directory.mkdir(mode=0o700)
                for name in ("request.json", "request.sha256"):
                    path = run_directory / name
                    path.write_bytes(b"sealed")
                    path.chmod(0o400)
                harness.prepare_calls.append(envelope)
                if harness.prepare_error_after_transport is not None:
                    raise harness.prepare_error_after_transport
                return SimpleNamespace(envelope=envelope)

        return Provider()

    def consume(self, prepared):
        self.consume_calls += 1
        if self.consume_error is not None:
            raise self.consume_error
        scenario_id = next(
            candidate
            for candidate in SCENARIO_IDS
            if hashlib.sha256(
                build_canonical_simulation_fixture(candidate).simulation_scenario
            ).hexdigest()
            == prepared.envelope.payload.simulation_scenario.sha256
        )
        run = self._run_by_scenario.get(scenario_id, 0) + 1
        self._run_by_scenario[scenario_id] = run
        return SimpleNamespace(
            argv=("inert-sandbox", scenario_id, str(run)),
            cwd=Path("/"),
            environment={},
            pass_fds=(),
            close_after_spawn_fds=(),
            timeout_seconds=7,
            envelope=prepared.envelope,
        )

    def popen(self, argv, **kwargs):
        scenario_id = argv[1]
        run = int(argv[2])
        envelope = self.prepare_calls[-1]
        assert kwargs["cwd"] == Path("/")
        assert kwargs["env"] == {}
        assert kwargs["pass_fds"] == ()
        self.popen_calls.append({"argv": argv, **kwargs})
        stdout = self.stdout_transform(_event_bytes(envelope), run)
        stderr = self.stderr

        class Process:
            returncode = 0

            def communicate(self, *, timeout: int):
                assert timeout == 7
                return stdout, stderr

        return Process()


@pytest.fixture
def external_paths() -> dict[str, Path]:
    with tempfile.TemporaryDirectory(
        prefix="nautilus-parity-test-", dir="/tmp"
    ) as directory:
        root = Path(directory)
        root.chmod(0o700)
        paths = {
            "rollback_closure": root / "rollback",
            "candidate_closure": root / "candidate",
            "rollback_artifact_directory": root / "rollback-artifacts",
            "artifact_directory": root / "artifacts",
            "sandbox": root / "sandbox",
            "campaign_directory": root / "campaign",
            "transport_root": root / "transport",
            "record": root / "evidence" / "parity.json",
        }
        for name in (
            "rollback_closure",
            "candidate_closure",
            "rollback_artifact_directory",
            "artifact_directory",
            "transport_root",
        ):
            paths[name].mkdir(mode=0o700)
        for name in ("rollback_artifact_directory", "artifact_directory"):
            marker = paths[name] / "artifact-manifest.json"
            marker.write_text("{}")
            marker.chmod(0o400)
            paths[name].chmod(0o500)
        paths["record"].parent.mkdir(mode=0o700)
        materialize_phase4_campaign(paths["campaign_directory"])
        yield paths


def _verify(paths: dict[str, Path], harness: _Harness, **overrides):
    arguments = {
        **paths,
        "attest_closure": harness.attest,
        "provider_factory": harness.provider_factory,
        "consume_spawn": harness.consume,
        "popen_factory": harness.popen,
    }
    arguments.update(overrides)
    return verifier.verify_nautilus_v12_r3_parity(**arguments)


def _assert_retained_runs(transport_root: Path, *, expected_count: int) -> None:
    run_directories = tuple(
        path
        for path in transport_root.rglob("run-*")
        if path.is_dir() and not path.is_symlink()
    )
    assert len(run_directories) == expected_count
    for run_directory in run_directories:
        assert stat.S_IMODE(run_directory.stat().st_mode) == 0o700
        assert {path.name for path in run_directory.iterdir()} == {
            "request.json",
            "request.sha256",
        }
        for member in run_directory.iterdir():
            observed = member.stat()
            assert stat.S_IMODE(observed.st_mode) == 0o400
            assert observed.st_nlink == 1


def test_launch_uses_exact_built_authority_and_closes_fds_before_wait() -> None:
    """Delaying descriptor closure would retain spawn authority while waiting."""
    read_fd, write_fd = os.pipe()
    environment: dict[str, str] = {}
    built = SimpleNamespace(
        argv=("inert-sandbox", "event-digest", "1"),
        cwd=Path("/"),
        environment=environment,
        pass_fds=(read_fd,),
        close_after_spawn_fds=(read_fd,),
        timeout_seconds=7,
    )

    class Process:
        returncode = 0

        def communicate(self, *, timeout: int):
            assert timeout == built.timeout_seconds
            with pytest.raises(OSError):
                os.fstat(read_fd)
            return b"{}\n", b""

    def popen(argv, **kwargs):
        os.fstat(read_fd)
        assert argv is built.argv
        assert kwargs["cwd"] is built.cwd
        assert kwargs["env"] is environment
        assert kwargs["pass_fds"] is built.pass_fds
        return Process()

    try:
        assert verifier._launch_once(built, popen_factory=popen) == b"{}"
    finally:
        os.close(write_fd)
        try:
            os.close(read_fd)
        except OSError:
            pass


def test_launch_rejects_timeout_after_killing_and_reaping_once() -> None:
    events: list[object] = []
    built = SimpleNamespace(
        argv=("inert-sandbox", "event-digest", "1"),
        cwd=Path("/"),
        environment={},
        pass_fds=(),
        close_after_spawn_fds=(),
        timeout_seconds=7,
    )

    class Process:
        returncode = -9
        communicate_calls = 0

        def communicate(self, *, timeout: int):
            self.communicate_calls += 1
            events.append(("communicate", timeout))
            if self.communicate_calls == 1:
                raise subprocess.TimeoutExpired(built.argv, timeout)
            return b"", b""

        def kill(self) -> None:
            events.append("kill")

    with pytest.raises(verifier.ParityVerificationError, match="timeout"):
        verifier._launch_once(
            built,
            popen_factory=lambda *_args, **_kwargs: Process(),
        )

    assert events == [("communicate", 7), "kill", ("communicate", 7)]


def test_launch_rejects_nonzero_process_completion() -> None:
    built = SimpleNamespace(
        argv=("inert-sandbox", "event-digest", "1"),
        cwd=Path("/"),
        environment={},
        pass_fds=(),
        close_after_spawn_fds=(),
        timeout_seconds=7,
    )

    class Process:
        returncode = 41

        def communicate(self, *, timeout: int):
            assert timeout == 7
            return b"valid-looking-event\n", b""

    with pytest.raises(verifier.ParityVerificationError, match="unsuccessfully"):
        verifier._launch_once(
            built,
            popen_factory=lambda *_args, **_kwargs: Process(),
        )


def test_verifier_runs_exact_eight_by_two_matrix_and_writes_digest_only_record(
    external_paths: dict[str, Path],
) -> None:
    """Reducing the matrix or leaking runtime inputs must fail this evidence test."""
    harness = _Harness()

    record = _verify(external_paths, harness)

    assert [item["scenario_id"] for item in record["scenarios"]] == list(SCENARIO_IDS)
    assert len(harness.prepare_calls) == 16
    assert harness.consume_calls == 16
    assert len(harness.popen_calls) == 16
    assert len(harness.provider_kwargs) == 16
    expected_roots = {
        external_paths["transport_root"] / f"parity-{scenario_id}-run-{run}"
        for scenario_id in SCENARIO_IDS
        for run in (1, 2)
    }
    assert all(
        item["expected_manifest_schema_version"] == 6
        for item in harness.provider_kwargs
    )
    assert {
        item["transport_root"] for item in harness.provider_kwargs
    } == expected_roots
    assert set(external_paths["transport_root"].iterdir()) == expected_roots
    _assert_retained_runs(external_paths["transport_root"], expected_count=16)
    assert record["schema_version"] == "nautilus-phase4-parity-evidence-v2"
    assert record["status"] == "passed"
    assert record["candidate_closure_sha256"] == "b" * 64
    assert record["candidate_manifest_sha256"] == "c" * 64
    assert record["candidate_manifest_schema_version"] == 6
    campaign_raw = (
        external_paths["campaign_directory"] / "campaign-manifest.json"
    ).read_bytes()
    assert record["scenario_campaign_sha256"] == hashlib.sha256(campaign_raw).hexdigest()
    assert record["strategy_source_sha256"] == json.loads(campaign_raw)[
        "strategy_source_sha256"
    ]
    assert all(
        item["run_1_event_sha256"] == item["run_2_event_sha256"]
        == item["independent_reference_event_sha256"]
        == item["nautilus_event_sha256"]
        for item in record["scenarios"]
    )
    assert all(
        item["independent_reference_result_sha256"]
        == item["nautilus_result_sha256"]
        for item in record["scenarios"]
    )
    assert all(
        source.is_relative_to(external_paths["campaign_directory"])
        for source in harness.input_sources
    )
    written = external_paths["record"].read_bytes()
    assert written == canonical_json_bytes(record) + b"\n"
    assert stat.S_IMODE(external_paths["record"].stat().st_mode) == 0o400
    assert not any(
        str(path).encode() in written for path in external_paths.values()
    )
    assert b"open_time" not in written
    assert b"NautilusBacktestSimulationCompleted" not in written


@pytest.mark.parametrize(
    ("scenario_ids", "run_count"),
    (
        (SCENARIO_IDS[:-1], 2),
        ((*SCENARIO_IDS, SCENARIO_IDS[-1]), 2),
        ((*SCENARIO_IDS[:-1], "unknown"), 2),
        (SCENARIO_IDS, 1),
        (SCENARIO_IDS, 3),
    ),
)
def test_verifier_rejects_any_matrix_other_than_exact_eight_by_two(
    external_paths: dict[str, Path], scenario_ids: tuple[str, ...], run_count: int
) -> None:
    """Making the matrix caller-configurable must not weaken parity evidence."""
    with pytest.raises(verifier.ParityVerificationError, match="matrix"):
        _verify(
            external_paths,
            _Harness(),
            scenario_ids=scenario_ids,
            run_count=run_count,
        )


@pytest.mark.parametrize("schema", (1, 2, 3, 4, 5, 6.0, True))
def test_verifier_rejects_candidate_other_than_schema_six(
    external_paths: dict[str, Path], schema: object
) -> None:
    """Accepting an older closure would bypass the native-entry authority."""
    with pytest.raises(verifier.ParityVerificationError, match="schema 6"):
        _verify(external_paths, _Harness(candidate_schema=schema))


@pytest.mark.parametrize("schema", (0, 4, 5))
def test_verifier_rejects_rollback_outside_schema_one_through_three(
    external_paths: dict[str, Path], schema: int
) -> None:
    with pytest.raises(verifier.ParityVerificationError, match="rollback"):
        _verify(external_paths, _Harness(rollback_schema=schema))


@pytest.mark.parametrize("mutation", ("nonempty", "public"))
def test_verifier_rejects_nonempty_or_public_transport_root(
    external_paths: dict[str, Path], mutation: str
) -> None:
    if mutation == "nonempty":
        (external_paths["transport_root"] / "foreign").write_text("occupied")
    else:
        external_paths["transport_root"].chmod(0o755)

    with pytest.raises(verifier.ParityVerificationError, match="transport"):
        _verify(external_paths, _Harness())


def test_verifier_rejects_record_inside_checkout(
    external_paths: dict[str, Path],
) -> None:
    inside = Path.cwd() / ".forbidden-parity-record.json"

    with pytest.raises(verifier.ParityVerificationError, match="record"):
        _verify(external_paths, _Harness(), record=inside)

    assert not inside.exists()


def test_verifier_rejects_record_beneath_ephemeral_transport(
    external_paths: dict[str, Path],
) -> None:
    record = external_paths["transport_root"] / "parity.json"
    harness = _Harness()

    with pytest.raises(verifier.ParityVerificationError, match="record"):
        _verify(external_paths, harness, record=record)

    assert harness.prepare_calls == []
    assert not record.exists()


def test_verifier_retains_partial_transport_when_prepare_fails_after_publication(
    external_paths: dict[str, Path],
) -> None:
    primary = EngineSpawnError("ENGINE_INPUT_STALE", "prepare authority changed")
    harness = _Harness(prepare_error_after_transport=primary)

    with pytest.raises(EngineSpawnError, match="prepare authority changed"):
        _verify(external_paths, harness)

    _assert_retained_runs(external_paths["transport_root"], expected_count=1)
    retained_subroot = next(external_paths["transport_root"].iterdir())
    assert retained_subroot.name == "parity-long-accounting-run-1"
    assert stat.S_IMODE(retained_subroot.stat().st_mode) == 0o500
    assert not external_paths["record"].exists()


def test_verifier_seals_mode_0500_subroot_when_open_fails_after_mkdir(
    external_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_open = verifier.os.open
    failed = False

    def fail_first_subroot_open(path, flags, *args, **kwargs):
        nonlocal failed
        if not failed and os.fspath(path) == "parity-long-accounting-run-1":
            failed = True
            raise OSError("inert parity subroot open gap")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(verifier.os, "open", fail_first_subroot_open)

    with pytest.raises(
        verifier.ParityVerificationError,
        match="transport subroot",
    ):
        _verify(external_paths, _Harness())

    retained = list(external_paths["transport_root"].iterdir())
    assert [path.name for path in retained] == ["parity-long-accounting-run-1"]
    assert stat.S_IMODE(retained[0].stat().st_mode) == 0o500
    assert list(retained[0].iterdir()) == []


def test_verifier_reseals_opened_subroot_when_construction_fchmod_fails(
    external_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_fchmod = verifier.os.fchmod
    failed = False

    def fail_first_construction_fchmod(descriptor: int, mode: int) -> None:
        nonlocal failed
        if not failed and mode == 0o700:
            failed = True
            raise OSError("inert parity subroot construction mode failure")
        real_fchmod(descriptor, mode)

    monkeypatch.setattr(verifier.os, "fchmod", fail_first_construction_fchmod)

    with pytest.raises(
        verifier.ParityVerificationError,
        match="transport subroot",
    ):
        _verify(external_paths, _Harness())

    assert failed is True
    retained = list(external_paths["transport_root"].iterdir())
    assert [path.name for path in retained] == ["parity-long-accounting-run-1"]
    assert stat.S_IMODE(retained[0].stat().st_mode) == 0o500
    assert list(retained[0].iterdir()) == []


def test_verifier_preserves_subroot_primary_and_notes_unexpected_prefix_entry(
    external_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_open = verifier.os.open
    failed = False

    def inject_unexpected_then_fail(path, flags, *args, **kwargs):
        nonlocal failed
        if not failed and os.fspath(path) == "parity-long-accounting-run-1":
            failed = True
            os.mkdir("unexpected", mode=0o500, dir_fd=kwargs["dir_fd"])
            raise OSError("inert parity subroot open gap")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(verifier.os, "open", inject_unexpected_then_fail)

    with pytest.raises(
        verifier.ParityVerificationError,
        match="transport subroot",
    ) as observed:
        _verify(external_paths, _Harness())

    assert any("forensic" in note for note in observed.value.__notes__)
    assert {path.name for path in external_paths["transport_root"].iterdir()} == {
        "parity-long-accounting-run-1",
        "unexpected",
    }


def test_verifier_preserves_primary_failure_when_forensic_validation_also_fails(
    external_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary = EngineSpawnError("ENGINE_INPUT_STALE", "primary consume failure")
    harness = _Harness(consume_error=primary)
    monkeypatch.setattr(
        verifier,
        "_validate_retained_transport_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            verifier.ParityVerificationError("secondary forensic validation failure")
        ),
        raising=False,
    )

    with pytest.raises(EngineSpawnError, match="primary consume failure") as observed:
        _verify(external_paths, harness)

    assert any("forensic" in note for note in observed.value.__notes__)
    _assert_retained_runs(external_paths["transport_root"], expected_count=1)


def test_verifier_rejects_record_parent_substitution_during_publication(
    external_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = external_paths["record"]
    parent = record.parent
    displaced = parent.parent / "displaced-evidence"
    real_fsync = os.fsync
    swapped = False

    def replace_parent(descriptor: int) -> None:
        nonlocal swapped
        if not swapped and record.exists():
            parent.rename(displaced)
            parent.mkdir(mode=0o700)
            swapped = True
        real_fsync(descriptor)

    monkeypatch.setattr(verifier.os, "fsync", replace_parent)

    with pytest.raises(verifier.ParityVerificationError, match="record.*identity|parent"):
        _verify(external_paths, _Harness())

    assert swapped is True
    assert not record.exists()
    retained = displaced / record.name
    assert retained.exists()
    assert stat.S_IMODE(retained.stat().st_mode) == 0o400


def test_verifier_rejects_record_replacement_and_preserves_replacement_inode(
    external_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = external_paths["record"]
    real_fsync = os.fsync
    replacement_identity: tuple[int, int] | None = None

    def replace_record(descriptor: int) -> None:
        nonlocal replacement_identity
        if replacement_identity is None and record.exists():
            record.unlink()
            record.write_bytes(b"replacement")
            record.chmod(0o400)
            observed = record.stat()
            replacement_identity = (observed.st_dev, observed.st_ino)
        real_fsync(descriptor)

    monkeypatch.setattr(verifier.os, "fsync", replace_record)

    with pytest.raises(verifier.ParityVerificationError, match="record.*identity|named"):
        _verify(external_paths, _Harness())

    assert replacement_identity is not None
    observed = record.stat()
    assert (observed.st_dev, observed.st_ino) == replacement_identity


def test_verifier_rejects_partial_record_write_and_retains_its_reserved_inode(
    external_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_write = verifier.os.write
    calls = 0

    def partial_then_fail(descriptor: int, value) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            return real_write(descriptor, value[:7])
        raise OSError("inert partial evidence write failure")

    monkeypatch.setattr(verifier.os, "write", partial_then_fail)

    with pytest.raises(verifier.ParityVerificationError, match="cannot be sealed"):
        _verify(external_paths, _Harness())

    assert calls == 2
    assert external_paths["record"].exists()
    assert stat.S_IMODE(external_paths["record"].stat().st_mode) == 0o400


def test_verifier_failed_record_publication_never_calls_unlink_reservation(
    external_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_write = verifier.os.write
    calls = 0
    rollback_calls: list[object] = []

    def partial_then_fail(descriptor: int, value) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            return real_write(descriptor, value[:7])
        raise OSError("inert partial evidence write failure")

    monkeypatch.setattr(verifier.os, "write", partial_then_fail)
    monkeypatch.setattr(
        verifier,
        "_unlink_reserved_record",
        lambda reservation: rollback_calls.append(reservation),
        raising=False,
    )

    with pytest.raises(verifier.ParityVerificationError, match="cannot be sealed"):
        _verify(external_paths, _Harness())

    assert rollback_calls == []
    assert external_paths["record"].exists()


@pytest.mark.parametrize(
    ("stderr", "transform", "message"),
    (
        (b"warning\n", None, "stderr"),
        (b"", lambda _value, _run: b"", "stdout"),
        (b"", lambda value, _run: value + b"\n" + value + b"\n", "stdout"),
        (b"", lambda value, _run: value + b" \n", "canonical"),
    ),
)
def test_verifier_rejects_noncanonical_process_output(
    external_paths: dict[str, Path],
    stderr: bytes,
    transform: Callable[[bytes, int], bytes] | None,
    message: str,
) -> None:
    harness = _Harness(stderr=stderr, stdout_transform=transform)

    with pytest.raises(verifier.ParityVerificationError, match=message):
        _verify(external_paths, harness)


def test_verifier_rejects_oracle_mismatch(
    external_paths: dict[str, Path],
) -> None:
    harness = _Harness(
        stdout_transform=lambda _value, _run: _event_bytes(
            harness.prepare_calls[-1], mismatch=True
        )
        + b"\n"
    )

    with pytest.raises(verifier.ParityVerificationError, match="oracle"):
        _verify(external_paths, harness)


def test_verifier_rejects_different_valid_event_bytes_between_runs(
    external_paths: dict[str, Path],
) -> None:
    harness = _Harness(
        stdout_transform=lambda value, run: (
            value
            if run == 1
            else _event_bytes(harness.prepare_calls[-1], reverse_attributes=True)
        )
        + b"\n"
    )

    with pytest.raises(verifier.ParityVerificationError, match="non-identical"):
        _verify(external_paths, harness)


@pytest.mark.parametrize("reason", ("ENGINE_CLOSURE_STALE", "ENGINE_INPUT_STALE"))
def test_verifier_propagates_prepare_consume_authority_drift(
    external_paths: dict[str, Path], reason: str
) -> None:
    error = EngineSpawnError(reason, "authority changed")

    with pytest.raises(EngineSpawnError, match="authority changed"):
        _verify(external_paths, _Harness(consume_error=error))


def test_verifier_rejects_candidate_authority_drift_between_matrix_runs(
    external_paths: dict[str, Path],
) -> None:
    """Every event must come from the candidate identity written to the record."""
    with pytest.raises(verifier.ParityVerificationError, match="candidate.*changed"):
        _verify(
            external_paths,
            _Harness(candidate_drift_after_initial=True),
        )


def test_verifier_binds_distinct_rollback_and_candidate_artifact_authorities(
    external_paths: dict[str, Path],
) -> None:
    """Binding rollback to candidate artifacts violates v3 and must fail tests."""
    harness = _Harness()

    _verify(external_paths, harness)

    assert harness.attest_calls[0] == (
        "zero-order",
        external_paths["rollback_artifact_directory"],
    )
    assert all(
        profile == "execution-simulation"
        and artifact_directory == external_paths["artifact_directory"]
        for profile, artifact_directory in harness.attest_calls[1:]
    )


def test_verifier_rejects_candidate_authority_substituted_for_rollback_before_launch(
    external_paths: dict[str, Path],
) -> None:
    """Candidate authority cannot satisfy the immutable v3 rollback binding."""
    harness = _Harness()

    with pytest.raises(verifier.ParityVerificationError, match="artifact.*distinct"):
        _verify(
            external_paths,
            harness,
            rollback_artifact_directory=external_paths["artifact_directory"],
        )

    assert harness.attest_calls == []
    assert harness.prepare_calls == []
    assert harness.popen_calls == []


@pytest.mark.parametrize(
    "mutation",
    ("relative", "noncanonical", "checkout", "empty", "non-private"),
)
def test_verifier_rejects_unsafe_rollback_artifact_authority_before_launch(
    external_paths: dict[str, Path], mutation: str
) -> None:
    """Unsafe rollback authority must fail before attestation or process launch."""
    rollback_artifacts = external_paths["rollback_artifact_directory"]
    if mutation == "relative":
        rollback_artifacts = Path("rollback-artifacts")
    elif mutation == "noncanonical":
        alias = rollback_artifacts.parent / "rollback-artifacts-alias"
        alias.symlink_to(rollback_artifacts, target_is_directory=True)
        rollback_artifacts = alias
    elif mutation == "checkout":
        rollback_artifacts = Path.cwd()
    elif mutation == "empty":
        rollback_artifacts = external_paths["transport_root"] / "empty-artifacts"
        rollback_artifacts.mkdir(mode=0o500)
    else:
        rollback_artifacts.chmod(0o700)
    harness = _Harness()

    with pytest.raises(verifier.ParityVerificationError, match="rollback artifact"):
        _verify(
            external_paths,
            harness,
            rollback_artifact_directory=rollback_artifacts,
        )

    assert harness.attest_calls == []
    assert harness.prepare_calls == []
    assert harness.popen_calls == []


def test_cli_requires_exactly_the_eight_named_arguments_including_campaign(
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = verifier._parser()
    required = [
        "--rollback-closure",
        "/tmp/rollback",
        "--candidate-closure",
        "/tmp/candidate",
        "--rollback-artifact-directory",
        "/tmp/rollback-artifacts",
        "--artifact-directory",
        "/tmp/artifacts",
        "--sandbox",
        "/tmp/sandbox",
        "--campaign-directory",
        "/tmp/campaign",
        "--transport-root",
        "/tmp/transport",
        "--record",
        "/tmp/record.json",
    ]

    parsed = parser.parse_args(required)
    assert vars(parsed) == {
        "rollback_closure": Path("/tmp/rollback"),
        "candidate_closure": Path("/tmp/candidate"),
        "rollback_artifact_directory": Path("/tmp/rollback-artifacts"),
        "artifact_directory": Path("/tmp/artifacts"),
        "sandbox": Path("/tmp/sandbox"),
        "campaign_directory": Path("/tmp/campaign"),
        "transport_root": Path("/tmp/transport"),
        "record": Path("/tmp/record.json"),
    }
    with pytest.raises(SystemExit):
        parser.parse_args([])
    with pytest.raises(SystemExit):
        parser.parse_args([*required[:4], *required[6:]])
    with pytest.raises(SystemExit):
        parser.parse_args([*required, "--runs", "2"])
    capsys.readouterr()
