"""Sealed, deterministic producers for the Phase-4 research campaign."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
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


def _sealed_directory(path: Path, *, label: str) -> tuple[Path, ...]:
    _require_external_path(path, label=label)
    try:
        observed = path.lstat()
        resolved = path.resolve(strict=True)
        entries = tuple(path.iterdir())
    except OSError as exc:
        raise CampaignEvidenceError(f"{label} is unavailable") from exc
    if (
        resolved != path
        or stat.S_ISLNK(observed.st_mode)
        or not stat.S_ISDIR(observed.st_mode)
        or observed.st_uid != os.geteuid()
        or stat.S_IMODE(observed.st_mode) != 0o500
    ):
        raise CampaignEvidenceError(f"{label} is not sealed")
    return entries


def _sealed_bytes(path: Path, *, label: str) -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
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
        named = path.stat(follow_symlinks=False)
        identity = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_uid",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if any(getattr(named, name) != getattr(opened, name) for name in identity):
            raise CampaignEvidenceError(f"{label} identity changed while being read")
        return b"".join(chunks)
    except CampaignEvidenceError:
        raise
    except OSError as exc:
        raise CampaignEvidenceError(f"{label} is unavailable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


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

    entries = _sealed_directory(directory, label="campaign directory")
    if {path.name for path in entries} != {CAMPAIGN_MANIFEST_NAME, *SCENARIO_IDS}:
        raise CampaignEvidenceError("campaign directory inventory is invalid")
    manifest_raw = _sealed_bytes(
        directory / CAMPAIGN_MANIFEST_NAME,
        label="campaign manifest",
    )
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
    for scenario_id, record in zip(SCENARIO_IDS, manifest["scenarios"], strict=True):
        if (
            not isinstance(record, dict)
            or set(record) != _SCENARIO_FIELDS
            or record.get("scenario_id") != scenario_id
        ):
            raise CampaignEvidenceError("campaign scenarios are incomplete or unordered")
        scenario_directory = directory / scenario_id
        scenario_entries = _sealed_directory(
            scenario_directory,
            label=f"campaign scenario {scenario_id}",
        )
        if {path.name for path in scenario_entries} != {
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
            source = scenario_directory / filename
            value = _sealed_bytes(source, label=f"{scenario_id} {filename}")
            digest = hashlib.sha256(value).hexdigest()
            if _sha256(record[field], label=f"{scenario_id} {field}") != digest:
                raise CampaignEvidenceError("campaign artifact digest does not match")
            if value != expected:
                raise CampaignEvidenceError("campaign artifact is not a canonical campaign fixture")
            values.append(value)
            bindings.append(EngineArtifactBinding(reference=reference, source=source))
        fixture = CanonicalSimulationFixtureV1(scenario_id, *values)
        scenarios.append(
            VerifiedCampaignScenarioV1(
                scenario_id=scenario_id,
                fixture=fixture,
                envelope=build_simulation_envelope(fixture),
                bindings=tuple(bindings),
                engine_configuration_sha256=str(record["engine_configuration_sha256"]),
                instrument_catalog_sha256=str(record["instrument_catalog_sha256"]),
                strategy_configuration_sha256=str(record["strategy_configuration_sha256"]),
                market_data_sha256=str(record["market_data_sha256"]),
                simulation_scenario_sha256=str(record["simulation_scenario_sha256"]),
            )
        )
    return VerifiedCampaignV1(
        root=directory,
        sha256=hashlib.sha256(manifest_raw).hexdigest(),
        strategy_source_sha256=strategy_digest,
        scenarios=tuple(scenarios),
    )


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
                raise OSError("short campaign write")
            remaining = remaining[written:]
        os.fsync(descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def materialize_phase4_campaign(destination: Path) -> VerifiedCampaignV1:
    """Create the canonical campaign once and return its sealed verification."""

    _require_external_path(destination, label="campaign destination")
    try:
        parent = destination.parent.resolve(strict=True)
        observed_parent = destination.parent.lstat()
    except OSError as exc:
        raise CampaignEvidenceError("campaign destination parent is unavailable") from exc
    if (
        parent != destination.parent
        or stat.S_ISLNK(observed_parent.st_mode)
        or not stat.S_ISDIR(observed_parent.st_mode)
        or observed_parent.st_uid != os.geteuid()
        or observed_parent.st_mode & 0o077
    ):
        raise CampaignEvidenceError("campaign destination parent is unsafe")
    created = False
    try:
        destination.mkdir(mode=0o700)
        created = True
        records: list[dict[str, object]] = []
        for scenario_id in SCENARIO_IDS:
            fixture = build_canonical_simulation_fixture(scenario_id)
            scenario_directory = destination / scenario_id
            scenario_directory.mkdir(mode=0o700)
            record: dict[str, object] = {"scenario_id": scenario_id}
            for (filename, field), value in zip(
                CAMPAIGN_ARTIFACTS,
                fixture.artifacts,
                strict=True,
            ):
                _write_sealed_file(scenario_directory / filename, value)
                record[field] = hashlib.sha256(value).hexdigest()
            scenario_directory.chmod(0o500)
            records.append(record)
        manifest = {
            "paper_scenario_id": "long-accounting",
            "scenarios": records,
            "schema_version": "nautilus-phase4-campaign-v1",
            "strategy_source_sha256": hashlib.sha256(
                _STRATEGY_SOURCE.read_bytes()
            ).hexdigest(),
        }
        _write_sealed_file(
            destination / CAMPAIGN_MANIFEST_NAME,
            canonical_json_bytes(manifest) + b"\n",
        )
        destination.chmod(0o500)
        return load_verified_campaign(destination)
    except Exception as exc:
        if created:
            try:
                destination.chmod(0o700)
                for child in destination.iterdir():
                    if child.is_dir() and not child.is_symlink():
                        child.chmod(0o700)
                        for artifact in child.iterdir():
                            if not artifact.is_symlink():
                                artifact.chmod(0o600)
                    elif not child.is_symlink():
                        child.chmod(0o600)
                shutil.rmtree(destination)
            except OSError:
                pass
        if isinstance(exc, FileExistsError):
            raise CampaignEvidenceError("campaign destination already exists") from exc
        raise


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
    path: Path,
    campaign: VerifiedCampaignV1,
) -> tuple[dict[str, object], bytes]:
    _require_external_path(path, label="parity record")
    raw = _sealed_bytes(path, label="parity record")
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
    path: Path,
    *,
    campaign: VerifiedCampaignV1,
    parity: dict[str, object],
    parity_sha256: str,
) -> tuple[PaperCompatibilityResultV1, bytes]:
    _require_external_path(path, label="paper record")
    raw = _sealed_bytes(path, label="paper record")
    document = _canonical_line_object(raw, label="paper record")
    try:
        paper = PaperCompatibilityResultV1.model_validate(document)
    except ValueError as exc:
        raise CampaignEvidenceError("paper record is invalid") from exc
    long_accounting = campaign.scenario("long-accounting")
    if (
        paper.scenario_campaign_sha256 != campaign.sha256
        or paper.strategy_source_sha256 != campaign.strategy_source_sha256
        or paper.candidate_closure_sha256 != parity["candidate_closure_sha256"]
        or paper.candidate_manifest_sha256 != parity["candidate_manifest_sha256"]
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
    directory: Path,
    campaign: VerifiedCampaignV1,
) -> tuple[tuple[dict[str, object], ...], str]:
    entries = _sealed_directory(directory, label="legacy record directory")
    if {entry.name for entry in entries} != {
        f"{scenario_id}.json" for scenario_id in SCENARIO_IDS
    }:
        raise CampaignEvidenceError("legacy record inventory is invalid")
    records: list[dict[str, object]] = []
    raw_digests: list[str] = []
    for campaign_item in campaign.scenarios:
        raw = _sealed_bytes(
            directory / f"{campaign_item.scenario_id}.json",
            label=f"legacy {campaign_item.scenario_id}",
        )
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
        rows = [json.loads(line) for line in item.fixture.market_data.splitlines()]
        state = hashlib.sha256(canonical_json_bytes(rows)).hexdigest()
        records.append(
            RecursiveIndicatorReplay(
                indicator_name=f"campaign-{index:02d}-{item.scenario_id}",
                input_artifacts_sha256=_input_identity_sha256(item),
                seed_sha256=hashlib.sha256(item.fixture.market_data).hexdigest(),
                prefix_state_sha256=state,
                replay_state_sha256=state,
                sample_count=len(rows),
            )
        )
    return tuple(records)


def _walk_forward_folds(
    campaign: VerifiedCampaignV1,
    comparisons: tuple[VerifiedScenarioComparisonV1, ...],
) -> tuple[WalkForwardFold, ...]:
    known_times = [item.known_at for item in _point_in_time(campaign)]
    start = max(known_times)
    folds: list[WalkForwardFold] = []
    for index in range(2):
        offset = index * 6
        comparison_slice = comparisons[index * 4 : (index + 1) * 4]
        matched_fraction = Decimal(
            sum(
                item.independent_reference_result_sha256
                == item.nautilus_result_sha256
                and item.independent_reference_event_sha256
                == item.nautilus_event_sha256
                for item in comparison_slice
            )
        ) / Decimal(len(comparison_slice))
        folds.append(
            WalkForwardFold(
                fold_id=f"campaign-fold-{index + 1}",
                input_artifacts_sha256=campaign.sha256,
                train_start_at=start + timedelta(minutes=offset),
                train_end_at=start + timedelta(minutes=offset + 1),
                validation_start_at=start + timedelta(minutes=offset + 2),
                validation_end_at=start + timedelta(minutes=offset + 3),
                out_of_sample_start_at=start + timedelta(minutes=offset + 4),
                out_of_sample_end_at=start + timedelta(minutes=offset + 5),
                out_of_sample_return=matched_fraction,
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
    parity, parity_raw = _load_parity(parity_record, campaign)
    paper, paper_raw = _load_paper(
        paper_record,
        campaign=campaign,
        parity=parity,
        parity_sha256=hashlib.sha256(parity_raw).hexdigest(),
    )
    legacy, legacy_sha256 = _load_legacy(legacy_record_directory, campaign)
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
    walk_forward_folds = _walk_forward_folds(campaign, comparisons)
    cost_scenarios = _cost_scenarios(campaign)
    paper_sha256 = hashlib.sha256(paper_raw).hexdigest()
    analysis_sha256 = campaign_analysis_output_sha256(
        comparisons=comparisons,
        paper_result=paper,
        point_in_time=point_in_time,
        recursive_replays=recursive_replays,
        walk_forward_folds=walk_forward_folds,
        minimum_walk_forward_return=Decimal("2"),
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
        minimum_walk_forward_return=Decimal("2"),
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
