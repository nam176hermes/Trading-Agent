from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from importlib import import_module
from uuid import uuid4

import pytest
from pydantic import ValidationError


def capabilities_values() -> dict[str, object]:
    contracts = import_module("packages.engine_contracts")
    return {
        "schema_version": "1.0.0",
        "engine_id": "engine-fixture",
        "engine_version": "1.2.3",
        "supported_commands": contracts.COMMAND_TYPES,
        "supported_event_families": tuple(contracts.EventFamily),
        "supported_modes": (
            contracts.EngineMode.BACKTEST,
            contracts.EngineMode.PAPER,
        ),
    }


def test_capabilities_are_closed_versioned_and_have_no_live_mode() -> None:
    contracts = import_module("packages.engine_contracts")
    capabilities = contracts.EngineCapabilities.model_validate(capabilities_values())

    assert tuple(mode.value for mode in contracts.EngineMode) == ("BACKTEST", "PAPER")
    assert all("live" not in mode.value.casefold() for mode in capabilities.supported_modes)
    assert contracts.EngineCapabilities.model_validate_json(
        capabilities.model_dump_json()
    ) == capabilities
    with pytest.raises(ValidationError, match="1.0.0"):
        contracts.EngineCapabilities.model_validate(
            {**capabilities_values(), "schema_version": "2.0.0"}
        )
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        contracts.EngineCapabilities.model_validate(
            {**capabilities_values(), "provider": "private"}
        )


@pytest.mark.parametrize(
    "field",
    ["supported_commands", "supported_event_families", "supported_modes"],
)
def test_capabilities_reject_duplicate_claims(field: str) -> None:
    contracts = import_module("packages.engine_contracts")
    values = capabilities_values()
    items = values[field]
    assert isinstance(items, tuple)
    values[field] = items + (items[0],)

    with pytest.raises(ValidationError, match="duplicate"):
        contracts.EngineCapabilities.model_validate(values)


def manifest_values() -> dict[str, object]:
    contracts = import_module("packages.engine_contracts")
    started_at = datetime(2026, 8, 4, 18, 30, tzinfo=UTC)
    return {
        "schema_version": "1.0.0",
        "engine_run_id": uuid4(),
        "command_type": "RunBacktest",
        "started_at": started_at,
        "completed_at": started_at + timedelta(seconds=1),
        "producer_identity": "engine-fixture",
        "source_commit": "a" * 40,
        "config_digest": "b" * 64,
        "artifacts": (
            contracts.ManifestArtifact(
                name=contracts.TransportArtifact.REQUEST,
                sha256="c" * 64,
                size_bytes=128,
            ),
            contracts.ManifestArtifact(
                name=contracts.TransportArtifact.RESULT,
                sha256="d" * 64,
                size_bytes=256,
            ),
        ),
    }


def test_run_manifest_is_strict_immutable_and_canonical() -> None:
    contracts = import_module("packages.engine_contracts")
    manifest = contracts.EngineRunManifest.model_validate(manifest_values())

    serialized = json.loads(manifest.model_dump_json())
    assert serialized["started_at"] == "2026-08-04T18:30:00Z"
    assert serialized["completed_at"] == "2026-08-04T18:30:01Z"
    assert contracts.EngineRunManifest.model_validate_json(
        manifest.model_dump_json()
    ) == manifest
    with pytest.raises(ValidationError, match="frozen"):
        manifest.command_type = "CancelBacktest"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        contracts.EngineRunManifest.model_validate(
            {**manifest_values(), "stdout": "raw output"}
        )


def test_run_manifest_rejects_duplicate_artifact_name_and_invalid_timeline() -> None:
    contracts = import_module("packages.engine_contracts")
    values = manifest_values()
    artifacts = values["artifacts"]
    assert isinstance(artifacts, tuple)
    values["artifacts"] = artifacts + (artifacts[0],)

    with pytest.raises(ValidationError, match="duplicate manifest artifact"):
        contracts.EngineRunManifest.model_validate(values)

    timeline = manifest_values()
    timeline["completed_at"] = timeline["started_at"] - timedelta(  # type: ignore[operator]
        microseconds=1
    )
    with pytest.raises(ValidationError, match="completed_at"):
        contracts.EngineRunManifest.model_validate(timeline)


def test_manifest_transport_names_are_closed_and_provider_neutral() -> None:
    contracts = import_module("packages.engine_contracts")

    assert tuple(item.value for item in contracts.TransportArtifact) == (
        "request.json",
        "request.sha256",
        "events.jsonl",
        "result.json",
        "manifest.json",
        "stdout.log",
        "stderr.log",
    )
    schemas = json.dumps(
        {
            "capabilities": contracts.EngineCapabilities.model_json_schema(),
            "manifest": contracts.EngineRunManifest.model_json_schema(),
        },
        sort_keys=True,
    ).casefold()
    assert "nautilus" not in schemas
    assert "provider_payload" not in schemas
