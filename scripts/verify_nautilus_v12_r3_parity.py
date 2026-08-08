#!/usr/bin/env python3
"""Produce finite digest-only evidence for Nautilus v12-r3 runtime parity."""

from __future__ import annotations

import argparse
import hashlib
import os
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypedDict

from pydantic import ValidationError

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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
    BacktestScenarioError,
    BacktestScenarioV1,
    NautilusBacktestError,
    calculate_reference_outcome,
    capture_prepared_engine_process,
    validate_isolated_simulation_result,
)
from packages.nautilus_backtest.scenarios import ScenarioId
from packages.research_validation.producers import (
    CampaignEvidenceError,
    load_verified_campaign,
)
from services.job_worker.engine_artifacts import HashBoundArtifactResolver
from services.job_worker.engine_spawn import (
    EngineSpawnProvider,
    consume_prepared_engine_spawn,
)
from services.job_worker.engine_spawn_interface import EngineSpawnError
from services.job_worker.nautilus_closure import (
    NautilusClosureConfig,
    attest_nautilus_backtest_closure,
)


_CHECKOUT = Path(__file__).resolve().parents[1]
_RUN_COUNT = 2
_SHA256_LENGTH = 64


class ParityVerificationError(RuntimeError):
    """The finite runtime parity evidence could not be established."""


class ScenarioParityRecord(TypedDict):
    scenario_id: ScenarioId
    engine_configuration_sha256: str
    instrument_catalog_sha256: str
    strategy_configuration_sha256: str
    market_data_sha256: str
    simulation_scenario_sha256: str
    independent_reference_result_sha256: str
    independent_reference_event_sha256: str
    nautilus_result_sha256: str
    nautilus_event_sha256: str
    run_1_event_sha256: str
    run_2_event_sha256: str


class V12R3ParityRecord(TypedDict):
    schema_version: Literal["nautilus-phase4-parity-evidence-v2"]
    status: Literal["passed"]
    scenario_campaign_sha256: str
    strategy_source_sha256: str
    candidate_closure_sha256: str
    candidate_manifest_sha256: str
    candidate_manifest_schema_version: Literal[6]
    scenarios: list[ScenarioParityRecord]


@dataclass(frozen=True, slots=True)
class _ReservedRecord:
    descriptor: int
    parent_descriptor: int
    path: Path
    parent_identity: tuple[int, int]
    identity: tuple[int, int]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify the fixed Nautilus v12-r3 8x2 runtime parity matrix.",
        allow_abbrev=False,
    )
    parser.add_argument("--rollback-closure", required=True, type=Path)
    parser.add_argument("--candidate-closure", required=True, type=Path)
    parser.add_argument("--rollback-artifact-directory", required=True, type=Path)
    parser.add_argument("--artifact-directory", required=True, type=Path)
    parser.add_argument("--sandbox", required=True, type=Path)
    parser.add_argument("--campaign-directory", required=True, type=Path)
    parser.add_argument("--transport-root", required=True, type=Path)
    parser.add_argument("--record", required=True, type=Path)
    return parser


def _is_beneath(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _require_absolute(path: Path, *, label: str) -> None:
    if (
        not isinstance(path, Path)
        or not path.is_absolute()
        or path == Path("/")
        or ".." in path.parts
    ):
        raise ParityVerificationError(f"{label} path is unsafe")


def _require_private_directory(
    path: Path,
    *,
    label: str,
    external: bool,
    empty: bool,
    nonempty: bool = False,
    expected_mode: int,
) -> None:
    _require_absolute(path, label=label)
    try:
        observed = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ParityVerificationError(f"{label} directory is unavailable") from exc
    if (
        resolved != path
        or stat.S_ISLNK(observed.st_mode)
        or not stat.S_ISDIR(observed.st_mode)
        or observed.st_uid != os.geteuid()
        or stat.S_IMODE(observed.st_mode) != expected_mode
        or (external and _is_beneath(path, _CHECKOUT))
    ):
        raise ParityVerificationError(f"{label} directory is unsafe")
    if empty or nonempty:
        try:
            occupied = next(path.iterdir(), None)
        except OSError as exc:
            raise ParityVerificationError(
                f"{label} directory cannot be inspected"
            ) from exc
        if occupied is not None:
            if empty:
                raise ParityVerificationError(f"{label} directory must be empty")
        elif nonempty:
            raise ParityVerificationError(f"{label} directory must be non-empty")


def _validate_record_path(
    record: Path,
    *,
    transport_root: Path,
) -> tuple[int, int]:
    _require_absolute(record, label="record")
    try:
        parent = record.parent.resolve(strict=True)
        observed_parent = record.parent.lstat()
    except OSError as exc:
        raise ParityVerificationError("record parent is unavailable") from exc
    if (
        parent != record.parent
        or _is_beneath(record, _CHECKOUT)
        or _is_beneath(record, transport_root)
        or stat.S_ISLNK(observed_parent.st_mode)
        or not stat.S_ISDIR(observed_parent.st_mode)
        or observed_parent.st_uid != os.geteuid()
        or stat.S_IMODE(observed_parent.st_mode) != 0o700
    ):
        raise ParityVerificationError("record path is unsafe")
    try:
        record.lstat()
    except FileNotFoundError:
        return observed_parent.st_dev, observed_parent.st_ino
    except OSError as exc:
        raise ParityVerificationError("record path cannot be inspected") from exc
    raise ParityVerificationError("record already exists")


def _named_record_stat(reservation: _ReservedRecord):
    try:
        parent = os.fstat(reservation.parent_descriptor)
        named_parent = reservation.path.parent.lstat()
        opened = os.fstat(reservation.descriptor)
        named = os.stat(
            reservation.path.name,
            dir_fd=reservation.parent_descriptor,
            follow_symlinks=False,
        )
    except OSError as exc:
        raise ParityVerificationError(
            "parity record named entry is unavailable"
        ) from exc
    if (
        (parent.st_dev, parent.st_ino) != reservation.parent_identity
        or (named_parent.st_dev, named_parent.st_ino)
        != reservation.parent_identity
        or stat.S_ISLNK(named_parent.st_mode)
        or not stat.S_ISDIR(named_parent.st_mode)
        or named_parent.st_uid != os.geteuid()
        or stat.S_IMODE(named_parent.st_mode) != 0o700
        or (opened.st_dev, opened.st_ino) != reservation.identity
        or (named.st_dev, named.st_ino) != reservation.identity
        or not stat.S_ISREG(opened.st_mode)
        or not stat.S_ISREG(named.st_mode)
        or opened.st_uid != os.geteuid()
        or named.st_uid != os.geteuid()
        or opened.st_nlink != 1
        or named.st_nlink != 1
        or stat.S_IMODE(opened.st_mode) != 0o400
        or stat.S_IMODE(named.st_mode) != 0o400
    ):
        raise ParityVerificationError(
            "parity record identity or mode 0400 changed"
        )
    return opened, named


def _verify_reserved_record(
    reservation: _ReservedRecord,
    *,
    expected_size: int,
) -> None:
    opened, named = _named_record_stat(reservation)
    if opened.st_size != expected_size or named.st_size != expected_size:
        raise ParityVerificationError("parity record identity changed")


def _unlink_reserved_record(reservation: _ReservedRecord) -> None:
    try:
        parent = os.fstat(reservation.parent_descriptor)
        opened = os.fstat(reservation.descriptor)
        named = os.stat(
            reservation.path.name,
            dir_fd=reservation.parent_descriptor,
            follow_symlinks=False,
        )
    except OSError:
        return
    if (
        (parent.st_dev, parent.st_ino) == reservation.parent_identity
        and (opened.st_dev, opened.st_ino) == reservation.identity
        and (named.st_dev, named.st_ino) == reservation.identity
    ):
        try:
            os.unlink(reservation.path.name, dir_fd=reservation.parent_descriptor)
        except OSError:
            pass


def _close_reserved_record(reservation: _ReservedRecord) -> None:
    try:
        os.close(reservation.descriptor)
    finally:
        os.close(reservation.parent_descriptor)


def _reserve_record(
    path: Path,
    *,
    parent_identity: tuple[int, int],
) -> _ReservedRecord:
    parent_descriptor = -1
    descriptor = -1
    reservation: _ReservedRecord | None = None
    try:
        parent_descriptor = os.open(
            path.parent,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        observed_parent = os.fstat(parent_descriptor)
        if (
            (observed_parent.st_dev, observed_parent.st_ino) != parent_identity
            or not stat.S_ISDIR(observed_parent.st_mode)
            or observed_parent.st_uid != os.geteuid()
            or stat.S_IMODE(observed_parent.st_mode) != 0o700
        ):
            raise ParityVerificationError("parity record parent identity changed")
        descriptor = os.open(
            path.name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o400,
            dir_fd=parent_descriptor,
        )
        os.fchmod(descriptor, 0o400)
        observed = os.fstat(descriptor)
        reservation = _ReservedRecord(
            descriptor=descriptor,
            parent_descriptor=parent_descriptor,
            path=path,
            parent_identity=parent_identity,
            identity=(observed.st_dev, observed.st_ino),
        )
        _verify_reserved_record(reservation, expected_size=0)
        return reservation
    except OSError as exc:
        if reservation is not None:
            _unlink_reserved_record(reservation)
            _close_reserved_record(reservation)
        else:
            if descriptor >= 0:
                os.close(descriptor)
            if parent_descriptor >= 0:
                os.close(parent_descriptor)
        raise ParityVerificationError("parity record cannot be reserved") from exc
    except ParityVerificationError:
        if reservation is not None:
            _unlink_reserved_record(reservation)
            _close_reserved_record(reservation)
        else:
            if descriptor >= 0:
                os.close(descriptor)
            if parent_descriptor >= 0:
                os.close(parent_descriptor)
        raise


def _validate_matrix(
    scenario_ids: tuple[str, ...],
    run_count: int,
) -> None:
    if (
        type(scenario_ids) is not tuple
        or scenario_ids != SCENARIO_IDS
        or len(scenario_ids) != len(set(scenario_ids))
        or type(run_count) is not int
        or run_count != _RUN_COUNT
    ):
        raise ParityVerificationError("parity matrix must be exactly eight by two")


def _cleanup_transport_run(
    transport_root: Path,
    envelope: EngineCommandEnvelope,
) -> None:
    """Remove only the exact sealed transport files created for this request."""
    run_directory = transport_root / f"run-{envelope.engine_run_id.hex}"
    try:
        observed_directory = run_directory.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ParityVerificationError(
            "provider transport run cannot be inspected"
        ) from exc
    if (
        stat.S_ISLNK(observed_directory.st_mode)
        or not stat.S_ISDIR(observed_directory.st_mode)
        or observed_directory.st_uid != os.geteuid()
        or stat.S_IMODE(observed_directory.st_mode) != 0o700
    ):
        raise ParityVerificationError("provider transport run is unsafe")
    allowed = {"request.json", "request.sha256"}
    try:
        entries = tuple(run_directory.iterdir())
    except OSError as exc:
        raise ParityVerificationError(
            "provider transport run cannot be inspected"
        ) from exc
    if any(entry.name not in allowed for entry in entries):
        raise ParityVerificationError(
            "provider transport run contains an unknown entry"
        )
    for entry in entries:
        try:
            observed = entry.lstat()
        except OSError as exc:
            raise ParityVerificationError(
                "provider transport file cannot be inspected"
            ) from exc
        if (
            stat.S_ISLNK(observed.st_mode)
            or not stat.S_ISREG(observed.st_mode)
            or observed.st_uid != os.geteuid()
            or observed.st_nlink != 1
            or stat.S_IMODE(observed.st_mode) != 0o400
        ):
            raise ParityVerificationError("provider transport file is unsafe")
        try:
            entry.unlink()
        except OSError as exc:
            raise ParityVerificationError(
                "provider transport file cannot be removed"
            ) from exc
    try:
        run_directory.rmdir()
    except OSError as exc:
        raise ParityVerificationError(
            "provider transport run cannot be removed"
        ) from exc


def _launch_once(
    built,
    *,
    popen_factory: Callable[..., object],
) -> bytes:
    try:
        captured = capture_prepared_engine_process(
            built,
            popen_factory=popen_factory,
        )
    except subprocess.TimeoutExpired as exc:
        raise ParityVerificationError("runtime exceeded the attested timeout") from exc
    except TypeError as exc:
        raise ParityVerificationError("runtime process completion is invalid") from exc
    if captured.returncode != 0:
        raise ParityVerificationError("runtime process exited unsuccessfully")
    if captured.stderr != b"":
        raise ParityVerificationError("runtime process emitted stderr")
    stdout = captured.stdout
    if (
        not stdout.endswith(b"\n")
        or stdout.count(b"\n") != 1
        or stdout == b"\n"
    ):
        raise ParityVerificationError(
            "runtime stdout must contain exactly one event line"
        )
    return stdout[:-1]


def _validated_event(
    event_bytes: bytes,
    *,
    envelope: EngineCommandEnvelope,
    fixture,
):
    try:
        event = EngineEventEnvelope.model_validate_json(event_bytes)
    except ValidationError as exc:
        raise ParityVerificationError("runtime stdout event is invalid") from exc
    if canonical_json_bytes(event) != event_bytes:
        raise ParityVerificationError("runtime stdout event is not canonical")
    try:
        scenario = BacktestScenarioV1.from_mounted_artifacts(
            scenario_bytes=fixture.simulation_scenario,
            catalog_bytes=fixture.instrument_catalog,
            strategy_bytes=fixture.strategy_configuration,
            market_data_bytes=fixture.market_data,
            start_time=envelope.payload.start_time,
            end_time=envelope.payload.end_time,
        )
        expected = calculate_reference_outcome(scenario)
        validated = validate_isolated_simulation_result(envelope, event, expected)
    except (BacktestScenarioError, NautilusBacktestError) as exc:
        raise ParityVerificationError(
            "runtime result does not equal the independent oracle"
        ) from exc
    return event, validated, expected


def _independent_reference_event(
    envelope: EngineCommandEnvelope,
    expected,
) -> bytes:
    """Build the expected event without importing or invoking engine code."""

    from uuid import uuid5

    values: tuple[tuple[str, str | int], ...] = (
        ("input_artifacts_sha256", hashlib.sha256(canonical_json_bytes({
            name: getattr(envelope.payload, name).sha256
            for name in (
                "engine_configuration",
                "instrument_catalog",
                "strategy_configuration",
                "market_data",
                "simulation_scenario",
            )
        })).hexdigest()),
        ("scenario_digest", expected.scenario_digest),
        ("scenario_id", expected.scenario_id),
        ("event_digest", expected.event_digest),
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
        ("stop_take_profit_precedence", expected.stop_take_profit_precedence),
    )
    payload = EngineEvent(
        event_type="NautilusBacktestSimulationCompleted",
        family=EventFamily.ENGINE_LIFECYCLE,
        attributes=tuple(
            EventAttribute(name=name, value=value) for name, value in values
        ),
    )
    event = EngineEventEnvelope(
        message_id=uuid5(
            envelope.message_id,
            "NautilusBacktestSimulationCompleted",
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


def _required_digest(value: object, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != _SHA256_LENGTH
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ParityVerificationError(f"{label} digest is invalid")
    return value


def _write_record(
    path: Path,
    record: V12R3ParityRecord,
    *,
    parent_identity: tuple[int, int],
) -> None:
    value = canonical_json_bytes(record) + b"\n"
    reservation = _reserve_record(path, parent_identity=parent_identity)
    published = False
    try:
        _verify_reserved_record(reservation, expected_size=0)
        remaining = memoryview(value)
        while remaining:
            written = os.write(reservation.descriptor, remaining)
            if written <= 0:
                raise OSError("short evidence write")
            remaining = remaining[written:]
        os.fsync(reservation.descriptor)
        _verify_reserved_record(reservation, expected_size=len(value))
        published = True
    except ParityVerificationError:
        raise
    except OSError as exc:
        raise ParityVerificationError("parity evidence record cannot be sealed") from exc
    finally:
        try:
            if not published:
                _unlink_reserved_record(reservation)
        finally:
            _close_reserved_record(reservation)


def verify_nautilus_v12_r3_parity(
    *,
    rollback_closure: Path,
    candidate_closure: Path,
    rollback_artifact_directory: Path,
    artifact_directory: Path,
    sandbox: Path,
    campaign_directory: Path,
    transport_root: Path,
    record: Path,
    scenario_ids: tuple[str, ...] = SCENARIO_IDS,
    run_count: int = _RUN_COUNT,
    attest_closure: Callable[..., object] = attest_nautilus_backtest_closure,
    provider_factory: Callable[..., object] = EngineSpawnProvider,
    consume_spawn: Callable[[object], object] = consume_prepared_engine_spawn,
    popen_factory: Callable[..., object] = subprocess.Popen,
) -> V12R3ParityRecord:
    """Run and record the fixed schema-6 simulation parity matrix."""
    _validate_matrix(scenario_ids, run_count)
    try:
        campaign = load_verified_campaign(campaign_directory)
    except CampaignEvidenceError as exc:
        raise ParityVerificationError("campaign authority is invalid") from exc
    if tuple(item.scenario_id for item in campaign.scenarios) != scenario_ids:
        raise ParityVerificationError("campaign matrix must be exactly eight by two")
    _require_private_directory(
        rollback_artifact_directory,
        label="rollback artifact",
        external=True,
        empty=False,
        nonempty=True,
        expected_mode=0o500,
    )
    _require_private_directory(
        artifact_directory,
        label="artifact",
        external=True,
        empty=False,
        expected_mode=0o500,
    )
    if rollback_artifact_directory == artifact_directory:
        raise ParityVerificationError(
            "rollback and candidate artifact directories must be distinct"
        )
    _require_private_directory(
        transport_root,
        label="transport",
        external=True,
        empty=True,
        expected_mode=0o700,
    )
    record_parent_identity = _validate_record_path(
        record,
        transport_root=transport_root,
    )
    for path, label in (
        (rollback_closure, "rollback closure"),
        (candidate_closure, "candidate closure"),
        (sandbox, "sandbox"),
    ):
        _require_absolute(path, label=label)

    rollback_config = NautilusClosureConfig(
        runtime_root=rollback_closure,
        artifact_directory=rollback_artifact_directory,
        sandbox_executable=sandbox,
    )
    candidate_config = NautilusClosureConfig(
        runtime_root=candidate_closure,
        artifact_directory=artifact_directory,
        sandbox_executable=sandbox,
    )
    rollback_attestation = attest_closure(
        rollback_config,
        expected_profile="zero-order",
    )
    rollback_schema = getattr(
        rollback_attestation,
        "manifest_schema_version",
        None,
    )
    if type(rollback_schema) is not int or rollback_schema not in {1, 2, 3}:
        raise ParityVerificationError(
            "rollback closure must remain schema 1 through 3 compatible"
        )
    candidate_attestation = attest_closure(
        candidate_config,
        expected_profile="execution-simulation",
    )
    candidate_schema = getattr(
        candidate_attestation,
        "manifest_schema_version",
        None,
    )
    if type(candidate_schema) is not int or candidate_schema != 6:
        raise ParityVerificationError("candidate closure must use schema 6")
    candidate_manifest = getattr(candidate_attestation, "closure_manifest", None)
    if candidate_manifest is None:
        raise ParityVerificationError("candidate schema 6 manifest is unavailable")

    def attest_pinned_candidate() -> object:
        observed = attest_closure(
            candidate_config,
            expected_profile="execution-simulation",
        )
        if observed != candidate_attestation:
            raise ParityVerificationError(
                "candidate closure authority changed during the parity matrix"
            )
        return observed

    scenario_records: list[ScenarioParityRecord] = []
    for campaign_scenario in campaign.scenarios:
        scenario_id = campaign_scenario.scenario_id
        fixture = campaign_scenario.fixture
        envelope = campaign_scenario.envelope
        provider = provider_factory(
            transport_root=transport_root,
            attest_closure=attest_pinned_candidate,
            expected_manifest_schema_version=6,
            attest_inputs=HashBoundArtifactResolver(campaign_scenario.bindings),
            monotonic_ns=time.monotonic_ns,
        )
        event_bytes_by_run: list[bytes] = []
        validated_by_run: list[object] = []
        expected = None
        for _run in range(run_count):
            primary_failure: BaseException | None = None
            try:
                prepared = provider.prepare(envelope)
                built = consume_spawn(prepared)
                event_bytes = _launch_once(built, popen_factory=popen_factory)
                _event, validated, expected = _validated_event(
                    event_bytes,
                    envelope=envelope,
                    fixture=fixture,
                )
                event_bytes_by_run.append(event_bytes)
                validated_by_run.append(validated)
            except BaseException as exc:
                primary_failure = exc
                raise
            finally:
                try:
                    _cleanup_transport_run(transport_root, envelope)
                except ParityVerificationError as cleanup_error:
                    if primary_failure is None:
                        raise
                    primary_failure.add_note(
                        f"secondary transport cleanup failure: {cleanup_error}"
                    )
        if event_bytes_by_run[0] != event_bytes_by_run[1]:
            raise ParityVerificationError("run-1 and run-2 events are non-identical")
        assert expected is not None
        reference_event = _independent_reference_event(envelope, expected)
        if event_bytes_by_run[0] != reference_event:
            raise ParityVerificationError(
                "runtime event bytes do not equal the independent oracle event"
            )
        reference_event_sha256 = hashlib.sha256(reference_event).hexdigest()
        reference_result_sha256 = hashlib.sha256(
            canonical_json_bytes(
                {
                    "event_sha256": reference_event_sha256,
                    "input_artifacts_sha256": getattr(
                        validated_by_run[0], "input_artifacts_sha256"
                    ),
                    "request_sha256": hashlib.sha256(
                        canonical_json_bytes(envelope)
                    ).hexdigest(),
                }
            )
        ).hexdigest()
        nautilus_result_sha256 = _required_digest(
            getattr(validated_by_run[0], "result_sha256", None),
            label="validated result",
        )
        if reference_result_sha256 != nautilus_result_sha256:
            raise ParityVerificationError(
                "runtime result does not equal the independent oracle result"
            )
        scenario_records.append(
            ScenarioParityRecord(
                scenario_id=scenario_id,
                engine_configuration_sha256=campaign_scenario.engine_configuration_sha256,
                instrument_catalog_sha256=campaign_scenario.instrument_catalog_sha256,
                strategy_configuration_sha256=campaign_scenario.strategy_configuration_sha256,
                market_data_sha256=campaign_scenario.market_data_sha256,
                simulation_scenario_sha256=campaign_scenario.simulation_scenario_sha256,
                independent_reference_result_sha256=reference_result_sha256,
                independent_reference_event_sha256=reference_event_sha256,
                nautilus_result_sha256=nautilus_result_sha256,
                nautilus_event_sha256=hashlib.sha256(
                    event_bytes_by_run[0]
                ).hexdigest(),
                run_1_event_sha256=hashlib.sha256(
                    event_bytes_by_run[0]
                ).hexdigest(),
                run_2_event_sha256=hashlib.sha256(
                    event_bytes_by_run[1]
                ).hexdigest(),
            )
        )

    parity_record = V12R3ParityRecord(
        schema_version="nautilus-phase4-parity-evidence-v2",
        status="passed",
        scenario_campaign_sha256=campaign.sha256,
        strategy_source_sha256=campaign.strategy_source_sha256,
        candidate_closure_sha256=_required_digest(
            getattr(candidate_attestation, "closure_sha256", None),
            label="candidate closure",
        ),
        candidate_manifest_sha256=_required_digest(
            getattr(candidate_manifest, "sha256", None),
            label="candidate manifest",
        ),
        candidate_manifest_schema_version=6,
        scenarios=scenario_records,
    )
    _write_record(
        record,
        parity_record,
        parent_identity=record_parent_identity,
    )
    return parity_record


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        verify_nautilus_v12_r3_parity(**vars(arguments))
    except (
        ParityVerificationError,
        EngineSpawnError,
        OSError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
