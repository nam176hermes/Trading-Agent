#!/usr/bin/env python3
"""Produce finite digest-only evidence for Nautilus v12-r3 runtime parity."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Literal, TypedDict

from pydantic import ValidationError

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.engine_contracts import (
    EngineCommandEnvelope,
    EngineEventEnvelope,
    canonical_json_bytes,
)
from packages.nautilus_backtest import (
    SCENARIO_IDS,
    BacktestScenarioError,
    BacktestScenarioV1,
    NautilusBacktestError,
    build_canonical_simulation_fixture,
    build_simulation_envelope,
    calculate_reference_outcome,
    capture_prepared_engine_process,
    validate_isolated_simulation_result,
)
from packages.nautilus_backtest.scenarios import ScenarioId
from services.job_worker.engine_artifacts import (
    EngineArtifactBinding,
    HashBoundArtifactResolver,
)
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
_ARTIFACT_NAMES = (
    "engine-configuration.json",
    "instrument-catalog.json",
    "strategy-configuration.json",
    "market-data.jsonl",
    "simulation-scenario.json",
)
_SHA256_LENGTH = 64


class ParityVerificationError(RuntimeError):
    """The finite runtime parity evidence could not be established."""


class ScenarioParityRecord(TypedDict):
    scenario_id: ScenarioId
    run_1_event_sha256: str
    run_2_event_sha256: str
    event_digest: str
    result_payload_digest: str


class V12R3ParityRecord(TypedDict):
    schema_version: Literal["nautilus-v12-r3-parity-evidence-v1"]
    rollback_closure_sha256: str
    candidate_closure_sha256: str
    candidate_manifest_sha256: str
    candidate_manifest_schema_version: Literal[5]
    scenarios: list[ScenarioParityRecord]


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


def _validate_record_path(record: Path) -> None:
    _require_absolute(record, label="record")
    try:
        parent = record.parent.resolve(strict=True)
        observed_parent = record.parent.lstat()
    except OSError as exc:
        raise ParityVerificationError("record parent is unavailable") from exc
    if (
        parent != record.parent
        or _is_beneath(record, _CHECKOUT)
        or stat.S_ISLNK(observed_parent.st_mode)
        or not stat.S_ISDIR(observed_parent.st_mode)
        or observed_parent.st_uid != os.geteuid()
        or observed_parent.st_mode & 0o077
    ):
        raise ParityVerificationError("record path is unsafe")
    try:
        record.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ParityVerificationError("record path cannot be inspected") from exc
    raise ParityVerificationError("record already exists")


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


def _write_sealed_file(path: Path, value: bytes) -> None:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o400,
        )
        os.fchmod(descriptor, 0o400)
        remaining = memoryview(value)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("short artifact write")
            remaining = remaining[written:]
        os.fsync(descriptor)
    except OSError as exc:
        raise ParityVerificationError("canonical artifact cannot be sealed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _scenario_bindings(
    fixture,
    envelope: EngineCommandEnvelope,
    scenario_directory: Path,
) -> tuple[EngineArtifactBinding, ...]:
    scenario_directory.mkdir(mode=0o700)
    scenario_directory.chmod(0o700)
    references = (
        envelope.payload.engine_configuration,
        envelope.payload.instrument_catalog,
        envelope.payload.strategy_configuration,
        envelope.payload.market_data,
        envelope.payload.simulation_scenario,
    )
    bindings: list[EngineArtifactBinding] = []
    for name, value, reference in zip(
        _ARTIFACT_NAMES,
        fixture.artifacts,
        references,
        strict=True,
    ):
        source = scenario_directory / name
        _write_sealed_file(source, value)
        bindings.append(EngineArtifactBinding(reference=reference, source=source))
    return tuple(bindings)


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
    return event, validated


def _required_digest(value: object, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != _SHA256_LENGTH
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ParityVerificationError(f"{label} digest is invalid")
    return value


def _write_record(path: Path, record: V12R3ParityRecord) -> None:
    value = canonical_json_bytes(record) + b"\n"
    descriptor = -1
    created = False
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o400,
        )
        created = True
        os.fchmod(descriptor, 0o400)
        remaining = memoryview(value)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("short evidence write")
            remaining = remaining[written:]
        os.fsync(descriptor)
    except OSError as exc:
        if created:
            try:
                path.unlink()
            except OSError:
                pass
        raise ParityVerificationError("parity evidence record cannot be sealed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def verify_nautilus_v12_r3_parity(
    *,
    rollback_closure: Path,
    candidate_closure: Path,
    rollback_artifact_directory: Path,
    artifact_directory: Path,
    sandbox: Path,
    transport_root: Path,
    record: Path,
    scenario_ids: tuple[str, ...] = SCENARIO_IDS,
    run_count: int = _RUN_COUNT,
    attest_closure: Callable[..., object] = attest_nautilus_backtest_closure,
    provider_factory: Callable[..., object] = EngineSpawnProvider,
    consume_spawn: Callable[[object], object] = consume_prepared_engine_spawn,
    popen_factory: Callable[..., object] = subprocess.Popen,
) -> V12R3ParityRecord:
    """Run and record the fixed schema-5 simulation parity matrix."""
    _validate_matrix(scenario_ids, run_count)
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
    _validate_record_path(record)
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
    if type(candidate_schema) is not int or candidate_schema != 5:
        raise ParityVerificationError("candidate closure must use schema 5")
    candidate_manifest = getattr(candidate_attestation, "closure_manifest", None)
    if candidate_manifest is None:
        raise ParityVerificationError("candidate schema 5 manifest is unavailable")

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

    task_root = Path(
        tempfile.mkdtemp(prefix=".v12-r3-parity-", dir=transport_root)
    )
    task_root.chmod(0o700)
    scenario_records: list[ScenarioParityRecord] = []
    try:
        for scenario_id in scenario_ids:
            fixture = build_canonical_simulation_fixture(scenario_id)
            envelope = build_simulation_envelope(fixture)
            bindings = _scenario_bindings(
                fixture,
                envelope,
                task_root / scenario_id,
            )
            provider = provider_factory(
                transport_root=transport_root,
                attest_closure=attest_pinned_candidate,
                expected_manifest_schema_version=5,
                attest_inputs=HashBoundArtifactResolver(bindings),
                monotonic_ns=time.monotonic_ns,
            )
            event_bytes_by_run: list[bytes] = []
            event_by_run: list[EngineEventEnvelope] = []
            validated_by_run: list[object] = []
            for _run in range(run_count):
                try:
                    prepared = provider.prepare(envelope)
                    built = consume_spawn(prepared)
                    event_bytes = _launch_once(built, popen_factory=popen_factory)
                    event, validated = _validated_event(
                        event_bytes,
                        envelope=envelope,
                        fixture=fixture,
                    )
                    event_bytes_by_run.append(event_bytes)
                    event_by_run.append(event)
                    validated_by_run.append(validated)
                finally:
                    _cleanup_transport_run(transport_root, envelope)
            if event_bytes_by_run[0] != event_bytes_by_run[1]:
                raise ParityVerificationError(
                    "run-1 and run-2 events are non-identical"
                )
            event_digest = getattr(validated_by_run[0], "event_digest", None)
            scenario_records.append(
                ScenarioParityRecord(
                    scenario_id=scenario_id,
                    run_1_event_sha256=hashlib.sha256(
                        event_bytes_by_run[0]
                    ).hexdigest(),
                    run_2_event_sha256=hashlib.sha256(
                        event_bytes_by_run[1]
                    ).hexdigest(),
                    event_digest=_required_digest(
                        event_digest,
                        label="validated event",
                    ),
                    result_payload_digest=_required_digest(
                        event_by_run[0].payload_digest,
                        label="result payload",
                    ),
                )
            )
    finally:
        shutil.rmtree(task_root)

    parity_record = V12R3ParityRecord(
        schema_version="nautilus-v12-r3-parity-evidence-v1",
        rollback_closure_sha256=_required_digest(
            getattr(rollback_attestation, "closure_sha256", None),
            label="rollback closure",
        ),
        candidate_closure_sha256=_required_digest(
            getattr(candidate_attestation, "closure_sha256", None),
            label="candidate closure",
        ),
        candidate_manifest_sha256=_required_digest(
            getattr(candidate_manifest, "sha256", None),
            label="candidate manifest",
        ),
        candidate_manifest_schema_version=5,
        scenarios=scenario_records,
    )
    _write_record(record, parity_record)
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
