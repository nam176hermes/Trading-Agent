"""Frozen, deterministic P3 alpha qualification protocol."""

from __future__ import annotations

from decimal import Decimal
import hashlib
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from packages.engine_contracts.serialization import Sha256Hex, canonical_json_bytes

from .baselines import BaselineResultV1


_Return = Annotated[Decimal, Field(ge=Decimal("-1"), le=Decimal("1000"))]
_NonNegative = Annotated[Decimal, Field(ge=0, le=Decimal("1000000"))]


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, revalidate_instances="always")


class PerformanceMetricsV1(_Frozen):
    total_return: _Return
    cagr: _Return
    volatility: _NonNegative
    max_drawdown: Annotated[Decimal, Field(ge=0, le=1)]
    downside_risk: _NonNegative
    sharpe: Decimal | None
    sortino: Decimal | None
    calmar: Decimal | None
    trade_count: Annotated[int, Field(ge=0)]
    turnover: _NonNegative
    average_holding_days: _NonNegative | None
    fees: _NonNegative
    spread: _NonNegative
    slippage: _NonNegative
    funding: _NonNegative
    borrow_cost: _NonNegative
    worst_day: _Return
    worst_week: _Return
    worst_month: _Return
    expected_shortfall_5: _Return
    failure_codes: tuple[str, ...] = Field(max_length=32)

    @model_validator(mode="after")
    def _canonical(self) -> "PerformanceMetricsV1":
        if self.failure_codes != tuple(sorted(set(self.failure_codes))):
            raise ValueError("metric failure codes must be sorted and unique")
        for name, value in self:
            if isinstance(value, Decimal) and not value.is_finite():
                raise ValueError(f"{name} must be finite")
        return self


class AlphaQualificationEvidenceV1(_Frozen):
    schema_version: Literal["alpha-qualification-evidence-v1"] = "alpha-qualification-evidence-v1"
    alpha_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9._-]{0,63}$")]
    alpha_version: Annotated[str, Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")]
    source_sha: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    dataset_snapshot_sha256: Sha256Hex
    parameter_set_sha256: Sha256Hex
    cost_model_sha256: Sha256Hex
    environment_sha256: Sha256Hex
    result_artifact_sha256: Sha256Hex
    replay_result_sha256s: tuple[Sha256Hex, ...] = Field(min_length=3, max_length=3)
    pit_adversarial_passed: bool
    metrics: PerformanceMetricsV1
    baseline_result: BaselineResultV1
    fold_excess_returns: tuple[_Return, ...] = Field(min_length=3, max_length=32)
    regime_excess_returns: tuple[_Return, ...] = Field(min_length=3, max_length=32)
    perturbation_net_returns: tuple[_Return, ...] = Field(min_length=2, max_length=64)
    double_cost_net_return: _Return
    delayed_execution_net_return: _Return
    median_participation: Annotated[Decimal, Field(ge=0, le=1)]
    peak_participation: Annotated[Decimal, Field(ge=0, le=1)]
    single_instrument_concentration: Literal[
        "NOT_APPLICABLE_SINGLE_INSTRUMENT_SCOPE"
    ]

    @model_validator(mode="after")
    def _same_campaign(self) -> "AlphaQualificationEvidenceV1":
        if (
            self.baseline_result.dataset_snapshot_sha256
            != self.dataset_snapshot_sha256
            or self.baseline_result.cost_model_sha256 != self.cost_model_sha256
        ):
            raise ValueError("baseline result must bind the alpha snapshot and cost model")
        return self


class QualificationCriterionV1(_Frozen):
    criterion_id: Annotated[str, Field(pattern=r"^C(0[1-9]|1[0-6])$")]
    passed: bool
    failure_code: Annotated[str, Field(pattern=r"^E_[A-Z0-9_]+$")]


class AlphaQualificationResultV1(_Frozen):
    schema_version: Literal["alpha-qualification-result-v1"] = "alpha-qualification-result-v1"
    evidence_sha256: Sha256Hex
    criteria: tuple[QualificationCriterionV1, ...] = Field(min_length=16, max_length=16)
    qualified: bool
    failure_codes: tuple[str, ...] = Field(max_length=16)
    result_sha256: Sha256Hex | None = None

    @model_validator(mode="after")
    def _complete(self) -> "AlphaQualificationResultV1":
        if tuple(item.criterion_id for item in self.criteria) != tuple(
            f"C{index:02d}" for index in range(1, 17)
        ):
            raise ValueError("qualification criteria must be complete and ordered")
        failures = tuple(sorted(item.failure_code for item in self.criteria if not item.passed))
        if self.failure_codes != failures or self.qualified != (not failures):
            raise ValueError("qualification result disagrees with criteria")
        digest = hashlib.sha256(
            canonical_json_bytes(self.model_dump(exclude={"result_sha256"}))
        ).hexdigest()
        if self.result_sha256 is not None and self.result_sha256 != digest:
            raise ValueError("qualification result digest is invalid")
        object.__setattr__(self, "result_sha256", digest)
        return self


def _median(values: tuple[Decimal, ...]) -> Decimal:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def evaluate_alpha_qualification(
    evidence: AlphaQualificationEvidenceV1,
) -> AlphaQualificationResultV1:
    """Apply the frozen gate without tuning or execution side effects."""

    value = AlphaQualificationEvidenceV1.model_validate(evidence)
    metrics = value.metrics
    excess = metrics.total_return - value.baseline_result.total_return
    checks = (
        (value.pit_adversarial_passed, "E_PIT_LEAKAGE"),
        (
            value.replay_result_sha256s == (value.result_artifact_sha256,) * 3,
            "E_REPRODUCIBILITY",
        ),
        (metrics.total_return > 0 and metrics.cagr > 0, "E_OOS_EDGE"),
        (metrics.total_return > 0 and not metrics.failure_codes, "E_AFTER_COST"),
        (excess >= Decimal("0.02"), "E_BASELINE_EXCESS"),
        (_median(value.fold_excess_returns) > 0, "E_MEDIAN_FOLD"),
        (sum(item > 0 for item in value.fold_excess_returns) >= 2, "E_FOLD_COUNT"),
        (metrics.sharpe is not None and metrics.sharpe >= Decimal("0.75"), "E_SHARPE"),
        (metrics.sortino is not None and metrics.sortino >= Decimal("1"), "E_SORTINO"),
        (metrics.max_drawdown <= Decimal("0.25"), "E_MAX_DRAWDOWN"),
        (metrics.trade_count >= 30, "E_TRADE_COUNT"),
        (
            sum(item > 0 for item in value.regime_excess_returns) >= 2
            and min(value.regime_excess_returns) >= Decimal("-0.10"),
            "E_REGIME_DEPENDENCE",
        ),
        (all(item > 0 for item in value.perturbation_net_returns), "E_PERTURBATION"),
        (value.double_cost_net_return > 0, "E_DOUBLE_COST"),
        (value.delayed_execution_net_return > 0, "E_DELAYED_EXECUTION"),
        (
            value.median_participation <= Decimal("0.02")
            and value.peak_participation <= Decimal("0.10"),
            "E_CAPACITY",
        ),
    )
    criteria = tuple(
        QualificationCriterionV1(
            criterion_id=f"C{index:02d}", passed=passed, failure_code=code
        )
        for index, (passed, code) in enumerate(checks, start=1)
    )
    failures = tuple(sorted(item.failure_code for item in criteria if not item.passed))
    return AlphaQualificationResultV1(
        evidence_sha256=hashlib.sha256(canonical_json_bytes(value)).hexdigest(),
        criteria=criteria,
        qualified=not failures,
        failure_codes=failures,
    )


__all__ = [
    "AlphaQualificationEvidenceV1",
    "AlphaQualificationResultV1",
    "PerformanceMetricsV1",
    "QualificationCriterionV1",
    "evaluate_alpha_qualification",
]
