"""Strict, hash-bound evidence contracts for WS-04 research gates."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from packages.data_catalog import MarketDatasetManifestV1
from packages.engine_contracts.serialization import CanonicalUtcDateTime, Sha256Hex, SourceCommit


_SAFE_NAME = Annotated[
    str,
    Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9._-]{0,63}$"),
]
_EXPLANATION = Annotated[str, Field(min_length=1, max_length=240)]
_BPS = Annotated[StrictInt, Field(ge=0, le=100_000)]
_RETURN = Annotated[Decimal, Field(ge=Decimal("-1"), le=Decimal("1000"))]


class ResearchValidationModel(BaseModel):
    """Closed immutable data-only records; no runtime authority is accepted."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, revalidate_instances="always")


class PointInTimeObservation(ResearchValidationModel):
    observation_id: _SAFE_NAME
    feature_event_at: CanonicalUtcDateTime
    known_at: CanonicalUtcDateTime
    decision_at: CanonicalUtcDateTime
    source_data_sha256: Sha256Hex


class RecursiveIndicatorReplay(ResearchValidationModel):
    indicator_name: _SAFE_NAME
    seed_sha256: Sha256Hex
    prefix_state_sha256: Sha256Hex
    replay_state_sha256: Sha256Hex
    sample_count: Annotated[StrictInt, Field(ge=1, le=10_000_000)]


class WalkForwardFold(ResearchValidationModel):
    fold_id: _SAFE_NAME
    train_start_at: CanonicalUtcDateTime
    train_end_at: CanonicalUtcDateTime
    validation_start_at: CanonicalUtcDateTime
    validation_end_at: CanonicalUtcDateTime
    out_of_sample_start_at: CanonicalUtcDateTime
    out_of_sample_end_at: CanonicalUtcDateTime
    out_of_sample_return: _RETURN


class CostScenario(ResearchValidationModel):
    name: Literal["baseline", "fee-stress", "slippage-stress", "combined-stress"]
    fee_bps: _BPS
    slippage_bps: _BPS
    net_return: _RETURN


class ComparisonRecord(ResearchValidationModel):
    comparator: Literal["reference", "legacy", "nautilus"]
    input_artifacts_sha256: Sha256Hex
    result_sha256: Sha256Hex
    event_sha256: Sha256Hex
    disposition: Literal["match", "explained-difference"]
    explanation: str | None = None

    @model_validator(mode="after")
    def _explanation_is_exact(self) -> "ComparisonRecord":
        if self.disposition == "match" and self.explanation is not None:
            raise ValueError("matching comparator must not carry an explanation")
        if self.disposition == "explained-difference":
            if not isinstance(self.explanation, str) or not self.explanation.strip():
                raise ValueError("explained difference requires an explanation")
            if len(self.explanation) > 240:
                raise ValueError("comparison explanation is too long")
        return self


class ResearchProvenanceV1(ResearchValidationModel):
    """All 04A–04C source/result hashes required by the research gates."""

    dataset: MarketDatasetManifestV1
    dataset_content_sha256: Sha256Hex
    canonical_rows_sha256: Sha256Hex
    engine_configuration_sha256: Sha256Hex
    instrument_catalog_sha256: Sha256Hex
    strategy_configuration_sha256: Sha256Hex
    market_data_sha256: Sha256Hex
    backtest_input_artifacts_sha256: Sha256Hex
    backtest_result_sha256: Sha256Hex
    source_commit: SourceCommit


class ResearchGateEvidenceV1(ResearchValidationModel):
    """Complete offline evidence set for all mandatory 04D research gates."""

    schema_version: Literal["research-gate-evidence-v1"] = "research-gate-evidence-v1"
    point_in_time: tuple[PointInTimeObservation, ...] = Field(max_length=4096)
    recursive_replays: tuple[RecursiveIndicatorReplay, ...] = Field(max_length=1024)
    walk_forward_folds: tuple[WalkForwardFold, ...] = Field(max_length=128)
    minimum_walk_forward_return: _RETURN
    cost_scenarios: tuple[CostScenario, ...] = Field(max_length=4)
    minimum_stressed_return: _RETURN
    comparisons: tuple[ComparisonRecord, ...] = Field(max_length=3)
    provenance: ResearchProvenanceV1
    promotion_authority: Literal["reference-and-nautilus"]

    @model_validator(mode="after")
    def _canonical_record_order(self) -> "ResearchGateEvidenceV1":
        if self.point_in_time != tuple(sorted(self.point_in_time, key=lambda item: item.observation_id)):
            raise ValueError("point-in-time observations must be sorted")
        if self.recursive_replays != tuple(
            sorted(self.recursive_replays, key=lambda item: item.indicator_name)
        ):
            raise ValueError("recursive replays must be sorted")
        if len({item.indicator_name for item in self.recursive_replays}) != len(self.recursive_replays):
            raise ValueError("recursive replay indicators must be unique")
        if self.walk_forward_folds != tuple(
            sorted(self.walk_forward_folds, key=lambda item: item.out_of_sample_start_at)
        ):
            raise ValueError("walk-forward folds must be sorted")
        if self.cost_scenarios != tuple(sorted(self.cost_scenarios, key=lambda item: item.name)):
            raise ValueError("cost scenarios must be sorted")
        if len({item.name for item in self.cost_scenarios}) != len(self.cost_scenarios):
            raise ValueError("cost scenario names must be unique")
        if self.comparisons != tuple(sorted(self.comparisons, key=lambda item: item.comparator)):
            raise ValueError("benchmark comparisons must be sorted")
        if len({item.comparator for item in self.comparisons}) != len(self.comparisons):
            raise ValueError("benchmark comparators must be unique")
        return self
