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
_FAILURE_RECEIPT_SUFFIX = ".failure.json"


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


class V12R3ParityFailureReceipt(TypedDict):
    """Digest-only observation of the post-launch result mismatch branch."""

    schema_version: Literal["nautilus-phase4-parity-failure-receipt-v1"]
    status: Literal["failed"]
    failure_class: Literal["independent-result-digest-mismatch"]
    digest_algorithm: Literal["sha256"]
    scenario_id: ScenarioId
    launcher_reference_result_sha256: str
    launcher_reference_result_digest_type: Literal["sha256-hex"]
    launcher_reference_result_digest_length: Literal[64]
    independent_oracle_result_sha256: str
    independent_oracle_result_digest_type: Literal["sha256-hex"]
    independent_oracle_result_digest_length: Literal[64]
    actual_result_sha256: str
    actual_result_digest_type: Literal["sha256-hex"]
    actual_result_digest_length: Literal[64]


class _ResultDigestMismatch(ParityVerificationError):
    """Carry only bounded digest observations across forensic transport sealing."""

    def __init__(
        self,
        *,
        scenario_id: ScenarioId,
        launcher_reference_result_sha256: str,
        independent_oracle_result_sha256: str,
        actual_result_sha256: str,
    ) -> None:
        super().__init__("runtime result does not equal the independent oracle result")
        self.scenario_id = scenario_id
        self.launcher_reference_result_sha256 = launcher_reference_result_sha256
        self.independent_oracle_result_sha256 = independent_oracle_result_sha256
        self.actual_result_sha256 = actual_result_sha256


@dataclass(frozen=True, slots=True)
class _ReservedRecord:
    descriptor: int
    parent_descriptor: int
    path: Path
    parent_identity: tuple[int, int]
    identity: tuple[int, int]


@dataclass(frozen=True, slots=True)
class _TransportCampaign:
    parent_descriptor: int
    root_descriptor: int
    path: Path
    parent_identity: tuple[int, ...]
    root_identity: tuple[int, int]


@dataclass(frozen=True, slots=True)
class _RetainedRunSnapshot:
    root_identity: tuple[int, ...]
    run_name: str
    run_identity: tuple[int, ...]
    member_identities: dict[str, tuple[int, ...]]


@dataclass(slots=True)
class _SubrootCustody:
    name: str
    path: Path
    created: bool = False
    descriptor: int = -1
    construction_complete: bool = False
    identity: tuple[int, ...] | None = None
    snapshot: _RetainedRunSnapshot | None = None


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


def _failure_receipt_path(record: Path) -> Path:
    """Keep the failed observation beside, but separate from, PASS evidence."""

    return record.with_name(f"{record.name}{_FAILURE_RECEIPT_SUFFIX}")


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


def _close_reserved_record(
    reservation: _ReservedRecord,
    *,
    primary: BaseException | None = None,
) -> BaseException | None:
    return _close_descriptors(
        (
            ("parity record", reservation.descriptor),
            ("parity record parent", reservation.parent_descriptor),
        ),
        primary=primary,
    )


def _reserve_record(
    path: Path,
    *,
    parent_identity: tuple[int, int],
) -> _ReservedRecord:
    parent_descriptor = -1
    descriptor = -1
    reservation: _ReservedRecord | None = None
    failure: BaseException | None = None
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
        failure = ParityVerificationError("parity record cannot be reserved")
        failure.__cause__ = exc
    except BaseException as exc:
        failure = exc
    assert failure is not None
    if reservation is not None:
        failure = _close_reserved_record(reservation, primary=failure)
    else:
        failure = _close_descriptors(
            (
                ("parity record", descriptor),
                ("parity record parent", parent_descriptor),
            ),
            primary=failure,
        )
    assert failure is not None
    raise failure


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


def _close_descriptors(
    descriptors: Sequence[tuple[str, int]],
    *,
    primary: BaseException | None = None,
) -> BaseException | None:
    """Close every unique descriptor without exposing close-error details."""

    observed: set[int] = set()
    close_primary: ParityVerificationError | None = None
    for label, descriptor in descriptors:
        if descriptor < 0 or descriptor in observed:
            continue
        observed.add(descriptor)
        try:
            os.close(descriptor)
        except OSError as exc:
            note = f"secondary {label} descriptor close failure: {type(exc).__name__}"
            if primary is not None:
                primary.add_note(note)
            elif close_primary is None:
                close_primary = ParityVerificationError(
                    f"{label} descriptor close failure: {type(exc).__name__}"
                )
                close_primary.add_note(
                    "secondary descriptor close failure: "
                    f"{type(exc).__name__}"
                )
            else:
                close_primary.add_note(note)
    return primary if primary is not None else close_primary


def _bounded_secondary_summary(error: BaseException) -> str:
    """Return error classes only, never a secondary error message."""

    error_types = [type(error).__name__]
    for note in getattr(error, "__notes__", ()):
        if note.endswith(": OSError"):
            error_types.append("OSError")
    return ", ".join(dict.fromkeys(error_types))


def _reseal_directory(descriptor: int, *, label: str) -> None:
    """Make one opened forensic directory immutable before inspecting it."""

    try:
        os.fchmod(descriptor, 0o500)
        os.fsync(descriptor)
    except OSError as exc:
        raise ParityVerificationError(f"{label} cannot be resealed") from exc


def _open_transport_campaign(path: Path) -> _TransportCampaign:
    parent_descriptor = -1
    root_descriptor = -1
    failure: BaseException | None = None
    try:
        parent_descriptor = os.open(
            path.parent,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        root_descriptor = os.open(
            path.name,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_descriptor,
        )
        parent = os.fstat(parent_descriptor)
        root = os.fstat(root_descriptor)
        named_parent = path.parent.lstat()
        named_root = os.stat(
            path.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        full_root = path.lstat()
        if (
            _transport_identity(parent) != _transport_identity(named_parent)
            or _transport_identity(root) != _transport_identity(named_root)
            or _transport_identity(root) != _transport_identity(full_root)
            or root.st_uid != os.geteuid()
            or stat.S_IMODE(root.st_mode) != 0o700
            or os.listdir(root_descriptor)
        ):
            raise ParityVerificationError("transport campaign identity changed")
        return _TransportCampaign(
            parent_descriptor=parent_descriptor,
            root_descriptor=root_descriptor,
            path=path,
            parent_identity=_transport_identity(parent),
            root_identity=(root.st_dev, root.st_ino),
        )
    except OSError as exc:
        failure = ParityVerificationError("transport campaign is unavailable")
        failure.__cause__ = exc
    except BaseException as exc:
        failure = exc
    assert failure is not None
    failure = _close_descriptors(
        (
            ("transport campaign root", root_descriptor),
            ("transport campaign parent", parent_descriptor),
        ),
        primary=failure,
    )
    assert failure is not None
    raise failure


def _close_transport_campaign(campaign: _TransportCampaign) -> None:
    failure = _close_descriptors(
        (
            ("transport campaign root", campaign.root_descriptor),
            ("transport campaign parent", campaign.parent_descriptor),
        )
    )
    if failure is not None:
        raise failure


def _create_transport_subroot(
    campaign: _TransportCampaign,
    *,
    custody: _SubrootCustody,
) -> tuple[Path, tuple[int, int]]:
    try:
        os.mkdir(custody.name, mode=0o500, dir_fd=campaign.root_descriptor)
        custody.created = True
        named_created = os.stat(
            custody.name,
            dir_fd=campaign.root_descriptor,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISDIR(named_created.st_mode)
            or named_created.st_uid != os.geteuid()
            or stat.S_IMODE(named_created.st_mode) != 0o500
        ):
            raise ParityVerificationError(
                "transport subroot was not created sealed"
            )
        custody.identity = _transport_identity(named_created)
        custody.descriptor = os.open(
            custody.name,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=campaign.root_descriptor,
        )
        opened_sealed = os.fstat(custody.descriptor)
        if _transport_identity(opened_sealed) != custody.identity:
            raise ParityVerificationError("transport subroot identity changed")
        os.fchmod(custody.descriptor, 0o700)
        os.fsync(custody.descriptor)
        os.fsync(campaign.root_descriptor)
        opened = os.fstat(custody.descriptor)
        named = os.stat(
            custody.name,
            dir_fd=campaign.root_descriptor,
            follow_symlinks=False,
        )
        if (
            _transport_identity(opened) != _transport_identity(named)
            or opened.st_uid != os.geteuid()
            or stat.S_IMODE(opened.st_mode) != 0o700
            or os.listdir(custody.descriptor)
        ):
            raise ParityVerificationError("transport subroot is unsafe")
        custody.construction_complete = True
        custody.identity = _transport_identity(opened)
        descriptor_to_close = custody.descriptor
        custody.descriptor = -1
        close_failure = _close_descriptors(
            (("created transport subroot", descriptor_to_close),)
        )
        if close_failure is not None:
            raise close_failure
        return custody.path, (opened.st_dev, opened.st_ino)
    except ParityVerificationError:
        raise
    except OSError as exc:
        raise ParityVerificationError("transport subroot cannot be created") from exc


def _seal_provider_run(
    subroot_descriptor: int,
    *,
    run_name: str,
    expected_snapshot: _RetainedRunSnapshot | None = None,
) -> tuple[
    tuple[int, ...],
    dict[str, tuple[int, ...]],
    ParityVerificationError | None,
]:
    """Seal one descriptor-bound provider run before its containing subroot."""

    run_descriptor = -1
    member_descriptors: list[tuple[str, int]] = []
    member_identities: dict[str, tuple[int, ...]] = {}
    refreshed_run: tuple[int, ...] | None = None
    failure: BaseException | None = None
    try:
        run_descriptor = os.open(
            run_name,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=subroot_descriptor,
        )
        opened_run = os.fstat(run_descriptor)
        named_run = os.stat(
            run_name,
            dir_fd=subroot_descriptor,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISDIR(opened_run.st_mode)
            or opened_run.st_uid != os.geteuid()
            or stat.S_IMODE(opened_run.st_mode) not in {0o700, 0o500}
            or _transport_identity(opened_run) != _transport_identity(named_run)
            or (
                expected_snapshot is not None
                and _transport_identity(opened_run)
                != expected_snapshot.run_identity
            )
        ):
            raise ParityVerificationError("provider transport run identity changed")
        _reseal_directory(run_descriptor, label="provider transport run")
        os.fsync(subroot_descriptor)
        opened_run = os.fstat(run_descriptor)
        named_run = os.stat(
            run_name,
            dir_fd=subroot_descriptor,
            follow_symlinks=False,
        )
        refreshed_run = _transport_identity(opened_run)
        if (
            refreshed_run != _transport_identity(named_run)
            or stat.S_IMODE(opened_run.st_mode) != 0o500
        ):
            raise ParityVerificationError(
                "provider transport run could not be sealed"
            )
        if set(os.listdir(subroot_descriptor)) != {run_name}:
            raise ParityVerificationError(
                "provider transport subroot inventory is invalid"
            )
        members = {"request.json", "request.sha256"}
        if set(os.listdir(run_descriptor)) != members:
            raise ParityVerificationError(
                "provider transport run inventory is invalid"
            )
        for member in sorted(members):
            descriptor = os.open(
                member,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=run_descriptor,
            )
            member_descriptors.append(
                (f"sealed provider transport member {member}", descriptor)
            )
            opened = os.fstat(descriptor)
            named = os.stat(
                member,
                dir_fd=run_descriptor,
                follow_symlinks=False,
            )
            identity = _transport_identity(opened)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_uid != os.geteuid()
                or opened.st_nlink != 1
                or stat.S_IMODE(opened.st_mode) != 0o400
                or opened.st_size <= 0
                or identity != _transport_identity(named)
                or (
                    expected_snapshot is not None
                    and identity
                    != expected_snapshot.member_identities.get(member)
                )
            ):
                raise ParityVerificationError(
                    "provider transport member identity changed"
                )
            member_identities[member] = identity
        opened_run = os.fstat(run_descriptor)
        named_run = os.stat(
            run_name,
            dir_fd=subroot_descriptor,
            follow_symlinks=False,
        )
        if (
            _transport_identity(opened_run) != _transport_identity(named_run)
            or stat.S_IMODE(opened_run.st_mode) != 0o500
            or set(os.listdir(run_descriptor)) != members
        ):
            raise ParityVerificationError(
                "provider transport run could not be sealed"
            )
        for member, expected in member_identities.items():
            if _transport_identity(
                os.stat(
                    member,
                    dir_fd=run_descriptor,
                    follow_symlinks=False,
                )
            ) != expected:
                raise ParityVerificationError(
                    "provider transport member identity changed"
                )
    except OSError as exc:
        failure = ParityVerificationError(
            "provider transport run cannot be sealed"
        )
        failure.__cause__ = exc
    except BaseException as exc:
        failure = exc
    finally:
        failure = _close_descriptors(
            (
                *member_descriptors,
                ("sealed provider transport run", run_descriptor),
            ),
            primary=failure,
        )
    if failure is not None and refreshed_run is None:
        raise failure
    assert refreshed_run is not None
    if failure is None:
        return refreshed_run, member_identities, None
    assert isinstance(failure, ParityVerificationError)
    return refreshed_run, member_identities, failure


def _seal_failed_subroot(
    campaign: _TransportCampaign,
    *,
    custody: _SubrootCustody,
    envelope: EngineCommandEnvelope,
) -> None:
    if not custody.created:
        return
    descriptor = custody.descriptor
    failure: BaseException | None = None
    close_failure: ParityVerificationError | None = None
    try:
        if descriptor < 0 and custody.construction_complete:
            descriptor = os.open(
                custody.name,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=campaign.root_descriptor,
            )
        if descriptor >= 0:
            opened = os.fstat(descriptor)
            if (
                custody.identity is not None
                and (opened.st_dev, opened.st_ino)
                != (custody.identity[0], custody.identity[1])
            ):
                raise ParityVerificationError(
                    "failed transport subroot identity changed"
                )
            _reseal_directory(descriptor, label="failed transport subroot")
            os.fsync(campaign.root_descriptor)
            opened = os.fstat(descriptor)
            named = os.stat(
                custody.name,
                dir_fd=campaign.root_descriptor,
                follow_symlinks=False,
            )
            if (
                _transport_identity(opened) != _transport_identity(named)
                or stat.S_IMODE(opened.st_mode) != 0o500
            ):
                raise ParityVerificationError(
                    "failed transport subroot could not be sealed"
                )
            custody.identity = _transport_identity(opened)
            subroot_entries = set(os.listdir(descriptor))
            if subroot_entries:
                run_name = f"run-{envelope.engine_run_id.hex}"
                run_identity, member_identities, close_failure = (
                    _seal_provider_run(
                        descriptor,
                        run_name=run_name,
                        expected_snapshot=custody.snapshot,
                    )
                )
            else:
                run_name = ""
                run_identity = ()
                member_identities = {}
            if subroot_entries:
                custody.snapshot = _RetainedRunSnapshot(
                    root_identity=custody.identity,
                    run_name=run_name,
                    run_identity=run_identity,
                    member_identities=member_identities,
                )
                validated = _validate_retained_transport_run(
                    custody.path,
                    envelope,
                    expected_root_identity=(
                        custody.identity[0],
                        custody.identity[1],
                    ),
                    expected_root_mode=0o500,
                    expected_run_mode=0o500,
                )
                custody.snapshot = validated
            if close_failure is not None:
                raise close_failure
        else:
            named = os.stat(
                custody.name,
                dir_fd=campaign.root_descriptor,
                follow_symlinks=False,
            )
            if (
                custody.identity is None
                or (named.st_dev, named.st_ino)
                != (custody.identity[0], custody.identity[1])
                or not stat.S_ISDIR(named.st_mode)
                or named.st_uid != os.geteuid()
                or stat.S_IMODE(named.st_mode) != 0o500
            ):
                raise ParityVerificationError(
                    "failed transport subroot identity changed"
                )
            custody.identity = _transport_identity(named)
    except ParityVerificationError as exc:
        failure = exc
    except OSError as exc:
        failure = ParityVerificationError(
            "failed transport subroot cannot be inspected"
        )
    except BaseException as exc:
        failure = exc
    finally:
        custody.descriptor = -1
        failure = _close_descriptors(
            (("failed subroot custody", descriptor),),
            primary=failure,
        )
    if failure is not None:
        raise failure


def _seal_completed_subroot(
    campaign: _TransportCampaign,
    *,
    name: str,
    snapshot: _RetainedRunSnapshot,
) -> tuple[_RetainedRunSnapshot, ParityVerificationError | None]:
    subroot_descriptor = -1
    refreshed: _RetainedRunSnapshot | None = None
    failure: BaseException | None = None
    try:
        named = os.stat(
            name,
            dir_fd=campaign.root_descriptor,
            follow_symlinks=False,
        )
        if (
            _transport_identity(named) != snapshot.root_identity
            or not stat.S_ISDIR(named.st_mode)
            or named.st_uid != os.geteuid()
            or stat.S_IMODE(named.st_mode) != 0o700
        ):
            raise ParityVerificationError(
                "completed transport subroot identity changed"
            )
        subroot_descriptor = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=campaign.root_descriptor,
        )
        if (
            _transport_identity(os.fstat(subroot_descriptor))
            != snapshot.root_identity
            or set(os.listdir(subroot_descriptor)) != {snapshot.run_name}
        ):
            raise ParityVerificationError(
                "completed transport subroot inventory changed"
            )
        run_identity, member_identities, failure = _seal_provider_run(
            subroot_descriptor,
            run_name=snapshot.run_name,
            expected_snapshot=snapshot,
        )
        os.fchmod(subroot_descriptor, 0o500)
        os.fsync(subroot_descriptor)
        os.fsync(campaign.root_descriptor)
        opened = os.fstat(subroot_descriptor)
        named = os.stat(
            name,
            dir_fd=campaign.root_descriptor,
            follow_symlinks=False,
        )
        if (
            _transport_identity(opened) != _transport_identity(named)
            or stat.S_IMODE(opened.st_mode) != 0o500
        ):
            raise ParityVerificationError(
                "completed transport subroot could not be sealed"
            )
        refreshed = _RetainedRunSnapshot(
            root_identity=_transport_identity(opened),
            run_name=snapshot.run_name,
            run_identity=run_identity,
            member_identities=member_identities,
        )
    except ParityVerificationError as exc:
        failure = exc
    except OSError:
        failure = ParityVerificationError(
            "completed transport subroot cannot be sealed"
        )
    except BaseException as exc:
        failure = exc
    finally:
        failure = _close_descriptors(
            (("completed transport subroot", subroot_descriptor),),
            primary=failure,
        )
    if failure is not None and refreshed is None:
        raise failure
    assert refreshed is not None
    if failure is None:
        return refreshed, None
    assert isinstance(failure, ParityVerificationError)
    return refreshed, failure


def _seal_completed_subroots(
    campaign: _TransportCampaign,
    *,
    subroots: dict[str, _RetainedRunSnapshot],
) -> None:
    failures: list[ParityVerificationError] = []
    for name, snapshot in tuple(subroots.items()):
        try:
            refreshed, close_failure = _seal_completed_subroot(
                campaign,
                name=name,
                snapshot=snapshot,
            )
            subroots[name] = refreshed
            if close_failure is not None:
                failures.append(close_failure)
        except ParityVerificationError as exc:
            failures.append(exc)
    if failures:
        failure = ParityVerificationError(
            "completed transport prefix could not be sealed"
        )
        for observed in failures:
            failure.add_note(
                "secondary completed-subroot failure: "
                f"{type(observed).__name__}"
            )
        raise failure


def _verify_transport_campaign(
    campaign: _TransportCampaign,
    *,
    subroots: dict[str, _RetainedRunSnapshot],
    current: _SubrootCustody | None = None,
    completed_root_mode: int = 0o700,
) -> None:
    subroot_descriptor = -1
    run_descriptor = -1
    failure: BaseException | None = None
    try:
        parent = os.fstat(campaign.parent_descriptor)
        named_parent = campaign.path.parent.lstat()
        root = os.fstat(campaign.root_descriptor)
        named_root = os.stat(
            campaign.path.name,
            dir_fd=campaign.parent_descriptor,
            follow_symlinks=False,
        )
        full_root = campaign.path.lstat()
        created_names = tuple(subroots)
        if current is not None and current.created:
            created_names = (*created_names, current.name)
        canonical_names = tuple(
            f"parity-{scenario_id}-run-{run_number}"
            for scenario_id in SCENARIO_IDS
            for run_number in range(1, _RUN_COUNT + 1)
        )
        if created_names != canonical_names[: len(created_names)]:
            raise ParityVerificationError(
                "transport campaign is not the exact ordered prefix"
            )
        if (
            _transport_identity(parent)
            != _transport_identity(named_parent)
            or _transport_identity(parent) != campaign.parent_identity
            or (root.st_dev, root.st_ino) != campaign.root_identity
            or (named_root.st_dev, named_root.st_ino)
            != campaign.root_identity
            or (full_root.st_dev, full_root.st_ino)
            != campaign.root_identity
            or root.st_uid != os.geteuid()
            or stat.S_IMODE(root.st_mode) != 0o700
            or set(os.listdir(campaign.root_descriptor)) != set(created_names)
        ):
            raise ParityVerificationError("transport campaign identity changed")
        for name, snapshot in subroots.items():
            observed = os.stat(
                name,
                dir_fd=campaign.root_descriptor,
                follow_symlinks=False,
            )
            if (
                _transport_identity(observed) != snapshot.root_identity
                or not stat.S_ISDIR(observed.st_mode)
                or observed.st_uid != os.geteuid()
                or stat.S_IMODE(observed.st_mode) != completed_root_mode
            ):
                raise ParityVerificationError(
                    "transport subroot identity changed"
                )
            subroot_descriptor = os.open(
                name,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=campaign.root_descriptor,
            )
            if (
                _transport_identity(os.fstat(subroot_descriptor))
                != snapshot.root_identity
                or set(os.listdir(subroot_descriptor)) != {snapshot.run_name}
            ):
                raise ParityVerificationError(
                    "transport subroot inventory changed"
                )
            run_descriptor = os.open(
                snapshot.run_name,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=subroot_descriptor,
            )
            if (
                _transport_identity(os.fstat(run_descriptor))
                != snapshot.run_identity
                or stat.S_IMODE(os.fstat(run_descriptor).st_mode)
                != (0o500 if completed_root_mode == 0o500 else 0o700)
                or set(os.listdir(run_descriptor))
                != set(snapshot.member_identities)
            ):
                raise ParityVerificationError(
                    "transport run identity changed"
                )
            for member, expected in snapshot.member_identities.items():
                if _transport_identity(
                    os.stat(
                        member,
                        dir_fd=run_descriptor,
                        follow_symlinks=False,
                    )
                ) != expected:
                    raise ParityVerificationError(
                        "transport member identity changed"
                    )
            descriptor_to_close = run_descriptor
            run_descriptor = -1
            close_failure = _close_descriptors(
                (("verified transport run", descriptor_to_close),)
            )
            if close_failure is not None:
                raise close_failure
            descriptor_to_close = subroot_descriptor
            subroot_descriptor = -1
            close_failure = _close_descriptors(
                (("verified transport subroot", descriptor_to_close),)
            )
            if close_failure is not None:
                raise close_failure
        if current is not None and current.created:
            if current.identity is None:
                raise ParityVerificationError(
                    "failed transport subroot has no identity"
                )
            observed = os.stat(
                current.name,
                dir_fd=campaign.root_descriptor,
                follow_symlinks=False,
            )
            if (
                _transport_identity(observed) != current.identity
                or not stat.S_ISDIR(observed.st_mode)
                or observed.st_uid != os.geteuid()
                or stat.S_IMODE(observed.st_mode) != 0o500
            ):
                raise ParityVerificationError(
                    "failed transport subroot identity changed"
                )
            subroot_descriptor = os.open(
                current.name,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=campaign.root_descriptor,
            )
            expected_inventory = (
                set()
                if current.snapshot is None
                else {current.snapshot.run_name}
            )
            if (
                _transport_identity(os.fstat(subroot_descriptor))
                != current.identity
                or set(os.listdir(subroot_descriptor)) != expected_inventory
            ):
                raise ParityVerificationError(
                    "failed transport subroot inventory changed"
                )
            if current.snapshot is not None:
                run_descriptor = os.open(
                    current.snapshot.run_name,
                    os.O_RDONLY
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=subroot_descriptor,
                )
                if (
                    _transport_identity(os.fstat(run_descriptor))
                    != current.snapshot.run_identity
                    or stat.S_IMODE(os.fstat(run_descriptor).st_mode) != 0o500
                    or set(os.listdir(run_descriptor))
                    != set(current.snapshot.member_identities)
                ):
                    raise ParityVerificationError(
                        "failed transport run identity changed"
                    )
                for member, expected in current.snapshot.member_identities.items():
                    if _transport_identity(
                        os.stat(
                            member,
                            dir_fd=run_descriptor,
                            follow_symlinks=False,
                        )
                    ) != expected:
                        raise ParityVerificationError(
                            "failed transport member identity changed"
                        )
                descriptor_to_close = run_descriptor
                run_descriptor = -1
                close_failure = _close_descriptors(
                    (("failed transport run", descriptor_to_close),)
                )
                if close_failure is not None:
                    raise close_failure
            descriptor_to_close = subroot_descriptor
            subroot_descriptor = -1
            close_failure = _close_descriptors(
                (("failed transport subroot", descriptor_to_close),)
            )
            if close_failure is not None:
                raise close_failure
    except ParityVerificationError as exc:
        failure = exc
    except OSError as exc:
        failure = ParityVerificationError(
            "transport campaign cannot be inspected"
        )
    except BaseException as exc:
        failure = exc
    finally:
        failure = _close_descriptors(
            (
                ("transport campaign run", run_descriptor),
                ("transport campaign subroot", subroot_descriptor),
            ),
            primary=failure,
        )
    if failure is not None:
        raise failure


def _validate_retained_transport_run(
    transport_root: Path,
    envelope: EngineCommandEnvelope,
    *,
    expected_root_identity: tuple[int, int] | None = None,
    expected_root_mode: int = 0o700,
    expected_run_mode: int = 0o700,
) -> _RetainedRunSnapshot:
    """Validate one retained provider run without mutating shared pathnames."""

    root_descriptor = -1
    run_descriptor = -1
    member_descriptors: list[tuple[str, int]] = []
    member_identities: dict[str, tuple[int, ...]] = {}
    run_name = f"run-{envelope.engine_run_id.hex}"
    result: _RetainedRunSnapshot | None = None
    failure: BaseException | None = None
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
            or stat.S_IMODE(opened_root.st_mode) != expected_root_mode
            or (
                expected_root_identity is not None
                and (opened_root.st_dev, opened_root.st_ino)
                != expected_root_identity
            )
        ):
            raise ParityVerificationError("provider transport root is unsafe")
        if set(os.listdir(root_descriptor)) != {run_name}:
            raise ParityVerificationError(
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
            or stat.S_IMODE(opened_run.st_mode) != expected_run_mode
            or _transport_identity(opened_run) != _transport_identity(named_run)
        ):
            raise ParityVerificationError("provider transport run is unsafe")
        members = {"request.json", "request.sha256"}
        if set(os.listdir(run_descriptor)) != members:
            raise ParityVerificationError(
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
            member_descriptors.append(
                (f"provider transport member {name}", descriptor)
            )
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
                raise ParityVerificationError(
                    "provider transport member is unsafe"
                )
            member_identities[name] = _transport_identity(opened)
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
            raise ParityVerificationError("provider transport identity changed")
        for name, expected in member_identities.items():
            if _transport_identity(
                os.stat(name, dir_fd=run_descriptor, follow_symlinks=False)
            ) != expected:
                raise ParityVerificationError(
                    "provider transport member identity changed"
                )
        result = _RetainedRunSnapshot(
            root_identity=_transport_identity(os.fstat(root_descriptor)),
            run_name=run_name,
            run_identity=_transport_identity(os.fstat(run_descriptor)),
            member_identities=member_identities,
        )
    except OSError as exc:
        failure = ParityVerificationError(
            "provider transport run cannot be inspected"
        )
        failure.__cause__ = exc
    except BaseException as exc:
        failure = exc
    finally:
        failure = _close_descriptors(
            (
                *member_descriptors,
                ("provider transport run", run_descriptor),
                ("provider transport root", root_descriptor),
            ),
            primary=failure,
        )
    if failure is not None:
        raise failure
    assert result is not None
    return result


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


def _write_sealed_record(
    path: Path,
    record: V12R3ParityRecord | V12R3ParityFailureReceipt,
    *,
    parent_identity: tuple[int, int],
    label: str,
) -> None:
    value = canonical_json_bytes(record) + b"\n"
    reservation = _reserve_record(path, parent_identity=parent_identity)
    failure: BaseException | None = None
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
    except OSError as exc:
        failure = ParityVerificationError(f"{label} cannot be sealed")
        failure.__cause__ = exc
    except BaseException as exc:
        failure = exc
    finally:
        failure = _close_reserved_record(reservation, primary=failure)
    if failure is not None:
        raise failure


def _write_record(
    path: Path,
    record: V12R3ParityRecord,
    *,
    parent_identity: tuple[int, int],
) -> None:
    _write_sealed_record(
        path,
        record,
        parent_identity=parent_identity,
        label="parity evidence record",
    )


def _failure_receipt(
    mismatch: _ResultDigestMismatch,
) -> V12R3ParityFailureReceipt:
    """Project a mismatch into the approved digest-only forensic vocabulary."""

    return V12R3ParityFailureReceipt(
        schema_version="nautilus-phase4-parity-failure-receipt-v1",
        status="failed",
        failure_class="independent-result-digest-mismatch",
        digest_algorithm="sha256",
        scenario_id=mismatch.scenario_id,
        launcher_reference_result_sha256=(
            mismatch.launcher_reference_result_sha256
        ),
        launcher_reference_result_digest_type="sha256-hex",
        launcher_reference_result_digest_length=_SHA256_LENGTH,
        independent_oracle_result_sha256=(
            mismatch.independent_oracle_result_sha256
        ),
        independent_oracle_result_digest_type="sha256-hex",
        independent_oracle_result_digest_length=_SHA256_LENGTH,
        actual_result_sha256=mismatch.actual_result_sha256,
        actual_result_digest_type="sha256-hex",
        actual_result_digest_length=_SHA256_LENGTH,
    )


def _write_failure_receipt(
    path: Path,
    mismatch: _ResultDigestMismatch,
    *,
    parent_identity: tuple[int, int],
) -> None:
    _write_sealed_record(
        path,
        _failure_receipt(mismatch),
        parent_identity=parent_identity,
        label="parity failure receipt",
    )


def _run_parity_matrix(
    *,
    campaign,
    transport_root: Path,
    run_count: int,
    attest_pinned_candidate: Callable[[], object],
    provider_factory: Callable[..., object],
    consume_spawn: Callable[[object], object],
    popen_factory: Callable[..., object],
) -> list[ScenarioParityRecord]:
    transport_campaign = _open_transport_campaign(transport_root)
    subroots: dict[str, _RetainedRunSnapshot] = {}
    scenario_records: list[ScenarioParityRecord] = []
    current: _SubrootCustody | None = None
    primary_failure: BaseException | None = None
    try:
        for campaign_scenario in campaign.scenarios:
            scenario_id = campaign_scenario.scenario_id
            fixture = campaign_scenario.fixture
            envelope = campaign_scenario.envelope
            event_bytes_by_run: list[bytes] = []
            validated_by_run: list[object] = []
            expected = None
            for run_number in range(1, run_count + 1):
                subroot_name = f"parity-{scenario_id}-run-{run_number}"
                current = _SubrootCustody(
                    name=subroot_name,
                    path=transport_campaign.path / subroot_name,
                )
                run_transport_root, run_transport_identity = (
                    _create_transport_subroot(
                        transport_campaign,
                        custody=current,
                    )
                )
                provider = provider_factory(
                    transport_root=run_transport_root,
                    attest_closure=attest_pinned_candidate,
                    expected_manifest_schema_version=6,
                    attest_inputs=HashBoundArtifactResolver(
                        campaign_scenario.bindings
                    ),
                    monotonic_ns=time.monotonic_ns,
                )
                prepared = provider.prepare(envelope)
                built = consume_spawn(prepared)
                event_bytes = _launch_once(
                    built,
                    popen_factory=popen_factory,
                )
                _event, validated, expected = _validated_event(
                    event_bytes,
                    envelope=envelope,
                    fixture=fixture,
                )
                event_bytes_by_run.append(event_bytes)
                validated_by_run.append(validated)
                snapshot = _validate_retained_transport_run(
                    run_transport_root,
                    envelope,
                    expected_root_identity=run_transport_identity,
                )
                subroots[subroot_name] = snapshot
                current = None
            if event_bytes_by_run[0] != event_bytes_by_run[1]:
                raise ParityVerificationError(
                    "run-1 and run-2 events are non-identical"
                )
            assert expected is not None
            reference_event = _independent_reference_event(envelope, expected)
            if event_bytes_by_run[0] != reference_event:
                raise ParityVerificationError(
                    "runtime event bytes do not equal the independent oracle event"
                )
            reference_event_sha256 = hashlib.sha256(reference_event).hexdigest()
            request_sha256 = hashlib.sha256(
                canonical_json_bytes(envelope)
            ).hexdigest()
            launcher_reference_result_sha256 = hashlib.sha256(
                canonical_json_bytes(
                    {
                        "event_sha256": hashlib.sha256(
                            event_bytes_by_run[0]
                        ).hexdigest(),
                        "input_artifacts_sha256": getattr(
                            validated_by_run[0], "input_artifacts_sha256"
                        ),
                        "request_sha256": request_sha256,
                    }
                )
            ).hexdigest()
            reference_result_sha256 = hashlib.sha256(
                canonical_json_bytes(
                    {
                        "event_sha256": reference_event_sha256,
                        "input_artifacts_sha256": getattr(
                            validated_by_run[0], "input_artifacts_sha256"
                        ),
                        "request_sha256": request_sha256,
                    }
                )
            ).hexdigest()
            nautilus_result_sha256 = _required_digest(
                getattr(validated_by_run[0], "result_sha256", None),
                label="validated result",
            )
            if reference_result_sha256 != nautilus_result_sha256:
                raise _ResultDigestMismatch(
                    scenario_id=scenario_id,
                    launcher_reference_result_sha256=(
                        launcher_reference_result_sha256
                    ),
                    independent_oracle_result_sha256=reference_result_sha256,
                    actual_result_sha256=nautilus_result_sha256,
                )
            scenario_records.append(
                ScenarioParityRecord(
                    scenario_id=scenario_id,
                    engine_configuration_sha256=(
                        campaign_scenario.engine_configuration_sha256
                    ),
                    instrument_catalog_sha256=(
                        campaign_scenario.instrument_catalog_sha256
                    ),
                    strategy_configuration_sha256=(
                        campaign_scenario.strategy_configuration_sha256
                    ),
                    market_data_sha256=campaign_scenario.market_data_sha256,
                    simulation_scenario_sha256=(
                        campaign_scenario.simulation_scenario_sha256
                    ),
                    independent_reference_result_sha256=(
                        reference_result_sha256
                    ),
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
        _verify_transport_campaign(transport_campaign, subroots=subroots)
    except BaseException as exc:
        primary_failure = exc
        try:
            _seal_completed_subroots(
                transport_campaign,
                subroots=subroots,
            )
        except ParityVerificationError as forensic_error:
            primary_failure.add_note(
                "secondary completed-prefix sealing failure: "
                f"{_bounded_secondary_summary(forensic_error)}"
            )
        if current is not None:
            try:
                _seal_failed_subroot(
                    transport_campaign,
                    custody=current,
                    envelope=envelope,
                )
            except ParityVerificationError as forensic_error:
                primary_failure.add_note(
                    "secondary forensic sealing failure: "
                    f"{_bounded_secondary_summary(forensic_error)}"
                )
        try:
            _verify_transport_campaign(
                transport_campaign,
                subroots=subroots,
                current=current,
                completed_root_mode=0o500,
            )
        except ParityVerificationError as forensic_error:
            primary_failure.add_note(
                "secondary forensic validation failure: "
                f"{_bounded_secondary_summary(forensic_error)}"
            )
    try:
        _close_transport_campaign(transport_campaign)
    except ParityVerificationError as close_error:
        if primary_failure is None:
            raise
        primary_failure.add_note(
            "secondary transport-campaign close failure: "
            f"{_bounded_secondary_summary(close_error)}"
        )
    if primary_failure is not None:
        raise primary_failure
    return scenario_records


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
    failure_receipt = _failure_receipt_path(record)
    failure_receipt_parent_identity = _validate_record_path(
        failure_receipt,
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

    try:
        scenario_records = _run_parity_matrix(
            campaign=campaign,
            transport_root=transport_root,
            run_count=run_count,
            attest_pinned_candidate=attest_pinned_candidate,
            provider_factory=provider_factory,
            consume_spawn=consume_spawn,
            popen_factory=popen_factory,
        )
    except _ResultDigestMismatch as primary_failure:
        try:
            _write_failure_receipt(
                failure_receipt,
                primary_failure,
                parent_identity=failure_receipt_parent_identity,
            )
        except ParityVerificationError as receipt_failure:
            primary_failure.add_note(
                "secondary failure receipt sealing failure: "
                f"{_bounded_secondary_summary(receipt_failure)}"
            )
        raise

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
