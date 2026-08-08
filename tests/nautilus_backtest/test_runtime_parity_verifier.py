from __future__ import annotations

import hashlib
import json
import os
import stat
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
        candidate_schema: int = 5,
        rollback_schema: int = 3,
        stderr: bytes = b"",
        stdout_transform: Callable[[bytes, int], bytes] | None = None,
        consume_error: EngineSpawnError | None = None,
        candidate_drift_after_initial: bool = False,
    ) -> None:
        self.candidate_schema = candidate_schema
        self.rollback_schema = rollback_schema
        self.stderr = stderr
        self.stdout_transform = stdout_transform or (lambda value, _run: value + b"\n")
        self.consume_error = consume_error
        self.candidate_drift_after_initial = candidate_drift_after_initial
        self.candidate_attest_calls = 0
        self.prepare_calls: list[EngineCommandEnvelope] = []
        self.consume_calls = 0
        self.popen_calls: list[dict[str, object]] = []
        self.provider_kwargs: list[dict[str, object]] = []
        self._run_by_scenario: dict[str, int] = {}

    def attest(self, config, *, expected_profile: str):
        assert stat.S_IMODE(config.artifact_directory.stat().st_mode) == 0o500
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
                kwargs["attest_inputs"](envelope.payload)
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
            "artifact_directory": root / "artifacts",
            "sandbox": root / "sandbox",
            "transport_root": root / "transport",
            "record": root / "evidence" / "parity.json",
        }
        for name in (
            "rollback_closure",
            "candidate_closure",
            "artifact_directory",
            "transport_root",
        ):
            paths[name].mkdir(mode=0o700)
        paths["artifact_directory"].chmod(0o500)
        paths["record"].parent.mkdir(mode=0o700)
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
    assert len(harness.provider_kwargs) == 8
    assert all(
        item["expected_manifest_schema_version"] == 5
        and item["transport_root"] == external_paths["transport_root"]
        for item in harness.provider_kwargs
    )
    assert list(external_paths["transport_root"].iterdir()) == []
    assert record["schema_version"] == "nautilus-v12-r3-parity-evidence-v1"
    assert record["rollback_closure_sha256"] == "a" * 64
    assert record["candidate_closure_sha256"] == "b" * 64
    assert record["candidate_manifest_sha256"] == "c" * 64
    assert record["candidate_manifest_schema_version"] == 5
    assert all(
        item["run_1_event_sha256"] == item["run_2_event_sha256"]
        for item in record["scenarios"]
    )
    written = external_paths["record"].read_bytes()
    assert written == canonical_json_bytes(record) + b"\n"
    assert stat.S_IMODE(external_paths["record"].stat().st_mode) == 0o400
    assert not any(
        str(path).encode() in written for path in external_paths.values()
    )
    assert b"market_data" not in written
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


@pytest.mark.parametrize("schema", (1, 2, 3, 4, 5.0, 6))
def test_verifier_rejects_candidate_other_than_schema_five(
    external_paths: dict[str, Path], schema: object
) -> None:
    """Accepting an older closure would bypass the native-entry authority."""
    with pytest.raises(verifier.ParityVerificationError, match="schema 5"):
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


def test_cli_requires_exactly_the_six_named_arguments(
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = verifier._parser()
    required = [
        "--rollback-closure",
        "/tmp/rollback",
        "--candidate-closure",
        "/tmp/candidate",
        "--artifact-directory",
        "/tmp/artifacts",
        "--sandbox",
        "/tmp/sandbox",
        "--transport-root",
        "/tmp/transport",
        "--record",
        "/tmp/record.json",
    ]

    parsed = parser.parse_args(required)
    assert vars(parsed) == {
        "rollback_closure": Path("/tmp/rollback"),
        "candidate_closure": Path("/tmp/candidate"),
        "artifact_directory": Path("/tmp/artifacts"),
        "sandbox": Path("/tmp/sandbox"),
        "transport_root": Path("/tmp/transport"),
        "record": Path("/tmp/record.json"),
    }
    with pytest.raises(SystemExit):
        parser.parse_args([])
    with pytest.raises(SystemExit):
        parser.parse_args([*required, "--runs", "2"])
    capsys.readouterr()
