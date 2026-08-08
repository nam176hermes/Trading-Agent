#!/usr/bin/env python3
"""Capture one external, private Nautilus runtime failure diagnostic."""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypedDict

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.engine_contracts import EngineCommandEnvelope, canonical_json_bytes
from packages.nautilus_backtest import (
    build_canonical_simulation_fixture,
    build_simulation_envelope,
    capture_prepared_engine_process,
)
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
_SCENARIO_ID = "long-accounting"
_ARTIFACT_NAMES = (
    "engine-configuration.json",
    "instrument-catalog.json",
    "strategy-configuration.json",
    "market-data.jsonl",
    "simulation-scenario.json",
)


class RuntimeFailureDiagnosticError(RuntimeError):
    """The fixed runtime failure diagnostic could not be captured safely."""


class RuntimeFailureDiagnosticRecord(TypedDict):
    schema_version: Literal["nautilus-v12-runtime-failure-diagnostic-v1"]
    exit_code: int
    stdout_sha256: str
    stderr_sha256: str
    stderr_base64: str


@dataclass(frozen=True, slots=True)
class _ReservedRecord:
    descriptor: int
    parent_descriptor: int
    path: Path
    parent_identity: tuple[int, int]
    identity: tuple[int, int]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture one external Nautilus long-accounting diagnostic.",
        allow_abbrev=False,
    )
    parser.add_argument("--rollback-closure", required=True, type=Path)
    parser.add_argument(
        "--rollback-artifact-directory", required=True, type=Path
    )
    parser.add_argument("--candidate-closure", required=True, type=Path)
    parser.add_argument("--artifact-directory", required=True, type=Path)
    parser.add_argument("--sandbox", required=True, type=Path)
    parser.add_argument("--transport-root", required=True, type=Path)
    parser.add_argument("--diagnostic-record", required=True, type=Path)
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
        raise RuntimeFailureDiagnosticError(f"{label} path is unsafe")


def _require_private_directory(
    path: Path,
    *,
    label: str,
    expected_mode: int,
    empty: bool,
    nonempty: bool = False,
) -> None:
    _require_absolute(path, label=label)
    try:
        observed = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise RuntimeFailureDiagnosticError(
            f"{label} directory is unavailable"
        ) from exc
    if (
        resolved != path
        or stat.S_ISLNK(observed.st_mode)
        or not stat.S_ISDIR(observed.st_mode)
        or observed.st_uid != os.geteuid()
        or stat.S_IMODE(observed.st_mode) != expected_mode
        or _is_beneath(path, _CHECKOUT)
    ):
        raise RuntimeFailureDiagnosticError(f"{label} directory is unsafe")
    try:
        occupied = next(path.iterdir(), None)
    except OSError as exc:
        raise RuntimeFailureDiagnosticError(
            f"{label} directory cannot be inspected"
        ) from exc
    if empty and occupied is not None:
        raise RuntimeFailureDiagnosticError(f"{label} directory must be empty")
    if nonempty and occupied is None:
        raise RuntimeFailureDiagnosticError(
            f"{label} directory must be non-empty"
        )


def _validate_record_path(
    record: Path,
    *,
    transport_root: Path,
) -> tuple[int, int]:
    _require_absolute(record, label="diagnostic record")
    try:
        parent = record.parent.resolve(strict=True)
        observed_parent = record.parent.lstat()
    except OSError as exc:
        raise RuntimeFailureDiagnosticError(
            "diagnostic record parent is unavailable"
        ) from exc
    if (
        parent != record.parent
        or _is_beneath(record, _CHECKOUT)
        or _is_beneath(record, transport_root)
        or stat.S_ISLNK(observed_parent.st_mode)
        or not stat.S_ISDIR(observed_parent.st_mode)
        or observed_parent.st_uid != os.geteuid()
        or stat.S_IMODE(observed_parent.st_mode) != 0o700
    ):
        raise RuntimeFailureDiagnosticError("diagnostic record path is unsafe")
    try:
        record.lstat()
    except FileNotFoundError:
        return observed_parent.st_dev, observed_parent.st_ino
    except OSError as exc:
        raise RuntimeFailureDiagnosticError(
            "diagnostic record path cannot be inspected"
        ) from exc
    raise RuntimeFailureDiagnosticError("diagnostic record already exists")


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
        raise RuntimeFailureDiagnosticError(
            "diagnostic record named entry is unavailable"
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
        raise RuntimeFailureDiagnosticError(
            "diagnostic record identity or mode 0400 changed"
        )
    return opened, named


def _verify_reserved_record(
    reservation: _ReservedRecord,
    *,
    expected_size: int,
) -> None:
    opened, named = _named_record_stat(reservation)
    if opened.st_size != expected_size or named.st_size != expected_size:
        raise RuntimeFailureDiagnosticError(
            "diagnostic record identity changed"
        )


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
            os.unlink(
                reservation.path.name,
                dir_fd=reservation.parent_descriptor,
            )
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
            (observed_parent.st_dev, observed_parent.st_ino)
            != parent_identity
            or not stat.S_ISDIR(observed_parent.st_mode)
            or observed_parent.st_uid != os.geteuid()
            or stat.S_IMODE(observed_parent.st_mode) != 0o700
        ):
            raise RuntimeFailureDiagnosticError(
                "diagnostic record parent identity changed"
            )
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
        raise RuntimeFailureDiagnosticError(
            "diagnostic record cannot be reserved"
        ) from exc
    except RuntimeFailureDiagnosticError:
        if reservation is not None:
            _unlink_reserved_record(reservation)
            _close_reserved_record(reservation)
        else:
            if descriptor >= 0:
                os.close(descriptor)
            if parent_descriptor >= 0:
                os.close(parent_descriptor)
        raise


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
                raise OSError("short diagnostic fixture write")
            remaining = remaining[written:]
        os.fsync(descriptor)
    except OSError as exc:
        raise RuntimeFailureDiagnosticError(
            "canonical diagnostic fixture cannot be sealed"
        ) from exc
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
    run_directory = transport_root / f"run-{envelope.engine_run_id.hex}"
    try:
        observed_directory = run_directory.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise RuntimeFailureDiagnosticError(
            "provider transport run cannot be inspected"
        ) from exc
    if (
        stat.S_ISLNK(observed_directory.st_mode)
        or not stat.S_ISDIR(observed_directory.st_mode)
        or observed_directory.st_uid != os.geteuid()
        or stat.S_IMODE(observed_directory.st_mode) != 0o700
    ):
        raise RuntimeFailureDiagnosticError("provider transport run is unsafe")
    allowed = {"request.json", "request.sha256"}
    try:
        entries = tuple(run_directory.iterdir())
    except OSError as exc:
        raise RuntimeFailureDiagnosticError(
            "provider transport run cannot be inspected"
        ) from exc
    if any(entry.name not in allowed for entry in entries):
        raise RuntimeFailureDiagnosticError(
            "provider transport run contains an unknown entry"
        )
    for entry in entries:
        try:
            observed = entry.lstat()
        except OSError as exc:
            raise RuntimeFailureDiagnosticError(
                "provider transport file cannot be inspected"
            ) from exc
        if (
            stat.S_ISLNK(observed.st_mode)
            or not stat.S_ISREG(observed.st_mode)
            or observed.st_uid != os.geteuid()
            or observed.st_nlink != 1
            or stat.S_IMODE(observed.st_mode) != 0o400
        ):
            raise RuntimeFailureDiagnosticError(
                "provider transport file is unsafe"
            )
        try:
            entry.unlink()
        except OSError as exc:
            raise RuntimeFailureDiagnosticError(
                "provider transport file cannot be removed"
            ) from exc
    try:
        run_directory.rmdir()
    except OSError as exc:
        raise RuntimeFailureDiagnosticError(
            "provider transport run cannot be removed"
        ) from exc


def _launch_once(
    built,
    *,
    popen_factory: Callable[..., object],
) -> tuple[int, bytes, bytes]:
    try:
        captured = capture_prepared_engine_process(
            built,
            popen_factory=popen_factory,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeFailureDiagnosticError(
            "diagnostic runtime exceeded the attested timeout"
        ) from exc
    except TypeError as exc:
        raise RuntimeFailureDiagnosticError(
            "diagnostic runtime completion is invalid"
        ) from exc
    return captured.returncode, captured.stdout, captured.stderr


def _write_reserved_record(
    reservation: _ReservedRecord,
    record: RuntimeFailureDiagnosticRecord,
) -> None:
    value = canonical_json_bytes(record) + b"\n"
    try:
        _verify_reserved_record(reservation, expected_size=0)
        remaining = memoryview(value)
        while remaining:
            written = os.write(reservation.descriptor, remaining)
            if written <= 0:
                raise OSError("short diagnostic record write")
            remaining = remaining[written:]
        os.fsync(reservation.descriptor)
        _verify_reserved_record(reservation, expected_size=len(value))
    except RuntimeFailureDiagnosticError:
        raise
    except OSError as exc:
        raise RuntimeFailureDiagnosticError(
            "diagnostic record cannot be sealed"
        ) from exc


def diagnose_nautilus_v12_runtime_failure(
    *,
    rollback_closure: Path,
    rollback_artifact_directory: Path,
    candidate_closure: Path,
    artifact_directory: Path,
    sandbox: Path,
    transport_root: Path,
    diagnostic_record: Path,
    attest_closure: Callable[..., object] = attest_nautilus_backtest_closure,
    provider_factory: Callable[..., object] = EngineSpawnProvider,
    consume_spawn: Callable[[object], object] = consume_prepared_engine_spawn,
    popen_factory: Callable[..., object] = subprocess.Popen,
) -> None:
    """Run exactly one fixed long-accounting command and seal its diagnostics."""
    _require_private_directory(
        rollback_artifact_directory,
        label="rollback artifact",
        expected_mode=0o500,
        empty=False,
        nonempty=True,
    )
    _require_private_directory(
        artifact_directory,
        label="candidate artifact",
        expected_mode=0o500,
        empty=False,
        nonempty=True,
    )
    if rollback_artifact_directory == artifact_directory:
        raise RuntimeFailureDiagnosticError(
            "rollback and candidate artifact directories must be distinct"
        )
    _require_private_directory(
        transport_root,
        label="transport",
        expected_mode=0o700,
        empty=True,
    )
    record_parent_identity = _validate_record_path(
        diagnostic_record,
        transport_root=transport_root,
    )
    for path, label in (
        (rollback_closure, "rollback closure"),
        (candidate_closure, "candidate closure"),
        (sandbox, "sandbox"),
    ):
        _require_absolute(path, label=label)

    record_reservation: _ReservedRecord | None = None
    record_published = False
    task_root: Path | None = None
    envelope: EngineCommandEnvelope | None = None
    try:
        record_reservation = _reserve_record(
            diagnostic_record,
            parent_identity=record_parent_identity,
        )
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
            rollback_attestation, "manifest_schema_version", None
        )
        if type(rollback_schema) is not int or rollback_schema not in {1, 2, 3}:
            raise RuntimeFailureDiagnosticError(
                "rollback closure must use schemas 1, 2, or 3"
            )
        candidate_attestation = attest_closure(
            candidate_config,
            expected_profile="execution-simulation",
        )
        candidate_schema = getattr(
            candidate_attestation, "manifest_schema_version", None
        )
        if type(candidate_schema) is not int or candidate_schema != 5:
            raise RuntimeFailureDiagnosticError(
                "candidate closure must use schema 5"
            )

        def attest_pinned_candidate() -> object:
            observed = attest_closure(
                candidate_config,
                expected_profile="execution-simulation",
            )
            if observed != candidate_attestation:
                raise RuntimeFailureDiagnosticError(
                    "candidate closure authority changed before launch"
                )
            return observed

        task_root = Path(
            tempfile.mkdtemp(prefix=".v12-runtime-diagnostic-", dir=transport_root)
        )
        task_root.chmod(0o700)
        fixture = build_canonical_simulation_fixture(_SCENARIO_ID)
        envelope = build_simulation_envelope(fixture)
        bindings = _scenario_bindings(fixture, envelope, task_root / _SCENARIO_ID)
        provider = provider_factory(
            transport_root=transport_root,
            attest_closure=attest_pinned_candidate,
            expected_manifest_schema_version=5,
            attest_inputs=HashBoundArtifactResolver(bindings),
            monotonic_ns=time.monotonic_ns,
        )
        try:
            prepared = provider.prepare(envelope)
            built = consume_spawn(prepared)
            returncode, stdout, stderr = _launch_once(
                built,
                popen_factory=popen_factory,
            )
        finally:
            _cleanup_transport_run(transport_root, envelope)
        record = RuntimeFailureDiagnosticRecord(
            schema_version="nautilus-v12-runtime-failure-diagnostic-v1",
            exit_code=returncode,
            stdout_sha256=hashlib.sha256(stdout).hexdigest(),
            stderr_sha256=hashlib.sha256(stderr).hexdigest(),
            stderr_base64=base64.b64encode(stderr).decode("ascii"),
        )
        _write_reserved_record(record_reservation, record)
        record_published = True
    finally:
        try:
            if task_root is not None:
                shutil.rmtree(task_root)
        finally:
            try:
                if record_reservation is not None and not record_published:
                    _unlink_reserved_record(record_reservation)
            finally:
                if record_reservation is not None:
                    _close_reserved_record(record_reservation)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        diagnose_nautilus_v12_runtime_failure(**vars(arguments))
    except (
        RuntimeFailureDiagnosticError,
        EngineSpawnError,
        OSError,
        subprocess.SubprocessError,
    ):
        print("error: runtime failure diagnostic did not complete", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
