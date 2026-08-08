from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

import packages.research_validation as research
from packages.data_catalog import MarketDatasetContinuityV1, MarketDatasetManifestV1
from packages.domain import InstrumentId, MarketTimeframe, ProductType
from packages.engine_contracts import canonical_json_bytes
from packages.research_validation import (
    ComparisonRecord,
    CostScenario,
    PointInTimeObservation,
    RecursiveIndicatorReplay,
    ResearchGateEvidenceV1,
    ResearchProvenanceV1,
    WalkForwardFold,
    analysis_output_sha256,
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
    input_artifacts_sha256 = hashlib.sha256(
        canonical_json_bytes(
            {
                "engine_configuration": _digest("e"),
                "instrument_catalog": _digest("f"),
                "strategy_configuration": _digest("1"),
                "market_data": _digest("d"),
            }
        )
    ).hexdigest()
    provenance = ResearchProvenanceV1(
        dataset=_manifest(),
        dataset_content_sha256=_digest("a"),
        canonical_rows_sha256=_digest("c"),
        engine_configuration_sha256=_digest("e"),
        instrument_catalog_sha256=_digest("f"),
        strategy_configuration_sha256=_digest("1"),
        market_data_sha256=_digest("d"),
        backtest_input_artifacts_sha256=input_artifacts_sha256,
        backtest_event_sha256=_digest("7"),
        backtest_result_sha256=_digest("3"),
        source_commit="0" * 40,
    )
    values: dict[str, object] = {
        "point_in_time": (
            PointInTimeObservation(
                observation_id="bar-1",
                input_artifacts_sha256=input_artifacts_sha256,
                feature_event_at=NOW,
                known_at=NOW + timedelta(minutes=4),
                decision_at=NOW + timedelta(minutes=5),
                source_data_sha256=_digest("c"),
            ),
        ),
        "recursive_replays": (
            RecursiveIndicatorReplay(
                indicator_name="ema-20",
                input_artifacts_sha256=input_artifacts_sha256,
                seed_sha256=_digest("4"),
                prefix_state_sha256=_digest("5"),
                replay_state_sha256=_digest("5"),
                sample_count=20,
            ),
        ),
        "walk_forward_folds": (
            WalkForwardFold(
                fold_id="fold-1",
                input_artifacts_sha256=input_artifacts_sha256,
                train_start_at=NOW,
                train_end_at=NOW + timedelta(minutes=1),
                validation_start_at=NOW + timedelta(minutes=2),
                validation_end_at=NOW + timedelta(minutes=3),
                out_of_sample_start_at=NOW + timedelta(minutes=4),
                out_of_sample_end_at=NOW + timedelta(minutes=5),
                out_of_sample_return=Decimal("0.01"),
            ),
            WalkForwardFold(
                fold_id="fold-2",
                input_artifacts_sha256=input_artifacts_sha256,
                train_start_at=NOW + timedelta(minutes=6),
                train_end_at=NOW + timedelta(minutes=7),
                validation_start_at=NOW + timedelta(minutes=8),
                validation_end_at=NOW + timedelta(minutes=9),
                out_of_sample_start_at=NOW + timedelta(minutes=10),
                out_of_sample_end_at=NOW + timedelta(minutes=11),
                out_of_sample_return=Decimal("0.01"),
            ),
        ),
        "minimum_walk_forward_return": Decimal("0"),
        "cost_scenarios": (
            CostScenario(name="baseline", input_artifacts_sha256=input_artifacts_sha256, fee_bps=0, slippage_bps=0, net_return=Decimal("0.02")),
            CostScenario(name="combined-stress", input_artifacts_sha256=input_artifacts_sha256, fee_bps=10, slippage_bps=10, net_return=Decimal("0.01")),
            CostScenario(name="fee-stress", input_artifacts_sha256=input_artifacts_sha256, fee_bps=10, slippage_bps=0, net_return=Decimal("0.015")),
            CostScenario(name="slippage-stress", input_artifacts_sha256=input_artifacts_sha256, fee_bps=0, slippage_bps=10, net_return=Decimal("0.015")),
        ),
        "minimum_stressed_return": Decimal("-0.01"),
        "comparisons": (
            ComparisonRecord(
                comparator="legacy",
                input_artifacts_sha256=input_artifacts_sha256,
                result_sha256=_digest("3"),
                event_sha256=_digest("7"),
                disposition="match",
            ),
            ComparisonRecord(
                comparator="nautilus",
                input_artifacts_sha256=input_artifacts_sha256,
                result_sha256=_digest("3"),
                event_sha256=_digest("7"),
                disposition="match",
            ),
            ComparisonRecord(
                comparator="reference",
                input_artifacts_sha256=input_artifacts_sha256,
                result_sha256=_digest("3"),
                event_sha256=_digest("7"),
                disposition="match",
            ),
        ),
        "provenance": provenance,
        "promotion_authority": "reference-and-nautilus",
    }
    values.update(updates)
    values["analysis_output_sha256"] = analysis_output_sha256(
        values["point_in_time"],
        values["recursive_replays"],
        values["walk_forward_folds"],
        values["minimum_walk_forward_return"],
        values["cost_scenarios"],
        values["minimum_stressed_return"],
        values["comparisons"],
    )
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
        input_artifacts_sha256=evidence().provenance.backtest_input_artifacts_sha256,
        seed_sha256=_digest("4"),
        prefix_state_sha256=_digest("5"),
        replay_state_sha256=_digest("5"),
        sample_count=20,
    )
    with pytest.raises(ValidationError, match="indicators must be unique"):
        evidence(recursive_replays=(replay, replay))

    later = PointInTimeObservation(
        observation_id="z-last",
        input_artifacts_sha256=evidence().provenance.backtest_input_artifacts_sha256,
        feature_event_at=NOW,
        known_at=NOW + timedelta(minutes=1),
        decision_at=NOW + timedelta(minutes=2),
        source_data_sha256=_digest("c"),
    )
    first = evidence().point_in_time[0]
    with pytest.raises(ValidationError, match="observations must be sorted"):
        evidence(point_in_time=(later, first))

    repeated_fold = evidence().walk_forward_folds[0]
    with pytest.raises(ValidationError, match="fold identifiers must be unique"):
        evidence(walk_forward_folds=(repeated_fold, repeated_fold))


def test_legacy_cannot_be_promotion_authority() -> None:
    with pytest.raises(ValidationError):
        evidence(promotion_authority="legacy")


def _scenario_comparison(scenario_id: str, index: int):
    digest = hashlib.sha256(scenario_id.encode("ascii")).hexdigest()
    return research.VerifiedScenarioComparisonV1(
        scenario_id=scenario_id,
        engine_configuration_sha256=_digest("e"),
        instrument_catalog_sha256=_digest("f"),
        strategy_configuration_sha256=_digest("1"),
        market_data_sha256=_digest("d"),
        simulation_scenario_sha256=digest,
        independent_reference_result_sha256=_digest(str((index + 1) % 10)),
        independent_reference_event_sha256=_digest(str((index + 2) % 10)),
        nautilus_result_sha256=_digest(str((index + 1) % 10)),
        nautilus_event_sha256=_digest(str((index + 2) % 10)),
        legacy_result_sha256=_digest(str((index + 3) % 10)),
        legacy_event_sha256=_digest(str((index + 4) % 10)),
        legacy_disposition="explained-difference",
        legacy_classification="legacy-minimum-50-bars",
        legacy_selected=False,
    )


def _campaign_evidence(comparisons: tuple, **updates: object):
    parity_sha256 = _digest("8")
    paper = research.PaperCompatibilityResultV1.create(
        candidate_closure_sha256=_digest("4"),
        candidate_manifest_sha256=_digest("5"),
        engine_configuration_sha256=comparisons[0].engine_configuration_sha256,
        instrument_catalog_sha256=comparisons[0].instrument_catalog_sha256,
        strategy_configuration_sha256=comparisons[0].strategy_configuration_sha256,
        strategy_source_sha256=_digest("6"),
        scenario_campaign_sha256=_digest("7"),
        parity_record_sha256=parity_sha256,
        launcher_result_sha256=_digest("9"),
    )
    base = evidence()
    comparison_inputs = tuple(
        hashlib.sha256(
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
        for item in comparisons
    )
    point_in_time = tuple(
        PointInTimeObservation(
            observation_id=f"scenario-{index:02d}",
            input_artifacts_sha256=input_sha256,
            feature_event_at=NOW,
            known_at=NOW + timedelta(minutes=1),
            decision_at=NOW + timedelta(minutes=2),
            source_data_sha256=comparisons[index].market_data_sha256,
        )
        for index, input_sha256 in enumerate(comparison_inputs)
    )
    recursive_replays = tuple(
        RecursiveIndicatorReplay(
            indicator_name=f"scenario-{index:02d}",
            input_artifacts_sha256=input_sha256,
            seed_sha256=_digest("4"),
            prefix_state_sha256=_digest("5"),
            replay_state_sha256=_digest("5"),
            sample_count=2,
        )
        for index, input_sha256 in enumerate(comparison_inputs)
    )
    campaign_sha256 = _digest("7")
    fold_sha256 = tuple(
        hashlib.sha256(
            canonical_json_bytes(
                {
                    "fold_id": f"campaign-fold-{index + 1}",
                    "scenario_inputs": [
                        {
                            "input_artifacts_sha256": input_sha256,
                            "scenario_id": item.scenario_id,
                        }
                        for item, input_sha256 in zip(
                            comparisons[index * 4 : (index + 1) * 4],
                            comparison_inputs[index * 4 : (index + 1) * 4],
                            strict=True,
                        )
                    ],
                }
            )
        ).hexdigest()
        for index in range(2)
    )
    values: dict[str, object] = {
        "scenario_campaign_sha256": campaign_sha256,
        "strategy_source_sha256": _digest("6"),
        "candidate_closure_sha256": _digest("4"),
        "candidate_manifest_sha256": _digest("5"),
        "parity_record_sha256": parity_sha256,
        "paper_record_sha256": hashlib.sha256(canonical_json_bytes(paper) + b"\n").hexdigest(),
        "legacy_records_sha256": _digest("a"),
        "comparisons": comparisons,
        "paper_result": paper,
        "point_in_time": point_in_time,
        "recursive_replays": recursive_replays,
        "walk_forward_folds": tuple(
            item.model_copy(
                update={"input_artifacts_sha256": fold_sha256[index]}
            )
            for index, item in enumerate(base.walk_forward_folds)
        ),
        "minimum_walk_forward_return": base.minimum_walk_forward_return,
        "cost_scenarios": tuple(
            item.model_copy(
                update={"input_artifacts_sha256": campaign_sha256}
            )
            for item in base.cost_scenarios
        ),
        "minimum_stressed_return": base.minimum_stressed_return,
        "promotion_authority": "reference-and-nautilus",
    }
    values.update(updates)
    values["analysis_output_sha256"] = research.campaign_analysis_output_sha256(
        comparisons=values["comparisons"],
        paper_result=values["paper_result"],
        point_in_time=values["point_in_time"],
        recursive_replays=values["recursive_replays"],
        walk_forward_folds=values["walk_forward_folds"],
        minimum_walk_forward_return=values["minimum_walk_forward_return"],
        cost_scenarios=values["cost_scenarios"],
        minimum_stressed_return=values["minimum_stressed_return"],
    )
    return research.ResearchCampaignEvidenceV2(**values)


def test_campaign_comparisons_require_exact_sorted_unique_eight_scenarios() -> None:
    comparisons = tuple(
        _scenario_comparison(scenario_id, index)
        for index, scenario_id in enumerate(research.PHASE4_SCENARIO_IDS)
    )

    value = _campaign_evidence(comparisons)

    assert value.promotion_authority == "reference-and-nautilus"
    with pytest.raises(ValidationError, match="at least 8|exact eight-scenario"):
        _campaign_evidence(comparisons[:-1])
    with pytest.raises(ValidationError, match="ordered"):
        _campaign_evidence(tuple(reversed(comparisons)))


def test_campaign_v2_is_strict_without_changing_v1_schema_or_digest() -> None:
    before = evidence()
    before_bytes = canonical_json_bytes(before)
    comparisons = tuple(
        _scenario_comparison(scenario_id, index)
        for index, scenario_id in enumerate(research.PHASE4_SCENARIO_IDS)
    )

    campaign = _campaign_evidence(comparisons)

    assert campaign.schema_version == "research-campaign-evidence-v2"
    assert evidence().schema_version == "research-gate-evidence-v1"
    assert canonical_json_bytes(evidence()) == before_bytes
    with pytest.raises(ValidationError, match="Extra inputs"):
        _campaign_evidence(comparisons, unexpected=True)
