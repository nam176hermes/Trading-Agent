from __future__ import annotations

import hashlib
import importlib.util
import json
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


def test_launcher_reads_only_the_four_hash_bound_input_artifacts(
    launcher_module, tmp_path: Path
) -> None:
    artifact_values = (
        ("engine_configuration", b'{"mode":"zero-order"}\n', "application/json"),
        ("instrument_catalog", b'{"schema_version":"market-dataset-manifest-v1"}\n', "application/json"),
        ("strategy_configuration", b'{"positions":[]}\n', "application/json"),
        ("market_data", b'{"close":"1"}\n', "application/jsonl"),
    )
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    references: list[ArtifactReference] = []
    for index, (name, value, media_type) in enumerate(artifact_values, start=1):
        digest = hashlib.sha256(value).hexdigest()
        extension = ".jsonl" if media_type == "application/jsonl" else ".json"
        (artifact_root / f"{name}-{digest}{extension}").write_bytes(value)
        references.append(
            ArtifactReference(
                artifact_id=UUID(f"{index}{index}{index}{index}{index}{index}{index}{index}-1111-4111-8111-111111111111"),
                sha256=digest,
                media_type=media_type,
            )
        )
    command = RunBacktest(
        command_type="RunBacktest",
        engine_configuration=references[0],
        instrument_catalog=references[1],
        strategy_configuration=references[2],
        market_data=references[3],
        start_time=datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
        end_time=datetime(2026, 8, 5, 12, 30, tzinfo=UTC),
    )
    envelope = _request().model_copy(
        update={
            "config_digest": payload_digest(
                {
                    "engine_configuration": command.engine_configuration,
                    "instrument_catalog": command.instrument_catalog,
                    "strategy_configuration": command.strategy_configuration,
                }
            ),
            "payload_digest": payload_digest(command),
            "payload": command,
        }
    )

    loaded = launcher_module.validated_input_artifacts(
        envelope.model_dump(mode="json"), artifact_root
    )

    assert loaded == tuple(value for _name, value, _media_type in artifact_values)


def test_launcher_accepts_only_a_zero_order_04a_catalog_and_04b_target(
    launcher_module,
) -> None:
    configuration = json.dumps(
        {
            "execution_mode": "zero-order",
            "run_analysis": False,
            "schema_version": "nautilus-backtest-engine-config-v1",
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    market_data = (
        b'{"close":"101.00","high":"102.00","low":"99.00","open":"100.00",'
        b'"open_time":"2026-08-05T12:00:00Z","volume":"12.500000"}\n'
    )
    catalog = json.dumps(
        {
            "canonical_rows_sha256": hashlib.sha256(
                b"[" + market_data[:-1] + b"]"
            ).hexdigest(),
            "content_digest": "a" * 64,
            "continuity": {"duplicate_report": [], "gap_report": [], "timeframe": "1m"},
            "fetched_at": "2026-08-05T12:01:00Z",
            "first_event_at": "2026-08-05T12:00:00Z",
            "importer_version": "fixture-catalog-v1",
            "instrument": {"product_type": "crypto_spot", "symbol": "BTCUSDT", "venue": "BINANCE"},
            "known_at": "2026-08-05T12:01:00Z",
            "last_event_at": "2026-08-05T12:00:00Z",
            "normalization_version": "market-normalization-v1",
            "observed_at": "2026-08-05T12:01:00Z",
            "parquet_sha256": "b" * 64,
            "provider": "deterministic-fixture-v1",
            "provenance_schema_version": "market-data-v1",
            "raw_evidence_sha256": "c" * 64,
            "row_count": 1,
            "schema_version": "market-dataset-manifest-v1",
            "snapshot_schema_version": "market-snapshot-v1",
            "timeframe": "1m",
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    target = json.dumps(
        {
            "effective_at": "2026-08-05T12:00:00Z",
            "positions": [],
            "schema_version": "1.0.0",
            "source_signal_ids": ["22222222-2222-4222-8222-222222222222"],
            "target_id": "11111111-1111-4111-8111-111111111111",
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    launcher_module.validate_zero_order_fixture_inputs(
        (configuration, catalog, target, market_data)
    )

    with pytest.raises(ValueError, match="zero target"):
        launcher_module.validate_zero_order_fixture_inputs(
            (configuration, catalog, target.replace(b"[]", b'[{}]'), market_data)
        )
    with pytest.raises(ValueError, match="strategy target"):
        launcher_module.validate_zero_order_fixture_inputs(
            (
                configuration,
                catalog,
                target.replace(b"2026-08-05T12:00:00Z", b"not-a-timestamp-Z"),
                market_data,
            )
        )
    with pytest.raises(ValueError, match="canonical rows"):
        launcher_module.validate_zero_order_fixture_inputs(
            (
                configuration,
                catalog,
                target,
                market_data.replace(b'"101.00"', b'"999.00"'),
            )
        )
