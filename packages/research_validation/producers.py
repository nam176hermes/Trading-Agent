"""Sealed, deterministic producers for the Phase-4 research campaign."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid5

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
    BacktestScenarioV1,
    PaperCompatibilityResultV1,
    SCENARIO_IDS,
    CanonicalSimulationFixtureV1,
    build_canonical_simulation_fixture,
    build_simulation_envelope,
    calculate_reference_outcome,
)
from packages.nautilus_backtest.scenarios import ScenarioId
from services.job_worker.engine_artifacts import EngineArtifactBinding

from .models import (
    CostScenario,
    PointInTimeObservation,
    RecursiveIndicatorReplay,
    ResearchCampaignEvidenceV2,
    VerifiedScenarioComparisonV1,
    WalkForwardFold,
    campaign_analysis_output_sha256,
)


class CampaignEvidenceError(ValueError):
    """A campaign source is not the exact sealed repository campaign."""


CAMPAIGN_MANIFEST_NAME = "campaign-manifest.json"
CAMPAIGN_ARTIFACTS: tuple[tuple[str, str], ...] = (
    ("engine-configuration.json", "engine_configuration_sha256"),
    ("instrument-catalog.json", "instrument_catalog_sha256"),
    ("strategy-configuration.json", "strategy_configuration_sha256"),
    ("market-data.json", "market_data_sha256"),
    ("simulation-scenario.json", "simulation_scenario_sha256"),
)
_MANIFEST_FIELDS = {
    "paper_scenario_id",
    "scenarios",
    "schema_version",
    "strategy_source_sha256",
}
_SCENARIO_FIELDS = {"scenario_id", *(field for _name, field in CAMPAIGN_ARTIFACTS)}
_CHECKOUT = Path(__file__).resolve().parents[2]
_STRATEGY_SOURCE = _CHECKOUT / "engines/nautilus/launcher/target_portfolio_strategy.py"
_MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
_PARITY_FIELDS = {
    "candidate_closure_sha256",
    "candidate_manifest_schema_version",
    "candidate_manifest_sha256",
    "scenario_campaign_sha256",
    "scenarios",
    "schema_version",
    "status",
    "strategy_source_sha256",
}
_PARITY_RESULT_FIELDS = {
    "independent_reference_event_sha256",
    "independent_reference_result_sha256",
    "nautilus_event_sha256",
    "nautilus_result_sha256",
    "run_1_event_sha256",
    "run_2_event_sha256",
}
_PARITY_SCENARIO_FIELDS = _SCENARIO_FIELDS | _PARITY_RESULT_FIELDS
_LEGACY_FIELDS = {
    "engine_configuration_sha256",
    "instrument_catalog_sha256",
    "legacy_classification",
    "legacy_disposition",
    "legacy_event_sha256",
    "legacy_result_sha256",
    "legacy_selected",
    "market_data_sha256",
    "scenario_id",
    "schema_version",
    "simulation_scenario_sha256",
    "strategy_configuration_sha256",
}


@dataclass(frozen=True, slots=True)
class VerifiedCampaignScenarioV1:
    scenario_id: ScenarioId
    fixture: CanonicalSimulationFixtureV1
    envelope: EngineCommandEnvelope
    bindings: tuple[EngineArtifactBinding, ...]
    engine_configuration_sha256: str
    instrument_catalog_sha256: str
    strategy_configuration_sha256: str
    market_data_sha256: str
    simulation_scenario_sha256: str


@dataclass(frozen=True, slots=True)
class VerifiedCampaignV1:
    root: Path
    sha256: str
    strategy_source_sha256: str
    scenarios: tuple[VerifiedCampaignScenarioV1, ...]

    def scenario(self, scenario_id: str) -> VerifiedCampaignScenarioV1:
        for item in self.scenarios:
            if item.scenario_id == scenario_id:
                return item
        raise CampaignEvidenceError("campaign scenario is unavailable")


@dataclass(frozen=True, slots=True)
class _DirectoryHandle:
    descriptor: int
    path: Path
    identity: tuple[int, ...]


@dataclass(slots=True)
class _AggregateDirectory:
    parent_descriptor: int
    parent_identity: tuple[int, int, int, int]
    root: _DirectoryHandle
    label: str
    member_identities: dict[str, tuple[int, ...]]
    expected_inventory: frozenset[str] | None = None


def _is_beneath(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _require_external_path(path: Path, *, label: str) -> None:
    if (
        not isinstance(path, Path)
        or not path.is_absolute()
        or path == Path("/")
        or ".." in path.parts
        or _is_beneath(path, _CHECKOUT)
    ):
        raise CampaignEvidenceError(f"{label} path is unsafe")


_IDENTITY_FIELDS = (
    "st_dev",
    "st_ino",
    "st_mode",
    "st_uid",
    "st_nlink",
    "st_size",
    "st_mtime_ns",
    "st_ctime_ns",
)


def _identity(observed: os.stat_result) -> tuple[int, ...]:
    return tuple(getattr(observed, field) for field in _IDENTITY_FIELDS)


def _require_directory_stat(
    observed: os.stat_result,
    *,
    label: str,
    expected_mode: int,
) -> None:
    if (
        stat.S_ISLNK(observed.st_mode)
        or not stat.S_ISDIR(observed.st_mode)
        or observed.st_uid != os.geteuid()
        or stat.S_IMODE(observed.st_mode) != expected_mode
    ):
        raise CampaignEvidenceError(f"{label} is not sealed")


def _open_campaign_root(
    path: Path,
) -> tuple[int, tuple[int, ...], _DirectoryHandle]:
    _require_external_path(path, label="campaign directory")
    parent_descriptor = -1
    root_descriptor = -1
    try:
        observed_parent = path.parent.lstat()
        resolved_parent = path.parent.resolve(strict=True)
        if (
            resolved_parent != path.parent
            or stat.S_ISLNK(observed_parent.st_mode)
            or not stat.S_ISDIR(observed_parent.st_mode)
            or observed_parent.st_uid != os.geteuid()
            or observed_parent.st_mode & 0o077
        ):
            raise CampaignEvidenceError("campaign directory parent is unsafe")
        parent_descriptor = os.open(
            path.parent,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        opened_parent = os.fstat(parent_descriptor)
        if _identity(opened_parent) != _identity(observed_parent):
            raise CampaignEvidenceError("campaign directory parent identity changed")
        root_descriptor = os.open(
            path.name,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_descriptor,
        )
        opened_root = os.fstat(root_descriptor)
        named_root = os.stat(
            path.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        full_root = path.lstat()
        _require_directory_stat(
            opened_root,
            label="campaign directory",
            expected_mode=0o500,
        )
        if not (
            _identity(opened_root) == _identity(named_root) == _identity(full_root)
        ):
            raise CampaignEvidenceError("campaign directory identity changed")
        return (
            parent_descriptor,
            _identity(opened_parent),
            _DirectoryHandle(root_descriptor, path, _identity(opened_root)),
        )
    except CampaignEvidenceError:
        if root_descriptor >= 0:
            os.close(root_descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)
        raise
    except OSError as exc:
        if root_descriptor >= 0:
            os.close(root_descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)
        raise CampaignEvidenceError("campaign directory is unavailable") from exc


def _open_campaign_scenario(
    root: _DirectoryHandle,
    scenario_id: str,
) -> _DirectoryHandle:
    descriptor = -1
    path = root.path / scenario_id
    try:
        descriptor = os.open(
            scenario_id,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=root.descriptor,
        )
        opened = os.fstat(descriptor)
        named = os.stat(
            scenario_id,
            dir_fd=root.descriptor,
            follow_symlinks=False,
        )
        _require_directory_stat(
            opened,
            label=f"campaign scenario {scenario_id}",
            expected_mode=0o500,
        )
        if _identity(opened) != _identity(named):
            raise CampaignEvidenceError(
                f"campaign scenario {scenario_id} identity changed"
            )
        return _DirectoryHandle(descriptor, path, _identity(opened))
    except CampaignEvidenceError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise CampaignEvidenceError(
            f"campaign scenario {scenario_id} is unavailable"
        ) from exc


def _sealed_bytes_at(
    directory: _DirectoryHandle,
    name: str,
    *,
    label: str,
) -> tuple[bytes, tuple[int, ...]]:
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory.descriptor,
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.geteuid()
            or opened.st_nlink != 1
            or stat.S_IMODE(opened.st_mode) != 0o400
            or opened.st_size <= 0
            or opened.st_size > _MAX_ARTIFACT_BYTES
        ):
            raise CampaignEvidenceError(f"{label} is not sealed")
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            block = os.read(descriptor, min(65_536, remaining))
            if not block:
                raise CampaignEvidenceError(f"{label} read was incomplete")
            chunks.append(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise CampaignEvidenceError(f"{label} changed while being read")
        named = os.stat(
            name,
            dir_fd=directory.descriptor,
            follow_symlinks=False,
        )
        if _identity(named) != _identity(opened):
            raise CampaignEvidenceError(f"{label} identity changed while being read")
        return b"".join(chunks), _identity(opened)
    except CampaignEvidenceError:
        raise
    except OSError as exc:
        raise CampaignEvidenceError(f"{label} is unavailable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _locator_identity(observed: os.stat_result) -> tuple[int, int, int, int]:
    return (
        observed.st_dev,
        observed.st_ino,
        observed.st_mode,
        observed.st_uid,
    )


def _open_aggregate_directory(
    path: Path,
    *,
    label: str,
    expected_mode: int,
    expected_inventory: frozenset[str] | None = None,
) -> _AggregateDirectory:
    _require_external_path(path, label=label)
    parent_descriptor = -1
    root_descriptor = -1
    try:
        observed_parent = path.parent.lstat()
        if (
            path.parent.resolve(strict=True) != path.parent
            or stat.S_ISLNK(observed_parent.st_mode)
            or not stat.S_ISDIR(observed_parent.st_mode)
        ):
            raise CampaignEvidenceError(f"{label} parent is unsafe")
        parent_descriptor = os.open(
            path.parent,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        opened_parent = os.fstat(parent_descriptor)
        if _locator_identity(opened_parent) != _locator_identity(observed_parent):
            raise CampaignEvidenceError(f"{label} parent identity changed")
        root_descriptor = os.open(
            path.name,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_descriptor,
        )
        opened_root = os.fstat(root_descriptor)
        named_root = os.stat(
            path.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        full_root = path.lstat()
        _require_directory_stat(
            opened_root,
            label=label,
            expected_mode=expected_mode,
        )
        if not (
            _identity(opened_root)
            == _identity(named_root)
            == _identity(full_root)
        ):
            raise CampaignEvidenceError(f"{label} identity changed")
        if (
            expected_inventory is not None
            and set(os.listdir(root_descriptor)) != expected_inventory
        ):
            raise CampaignEvidenceError(f"{label} inventory is invalid")
        return _AggregateDirectory(
            parent_descriptor=parent_descriptor,
            parent_identity=_locator_identity(opened_parent),
            root=_DirectoryHandle(
                descriptor=root_descriptor,
                path=path,
                identity=_identity(opened_root),
            ),
            label=label,
            member_identities={},
            expected_inventory=expected_inventory,
        )
    except CampaignEvidenceError:
        if root_descriptor >= 0:
            os.close(root_descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)
        raise
    except OSError as exc:
        if root_descriptor >= 0:
            os.close(root_descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)
        raise CampaignEvidenceError(f"{label} is unavailable") from exc


def _read_aggregate_member(
    directory: _AggregateDirectory,
    name: str,
    *,
    label: str,
) -> bytes:
    raw, identity = _sealed_bytes_at(directory.root, name, label=label)
    if name in directory.member_identities:
        raise CampaignEvidenceError(f"{directory.label} member was selected twice")
    directory.member_identities[name] = identity
    return raw


def _verify_aggregate_directory(directory: _AggregateDirectory) -> None:
    try:
        parent = os.fstat(directory.parent_descriptor)
        named_parent = directory.root.path.parent.lstat()
        opened_root = os.fstat(directory.root.descriptor)
        named_root = os.stat(
            directory.root.path.name,
            dir_fd=directory.parent_descriptor,
            follow_symlinks=False,
        )
        full_root = directory.root.path.lstat()
        if (
            _locator_identity(parent) != directory.parent_identity
            or _locator_identity(named_parent) != directory.parent_identity
            or _identity(opened_root) != directory.root.identity
            or _identity(named_root) != directory.root.identity
            or _identity(full_root) != directory.root.identity
            or (
                directory.expected_inventory is not None
                and set(os.listdir(directory.root.descriptor))
                != directory.expected_inventory
            )
        ):
            raise CampaignEvidenceError(
                f"{directory.label} aggregate identity changed"
            )
        for name, expected in directory.member_identities.items():
            observed = os.stat(
                name,
                dir_fd=directory.root.descriptor,
                follow_symlinks=False,
            )
            if _identity(observed) != expected:
                raise CampaignEvidenceError(
                    f"{directory.label} member identity changed"
                )
    except CampaignEvidenceError:
        raise
    except OSError as exc:
        raise CampaignEvidenceError(
            f"{directory.label} aggregate identity changed"
        ) from exc


def _close_aggregate_directory(directory: _AggregateDirectory) -> None:
    try:
        os.close(directory.root.descriptor)
    finally:
        os.close(directory.parent_descriptor)


def _verify_campaign_snapshot(
    *,
    parent_descriptor: int,
    parent_identity: tuple[int, ...],
    root: _DirectoryHandle,
    scenarios: tuple[_DirectoryHandle, ...],
    files: dict[int, dict[str, tuple[int, ...]]],
) -> None:
    try:
        opened_parent = os.fstat(parent_descriptor)
        named_parent = root.path.parent.lstat()
        opened_root = os.fstat(root.descriptor)
        named_root = os.stat(
            root.path.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        full_root = root.path.lstat()
        if not (
            _identity(opened_parent)
            == _identity(named_parent)
            == parent_identity
        ) or not (
            _identity(opened_root)
            == _identity(named_root)
            == _identity(full_root)
            == root.identity
        ):
            raise CampaignEvidenceError("campaign directory identity changed")
        for name, expected_identity in files.get(root.descriptor, {}).items():
            named = os.stat(
                name,
                dir_fd=root.descriptor,
                follow_symlinks=False,
            )
            if _identity(named) != expected_identity:
                raise CampaignEvidenceError(
                    f"campaign manifest {name} identity changed"
                )
        for scenario in scenarios:
            opened_scenario = os.fstat(scenario.descriptor)
            named_scenario = os.stat(
                scenario.path.name,
                dir_fd=root.descriptor,
                follow_symlinks=False,
            )
            full_scenario = scenario.path.lstat()
            if not (
                _identity(opened_scenario)
                == _identity(named_scenario)
                == _identity(full_scenario)
                == scenario.identity
            ):
                raise CampaignEvidenceError(
                    f"campaign scenario {scenario.path.name} identity changed"
                )
            for name, expected_identity in files.get(
                scenario.descriptor, {}
            ).items():
                named = os.stat(
                    name,
                    dir_fd=scenario.descriptor,
                    follow_symlinks=False,
                )
                if _identity(named) != expected_identity:
                    raise CampaignEvidenceError(
                        f"campaign artifact {scenario.path.name}/{name} identity changed"
                    )
    except CampaignEvidenceError:
        raise
    except OSError as exc:
        raise CampaignEvidenceError("campaign snapshot identity changed") from exc


def _canonical_line_object(raw: bytes, *, label: str) -> dict[str, object]:
    if not raw.endswith(b"\n") or raw.count(b"\n") != 1:
        raise CampaignEvidenceError(f"{label} must be one canonical JSON line")

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for name, value in pairs:
            if name in result:
                raise CampaignEvidenceError(f"{label} contains a duplicate field")
            result[name] = value
        return result

    try:
        value = json.loads(raw[:-1], object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CampaignEvidenceError(f"{label} JSON is invalid") from exc
    if not isinstance(value, dict) or canonical_json_bytes(value) + b"\n" != raw:
        raise CampaignEvidenceError(f"{label} is not canonical")
    return value


def _sha256(value: object, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise CampaignEvidenceError(f"{label} is not SHA-256")
    return value


def load_verified_campaign(directory: Path) -> VerifiedCampaignV1:
    """Load the exact eight canonical fixtures from a sealed external root."""
    parent_descriptor, parent_identity, root = _open_campaign_root(directory)
    scenario_handles: list[_DirectoryHandle] = []
    file_identities: dict[int, dict[str, tuple[int, ...]]] = {
        root.descriptor: {}
    }
    try:
        if set(os.listdir(root.descriptor)) != {
            CAMPAIGN_MANIFEST_NAME,
            *SCENARIO_IDS,
        }:
            raise CampaignEvidenceError("campaign directory inventory is invalid")
        manifest_raw, manifest_identity = _sealed_bytes_at(
            root,
            CAMPAIGN_MANIFEST_NAME,
            label="campaign manifest",
        )
        file_identities[root.descriptor][CAMPAIGN_MANIFEST_NAME] = manifest_identity
        manifest = _canonical_line_object(manifest_raw, label="campaign manifest")
        strategy_digest = hashlib.sha256(_STRATEGY_SOURCE.read_bytes()).hexdigest()
        if (
            set(manifest) != _MANIFEST_FIELDS
            or manifest.get("schema_version") != "nautilus-phase4-campaign-v1"
            or manifest.get("paper_scenario_id") != "long-accounting"
            or manifest.get("strategy_source_sha256") != strategy_digest
            or not isinstance(manifest.get("scenarios"), list)
            or len(manifest["scenarios"]) != len(SCENARIO_IDS)
        ):
            raise CampaignEvidenceError("campaign manifest is invalid")

        scenarios: list[VerifiedCampaignScenarioV1] = []
        for scenario_id, record in zip(
            SCENARIO_IDS, manifest["scenarios"], strict=True
        ):
            if (
                not isinstance(record, dict)
                or set(record) != _SCENARIO_FIELDS
                or record.get("scenario_id") != scenario_id
            ):
                raise CampaignEvidenceError(
                    "campaign scenarios are incomplete or unordered"
                )
            scenario_handle = _open_campaign_scenario(root, scenario_id)
            scenario_handles.append(scenario_handle)
            file_identities[scenario_handle.descriptor] = {}
            if set(os.listdir(scenario_handle.descriptor)) != {
                name for name, _field in CAMPAIGN_ARTIFACTS
            }:
                raise CampaignEvidenceError("campaign scenario inventory is invalid")
            expected_fixture = build_canonical_simulation_fixture(scenario_id)
            values: list[bytes] = []
            bindings: list[EngineArtifactBinding] = []
            envelope = build_simulation_envelope(expected_fixture)
            references = (
                envelope.payload.engine_configuration,
                envelope.payload.instrument_catalog,
                envelope.payload.strategy_configuration,
                envelope.payload.market_data,
                envelope.payload.simulation_scenario,
            )
            for (filename, field), expected, reference in zip(
                CAMPAIGN_ARTIFACTS,
                expected_fixture.artifacts,
                references,
                strict=True,
            ):
                source = scenario_handle.path / filename
                value, artifact_identity = _sealed_bytes_at(
                    scenario_handle,
                    filename,
                    label=f"{scenario_id} {filename}",
                )
                file_identities[scenario_handle.descriptor][filename] = (
                    artifact_identity
                )
                digest = hashlib.sha256(value).hexdigest()
                if _sha256(record[field], label=f"{scenario_id} {field}") != digest:
                    raise CampaignEvidenceError(
                        "campaign artifact digest does not match"
                    )
                if value != expected:
                    raise CampaignEvidenceError(
                        "campaign artifact is not a canonical campaign fixture"
                    )
                values.append(value)
                bindings.append(
                    EngineArtifactBinding(reference=reference, source=source)
                )
            fixture = CanonicalSimulationFixtureV1(scenario_id, *values)
            scenarios.append(
                VerifiedCampaignScenarioV1(
                    scenario_id=scenario_id,
                    fixture=fixture,
                    envelope=build_simulation_envelope(fixture),
                    bindings=tuple(bindings),
                    engine_configuration_sha256=str(
                        record["engine_configuration_sha256"]
                    ),
                    instrument_catalog_sha256=str(
                        record["instrument_catalog_sha256"]
                    ),
                    strategy_configuration_sha256=str(
                        record["strategy_configuration_sha256"]
                    ),
                    market_data_sha256=str(record["market_data_sha256"]),
                    simulation_scenario_sha256=str(
                        record["simulation_scenario_sha256"]
                    ),
                )
            )
        _verify_campaign_snapshot(
            parent_descriptor=parent_descriptor,
            parent_identity=parent_identity,
            root=root,
            scenarios=tuple(scenario_handles),
            files=file_identities,
        )
        return VerifiedCampaignV1(
            root=directory,
            sha256=hashlib.sha256(manifest_raw).hexdigest(),
            strategy_source_sha256=strategy_digest,
            scenarios=tuple(scenarios),
        )
    except CampaignEvidenceError:
        raise
    except OSError as exc:
        raise CampaignEvidenceError("campaign snapshot is unavailable") from exc
    finally:
        for scenario in scenario_handles:
            os.close(scenario.descriptor)
        os.close(root.descriptor)
        os.close(parent_descriptor)


def _write_sealed_file(
    path: Path,
    value: bytes,
    *,
    directory_fd: int | None = None,
) -> tuple[int, ...]:
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
            dir_fd=directory_fd,
        )
        os.fchmod(descriptor, 0o400)
        remaining = memoryview(value)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("short campaign write")
            remaining = remaining[written:]
        os.fsync(descriptor)
        opened = os.fstat(descriptor)
        named = os.stat(path, dir_fd=directory_fd, follow_symlinks=False)
        if _identity(opened) != _identity(named):
            raise CampaignEvidenceError("campaign artifact identity changed")
        return _identity(opened)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _seal_failed_campaign(
    *,
    root: _DirectoryHandle,
    scenarios: tuple[_DirectoryHandle, ...],
) -> None:
    """Seal task-owned descriptors as retained forensic state without deletion."""

    failures: list[OSError] = []
    for scenario in scenarios:
        try:
            os.fchmod(scenario.descriptor, 0o500)
            os.fsync(scenario.descriptor)
        except OSError as exc:
            failures.append(exc)
    try:
        os.fchmod(root.descriptor, 0o500)
        os.fsync(root.descriptor)
    except OSError as exc:
        failures.append(exc)
    if failures:
        raise CampaignEvidenceError(
            "failed campaign residue could not be sealed"
        ) from failures[0]


def materialize_phase4_campaign(destination: Path) -> VerifiedCampaignV1:
    """Create the canonical campaign once and return its sealed verification."""

    _require_external_path(destination, label="campaign destination")
    parent_descriptor = -1
    root_descriptor = -1
    root: _DirectoryHandle | None = None
    parent_identity: tuple[int, ...] | None = None
    scenario_handles: list[_DirectoryHandle] = []
    files: dict[int, dict[str, tuple[int, ...]]] = {}
    try:
        parent = destination.parent.resolve(strict=True)
        observed_parent = destination.parent.lstat()
        if (
            parent != destination.parent
            or stat.S_ISLNK(observed_parent.st_mode)
            or not stat.S_ISDIR(observed_parent.st_mode)
            or observed_parent.st_uid != os.geteuid()
            or observed_parent.st_mode & 0o077
        ):
            raise CampaignEvidenceError("campaign destination parent is unsafe")
        parent_descriptor = os.open(
            destination.parent,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        if _identity(os.fstat(parent_descriptor)) != _identity(observed_parent):
            raise CampaignEvidenceError(
                "campaign destination parent identity changed"
            )
        os.mkdir(destination.name, mode=0o500, dir_fd=parent_descriptor)
        parent_identity = _identity(os.fstat(parent_descriptor))
        root_descriptor = os.open(
            destination.name,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_descriptor,
        )
        root = _DirectoryHandle(
            root_descriptor,
            destination,
            _identity(os.fstat(root_descriptor)),
        )
        os.fchmod(root_descriptor, 0o700)
        root = _DirectoryHandle(
            root_descriptor,
            destination,
            _identity(os.fstat(root_descriptor)),
        )
        files[root_descriptor] = {}
        records: list[dict[str, object]] = []
        for scenario_id in SCENARIO_IDS:
            fixture = build_canonical_simulation_fixture(scenario_id)
            scenario_directory = destination / scenario_id
            os.mkdir(scenario_id, mode=0o500, dir_fd=root_descriptor)
            scenario_descriptor = os.open(
                scenario_id,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=root_descriptor,
            )
            try:
                scenario_identity = _identity(os.fstat(scenario_descriptor))
            except BaseException as exc:
                try:
                    os.close(scenario_descriptor)
                except OSError as close_error:
                    exc.add_note(
                        "secondary scenario descriptor close failure: "
                        f"{type(close_error).__name__}"
                    )
                raise
            scenario_handle = _DirectoryHandle(
                scenario_descriptor,
                scenario_directory,
                scenario_identity,
            )
            scenario_handles.append(scenario_handle)
            os.fchmod(scenario_descriptor, 0o700)
            scenario_handles[-1] = _DirectoryHandle(
                scenario_descriptor,
                scenario_directory,
                _identity(os.fstat(scenario_descriptor)),
            )
            files[scenario_descriptor] = {}
            record: dict[str, object] = {"scenario_id": scenario_id}
            for (filename, field), value in zip(
                CAMPAIGN_ARTIFACTS,
                fixture.artifacts,
                strict=True,
            ):
                files[scenario_descriptor][filename] = _write_sealed_file(
                    Path(filename),
                    value,
                    directory_fd=scenario_descriptor,
                )
                record[field] = hashlib.sha256(value).hexdigest()
            os.fchmod(scenario_descriptor, 0o500)
            os.fsync(scenario_descriptor)
            scenario_handles[-1] = _DirectoryHandle(
                scenario_descriptor,
                scenario_directory,
                _identity(os.fstat(scenario_descriptor)),
            )
            records.append(record)
        manifest = {
            "paper_scenario_id": "long-accounting",
            "scenarios": records,
            "schema_version": "nautilus-phase4-campaign-v1",
            "strategy_source_sha256": hashlib.sha256(
                _STRATEGY_SOURCE.read_bytes()
            ).hexdigest(),
        }
        files[root_descriptor][CAMPAIGN_MANIFEST_NAME] = _write_sealed_file(
            Path(CAMPAIGN_MANIFEST_NAME),
            canonical_json_bytes(manifest) + b"\n",
            directory_fd=root_descriptor,
        )
        os.fchmod(root_descriptor, 0o500)
        os.fsync(root_descriptor)
        root = _DirectoryHandle(
            root_descriptor,
            destination,
            _identity(os.fstat(root_descriptor)),
        )
        assert parent_identity is not None
        _verify_campaign_snapshot(
            parent_descriptor=parent_descriptor,
            parent_identity=parent_identity,
            root=root,
            scenarios=tuple(scenario_handles),
            files=files,
        )
        verified = load_verified_campaign(destination)
        _verify_campaign_snapshot(
            parent_descriptor=parent_descriptor,
            parent_identity=parent_identity,
            root=root,
            scenarios=tuple(scenario_handles),
            files=files,
        )
        return verified
    except Exception as exc:
        if root is not None:
            try:
                _seal_failed_campaign(root=root, scenarios=tuple(scenario_handles))
            except CampaignEvidenceError as sealing_error:
                exc.add_note(str(sealing_error))
        if isinstance(exc, FileExistsError):
            raise CampaignEvidenceError("campaign destination already exists") from exc
        raise
    finally:
        for scenario in scenario_handles:
            os.close(scenario.descriptor)
        if root_descriptor >= 0:
            os.close(root_descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)


def _input_identity_sha256(item: VerifiedCampaignScenarioV1) -> str:
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "engine_configuration": item.engine_configuration_sha256,
                "instrument_catalog": item.instrument_catalog_sha256,
                "market_data": item.market_data_sha256,
                "simulation_scenario": item.simulation_scenario_sha256,
                "strategy_configuration": item.strategy_configuration_sha256,
            }
        )
    ).hexdigest()


def _decimal_text(value: Decimal) -> str:
    if value.is_zero():
        return "0"
    result = format(value, "f")
    return result.rstrip("0").rstrip(".") if "." in result else result


def _reference_digests(item: VerifiedCampaignScenarioV1) -> tuple[str, str]:
    scenario = BacktestScenarioV1.from_mounted_artifacts(
        scenario_bytes=item.fixture.simulation_scenario,
        catalog_bytes=item.fixture.instrument_catalog,
        strategy_bytes=item.fixture.strategy_configuration,
        market_data_bytes=item.fixture.market_data,
        start_time=item.envelope.payload.start_time,
        end_time=item.envelope.payload.end_time,
    )
    expected = calculate_reference_outcome(scenario)
    payload = EngineEvent(
        event_type="NautilusBacktestSimulationCompleted",
        family=EventFamily.ENGINE_LIFECYCLE,
        attributes=tuple(
            EventAttribute(name=name, value=value)
            for name, value in (
                ("input_artifacts_sha256", _input_identity_sha256(item)),
                ("scenario_digest", expected.scenario_digest),
                ("scenario_id", expected.scenario_id),
                ("event_digest", expected.event_digest),
                ("iterations", expected.iterations),
                ("total_events", expected.total_events),
                ("total_orders", expected.total_orders),
                ("total_fills", expected.total_fills),
                ("total_positions", expected.total_positions),
                ("filled_quantity", _decimal_text(expected.filled_quantity)),
                ("remaining_quantity", _decimal_text(expected.remaining_quantity)),
                ("position_quantity", _decimal_text(expected.position_quantity)),
                ("average_entry_price", _decimal_text(expected.average_entry_price)),
                ("fees", _decimal_text(expected.fees)),
                ("realized_pnl", _decimal_text(expected.realized_pnl)),
                ("unrealized_pnl", _decimal_text(expected.unrealized_pnl)),
                (
                    "stop_take_profit_precedence",
                    expected.stop_take_profit_precedence,
                ),
            )
        ),
    )
    envelope = item.envelope
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
    event_sha256 = hashlib.sha256(canonical_json_bytes(event)).hexdigest()
    result_sha256 = hashlib.sha256(
        canonical_json_bytes(
            {
                "event_sha256": event_sha256,
                "input_artifacts_sha256": _input_identity_sha256(item),
                "request_sha256": hashlib.sha256(
                    canonical_json_bytes(envelope)
                ).hexdigest(),
            }
        )
    ).hexdigest()
    return event_sha256, result_sha256


def _load_parity(
    raw: bytes,
    campaign: VerifiedCampaignV1,
) -> tuple[dict[str, object], bytes]:
    value = _canonical_line_object(raw, label="parity record")
    if (
        set(value) != _PARITY_FIELDS
        or value.get("schema_version") != "nautilus-phase4-parity-evidence-v2"
        or value.get("status") != "passed"
        or type(value.get("candidate_manifest_schema_version")) is not int
        or value.get("candidate_manifest_schema_version") != 6
        or value.get("scenario_campaign_sha256") != campaign.sha256
        or value.get("strategy_source_sha256") != campaign.strategy_source_sha256
        or not isinstance(value.get("scenarios"), list)
        or len(value["scenarios"]) != len(SCENARIO_IDS)
    ):
        raise CampaignEvidenceError("parity record is invalid")
    _sha256(value["candidate_closure_sha256"], label="candidate closure")
    _sha256(value["candidate_manifest_sha256"], label="candidate manifest")
    for campaign_item, scenario in zip(
        campaign.scenarios,
        value["scenarios"],
        strict=True,
    ):
        if (
            not isinstance(scenario, dict)
            or set(scenario) != _PARITY_SCENARIO_FIELDS
            or scenario.get("scenario_id") != campaign_item.scenario_id
        ):
            raise CampaignEvidenceError("parity scenarios are incomplete or unordered")
        for field in _SCENARIO_FIELDS - {"scenario_id"}:
            if scenario[field] != getattr(campaign_item, field):
                raise CampaignEvidenceError("parity scenario identity drifted")
        for field in _PARITY_RESULT_FIELDS:
            _sha256(scenario[field], label=f"parity {field}")
        if len(
            {
                scenario["independent_reference_event_sha256"],
                scenario["nautilus_event_sha256"],
                scenario["run_1_event_sha256"],
                scenario["run_2_event_sha256"],
            }
        ) != 1:
            raise CampaignEvidenceError("parity event digests differ")
        if (
            scenario["independent_reference_result_sha256"]
            != scenario["nautilus_result_sha256"]
        ):
            raise CampaignEvidenceError("parity result digests differ")
        expected_event, expected_result = _reference_digests(campaign_item)
        if (
            scenario["independent_reference_event_sha256"] != expected_event
            or scenario["independent_reference_result_sha256"] != expected_result
        ):
            raise CampaignEvidenceError("parity record does not match the oracle")
    return value, raw


def _load_paper(
    raw: bytes,
    *,
    campaign: VerifiedCampaignV1,
    parity: dict[str, object],
    parity_sha256: str,
) -> tuple[PaperCompatibilityResultV1, bytes]:
    document = _canonical_line_object(raw, label="paper record")
    try:
        paper = PaperCompatibilityResultV1.model_validate(document)
    except ValueError as exc:
        raise CampaignEvidenceError("paper record is invalid") from exc
    long_accounting = campaign.scenario("long-accounting")
    if (
        paper.scenario_campaign_sha256 != campaign.sha256
        or paper.strategy_source_sha256 != campaign.strategy_source_sha256
        or paper.parity_record_sha256 != parity_sha256
        or paper.engine_configuration_sha256
        != long_accounting.engine_configuration_sha256
        or paper.instrument_catalog_sha256
        != long_accounting.instrument_catalog_sha256
        or paper.strategy_configuration_sha256
        != long_accounting.strategy_configuration_sha256
    ):
        raise CampaignEvidenceError("paper record binding drifted")
    return paper, raw


def _load_legacy(
    raw_records: dict[str, bytes],
    campaign: VerifiedCampaignV1,
) -> tuple[tuple[dict[str, object], ...], str]:
    expected_names = {f"{scenario_id}.json" for scenario_id in SCENARIO_IDS}
    if set(raw_records) != expected_names:
        raise CampaignEvidenceError("legacy record inventory is invalid")
    records: list[dict[str, object]] = []
    raw_digests: list[str] = []
    for campaign_item in campaign.scenarios:
        raw = raw_records[f"{campaign_item.scenario_id}.json"]
        record = _canonical_line_object(raw, label="legacy record")
        if (
            set(record) != _LEGACY_FIELDS
            or record.get("schema_version")
            != "nautilus-legacy-scenario-comparison-v1"
            or record.get("scenario_id") != campaign_item.scenario_id
            or record.get("legacy_disposition") != "explained-difference"
            or record.get("legacy_classification") != "legacy-minimum-50-bars"
            or record.get("legacy_selected") is not False
        ):
            raise CampaignEvidenceError("legacy comparison is invalid")
        for field in _SCENARIO_FIELDS - {"scenario_id"}:
            if record[field] != getattr(campaign_item, field):
                raise CampaignEvidenceError("legacy scenario identity drifted")
        _sha256(record["legacy_result_sha256"], label="legacy result")
        _sha256(record["legacy_event_sha256"], label="legacy event")
        records.append(record)
        raw_digests.append(hashlib.sha256(raw).hexdigest())
    return tuple(records), hashlib.sha256(canonical_json_bytes(raw_digests)).hexdigest()


def _point_in_time(
    campaign: VerifiedCampaignV1,
) -> tuple[PointInTimeObservation, ...]:
    observations: list[PointInTimeObservation] = []
    for index, item in enumerate(campaign.scenarios, start=1):
        catalog = json.loads(item.fixture.instrument_catalog)
        scenario = BacktestScenarioV1.from_mounted_artifacts(
            scenario_bytes=item.fixture.simulation_scenario,
            catalog_bytes=item.fixture.instrument_catalog,
            strategy_bytes=item.fixture.strategy_configuration,
            market_data_bytes=item.fixture.market_data,
            start_time=item.envelope.payload.start_time,
            end_time=item.envelope.payload.end_time,
        )
        feature_time = datetime.fromisoformat(
            scenario.events[0].event_time[:-1] + "+00:00"
        )
        known_time = datetime.fromisoformat(str(catalog["known_at"])[:-1] + "+00:00")
        observations.append(
            PointInTimeObservation(
                observation_id=f"scenario-{index:02d}-{item.scenario_id}",
                input_artifacts_sha256=_input_identity_sha256(item),
                feature_event_at=feature_time,
                known_at=known_time,
                decision_at=known_time,
                source_data_sha256=str(catalog["canonical_rows_sha256"]),
            )
        )
    return tuple(observations)


def _recursive_replays(
    campaign: VerifiedCampaignV1,
) -> tuple[RecursiveIndicatorReplay, ...]:
    records: list[RecursiveIndicatorReplay] = []
    for index, item in enumerate(campaign.scenarios, start=1):
        seed_sha256 = hashlib.sha256(item.fixture.market_data).hexdigest()

        def replay() -> str:
            state = bytes.fromhex(seed_sha256)
            for line in item.fixture.market_data.splitlines():
                row = json.loads(line)
                state = hashlib.sha256(
                    state + canonical_json_bytes(row)
                ).digest()
            return state.hex()

        prefix_state_sha256 = replay()
        replay_state_sha256 = replay()
        sample_count = len(item.fixture.market_data.splitlines())
        records.append(
            RecursiveIndicatorReplay(
                indicator_name=f"campaign-{index:02d}-{item.scenario_id}",
                input_artifacts_sha256=_input_identity_sha256(item),
                seed_sha256=seed_sha256,
                prefix_state_sha256=prefix_state_sha256,
                replay_state_sha256=replay_state_sha256,
                sample_count=sample_count,
            )
        )
    return tuple(records)


def _walk_forward_folds(
    campaign: VerifiedCampaignV1,
) -> tuple[WalkForwardFold, ...]:
    known_times = [item.known_at for item in _point_in_time(campaign)]
    start = max(known_times)
    folds: list[WalkForwardFold] = []
    for index in range(2):
        offset = index * 6
        scenario_slice = campaign.scenarios[index * 4 : (index + 1) * 4]
        scenario_inputs = [
            {
                "input_artifacts_sha256": _input_identity_sha256(item),
                "scenario_id": item.scenario_id,
            }
            for item in scenario_slice
        ]
        fold_id = f"campaign-fold-{index + 1}"
        input_artifacts_sha256 = hashlib.sha256(
            canonical_json_bytes(
                {
                    "fold_id": fold_id,
                    "scenario_inputs": scenario_inputs,
                }
            )
        ).hexdigest()
        out_of_sample_return = Decimal("0")
        for item in scenario_slice[2:]:
            scenario = BacktestScenarioV1.from_mounted_artifacts(
                scenario_bytes=item.fixture.simulation_scenario,
                catalog_bytes=item.fixture.instrument_catalog,
                strategy_bytes=item.fixture.strategy_configuration,
                market_data_bytes=item.fixture.market_data,
                start_time=item.envelope.payload.start_time,
                end_time=item.envelope.payload.end_time,
            )
            outcome = calculate_reference_outcome(scenario)
            out_of_sample_return += (
                outcome.realized_pnl + outcome.unrealized_pnl - outcome.fees
            )
        out_of_sample_return /= Decimal(len(scenario_slice[2:]))
        folds.append(
            WalkForwardFold(
                fold_id=fold_id,
                input_artifacts_sha256=input_artifacts_sha256,
                train_start_at=start + timedelta(minutes=offset),
                train_end_at=start + timedelta(minutes=offset + 1),
                validation_start_at=start + timedelta(minutes=offset + 2),
                validation_end_at=start + timedelta(minutes=offset + 3),
                out_of_sample_start_at=start + timedelta(minutes=offset + 4),
                out_of_sample_end_at=start + timedelta(minutes=offset + 5),
                out_of_sample_return=out_of_sample_return,
            )
        )
    return tuple(folds)


def _cost_scenarios(campaign: VerifiedCampaignV1) -> tuple[CostScenario, ...]:
    item = campaign.scenario("long-accounting")
    scenario = BacktestScenarioV1.from_mounted_artifacts(
        scenario_bytes=item.fixture.simulation_scenario,
        catalog_bytes=item.fixture.instrument_catalog,
        strategy_bytes=item.fixture.strategy_configuration,
        market_data_bytes=item.fixture.market_data,
        start_time=item.envelope.payload.start_time,
        end_time=item.envelope.payload.end_time,
    )
    baseline_fee = int(scenario.fee_rate * Decimal("10000"))
    baseline_slippage = int(scenario.slippage_bps)
    parameters = {
        "baseline": (baseline_fee, baseline_slippage),
        "combined-stress": (baseline_fee + 10, baseline_slippage + 10),
        "fee-stress": (baseline_fee + 10, baseline_slippage),
        "slippage-stress": (baseline_fee, baseline_slippage + 10),
    }
    results: list[CostScenario] = []
    for name in sorted(parameters):
        fee_bps, slippage_bps = parameters[name]
        stressed = scenario.model_copy(
            update={
                "fee_rate": Decimal(fee_bps) / Decimal("10000"),
                "slippage_bps": Decimal(slippage_bps),
            }
        )
        outcome = calculate_reference_outcome(stressed)
        results.append(
            CostScenario(
                name=name,
                input_artifacts_sha256=campaign.sha256,
                fee_bps=fee_bps,
                slippage_bps=slippage_bps,
                net_return=(
                    outcome.realized_pnl + outcome.unrealized_pnl - outcome.fees
                ),
            )
        )
    return tuple(results)


def produce_research_campaign_evidence(
    *,
    campaign_directory: Path,
    parity_record: Path,
    paper_record: Path,
    legacy_record_directory: Path,
) -> ResearchCampaignEvidenceV2:
    """Derive the complete V2 research evidence from sealed records only."""

    campaign = load_verified_campaign(campaign_directory)
    aggregate_directories: list[_AggregateDirectory] = []
    record_directories: dict[Path, _AggregateDirectory] = {}
    primary_failure: BaseException | None = None
    try:
        for path, label in (
            (parity_record, "parity record"),
            (paper_record, "paper record"),
        ):
            _require_external_path(path, label=label)
            if path.parent not in record_directories:
                directory = _open_aggregate_directory(
                    path.parent,
                    label=f"{label} parent",
                    expected_mode=0o700,
                )
                record_directories[path.parent] = directory
                aggregate_directories.append(directory)
        parity_raw = _read_aggregate_member(
            record_directories[parity_record.parent],
            parity_record.name,
            label="parity record",
        )
        paper_raw = _read_aggregate_member(
            record_directories[paper_record.parent],
            paper_record.name,
            label="paper record",
        )
        legacy_names = frozenset(
            f"{scenario_id}.json" for scenario_id in SCENARIO_IDS
        )
        legacy_directory = _open_aggregate_directory(
            legacy_record_directory,
            label="legacy record directory",
            expected_mode=0o500,
            expected_inventory=legacy_names,
        )
        aggregate_directories.append(legacy_directory)
        legacy_raw = {
            name: _read_aggregate_member(
                legacy_directory,
                name,
                label=f"legacy {name.removesuffix('.json')}",
            )
            for name in sorted(legacy_names)
        }
        parity, parity_raw = _load_parity(parity_raw, campaign)
        paper, paper_raw = _load_paper(
            paper_raw,
            campaign=campaign,
            parity=parity,
            parity_sha256=hashlib.sha256(parity_raw).hexdigest(),
        )
        legacy, legacy_sha256 = _load_legacy(legacy_raw, campaign)
    except BaseException as exc:
        primary_failure = exc
        raise
    finally:
        verification_failure: CampaignEvidenceError | None = None
        for directory in aggregate_directories:
            try:
                _verify_aggregate_directory(directory)
            except CampaignEvidenceError as exc:
                if primary_failure is not None:
                    primary_failure.add_note(str(exc))
                elif verification_failure is None:
                    verification_failure = exc
        for directory in reversed(aggregate_directories):
            _close_aggregate_directory(directory)
        if primary_failure is None and verification_failure is not None:
            raise verification_failure
    comparisons = tuple(
        VerifiedScenarioComparisonV1(
            scenario_id=campaign_item.scenario_id,
            engine_configuration_sha256=campaign_item.engine_configuration_sha256,
            instrument_catalog_sha256=campaign_item.instrument_catalog_sha256,
            strategy_configuration_sha256=campaign_item.strategy_configuration_sha256,
            market_data_sha256=campaign_item.market_data_sha256,
            simulation_scenario_sha256=campaign_item.simulation_scenario_sha256,
            independent_reference_result_sha256=parity_item[
                "independent_reference_result_sha256"
            ],
            independent_reference_event_sha256=parity_item[
                "independent_reference_event_sha256"
            ],
            nautilus_result_sha256=parity_item["nautilus_result_sha256"],
            nautilus_event_sha256=parity_item["nautilus_event_sha256"],
            legacy_result_sha256=legacy_item["legacy_result_sha256"],
            legacy_event_sha256=legacy_item["legacy_event_sha256"],
            legacy_disposition="explained-difference",
            legacy_classification="legacy-minimum-50-bars",
            legacy_selected=False,
        )
        for campaign_item, parity_item, legacy_item in zip(
            campaign.scenarios,
            parity["scenarios"],
            legacy,
            strict=True,
        )
    )
    point_in_time = _point_in_time(campaign)
    recursive_replays = _recursive_replays(campaign)
    walk_forward_folds = _walk_forward_folds(campaign)
    cost_scenarios = _cost_scenarios(campaign)
    paper_sha256 = hashlib.sha256(paper_raw).hexdigest()
    analysis_sha256 = campaign_analysis_output_sha256(
        comparisons=comparisons,
        paper_result=paper,
        point_in_time=point_in_time,
        recursive_replays=recursive_replays,
        walk_forward_folds=walk_forward_folds,
        minimum_walk_forward_return=Decimal("-0.5"),
        cost_scenarios=cost_scenarios,
        minimum_stressed_return=Decimal("0"),
    )
    return ResearchCampaignEvidenceV2(
        scenario_campaign_sha256=campaign.sha256,
        strategy_source_sha256=campaign.strategy_source_sha256,
        candidate_closure_sha256=parity["candidate_closure_sha256"],
        candidate_manifest_sha256=parity["candidate_manifest_sha256"],
        parity_record_sha256=hashlib.sha256(parity_raw).hexdigest(),
        paper_record_sha256=paper_sha256,
        legacy_records_sha256=legacy_sha256,
        comparisons=comparisons,
        paper_result=paper,
        point_in_time=point_in_time,
        recursive_replays=recursive_replays,
        walk_forward_folds=walk_forward_folds,
        minimum_walk_forward_return=Decimal("-0.5"),
        cost_scenarios=cost_scenarios,
        minimum_stressed_return=Decimal("0"),
        analysis_output_sha256=analysis_sha256,
        promotion_authority="reference-and-nautilus",
    )


__all__ = [
    "CAMPAIGN_ARTIFACTS",
    "CAMPAIGN_MANIFEST_NAME",
    "CampaignEvidenceError",
    "VerifiedCampaignScenarioV1",
    "VerifiedCampaignV1",
    "load_verified_campaign",
    "materialize_phase4_campaign",
    "produce_research_campaign_evidence",
]
