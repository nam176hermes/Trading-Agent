from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from pydantic import ValidationError

import packages.engine_contracts as contracts
import packages.nautilus_backtest as backtest
from services.job_worker.engine_spawn_interface import EngineSpawnError


ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "engines/nautilus/launcher/nautilus_paper_compat.py"
HARNESS = ROOT / "scripts/verify_nautilus_paper_compatibility.py"
CAMPAIGN_FILES = (
    "engine-configuration.json",
    "instrument-catalog.json",
    "strategy-configuration.json",
    "market-data.json",
    "simulation-scenario.json",
)
CAMPAIGN_DIGEST_FIELDS = (
    "engine_configuration_sha256",
    "instrument_catalog_sha256",
    "strategy_configuration_sha256",
    "market_data_sha256",
    "simulation_scenario_sha256",
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _reference(number: int, value: bytes) -> contracts.ArtifactReference:
    return contracts.ArtifactReference(
        artifact_id=UUID(
            f"{number}{number}{number}{number}{number}{number}{number}{number}"
            "-1111-4111-8111-111111111111"
        ),
        sha256=hashlib.sha256(value).hexdigest(),
        media_type="application/json",
    )


def _command():
    fixture = backtest.build_canonical_simulation_fixture("event-digest")
    values = (
        fixture.engine_configuration,
        fixture.instrument_catalog,
        fixture.strategy_configuration,
    )
    model = getattr(contracts, "ValidatePaperCompatibility", None)
    assert model is not None, "research-only paper compatibility command is missing"
    command = model(
        command_type="ValidatePaperCompatibility",
        engine_configuration=_reference(1, values[0]),
        instrument_catalog=_reference(2, values[1]),
        strategy_configuration=_reference(3, values[2]),
        strategy_source_sha256="4" * 64,
        scenario_campaign_sha256="5" * 64,
    )
    return command, values


def _load(path: Path, name: str):
    assert path.is_file(), f"{path.name} is missing"
    specification = importlib.util.spec_from_file_location(name, path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def _paper_campaign_evidence() -> tuple[
    Path, Path, str, str, dict[str, object], dict[str, object]
]:
    evidence_root = Path(
        tempfile.mkdtemp(prefix="paper-campaign-evidence-test-", dir="/tmp")
    )
    evidence_root.chmod(0o700)
    campaign = evidence_root / "campaign"
    campaign.mkdir(mode=0o700)
    scenario_records: list[dict[str, object]] = []
    for scenario_id in backtest.SCENARIO_IDS:
        scenario = campaign / scenario_id
        scenario.mkdir(mode=0o700)
        record: dict[str, object] = {"scenario_id": scenario_id}
        for filename, field in zip(
            CAMPAIGN_FILES, CAMPAIGN_DIGEST_FIELDS, strict=True
        ):
            raw = _canonical(
                {"filename": filename, "scenario_id": scenario_id}
            ) + b"\n"
            path = scenario / filename
            path.write_bytes(raw)
            path.chmod(0o400)
            record[field] = hashlib.sha256(raw).hexdigest()
        scenario.chmod(0o500)
        scenario_records.append(record)
    manifest: dict[str, object] = {
        "paper_scenario_id": "long-accounting",
        "scenarios": scenario_records,
        "schema_version": "nautilus-phase4-campaign-v1",
        "strategy_source_sha256": "4" * 64,
    }
    manifest_raw = _canonical(manifest) + b"\n"
    manifest_path = campaign / "campaign-manifest.json"
    manifest_path.write_bytes(manifest_raw)
    manifest_path.chmod(0o400)
    campaign.chmod(0o500)
    campaign_sha256 = hashlib.sha256(manifest_raw).hexdigest()
    candidate_closure_sha256 = "a" * 64
    candidate_manifest_sha256 = "b" * 64
    parity_scenarios: list[dict[str, object]] = []
    for record in scenario_records:
        event_sha256 = hashlib.sha256(
            f"event:{record['scenario_id']}".encode("ascii")
        ).hexdigest()
        result_sha256 = hashlib.sha256(
            f"result:{record['scenario_id']}".encode("ascii")
        ).hexdigest()
        parity_scenarios.append(
            {
                **record,
                "independent_reference_event_sha256": event_sha256,
                "independent_reference_result_sha256": result_sha256,
                "nautilus_event_sha256": event_sha256,
                "nautilus_result_sha256": result_sha256,
                "run_1_event_sha256": event_sha256,
                "run_2_event_sha256": event_sha256,
            }
        )
    parity: dict[str, object] = {
        "candidate_closure_sha256": candidate_closure_sha256,
        "candidate_manifest_schema_version": 6,
        "candidate_manifest_sha256": candidate_manifest_sha256,
        "scenario_campaign_sha256": campaign_sha256,
        "scenarios": parity_scenarios,
        "schema_version": "nautilus-phase4-parity-evidence-v2",
        "status": "passed",
        "strategy_source_sha256": "4" * 64,
    }
    parity_path = evidence_root / "parity-record.json"
    parity_path.write_bytes(_canonical(parity) + b"\n")
    parity_path.chmod(0o400)
    return (
        campaign,
        parity_path,
        candidate_closure_sha256,
        candidate_manifest_sha256,
        manifest,
        parity,
    )


def _unseal_campaign(campaign: Path, parity: Path) -> None:
    parity.chmod(0o600)
    campaign.chmod(0o700)
    for child in campaign.iterdir():
        if child.is_dir():
            child.chmod(0o700)
            for artifact in child.iterdir():
                artifact.chmod(0o600)
        else:
            child.chmod(0o600)
    shutil.rmtree(campaign.parent)


def test_paper_command_is_strict_read_only_and_not_job_parseable() -> None:
    command, _values = _command()

    assert tuple(type(command).model_fields) == (
        "command_type",
        "engine_configuration",
        "instrument_catalog",
        "strategy_configuration",
        "strategy_source_sha256",
        "scenario_campaign_sha256",
    )
    wire = command.model_dump(mode="json")
    forbidden = {
        "host",
        "port",
        "provider",
        "broker",
        "credential",
        "database",
        "client",
        "output_path",
        "persistent_runtime",
    }
    assert forbidden.isdisjoint(wire)
    with pytest.raises(ValidationError):
        type(command).model_validate({**wire, "host": "127.0.0.1"})
    with pytest.raises(ValueError, match="unsupported engine command"):
        contracts.parse_command(wire)
    with pytest.raises(ValidationError):
        contracts.EngineCommandEnvelope.model_validate(
            {
                "message_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "correlation_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                "causation_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
                "engine_run_id": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
                "stream_sequence": 1,
                "event_time": "2026-08-05T12:00:00Z",
                "initialization_time": "2026-08-05T12:00:00Z",
                "schema_version": "1.0.0",
                "producer_identity": "research-harness-1",
                "source_commit": "0" * 40,
                "config_digest": "1" * 64,
                "payload_digest": contracts.payload_digest(command),
                "payload": wire,
            }
        )


def test_paper_command_rejects_duplicate_artifact_authority() -> None:
    command, _values = _command()

    with pytest.raises(ValidationError, match="duplicate artifact"):
        type(command).model_validate(
            {
                **command.model_dump(),
                "instrument_catalog": command.engine_configuration,
            }
        )


def test_paper_launcher_accepts_only_the_exact_canonical_command_and_inputs() -> None:
    launcher = _load(LAUNCHER, "paper_compat_launcher_validation")
    command, values = _command()
    raw = contracts.canonical_json_bytes(command)

    request = launcher.validate_paper_compatibility_request(raw)
    event = launcher.build_paper_compatibility_event(
        request,
        values,
        initialize_and_dispose=lambda *_arguments: None,
        manifest_strategy_sha256=lambda: command.strategy_source_sha256,
    )

    assert event == {
        "compatible": True,
        "engine_configuration_sha256": command.engine_configuration.sha256,
        "event_type": "PaperCompatibilityValidated",
        "instrument_catalog_sha256": command.instrument_catalog.sha256,
        "scenario_campaign_sha256": command.scenario_campaign_sha256,
        "strategy_configuration_sha256": command.strategy_configuration.sha256,
        "strategy_source_sha256": command.strategy_source_sha256,
    }
    assert b"/" not in _canonical(event)
    with pytest.raises(ValueError, match="canonical"):
        launcher.validate_paper_compatibility_request(raw + b"\n")


def test_paper_launcher_rejects_a_campaign_bound_to_other_strategy_bytes() -> None:
    launcher = _load(LAUNCHER, "paper_compat_launcher_strategy_binding")
    command, values = _command()
    request = launcher.validate_paper_compatibility_request(
        contracts.canonical_json_bytes(command)
    )

    with pytest.raises(ValueError, match="strategy source"):
        launcher.build_paper_compatibility_event(
            request,
            values,
            initialize_and_dispose=lambda *_arguments: None,
            manifest_strategy_sha256=lambda: "f" * 64,
        )


def test_paper_launcher_initializes_and_disposes_the_fixed_strategy_once() -> None:
    launcher = _load(LAUNCHER, "paper_compat_launcher_lifecycle")
    calls: list[object] = []

    class Configuration:
        def __init__(self, **values: object) -> None:
            calls.append(("configuration", values))

    class Strategy:
        def __init__(self, configuration: Configuration) -> None:
            calls.append(("strategy", configuration))

        def dispose(self) -> None:
            calls.append("dispose")

    launcher.initialize_and_dispose_paper_strategy(
        engine_configuration={
            "execution_mode": "execution-simulation",
            "run_analysis": False,
            "schema_version": "nautilus-backtest-engine-config-v1",
        },
        instrument_catalog={
            "instrument": {
                "product_type": "crypto_spot",
                "symbol": "BTCUSDT",
                "venue": "BINANCE",
            },
            "timeframe": "1m",
        },
        strategy_configuration={
            "effective_at": "2026-08-05T12:00:00Z",
            "positions": [
                {
                    "instrument": {
                        "product_type": "crypto_spot",
                        "symbol": "BTCUSDT",
                        "venue": "BINANCE",
                    },
                    "target_quantity": "1",
                }
            ],
            "schema_version": "nautilus-execution-target-v1",
        },
        strategy_type=Strategy,
        configuration_type=Configuration,
        instrument_id_factory=lambda value: ("instrument", value),
        bar_type_factory=lambda value: ("bar-type", value),
    )

    assert calls[0][0] == "configuration"
    assert calls[0][1]["target_quantity"] == "1"
    assert calls[0][1]["instrument_id"] == ("instrument", "BTCUSDT.BINANCE")
    assert calls[0][1]["bar_type"] == (
        "bar-type",
        "BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL",
    )
    assert calls[1][0] == "strategy"
    assert calls[2:] == ["dispose"]


def test_paper_launcher_disposes_when_initialization_proof_fails() -> None:
    launcher = _load(LAUNCHER, "paper_compat_launcher_disposal")
    disposed: list[bool] = []

    class Configuration:
        def __init__(self, **_values: object) -> None:
            pass

    class Strategy:
        def __init__(self, _configuration: Configuration) -> None:
            pass

        @property
        def config(self) -> object:
            raise RuntimeError("cannot prove initialization")

        def dispose(self) -> None:
            disposed.append(True)

    with pytest.raises(ValueError, match="initialization"):
        launcher.initialize_and_dispose_paper_strategy(
            engine_configuration={
                "execution_mode": "execution-simulation",
                "run_analysis": False,
                "schema_version": "nautilus-backtest-engine-config-v1",
            },
            instrument_catalog={
                "instrument": {
                    "product_type": "crypto_spot",
                    "symbol": "BTCUSDT",
                    "venue": "BINANCE",
                },
                "timeframe": "1m",
            },
            strategy_configuration={
                "effective_at": "2026-08-05T12:00:00Z",
                "positions": [
                    {
                        "instrument": {
                            "product_type": "crypto_spot",
                            "symbol": "BTCUSDT",
                            "venue": "BINANCE",
                        },
                        "target_quantity": "1",
                    }
                ],
                "schema_version": "nautilus-execution-target-v1",
            },
            strategy_type=Strategy,
            configuration_type=Configuration,
            instrument_id_factory=lambda value: value,
            bar_type_factory=lambda value: value,
        )

    assert disposed == [True]


def test_paper_result_is_digest_only_canonical_and_self_bound() -> None:
    model = getattr(backtest, "PaperCompatibilityResultV1", None)
    assert model is not None, "paper compatibility result contract is missing"

    result = model.create(
        candidate_closure_sha256="1" * 64,
        candidate_manifest_sha256="2" * 64,
        engine_configuration_sha256="3" * 64,
        instrument_catalog_sha256="4" * 64,
        strategy_configuration_sha256="5" * 64,
        strategy_source_sha256="6" * 64,
        scenario_campaign_sha256="7" * 64,
        parity_record_sha256="8" * 64,
        launcher_result_sha256="9" * 64,
    )
    raw = contracts.canonical_json_bytes(result)
    document = json.loads(raw)

    assert document["schema_version"] == "nautilus-paper-compatibility-result-v1"
    assert document["compatible"] is True
    assert set(document) == {
        "candidate_closure_sha256",
        "candidate_manifest_sha256",
        "compatible",
        "engine_configuration_sha256",
        "instrument_catalog_sha256",
        "launcher_result_sha256",
        "parity_record_sha256",
        "result_sha256",
        "scenario_campaign_sha256",
        "schema_version",
        "strategy_configuration_sha256",
        "strategy_source_sha256",
    }
    domain = {name: value for name, value in document.items() if name != "result_sha256"}
    assert document["result_sha256"] == hashlib.sha256(_canonical(domain)).hexdigest()
    assert b"/" not in raw


def test_root_harness_cli_has_one_fixed_record_destination() -> None:
    harness = _load(HARNESS, "paper_compat_harness_parser")
    parser = harness._parser()
    arguments = parser.parse_args(
        [
            "--candidate-closure",
            "/packet/candidate",
            "--artifact-directory",
            "/packet/artifacts",
            "--sandbox",
            "/usr/bin/bwrap",
            "--campaign-directory",
            "/packet/campaign",
            "--parity-record",
            "/packet/parity.json",
            "--transport-root",
            "/packet/transport",
        ]
    )

    assert not hasattr(arguments, "record")
    assert harness.paper_record_path(arguments.transport_root) == (
        arguments.transport_root / "paper-compatibility-result.json"
    )


def test_root_harness_owns_exactly_one_prepare_consume_and_capture() -> None:
    harness = _load(HARNESS, "paper_compat_harness_lifecycle")
    command, _values = _command()
    event = {
        "compatible": True,
        "engine_configuration_sha256": command.engine_configuration.sha256,
        "event_type": "PaperCompatibilityValidated",
        "instrument_catalog_sha256": command.instrument_catalog.sha256,
        "scenario_campaign_sha256": command.scenario_campaign_sha256,
        "strategy_configuration_sha256": command.strategy_configuration.sha256,
        "strategy_source_sha256": command.strategy_source_sha256,
    }
    calls: list[object] = []

    class Provider:
        def prepare(self, observed: object) -> object:
            calls.append(("prepare", observed))
            return "prepared"

    def consume(prepared: object) -> object:
        calls.append(("consume", prepared))
        return "built"

    def capture(built: object, *, popen_factory: object) -> object:
        calls.append(("capture", built, popen_factory))
        return SimpleNamespace(
            stdout=_canonical(event) + b"\n",
            stderr=b"",
            returncode=0,
        )

    result = harness.capture_paper_compatibility(
        provider=Provider(),
        command=command,
        candidate_closure_sha256="a" * 64,
        candidate_manifest_sha256="b" * 64,
        parity_record_sha256="c" * 64,
        popen_factory="inert-popen",
        consume=consume,
        capture=capture,
        cleanup=lambda: calls.append("cleanup"),
    )

    assert calls == [
        ("prepare", command),
        ("consume", "prepared"),
        ("capture", "built", "inert-popen"),
        "cleanup",
    ]
    assert result.compatible is True
    assert result.launcher_result_sha256 == hashlib.sha256(_canonical(event)).hexdigest()


def test_root_harness_publishes_only_one_sealed_fixed_result() -> None:
    harness = _load(HARNESS, "paper_compat_harness_publication")
    root = Path(tempfile.mkdtemp(prefix="paper-result-test-", dir="/tmp"))
    root.chmod(0o700)
    transport = root / "transport"
    transport.mkdir(mode=0o700)
    result_model = getattr(backtest, "PaperCompatibilityResultV1")
    result = result_model.create(
        candidate_closure_sha256="1" * 64,
        candidate_manifest_sha256="2" * 64,
        engine_configuration_sha256="3" * 64,
        instrument_catalog_sha256="4" * 64,
        strategy_configuration_sha256="5" * 64,
        strategy_source_sha256="6" * 64,
        scenario_campaign_sha256="7" * 64,
        parity_record_sha256="8" * 64,
        launcher_result_sha256="9" * 64,
    )

    try:
        path = harness.publish_paper_compatibility_result(transport, result)

        assert path == transport / "paper-compatibility-result.json"
        assert path.read_bytes() == contracts.canonical_json_bytes(result) + b"\n"
        assert path.stat().st_mode & 0o777 == 0o400
        with pytest.raises(harness.PaperCompatibilityVerificationError, match="exists"):
            harness.publish_paper_compatibility_result(transport, result)
    finally:
        for candidate in transport.iterdir():
            candidate.chmod(0o600)
        shutil.rmtree(root)


def test_root_harness_requires_exact_eight_scenario_parity_and_selects_long_accounting() -> None:
    harness = _load(HARNESS, "paper_compat_harness_campaign")
    campaign, parity_path, closure_sha, manifest_sha, manifest, _parity = (
        _paper_campaign_evidence()
    )
    try:
        command, bindings, parity_sha = harness._campaign_authority(
            campaign,
            parity_path,
            candidate_closure_sha256=closure_sha,
            candidate_manifest_sha256=manifest_sha,
        )

        assert tuple(
            record["scenario_id"] for record in manifest["scenarios"]
        ) == backtest.SCENARIO_IDS
        assert len(bindings) == 3
        assert all(
            binding.source.parent.name == "long-accounting" for binding in bindings
        )
        assert tuple(binding.source.name for binding in bindings) == CAMPAIGN_FILES[:3]
        assert command.strategy_source_sha256 == "4" * 64
        assert parity_sha == hashlib.sha256(parity_path.read_bytes()).hexdigest()
    finally:
        _unseal_campaign(campaign, parity_path)


@pytest.mark.parametrize(
    "mutation",
    (
        "pass-shaped",
        "out-of-order",
        "campaign-identity",
        "candidate-identity",
        "event-mismatch",
        "result-mismatch",
    ),
)
def test_root_harness_rejects_fabricated_or_inconsistent_parity_evidence(
    mutation: str,
) -> None:
    harness = _load(HARNESS, f"paper_compat_harness_bad_parity_{mutation}")
    campaign, parity_path, closure_sha, manifest_sha, _manifest, parity = (
        _paper_campaign_evidence()
    )
    try:
        if mutation == "pass-shaped":
            parity = {
                "scenario_campaign_sha256": parity["scenario_campaign_sha256"],
                "status": "passed",
            }
        elif mutation == "out-of-order":
            parity["scenarios"] = list(reversed(parity["scenarios"]))
        elif mutation == "campaign-identity":
            parity["scenarios"][0]["market_data_sha256"] = "0" * 64
        elif mutation == "candidate-identity":
            parity["candidate_closure_sha256"] = "0" * 64
        elif mutation == "event-mismatch":
            parity["scenarios"][0]["run_2_event_sha256"] = "0" * 64
        else:
            parity["scenarios"][0]["nautilus_result_sha256"] = "0" * 64
        parity_path.chmod(0o600)
        parity_path.write_bytes(_canonical(parity) + b"\n")
        parity_path.chmod(0o400)

        with pytest.raises(harness.PaperCompatibilityVerificationError):
            harness._campaign_authority(
                campaign,
                parity_path,
                candidate_closure_sha256=closure_sha,
                candidate_manifest_sha256=manifest_sha,
            )
    finally:
        _unseal_campaign(campaign, parity_path)


@pytest.mark.parametrize(
    "mutation", ("missing", "extra", "duplicate", "out-of-order")
)
def test_root_harness_rejects_non_exact_campaign_scenario_inventory(
    mutation: str,
) -> None:
    harness = _load(HARNESS, f"paper_compat_harness_bad_campaign_{mutation}")
    campaign, parity_path, closure_sha, manifest_sha, manifest, _parity = (
        _paper_campaign_evidence()
    )
    scenarios = manifest["scenarios"]
    assert isinstance(scenarios, list)
    if mutation == "missing":
        scenarios.pop()
    elif mutation == "extra":
        scenarios.append(dict(scenarios[-1]))
    elif mutation == "duplicate":
        scenarios[1] = dict(scenarios[0])
    else:
        scenarios[0], scenarios[1] = scenarios[1], scenarios[0]
    manifest_path = campaign / "campaign-manifest.json"
    manifest_path.chmod(0o600)
    manifest_path.write_bytes(_canonical(manifest) + b"\n")
    manifest_path.chmod(0o400)
    try:
        with pytest.raises(harness.PaperCompatibilityVerificationError):
            harness._campaign_authority(
                campaign,
                parity_path,
                candidate_closure_sha256=closure_sha,
                candidate_manifest_sha256=manifest_sha,
            )
    finally:
        _unseal_campaign(campaign, parity_path)


def test_sealed_evidence_reader_uses_descriptor_snapshot_not_path_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _load(HARNESS, "paper_compat_harness_descriptor_snapshot")
    root = Path(tempfile.mkdtemp(prefix="paper-sealed-reader-test-", dir="/tmp"))
    root.chmod(0o700)
    evidence = root / "evidence.json"
    raw = b'{"schema_version":"evidence"}\n'
    evidence.write_bytes(raw)
    evidence.chmod(0o400)
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda _path: (_ for _ in ()).throw(
            AssertionError("path-based evidence read is forbidden")
        ),
    )
    try:
        assert harness._sealed_bytes(evidence, label="test evidence") == raw
    finally:
        evidence.chmod(0o600)
        shutil.rmtree(root)


def test_capture_cleans_transport_after_consume_failure_without_masking_primary() -> None:
    harness = _load(HARNESS, "paper_compat_harness_cleanup")
    command, _values = _command()
    calls: list[str] = []

    class Provider:
        def prepare(self, _command: object) -> object:
            calls.append("prepare")
            return object()

    def fail_consume(_prepared: object) -> object:
        calls.append("consume")
        raise EngineSpawnError("ENGINE_SPAWN_BLOCKED", "expected failure")

    def fail_cleanup() -> None:
        calls.append("cleanup")
        raise harness.PaperCompatibilityVerificationError("cleanup also failed")

    with pytest.raises(
        harness.PaperCompatibilityVerificationError, match="expected failure"
    ) as observed:
        harness.capture_paper_compatibility(
            provider=Provider(),
            command=command,
            candidate_closure_sha256="a" * 64,
            candidate_manifest_sha256="b" * 64,
            parity_record_sha256="c" * 64,
            consume=fail_consume,
            cleanup=fail_cleanup,
        )

    assert calls == ["prepare", "consume", "cleanup"]
    assert any("cleanup" in note for note in observed.value.__notes__)


def test_capture_cleans_transport_after_subprocess_failure() -> None:
    harness = _load(HARNESS, "paper_compat_harness_process_cleanup")
    command, _values = _command()
    calls: list[str] = []

    class Provider:
        def prepare(self, _command: object) -> object:
            calls.append("prepare")
            return object()

    def consume(_prepared: object) -> object:
        calls.append("consume")
        return object()

    def capture(_built: object, *, popen_factory: object) -> object:
        calls.append("capture")
        raise subprocess.TimeoutExpired("paper", 120)

    with pytest.raises(harness.PaperCompatibilityVerificationError):
        harness.capture_paper_compatibility(
            provider=Provider(),
            command=command,
            candidate_closure_sha256="a" * 64,
            candidate_manifest_sha256="b" * 64,
            parity_record_sha256="c" * 64,
            consume=consume,
            capture=capture,
            cleanup=lambda: calls.append("cleanup"),
        )

    assert calls == ["prepare", "consume", "capture", "cleanup"]


@pytest.mark.parametrize(
    "failure",
    (
        EngineSpawnError("ENGINE_CLOSURE_INVALID", "attestation rejected"),
        OSError("OS snapshot rejected"),
        subprocess.TimeoutExpired("paper", 120),
        ValueError("model rejected"),
    ),
)
def test_main_normalizes_expected_attestation_failure_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: BaseException,
) -> None:
    harness = _load(HARNESS, "paper_compat_harness_main_failure")
    monkeypatch.setattr(
        harness,
        "verify_paper_compatibility",
        lambda **_arguments: (_ for _ in ()).throw(failure),
    )
    monkeypatch.setattr(
        harness,
        "_parser",
        lambda: SimpleNamespace(
            parse_args=lambda: SimpleNamespace(
                candidate_closure=Path("/candidate"),
                artifact_directory=Path("/artifacts"),
                sandbox=Path("/sandbox"),
                campaign_directory=Path("/campaign"),
                parity_record=Path("/parity"),
                transport_root=Path("/transport"),
            )
        ),
    )

    assert harness.main() == 1
    captured = capsys.readouterr()
    assert "paper compatibility failed:" in captured.err
    assert "Traceback" not in captured.err


def test_failed_result_write_removes_partial_final_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _load(HARNESS, "paper_compat_harness_short_write")
    result = backtest.PaperCompatibilityResultV1.create(
        candidate_closure_sha256="1" * 64,
        candidate_manifest_sha256="2" * 64,
        engine_configuration_sha256="3" * 64,
        instrument_catalog_sha256="4" * 64,
        strategy_configuration_sha256="5" * 64,
        strategy_source_sha256="6" * 64,
        scenario_campaign_sha256="7" * 64,
        parity_record_sha256="8" * 64,
        launcher_result_sha256="9" * 64,
    )
    root = Path(tempfile.mkdtemp(prefix="paper-short-write-test-", dir="/tmp"))
    root.chmod(0o700)
    transport = root / "transport"
    transport.mkdir(mode=0o700)
    monkeypatch.setattr(harness.os, "write", lambda _descriptor, _raw: 0)

    try:
        with pytest.raises(
            harness.PaperCompatibilityVerificationError, match="published"
        ):
            harness.publish_paper_compatibility_result(transport, result)

        assert not harness.paper_record_path(transport).exists()
    finally:
        for child in transport.iterdir():
            child.chmod(0o600)
        shutil.rmtree(root)
