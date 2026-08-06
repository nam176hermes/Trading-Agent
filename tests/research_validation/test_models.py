from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from packages.data_catalog import MarketDatasetContinuityV1, MarketDatasetManifestV1
from packages.domain import InstrumentId, MarketTimeframe, ProductType
from packages.research_validation import (
    ComparisonRecord,
    CostScenario,
    PointInTimeObservation,
    RecursiveIndicatorReplay,
    ResearchGateEvidenceV1,
    ResearchProvenanceV1,
    WalkForwardFold,
)


NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


def _digest(value: str) -> str:
    return value * 64


def _manifest() -> MarketDatasetManifestV1:
    return MarketDatasetManifestV1(
        provider="binance",
        instrument=InstrumentId("BTCUSDT", ProductType.CRYPTO_SPOT, "BINANCE"),
        timeframe=MarketTimeframe.ONE_MINUTE,
        first_event_at=NOW,
        last_event_at=NOW + timedelta(minutes=1),
        observed_at=NOW + timedelta(minutes=2),
        fetched_at=NOW + timedelta(minutes=3),
        known_at=NOW + timedelta(minutes=4),
        snapshot_schema_version="market-snapshot-v1",
        provenance_schema_version="market-data-provenance-v1",
        normalization_version="normalization-v1",
        row_count=2,
        content_digest=_digest("a"),
        raw_evidence_sha256=_digest("b"),
        canonical_rows_sha256=_digest("c"),
        parquet_sha256=_digest("d"),
        continuity=MarketDatasetContinuityV1(
            timeframe=MarketTimeframe.ONE_MINUTE, gap_report=(), duplicate_report=()
        ),
        importer_version="fixture-v1",
    )


def evidence(**updates: object) -> ResearchGateEvidenceV1:
    provenance = ResearchProvenanceV1(
        dataset=_manifest(),
        dataset_content_sha256=_digest("a"),
        canonical_rows_sha256=_digest("c"),
        engine_configuration_sha256=_digest("e"),
        instrument_catalog_sha256=_digest("f"),
        strategy_configuration_sha256=_digest("1"),
        market_data_sha256=_digest("c"),
        backtest_input_artifacts_sha256=_digest("2"),
        backtest_result_sha256=_digest("3"),
        source_commit="0" * 40,
    )
    values: dict[str, object] = {
        "point_in_time": (
            PointInTimeObservation(
                observation_id="bar-1",
                feature_event_at=NOW,
                known_at=NOW + timedelta(minutes=1),
                decision_at=NOW + timedelta(minutes=2),
                source_data_sha256=_digest("c"),
            ),
        ),
        "recursive_replays": (
            RecursiveIndicatorReplay(
                indicator_name="ema-20",
                seed_sha256=_digest("4"),
                prefix_state_sha256=_digest("5"),
                replay_state_sha256=_digest("5"),
                sample_count=20,
            ),
        ),
        "walk_forward_folds": (
            WalkForwardFold(
                fold_id="fold-1",
                train_start_at=NOW,
                train_end_at=NOW + timedelta(minutes=1),
                validation_start_at=NOW + timedelta(minutes=2),
                validation_end_at=NOW + timedelta(minutes=3),
                out_of_sample_start_at=NOW + timedelta(minutes=4),
                out_of_sample_end_at=NOW + timedelta(minutes=5),
                out_of_sample_return=Decimal("0.01"),
            ),
        ),
        "minimum_walk_forward_return": Decimal("0"),
        "cost_scenarios": (
            CostScenario(name="baseline", fee_bps=0, slippage_bps=0, net_return=Decimal("0.02")),
        ),
        "minimum_stressed_return": Decimal("-0.01"),
        "comparisons": (
            ComparisonRecord(
                comparator="legacy",
                input_artifacts_sha256=_digest("2"),
                result_sha256=_digest("6"),
                event_sha256=_digest("7"),
                disposition="match",
            ),
        ),
        "provenance": provenance,
        "promotion_authority": "reference-and-nautilus",
    }
    values.update(updates)
    return ResearchGateEvidenceV1(**values)


def test_research_gate_evidence_contract_is_available() -> None:
    assert ResearchGateEvidenceV1 is not None


def test_evidence_is_strict_and_immutable() -> None:
    value = evidence()

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        evidence(unexpected=True)
    with pytest.raises(ValidationError):
        CostScenario(name="baseline", fee_bps=0.0, slippage_bps=0, net_return=Decimal("0"))
    with pytest.raises(ValidationError):
        value.promotion_authority = "legacy"  # type: ignore[misc]


def test_evidence_requires_canonical_unique_records() -> None:
    replay = RecursiveIndicatorReplay(
        indicator_name="ema-20",
        seed_sha256=_digest("4"),
        prefix_state_sha256=_digest("5"),
        replay_state_sha256=_digest("5"),
        sample_count=20,
    )
    with pytest.raises(ValidationError, match="indicators must be unique"):
        evidence(recursive_replays=(replay, replay))

    later = PointInTimeObservation(
        observation_id="z-last",
        feature_event_at=NOW,
        known_at=NOW + timedelta(minutes=1),
        decision_at=NOW + timedelta(minutes=2),
        source_data_sha256=_digest("c"),
    )
    first = evidence().point_in_time[0]
    with pytest.raises(ValidationError, match="observations must be sorted"):
        evidence(point_in_time=(later, first))


def test_legacy_cannot_be_promotion_authority() -> None:
    with pytest.raises(ValidationError):
        evidence(promotion_authority="legacy")
