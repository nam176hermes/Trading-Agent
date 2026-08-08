from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import uuid5

import pytest

import packages.research_validation as research
from packages.research_validation import producers
from packages.engine_contracts import (
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
    build_simulation_envelope,
    calculate_reference_outcome,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MATERIALIZER = REPOSITORY_ROOT / "scripts" / "materialize_phase4_campaign_inputs.py"
RESEARCH_CLOSER = REPOSITORY_ROOT / "scripts" / "close_phase4_research_evidence.py"
ARTIFACT_NAMES = (
    "engine-configuration.json",
    "instrument-catalog.json",
    "strategy-configuration.json",
    "market-data.json",
    "simulation-scenario.json",
)
DIGEST_FIELDS = (
    "engine_configuration_sha256",
    "instrument_catalog_sha256",
    "strategy_configuration_sha256",
    "market_data_sha256",
    "simulation_scenario_sha256",
)


@pytest.fixture
def private_root() -> Path:
    root = Path(tempfile.mkdtemp(prefix="phase4-campaign-test-", dir="/tmp"))
    root.chmod(0o700)
    try:
        yield root
    finally:
        for directory, _children, files in os.walk(root):
            Path(directory).chmod(0o700)
            for filename in files:
                path = Path(directory) / filename
                if not path.is_symlink():
                    path.chmod(0o600)
        shutil.rmtree(root)


def _run_materializer(destination: Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, str(MATERIALIZER), "--destination", str(destination)],
        cwd=REPOSITORY_ROOT,
        env={"PYTHONDONTWRITEBYTECODE": "1"},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_materializer_writes_one_no_clobber_exact_eight_scenario_campaign(
    private_root: Path,
) -> None:
    """A producer that omits, aliases, or rewrites a scenario breaks authority."""
    destination = private_root / "campaign"

    first = _run_materializer(destination)
    second = _run_materializer(destination)

    assert first.returncode == 0, first.stderr.decode("utf-8", errors="replace")
    assert first.stdout == b""
    assert second.returncode == 1
    assert stat.S_IMODE(destination.stat().st_mode) == 0o500
    assert sorted(path.name for path in destination.iterdir()) == sorted(
        ("campaign-manifest.json", *SCENARIO_IDS)
    )
    manifest_raw = (destination / "campaign-manifest.json").read_bytes()
    manifest = json.loads(manifest_raw)
    assert manifest_raw == canonical_json_bytes(manifest) + b"\n"
    assert manifest["schema_version"] == "nautilus-phase4-campaign-v1"
    assert manifest["paper_scenario_id"] == "long-accounting"
    assert [item["scenario_id"] for item in manifest["scenarios"]] == list(
        SCENARIO_IDS
    )
    for scenario_id, record in zip(SCENARIO_IDS, manifest["scenarios"], strict=True):
        scenario = destination / scenario_id
        assert stat.S_IMODE(scenario.stat().st_mode) == 0o500
        assert tuple(sorted(path.name for path in scenario.iterdir())) == tuple(
            sorted(ARTIFACT_NAMES)
        )
        fixture = build_canonical_simulation_fixture(scenario_id)
        for filename, field, expected in zip(
            ARTIFACT_NAMES, DIGEST_FIELDS, fixture.artifacts, strict=True
        ):
            artifact = scenario / filename
            assert stat.S_IMODE(artifact.stat().st_mode) == 0o400
            assert artifact.read_bytes() == expected
            assert record[field] == hashlib.sha256(expected).hexdigest()


def test_materializer_removes_its_partial_destination_after_nested_collision(
    private_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = private_root / "campaign"

    def collide(_path: Path, _value: bytes) -> None:
        raise FileExistsError("simulated nested collision")

    monkeypatch.setattr(producers, "_write_sealed_file", collide)

    with pytest.raises(ValueError, match="already exists"):
        research.materialize_phase4_campaign(destination)

    assert not destination.exists()


def test_verified_campaign_rejects_a_self_consistent_noncanonical_artifact(
    private_root: Path,
) -> None:
    """A manifest digest alone must not turn caller-chosen bytes into fixtures."""
    destination = private_root / "campaign"
    assert _run_materializer(destination).returncode == 0
    manifest_path = destination / "campaign-manifest.json"
    artifact = destination / "long-accounting" / "market-data.json"
    destination.chmod(0o700)
    (destination / "long-accounting").chmod(0o700)
    artifact.chmod(0o600)
    artifact.write_bytes(b'{"close":"999"}\n')
    artifact.chmod(0o400)
    manifest_path.chmod(0o600)
    manifest = json.loads(manifest_path.read_bytes())
    manifest["scenarios"][0]["market_data_sha256"] = hashlib.sha256(
        artifact.read_bytes()
    ).hexdigest()
    manifest_path.write_bytes(canonical_json_bytes(manifest) + b"\n")
    manifest_path.chmod(0o400)
    (destination / "long-accounting").chmod(0o500)
    destination.chmod(0o500)

    with pytest.raises(ValueError, match="canonical campaign fixture"):
        research.load_verified_campaign(destination)


def test_verified_campaign_exposes_only_fixed_hash_bound_five_artifact_members(
    private_root: Path,
) -> None:
    destination = private_root / "campaign"
    assert _run_materializer(destination).returncode == 0

    campaign = research.load_verified_campaign(destination)

    assert campaign.sha256 == hashlib.sha256(
        (destination / "campaign-manifest.json").read_bytes()
    ).hexdigest()
    assert tuple(item.scenario_id for item in campaign.scenarios) == SCENARIO_IDS
    assert all(len(item.bindings) == 5 for item in campaign.scenarios)
    assert tuple(binding.source.name for binding in campaign.scenarios[0].bindings) == ARTIFACT_NAMES


def _seal(path: Path, value: bytes) -> None:
    path.write_bytes(value)
    path.chmod(0o400)


def _reference_digests(scenario_id: str) -> tuple[str, str]:
    fixture = build_canonical_simulation_fixture(scenario_id)
    envelope = build_simulation_envelope(fixture)
    scenario = BacktestScenarioV1.from_mounted_artifacts(
        scenario_bytes=fixture.simulation_scenario,
        catalog_bytes=fixture.instrument_catalog,
        strategy_bytes=fixture.strategy_configuration,
        market_data_bytes=fixture.market_data,
        start_time=envelope.payload.start_time,
        end_time=envelope.payload.end_time,
    )
    expected = calculate_reference_outcome(scenario)
    input_sha256 = hashlib.sha256(
        canonical_json_bytes(
            {
                name: getattr(envelope.payload, name).sha256
                for name in (
                    "engine_configuration",
                    "instrument_catalog",
                    "strategy_configuration",
                    "market_data",
                    "simulation_scenario",
                )
            }
        )
    ).hexdigest()
    attributes: tuple[tuple[str, str | int], ...] = (
        ("input_artifacts_sha256", input_sha256),
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
            EventAttribute(name=name, value=value) for name, value in attributes
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
    event_sha256 = hashlib.sha256(canonical_json_bytes(event)).hexdigest()
    result_sha256 = hashlib.sha256(
        canonical_json_bytes(
            {
                "event_sha256": event_sha256,
                "input_artifacts_sha256": input_sha256,
                "request_sha256": hashlib.sha256(
                    canonical_json_bytes(envelope)
                ).hexdigest(),
            }
        )
    ).hexdigest()
    return event_sha256, result_sha256


def _research_authorities(private_root: Path) -> tuple[Path, Path, Path, Path]:
    campaign = private_root / "campaign"
    assert _run_materializer(campaign).returncode == 0
    manifest_raw = (campaign / "campaign-manifest.json").read_bytes()
    manifest = json.loads(manifest_raw)
    records = manifest["scenarios"]
    candidate_closure = "b" * 64
    candidate_manifest = "c" * 64
    parity_scenarios: list[dict[str, object]] = []
    for record in records:
        event, result = _reference_digests(record["scenario_id"])
        parity_scenarios.append(
            {
                **record,
                "independent_reference_event_sha256": event,
                "independent_reference_result_sha256": result,
                "nautilus_event_sha256": event,
                "nautilus_result_sha256": result,
                "run_1_event_sha256": event,
                "run_2_event_sha256": event,
            }
        )
    parity = {
        "candidate_closure_sha256": candidate_closure,
        "candidate_manifest_schema_version": 6,
        "candidate_manifest_sha256": candidate_manifest,
        "scenario_campaign_sha256": hashlib.sha256(manifest_raw).hexdigest(),
        "scenarios": parity_scenarios,
        "schema_version": "nautilus-phase4-parity-evidence-v2",
        "status": "passed",
        "strategy_source_sha256": manifest["strategy_source_sha256"],
    }
    parity_path = private_root / "parity.json"
    parity_raw = canonical_json_bytes(parity) + b"\n"
    _seal(parity_path, parity_raw)
    long_accounting = records[0]
    paper = research.PaperCompatibilityResultV1.create(
        candidate_closure_sha256=candidate_closure,
        candidate_manifest_sha256=candidate_manifest,
        engine_configuration_sha256=long_accounting["engine_configuration_sha256"],
        instrument_catalog_sha256=long_accounting["instrument_catalog_sha256"],
        strategy_configuration_sha256=long_accounting["strategy_configuration_sha256"],
        strategy_source_sha256=manifest["strategy_source_sha256"],
        scenario_campaign_sha256=hashlib.sha256(manifest_raw).hexdigest(),
        parity_record_sha256=hashlib.sha256(parity_raw).hexdigest(),
        launcher_result_sha256="d" * 64,
    )
    paper_path = private_root / "paper.json"
    _seal(paper_path, canonical_json_bytes(paper) + b"\n")
    legacy = private_root / "legacy"
    legacy.mkdir(mode=0o700)
    for index, record in enumerate(records):
        legacy_record = {
            "engine_configuration_sha256": record["engine_configuration_sha256"],
            "instrument_catalog_sha256": record["instrument_catalog_sha256"],
            "legacy_classification": "legacy-minimum-50-bars",
            "legacy_disposition": "explained-difference",
            "legacy_event_sha256": hashlib.sha256(
                f"legacy-event:{index}".encode("ascii")
            ).hexdigest(),
            "legacy_result_sha256": hashlib.sha256(
                f"legacy-result:{index}".encode("ascii")
            ).hexdigest(),
            "legacy_selected": False,
            "market_data_sha256": record["market_data_sha256"],
            "scenario_id": record["scenario_id"],
            "schema_version": "nautilus-legacy-scenario-comparison-v1",
            "simulation_scenario_sha256": record["simulation_scenario_sha256"],
            "strategy_configuration_sha256": record["strategy_configuration_sha256"],
        }
        _seal(
            legacy / f"{record['scenario_id']}.json",
            canonical_json_bytes(legacy_record) + b"\n",
        )
    legacy.chmod(0o500)
    return campaign, parity_path, paper_path, legacy


def test_research_producer_derives_complete_campaign_evidence_from_sealed_inputs(
    private_root: Path,
) -> None:
    campaign, parity, paper, legacy = _research_authorities(private_root)

    evidence = research.produce_research_campaign_evidence(
        campaign_directory=campaign,
        parity_record=parity,
        paper_record=paper,
        legacy_record_directory=legacy,
    )

    assert evidence.schema_version == "research-campaign-evidence-v2"
    assert tuple(item.scenario_id for item in evidence.comparisons) == SCENARIO_IDS
    assert len(evidence.point_in_time) == 8
    assert len(evidence.recursive_replays) == 8
    assert len(evidence.walk_forward_folds) == 2
    assert {item.name for item in evidence.cost_scenarios} == {
        "baseline",
        "combined-stress",
        "fee-stress",
        "slippage-stress",
    }
    assert research.evaluate_research_campaign(evidence).passed is True
    assert research.close_ws04_research_campaign(evidence).closure_sha256 is not None


def test_research_producer_rejects_caller_authored_status_or_legacy_authority(
    private_root: Path,
) -> None:
    campaign, parity, paper, legacy = _research_authorities(private_root)
    parity.chmod(0o600)
    value = json.loads(parity.read_bytes())
    value["status"] = "failed"
    parity.write_bytes(canonical_json_bytes(value) + b"\n")
    parity.chmod(0o400)

    with pytest.raises(ValueError, match="parity"):
        research.produce_research_campaign_evidence(
            campaign_directory=campaign,
            parity_record=parity,
            paper_record=paper,
            legacy_record_directory=legacy,
        )

    second = private_root / "second"
    second.mkdir(mode=0o700)
    campaign, parity, paper, legacy = _research_authorities(second)
    selected = legacy / "long-accounting.json"
    legacy.chmod(0o700)
    selected.chmod(0o600)
    value = json.loads(selected.read_bytes())
    value["legacy_selected"] = True
    selected.write_bytes(canonical_json_bytes(value) + b"\n")
    selected.chmod(0o400)
    legacy.chmod(0o500)
    with pytest.raises(ValueError, match="legacy"):
        research.produce_research_campaign_evidence(
            campaign_directory=campaign,
            parity_record=parity,
            paper_record=paper,
            legacy_record_directory=legacy,
        )


def test_research_producer_rejects_equal_but_caller_chosen_parity_digests(
    private_root: Path,
) -> None:
    campaign, parity, paper, legacy = _research_authorities(private_root)
    parity.chmod(0o600)
    parity_value = json.loads(parity.read_bytes())
    parity_value["scenarios"][0].update(
        {
            "independent_reference_event_sha256": "0" * 64,
            "independent_reference_result_sha256": "1" * 64,
            "nautilus_event_sha256": "0" * 64,
            "nautilus_result_sha256": "1" * 64,
            "run_1_event_sha256": "0" * 64,
            "run_2_event_sha256": "0" * 64,
        }
    )
    parity_raw = canonical_json_bytes(parity_value) + b"\n"
    parity.write_bytes(parity_raw)
    parity.chmod(0o400)

    paper_value = research.PaperCompatibilityResultV1.model_validate_json(
        paper.read_bytes()
    )
    paper_value = research.PaperCompatibilityResultV1.create(
        candidate_closure_sha256=paper_value.candidate_closure_sha256,
        candidate_manifest_sha256=paper_value.candidate_manifest_sha256,
        engine_configuration_sha256=paper_value.engine_configuration_sha256,
        instrument_catalog_sha256=paper_value.instrument_catalog_sha256,
        strategy_configuration_sha256=paper_value.strategy_configuration_sha256,
        strategy_source_sha256=paper_value.strategy_source_sha256,
        scenario_campaign_sha256=paper_value.scenario_campaign_sha256,
        parity_record_sha256=hashlib.sha256(parity_raw).hexdigest(),
        launcher_result_sha256=paper_value.launcher_result_sha256,
    )
    paper.chmod(0o600)
    paper.write_bytes(canonical_json_bytes(paper_value) + b"\n")
    paper.chmod(0o400)

    with pytest.raises(ValueError, match="oracle"):
        research.produce_research_campaign_evidence(
            campaign_directory=campaign,
            parity_record=parity,
            paper_record=paper,
            legacy_record_directory=legacy,
        )


def test_research_cli_has_only_four_inputs_and_emits_one_canonical_closure_line(
    private_root: Path,
) -> None:
    campaign, parity, paper, legacy = _research_authorities(private_root)
    required = [
        "--campaign-directory",
        str(campaign),
        "--parity-record",
        str(parity),
        "--paper-record",
        str(paper),
        "--legacy-record-directory",
        str(legacy),
    ]

    completed = subprocess.run(
        [sys.executable, str(RESEARCH_CLOSER), *required],
        cwd=REPOSITORY_ROOT,
        env={"PYTHONDONTWRITEBYTECODE": "1"},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
    assert completed.stderr == b""
    assert completed.stdout.endswith(b"\n") and completed.stdout.count(b"\n") == 1
    closure = json.loads(completed.stdout)
    assert completed.stdout == canonical_json_bytes(closure) + b"\n"
    assert closure["schema_version"] == "ws04-campaign-closure-v2"
    rejected = subprocess.run(
        [sys.executable, str(RESEARCH_CLOSER), *required, "--evidence-root", "/tmp/x"],
        cwd=REPOSITORY_ROOT,
        env={"PYTHONDONTWRITEBYTECODE": "1"},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert rejected.returncode == 2
