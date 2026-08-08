from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from pydantic import ValidationError

import packages.engine_contracts as contracts
import packages.nautilus_backtest as backtest


ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "engines/nautilus/launcher/nautilus_paper_compat.py"
HARNESS = ROOT / "scripts/verify_nautilus_paper_compatibility.py"


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
    )

    assert calls == [
        ("prepare", command),
        ("consume", "prepared"),
        ("capture", "built", "inert-popen"),
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
