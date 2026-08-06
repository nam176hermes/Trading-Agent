from __future__ import annotations

import hashlib
import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from packages.engine_contracts import (
    ArtifactReference,
    CURRENT_SCHEMA_VERSION,
    EngineCommandEnvelope,
    RunBacktest,
    canonical_json_bytes,
    payload_digest,
)


LAUNCHER = Path("engines/nautilus/launcher/nautilus_backtest.py")


@pytest.fixture(scope="module")
def launcher_module():
    spec = importlib.util.spec_from_file_location("nautilus_backtest_launcher", LAUNCHER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _request() -> EngineCommandEnvelope:
    configuration = ArtifactReference(
        artifact_id=UUID("11111111-1111-4111-8111-111111111111"),
        sha256="1" * 64,
        media_type="application/json",
    )
    command = RunBacktest(
        command_type="RunBacktest",
        engine_configuration=configuration,
        instrument_catalog=ArtifactReference(
            artifact_id=UUID("22222222-2222-4222-8222-222222222222"),
            sha256="2" * 64,
            media_type="application/json",
        ),
        strategy_configuration=ArtifactReference(
            artifact_id=UUID("33333333-3333-4333-8333-333333333333"),
            sha256="3" * 64,
            media_type="application/json",
        ),
        market_data=ArtifactReference(
            artifact_id=UUID("44444444-4444-4444-8444-444444444444"),
            sha256="4" * 64,
            media_type="application/jsonl",
        ),
        start_time=datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
        end_time=datetime(2026, 8, 5, 12, 30, tzinfo=UTC),
    )
    return EngineCommandEnvelope(
        message_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        correlation_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        causation_id=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
        engine_run_id=UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
        stream_sequence=1,
        event_time=datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
        initialization_time=datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
        schema_version=CURRENT_SCHEMA_VERSION,
        producer_identity="worker-authority-1",
        source_commit="0123456789abcdef0123456789abcdef01234567",
        config_digest=payload_digest(
            {
                "engine_configuration": command.engine_configuration,
                "instrument_catalog": command.instrument_catalog,
                "strategy_configuration": command.strategy_configuration,
            }
        ),
        payload_digest=payload_digest(command),
        payload=command,
    )


def test_launcher_accepts_only_hash_bound_canonical_run_backtest(
    launcher_module, tmp_path: Path
) -> None:
    request = canonical_json_bytes(_request())
    request_path = tmp_path / "request.json"
    sidecar_path = tmp_path / "request.sha256"
    request_path.write_bytes(request)
    sidecar_path.write_text(hashlib.sha256(request).hexdigest() + "\n", encoding="ascii")

    accepted = launcher_module.validated_request(request_path, sidecar_path)

    assert accepted["payload"]["command_type"] == "RunBacktest"


def test_launcher_rejects_request_digest_drift(launcher_module, tmp_path: Path) -> None:
    request = canonical_json_bytes(_request())
    request_path = tmp_path / "request.json"
    sidecar_path = tmp_path / "request.sha256"
    request_path.write_bytes(request)
    sidecar_path.write_text("0" * 64 + "\n", encoding="ascii")

    with pytest.raises(ValueError, match="digest"):
        launcher_module.validated_request(request_path, sidecar_path)
