#!/usr/bin/env python3
"""Capture one external, private Nautilus runtime failure diagnostic."""

from __future__ import annotations

import argparse
import base64
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

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.engine_contracts import EngineCommandEnvelope, canonical_json_bytes
from packages.nautilus_backtest import (
    capture_prepared_engine_process,
)
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
_SCENARIO_ID = "long-accounting"


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


@dataclass(frozen=True, slots=True)
class _RetainedRunSnapshot:
    root_identity: tuple[int, ...]
    run_name: str
    run_identity: tuple[int, ...]
    member_identities: dict[str, tuple[int, ...]]


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
    parser.add_argument("--campaign-directory", required=True, type=Path)
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
            _close_reserved_record(reservation)
        else:
            if descriptor >= 0:
                os.close(descriptor)
            if parent_descriptor >= 0:
                os.close(parent_descriptor)
        raise


def _transport_identity(observed: os.stat_result) -> tuple[int, ...]:
    return (
        observed.st_dev,
        observed.st_ino,
        observed.st_mode,
        observed.st_uid,
        observed.st_nlink,
        observed.st_size,
        observed.st_mtime_ns,
        observed.st_ctime_ns,
    )


def _validate_retained_transport_run(
    transport_root: Path,
    envelope: EngineCommandEnvelope,
    *,
    expected_root_identity: tuple[int, int] | None = None,
) -> _RetainedRunSnapshot:
    root_descriptor = -1
    run_descriptor = -1
    member_identities: dict[str, tuple[int, ...]] = {}
    run_name = f"run-{envelope.engine_run_id.hex}"
    try:
        root_descriptor = os.open(
            transport_root,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        opened_root = os.fstat(root_descriptor)
        if (
            not stat.S_ISDIR(opened_root.st_mode)
            or opened_root.st_uid != os.geteuid()
            or stat.S_IMODE(opened_root.st_mode) != 0o700
            or (
                expected_root_identity is not None
                and (opened_root.st_dev, opened_root.st_ino)
                != expected_root_identity
            )
        ):
            raise RuntimeFailureDiagnosticError("provider transport root is unsafe")
        if set(os.listdir(root_descriptor)) != {run_name}:
            raise RuntimeFailureDiagnosticError(
                "provider transport root inventory is invalid"
            )
        run_descriptor = os.open(
            run_name,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=root_descriptor,
        )
        opened_run = os.fstat(run_descriptor)
        named_run = os.stat(
            run_name,
            dir_fd=root_descriptor,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISDIR(opened_run.st_mode)
            or opened_run.st_uid != os.geteuid()
            or stat.S_IMODE(opened_run.st_mode) != 0o700
            or _transport_identity(opened_run) != _transport_identity(named_run)
        ):
            raise RuntimeFailureDiagnosticError("provider transport run is unsafe")
        members = {"request.json", "request.sha256"}
        if set(os.listdir(run_descriptor)) != members:
            raise RuntimeFailureDiagnosticError(
                "provider transport run inventory is invalid"
            )
        for name in sorted(members):
            descriptor = os.open(
                name,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=run_descriptor,
            )
            try:
                opened = os.fstat(descriptor)
                named = os.stat(
                    name,
                    dir_fd=run_descriptor,
                    follow_symlinks=False,
                )
                if (
                    not stat.S_ISREG(opened.st_mode)
                    or opened.st_uid != os.geteuid()
                    or opened.st_nlink != 1
                    or stat.S_IMODE(opened.st_mode) != 0o400
                    or opened.st_size <= 0
                    or _transport_identity(opened) != _transport_identity(named)
                ):
                    raise RuntimeFailureDiagnosticError(
                        "provider transport member is unsafe"
                    )
                member_identities[name] = _transport_identity(opened)
            finally:
                os.close(descriptor)
        named_root = transport_root.lstat()
        if (
            (named_root.st_dev, named_root.st_ino)
            != (opened_root.st_dev, opened_root.st_ino)
            or set(os.listdir(root_descriptor)) != {run_name}
            or _transport_identity(os.fstat(run_descriptor))
            != _transport_identity(
                os.stat(
                    run_name,
                    dir_fd=root_descriptor,
                    follow_symlinks=False,
                )
            )
        ):
            raise RuntimeFailureDiagnosticError(
                "provider transport identity changed"
            )
        for name, expected in member_identities.items():
            if _transport_identity(
                os.stat(name, dir_fd=run_descriptor, follow_symlinks=False)
            ) != expected:
                raise RuntimeFailureDiagnosticError(
                    "provider transport member identity changed"
                )
        return _RetainedRunSnapshot(
            root_identity=_transport_identity(os.fstat(root_descriptor)),
            run_name=run_name,
            run_identity=_transport_identity(os.fstat(run_descriptor)),
            member_identities=member_identities,
        )
    except OSError as exc:
        raise RuntimeFailureDiagnosticError(
            "provider transport run cannot be inspected"
        ) from exc
    finally:
        if run_descriptor >= 0:
            os.close(run_descriptor)
        if root_descriptor >= 0:
            os.close(root_descriptor)


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
    campaign_directory: Path,
    transport_root: Path,
    diagnostic_record: Path,
    attest_closure: Callable[..., object] = attest_nautilus_backtest_closure,
    provider_factory: Callable[..., object] = EngineSpawnProvider,
    consume_spawn: Callable[[object], object] = consume_prepared_engine_spawn,
    popen_factory: Callable[..., object] = subprocess.Popen,
) -> None:
    """Run exactly one fixed long-accounting command and seal its diagnostics."""
    try:
        campaign = load_verified_campaign(campaign_directory)
        campaign_scenario = campaign.scenario(_SCENARIO_ID)
    except CampaignEvidenceError as exc:
        raise RuntimeFailureDiagnosticError("campaign authority is invalid") from exc
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
    observed_transport = transport_root.lstat()
    transport_identity = (
        observed_transport.st_dev,
        observed_transport.st_ino,
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
        if type(candidate_schema) is not int or candidate_schema != 6:
            raise RuntimeFailureDiagnosticError(
                "candidate closure must use schema 6"
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

        envelope = campaign_scenario.envelope
        provider = provider_factory(
            transport_root=transport_root,
            attest_closure=attest_pinned_candidate,
            expected_manifest_schema_version=6,
            attest_inputs=HashBoundArtifactResolver(campaign_scenario.bindings),
            monotonic_ns=time.monotonic_ns,
        )
        primary_failure: BaseException | None = None
        transport_snapshot: _RetainedRunSnapshot | None = None
        try:
            prepared = provider.prepare(envelope)
            built = consume_spawn(prepared)
            returncode, stdout, stderr = _launch_once(
                built,
                popen_factory=popen_factory,
            )
        except BaseException as exc:
            primary_failure = exc
            raise
        finally:
            try:
                transport_snapshot = _validate_retained_transport_run(
                    transport_root,
                    envelope,
                    expected_root_identity=transport_identity,
                )
            except RuntimeFailureDiagnosticError as forensic_error:
                if primary_failure is None:
                    raise
                primary_failure.add_note(
                    "secondary forensic validation failure: "
                    f"{forensic_error}"
                )
        record = RuntimeFailureDiagnosticRecord(
            schema_version="nautilus-v12-runtime-failure-diagnostic-v1",
            exit_code=returncode,
            stdout_sha256=hashlib.sha256(stdout).hexdigest(),
            stderr_sha256=hashlib.sha256(stderr).hexdigest(),
            stderr_base64=base64.b64encode(stderr).decode("ascii"),
        )
        _write_reserved_record(record_reservation, record)
        final_transport_snapshot = _validate_retained_transport_run(
            transport_root,
            envelope,
            expected_root_identity=transport_identity,
        )
        if final_transport_snapshot != transport_snapshot:
            raise RuntimeFailureDiagnosticError(
                "provider transport identity changed after capture"
            )
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
