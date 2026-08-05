"""Black-box regression tests for the isolated engine fixture CLI."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid5

import pytest

from packages import engine_contracts as contracts


CLI = Path(sys.executable).with_name("trading-agent-nautilus")


def _run(*arguments: str, environment: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    assert CLI.is_file(), "the project console script must be registered"
    return subprocess.run(
        [str(CLI), *arguments],
        capture_output=True,
        check=False,
        text=True,
        env=environment,
    )


def _command(command_type: str) -> dict[str, object]:
    artifact = {
        "artifact_id": "11111111-1111-1111-1111-111111111111",
        "sha256": "a" * 64,
        "media_type": "application/json",
    }
    if command_type == "RunBacktest":
        return {
            "command_type": command_type,
            "engine_configuration": artifact,
            "instrument_catalog": artifact,
            "strategy_configuration": artifact,
            "market_data": artifact,
            "start_time": "2026-08-04T18:00:00Z",
            "end_time": "2026-08-04T18:30:00Z",
        }
    if command_type == "StartPaperEngine":
        return {
            "command_type": command_type,
            "engine_configuration": artifact,
            "instrument_catalog": artifact,
            "strategy_configuration": artifact,
        }
    return {"command_type": command_type}


def _request(command_type: str = "RunBacktest") -> dict[str, object]:
    payload = _command(command_type)
    return {
        "message_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "correlation_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        "causation_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
        "engine_run_id": "dddddddd-dddd-dddd-dddd-dddddddddddd",
        "stream_sequence": 7,
        "event_time": "2026-08-04T18:30:00Z",
        "initialization_time": "2026-08-04T18:00:00Z",
        "schema_version": "1.0.0",
        "producer_identity": "trading-agent-control-plane",
        "source_commit": "e" * 40,
        "config_digest": "f" * 64,
        "payload_digest": contracts.payload_digest(payload),
        "payload": payload,
    }


def _request_files(
    tmp_path: Path, request: dict[str, object], *, sidecar: str | None = None
) -> tuple[Path, Path]:
    raw_request = json.dumps(request, indent=1, ensure_ascii=False).encode("utf-8")
    request_path = tmp_path / "request.json"
    request_path.write_bytes(raw_request)
    sidecar_path = tmp_path / "request.sha256"
    sidecar_path.write_text(
        sidecar if sidecar is not None else hashlib.sha256(raw_request).hexdigest(),
        encoding="ascii",
    )
    return request_path, sidecar_path


def _canonical_output(completed: subprocess.CompletedProcess[str]) -> dict[str, object]:
    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    result = json.loads(completed.stdout)
    assert completed.stdout == contracts.canonical_json(result) + "\n"
    return result


def test_capabilities_are_closed_and_canonical() -> None:
    result = _canonical_output(_run("capabilities"))

    assert result == {
        "schema_version": "1.0.0",
        "engine_id": "trading-agent-nautilus-fixture-v1",
        "engine_version": "fixture-1.0.0",
        "supported_commands": list(contracts.COMMAND_TYPES),
        "supported_event_families": [family.value for family in contracts.EventFamily],
        "supported_modes": ["BACKTEST", "PAPER"],
    }
    assert "LIVE" not in result["supported_modes"]


def test_validate_request_requires_hash_before_parsing_and_emits_canonical_envelope(
    tmp_path: Path,
) -> None:
    request = _request()
    request_path, sidecar_path = _request_files(tmp_path, request)

    validated = _canonical_output(
        _run("validate-request", str(request_path), str(sidecar_path))
    )

    assert validated == contracts.EngineCommandEnvelope.model_validate_json(
        json.dumps(request)
    ).model_dump(mode="json")

    bad_hash = _run("validate-request", str(request_path), str(sidecar_path) + ".missing")
    assert bad_hash.returncode != 0
    assert bad_hash.stdout == ""
    assert bad_hash.stderr


@pytest.mark.parametrize(
    ("request_update", "sidecar"),
    [
        ({}, "0" * 64),
        ({"stream_sequence": 0}, None),
    ],
)
def test_validate_request_rejects_bad_hash_or_invalid_envelope_without_result(
    tmp_path: Path, request_update: dict[str, object], sidecar: str | None
) -> None:
    request = _request()
    request.update(request_update)
    request_path, sidecar_path = _request_files(tmp_path, request, sidecar=sidecar)

    completed = _run("validate-request", str(request_path), str(sidecar_path))

    assert completed.returncode != 0
    assert completed.stdout == ""
    assert completed.stderr


@pytest.mark.parametrize(
    ("subcommand", "command_type", "event_type"),
    [
        ("backtest-fixture", "RunBacktest", "BacktestFixtureCompleted"),
        ("paper-fixture", "StartPaperEngine", "PaperFixtureReady"),
    ],
)
def test_fixture_commands_emit_deterministic_lifecycle_events(
    tmp_path: Path, subcommand: str, command_type: str, event_type: str
) -> None:
    request = _request(command_type)
    request_path, sidecar_path = _request_files(tmp_path, request)

    first = _canonical_output(_run(subcommand, str(request_path), str(sidecar_path)))
    second = _canonical_output(_run(subcommand, str(request_path), str(sidecar_path)))

    assert first == second
    assert first == {
        **{
            key: value
            for key, value in request.items()
            if key
            in {
                "correlation_id",
                "engine_run_id",
                "event_time",
                "initialization_time",
                "schema_version",
                "producer_identity",
                "source_commit",
                "config_digest",
            }
        },
        "message_id": str(uuid5(UUID(str(request["message_id"])), event_type)),
        "causation_id": request["message_id"],
        "stream_sequence": request["stream_sequence"] + 1,
        "payload": {
            "event_type": event_type,
            "family": "ENGINE_LIFECYCLE",
            "attributes": [],
        },
        "payload_digest": contracts.payload_digest(
            {
                "event_type": event_type,
                "family": "ENGINE_LIFECYCLE",
                "attributes": [],
            }
        ),
    }
    assert contracts.EngineEventEnvelope.model_validate_json(json.dumps(first))


@pytest.mark.parametrize(
    ("subcommand", "command_type"),
    [
        ("backtest-fixture", "StartPaperEngine"),
        ("paper-fixture", "RunBacktest"),
    ],
)
def test_fixture_commands_reject_a_different_command_type(
    tmp_path: Path, subcommand: str, command_type: str
) -> None:
    request_path, sidecar_path = _request_files(tmp_path, _request(command_type))

    completed = _run(subcommand, str(request_path), str(sidecar_path))

    assert completed.returncode != 0
    assert completed.stdout == ""
    assert completed.stderr


def test_capabilities_path_does_not_open_a_network_connection(tmp_path: Path) -> None:
    (tmp_path / "sitecustomize.py").write_text(
        "import socket\n"
        "def blocked(*args, **kwargs):\n"
        "    raise AssertionError('network access is forbidden')\n"
        "socket.socket = blocked\n"
        "socket.create_connection = blocked\n",
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(tmp_path)

    _canonical_output(_run("capabilities", environment=environment))


def test_cli_source_has_no_nautilus_or_provider_import_leakage() -> None:
    source_root = Path(__file__).parents[2] / "packages" / "nautilus_engine_cli"
    source = "\n".join(path.read_text(encoding="utf-8") for path in source_root.glob("*.py"))

    assert "nautilustrader" not in source.casefold()
    assert "nautilus_trader" not in source.casefold()
    assert "provider" not in source.casefold()
