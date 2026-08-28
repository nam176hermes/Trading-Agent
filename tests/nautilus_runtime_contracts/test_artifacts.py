from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from packages.engine_contracts import canonical_json_bytes
from packages.nautilus_runtime_contracts import (
    P1EngineConfigurationV1,
    P1InstrumentCatalogV1,
    P1MarketDataManifestV1,
    P1TargetScheduleV1,
    parse_canonical_artifact,
)


FIXTURES = Path(__file__).parents[1] / "fixtures" / "p1_nautilus" / "contracts"


def _documents() -> dict[str, tuple[type, dict[str, object]]]:
    return {
        "engine-configuration.json": (
            P1EngineConfigurationV1,
            {
                "account_type": "CASH",
                "allow_leverage": False,
                "allow_short": False,
                "bar_execution": False,
                "fee_model": "fixed-rate",
                "fee_rate": "0.001",
                "fill_model": "deterministic",
                "load_state": False,
                "logging_bypass": True,
                "network_access": False,
                "oms_type": "NETTING",
                "run_analysis": False,
                "save_state": False,
                "schema_version": "nautilus-p1-engine-configuration-v1",
                "starting_balance": "1000000",
                "starting_currency": "USDT",
                "venue": "BINANCE",
            },
        ),
        "instrument-catalog.json": (
            P1InstrumentCatalogV1,
            {
                "base_currency": "BTC",
                "instrument_id": "BTCUSDT.BINANCE",
                "min_notional": "10",
                "min_quantity": "0.000001",
                "price_precision": 2,
                "product_type": "crypto_spot",
                "provenance_sha256": "a" * 64,
                "quote_currency": "USDT",
                "schema_version": "nautilus-p1-instrument-catalog-v1",
                "size_precision": 6,
                "step_size": "0.000001",
                "symbol": "BTCUSDT",
                "tick_size": "0.01",
                "venue": "BINANCE",
            },
        ),
        "target-schedule.json": (
            P1TargetScheduleV1,
            {
                "schema_version": "nautilus-p1-target-schedule-v1",
                "targets": [
                    {
                        "effective_at": "2026-08-05T12:00:00Z",
                        "positions": [
                            {
                                "instrument": {
                                    "product_type": "crypto_spot",
                                    "symbol": "BTCUSDT",
                                    "venue": "BINANCE",
                                },
                                "target_weight": "1",
                            }
                        ],
                        "schema_version": "1.0.0",
                        "source_signal_ids": ["22222222-2222-4222-8222-222222222222"],
                        "target_id": "11111111-1111-4111-8111-111111111111",
                    },
                    {
                        "effective_at": "2026-08-05T12:01:00Z",
                        "positions": [
                            {
                                "instrument": {
                                    "product_type": "crypto_spot",
                                    "symbol": "BTCUSDT",
                                    "venue": "BINANCE",
                                },
                                "target_weight": "0",
                            }
                        ],
                        "schema_version": "1.0.0",
                        "source_signal_ids": ["33333333-3333-4333-8333-333333333333"],
                        "target_id": "44444444-4444-4444-8444-444444444444",
                    },
                ],
            },
        ),
        "market-data-manifest.json": (
            P1MarketDataManifestV1,
            {
                "catalog_sha256": "b" * 64,
                "data_sha256": "c" * 64,
                "first_timestamp": "2026-08-05T12:00:00Z",
                "last_timestamp": "2026-08-05T12:01:00Z",
                "media_type": "application/jsonl",
                "normalization_version": "market-normalization-v1",
                "quote_bar_pair_policy": "quote-then-bar",
                "row_count": 2,
                "schema_version": "nautilus-p1-market-data-manifest-v1",
                "timeframe": "1m",
                "timestamp_policy": "close",
            },
        ),
    }


def test_golden_contracts_round_trip_byte_identically() -> None:
    for filename, (model, document) in _documents().items():
        raw = (FIXTURES / filename).read_bytes()
        assert raw == canonical_json_bytes(document) + b"\n"
        parsed = parse_canonical_artifact(model, raw)
        assert canonical_json_bytes(parsed) + b"\n" == raw


@pytest.mark.parametrize("filename", tuple(_documents()))
def test_contracts_reject_unknown_fields_and_floats(filename: str) -> None:
    model, document = _documents()[filename]
    for mutation in ({**document, "unknown": True}, {**document, "float": 0.1}):
        with pytest.raises((ValueError, ValidationError)):
            parse_canonical_artifact(model, canonical_json_bytes(mutation) + b"\n")


def test_engine_configuration_is_closed_to_paper_safe_values() -> None:
    model, document = _documents()["engine-configuration.json"]
    for key, value in (
        ("account_type", "MARGIN"),
        ("oms_type", "HEDGING"),
        ("bar_execution", True),
        ("network_access", True),
        ("load_state", True),
        ("save_state", True),
        ("run_analysis", True),
        ("logging_bypass", False),
        ("fee_rate", "NaN"),
    ):
        mutation = {**document, key: value}
        with pytest.raises(ValidationError):
            model.model_validate(mutation)


def test_instrument_catalog_rejects_unsupported_or_invalid_values() -> None:
    model, document = _documents()["instrument-catalog.json"]
    for key, value in (
        ("venue", "KRAKEN"),
        ("product_type", "equity"),
        ("provenance_sha256", "A" * 64),
        ("tick_size", "0"),
        ("min_quantity", "-1"),
    ):
        mutation = {**document, key: value}
        with pytest.raises(ValidationError):
            model.model_validate(mutation)


def test_target_schedule_rejects_duplicate_unordered_short_and_oversized_targets() -> None:
    model, document = _documents()["target-schedule.json"]
    mutations: list[dict[str, object]] = []
    duplicate = deepcopy(document)
    duplicate["targets"][1]["target_id"] = duplicate["targets"][0]["target_id"]  # type: ignore[index]
    mutations.append(duplicate)
    unordered = deepcopy(document)
    unordered["targets"].reverse()  # type: ignore[union-attr]
    mutations.append(unordered)
    for weight in ("-0.1", "1.1"):
        mutation = deepcopy(document)
        mutation["targets"][0]["positions"][0]["target_weight"] = weight  # type: ignore[index]
        mutations.append(mutation)
    for mutation in mutations:
        with pytest.raises(ValidationError):
            model.model_validate(mutation)


def test_manifest_rejects_invalid_digest_timeframe_and_window() -> None:
    model, document = _documents()["market-data-manifest.json"]
    for key, value in (
        ("data_sha256", "not-a-digest"),
        ("timeframe", "5m"),
        ("first_timestamp", "2026-08-05T12:02:00Z"),
    ):
        mutation = {**document, key: value}
        with pytest.raises(ValidationError):
            model.model_validate(mutation)


def test_parser_rejects_duplicate_noncanonical_and_oversized_json() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        parse_canonical_artifact(
            P1EngineConfigurationV1,
            b'{"schema_version":"nautilus-p1-engine-configuration-v1","schema_version":"nautilus-p1-engine-configuration-v1"}\n',
        )
    _, document = _documents()["engine-configuration.json"]
    with pytest.raises(ValueError, match="canonical"):
        parse_canonical_artifact(
            P1EngineConfigurationV1,
            json.dumps(document, indent=2).encode() + b"\n",
        )
    with pytest.raises(ValueError, match="maximum"):
        parse_canonical_artifact(
            P1EngineConfigurationV1,
            b" " * (P1EngineConfigurationV1.MAX_BYTES + 1),
        )


def test_json_schema_closes_enums_and_decimal_shapes() -> None:
    schema = P1EngineConfigurationV1.model_json_schema(mode="validation")
    assert schema["additionalProperties"] is False
    assert schema["properties"]["account_type"]["const"] == "CASH"
    assert schema["properties"]["fee_rate"]["type"] == "string"
    assert hashlib.sha256(canonical_json_bytes(schema)).hexdigest()
