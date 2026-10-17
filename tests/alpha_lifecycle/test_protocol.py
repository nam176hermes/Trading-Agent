from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from packages.alpha_lifecycle.baselines import BaselineId, BaselineResultV1
from packages.alpha_lifecycle.protocol import (
    AlphaQualificationEvidenceV1,
    PerformanceMetricsV1,
    evaluate_alpha_qualification,
)


def _metrics(**updates: object) -> PerformanceMetricsV1:
    values: dict[str, object] = {
        "total_return": Decimal("0.20"),
        "cagr": Decimal("0.12"),
        "volatility": Decimal("0.15"),
        "max_drawdown": Decimal("0.10"),
        "downside_risk": Decimal("0.08"),
        "sharpe": Decimal("0.80"),
        "sortino": Decimal("1.20"),
        "calmar": Decimal("1.20"),
        "trade_count": 40,
        "turnover": Decimal("4"),
        "average_holding_days": Decimal("10"),
        "fees": Decimal("0.01"),
        "spread": Decimal("0.005"),
        "slippage": Decimal("0.005"),
        "funding": Decimal(0),
        "borrow_cost": Decimal(0),
        "worst_day": Decimal("-0.03"),
        "worst_week": Decimal("-0.05"),
        "worst_month": Decimal("-0.07"),
        "expected_shortfall_5": Decimal("-0.04"),
        "failure_codes": (),
    }
    values.update(updates)
    return PerformanceMetricsV1(**values)


def _evidence(**updates: object) -> AlphaQualificationEvidenceV1:
    values: dict[str, object] = {
        "alpha_id": "alpha-fixture",
        "alpha_version": "1.0.0",
        "source_sha": "a" * 40,
        "dataset_snapshot_sha256": "b" * 64,
        "parameter_set_sha256": "c" * 64,
        "cost_model_sha256": "d" * 64,
        "environment_sha256": "e" * 64,
        "result_artifact_sha256": "f" * 64,
        "replay_result_sha256s": ("f" * 64,) * 3,
        "pit_adversarial_passed": True,
        "metrics": _metrics(),
        "baseline_result": BaselineResultV1(
            baseline_id=BaselineId.BUY_AND_HOLD,
            baseline_version="1.0.0",
            dataset_snapshot_sha256="b" * 64,
            cost_model_sha256="d" * 64,
            metrics_sha256="1" * 64,
            total_return=Decimal("0.15"),
        ),
        "fold_excess_returns": (Decimal("0.01"), Decimal("0.02"), Decimal("-0.01")),
        "regime_excess_returns": (Decimal("0.03"), Decimal("0.01"), Decimal("-0.02")),
        "perturbation_net_returns": (Decimal("0.01"), Decimal("0.02")),
        "double_cost_net_return": Decimal("0.01"),
        "delayed_execution_net_return": Decimal("0.01"),
        "median_participation": Decimal("0.01"),
        "peak_participation": Decimal("0.08"),
        "single_instrument_concentration": "NOT_APPLICABLE_SINGLE_INSTRUMENT_SCOPE",
    }
    values.update(updates)
    return AlphaQualificationEvidenceV1(**values)


def test_exact_frozen_criteria_pass_only_complete_robust_evidence() -> None:
    result = evaluate_alpha_qualification(_evidence())

    assert result.qualified is True
    assert result.failure_codes == ()
    assert len(result.criteria) == 16


def test_positive_return_alone_cannot_qualify() -> None:
    evidence = _evidence(
        metrics=_metrics(sharpe=Decimal("0.74"), trade_count=29),
        pit_adversarial_passed=False,
        replay_result_sha256s=("f" * 64, "0" * 64, "f" * 64),
        double_cost_net_return=Decimal("-0.01"),
    )

    result = evaluate_alpha_qualification(evidence)

    assert result.qualified is False
    assert {
        "E_PIT_LEAKAGE",
        "E_REPRODUCIBILITY",
        "E_SHARPE",
        "E_TRADE_COUNT",
        "E_DOUBLE_COST",
    } <= set(result.failure_codes)


def test_baseline_must_bind_the_same_snapshot_and_cost_model() -> None:
    with pytest.raises(ValidationError, match="baseline result must bind"):
        _evidence(
            baseline_result=BaselineResultV1(
                baseline_id=BaselineId.BUY_AND_HOLD,
                baseline_version="1.0.0",
                dataset_snapshot_sha256="9" * 64,
                cost_model_sha256="d" * 64,
                metrics_sha256="1" * 64,
                total_return=Decimal("0.15"),
            )
        )


def test_regime_excess_below_single_return_floor_reaches_criteria() -> None:
    values = (Decimal("0.03"), Decimal("0.01"), Decimal("-1.3666"))
    evidence = _evidence(regime_excess_returns=values)

    result = evaluate_alpha_qualification(evidence)

    assert evidence.regime_excess_returns == values
    assert result.failure_codes == ("E_REGIME_DEPENDENCE",)
    assert result.qualified is False


def test_fold_excess_below_single_return_floor_can_fail_criteria() -> None:
    values = (Decimal("-1.2"), Decimal("-0.5"), Decimal("0.01"))
    evidence = _evidence(fold_excess_returns=values)

    result = evaluate_alpha_qualification(evidence)

    assert evidence.fold_excess_returns == values
    assert result.failure_codes == ("E_FOLD_COUNT", "E_MEDIAN_FOLD")
    assert result.qualified is False


def test_fold_excess_below_single_return_floor_can_qualify() -> None:
    values = (Decimal("-1.2"), Decimal("0.01"), Decimal("0.02"))
    evidence = _evidence(fold_excess_returns=values)

    result = evaluate_alpha_qualification(evidence)

    assert evidence.fold_excess_returns == values
    assert result.failure_codes == ()
    assert result.qualified is True


def test_regime_excess_above_fold_ceiling_can_qualify() -> None:
    values = (Decimal("1023"), Decimal("0.01"), Decimal("-0.02"))
    evidence = _evidence(regime_excess_returns=values)

    result = evaluate_alpha_qualification(evidence)

    assert evidence.regime_excess_returns == values
    assert result.failure_codes == ()
    assert result.qualified is True


def test_regime_excess_below_fold_floor_reaches_criteria() -> None:
    values = (Decimal("-1023"), Decimal("0.01"), Decimal("0.02"))
    evidence = _evidence(regime_excess_returns=values)

    result = evaluate_alpha_qualification(evidence)

    assert evidence.regime_excess_returns == values
    assert result.failure_codes == ("E_REGIME_DEPENDENCE",)
    assert result.qualified is False


@pytest.mark.parametrize(
    ("first", "error_type", "failure_codes", "qualified"),
    [
        ("-1001", None, ("E_FOLD_COUNT", "E_MEDIAN_FOLD"), False),
        ("1001", None, (), True),
        ("-1001.0001", "greater_than_equal", None, None),
        ("1001.0001", "less_than_equal", None, None),
    ],
)
def test_fold_excess_endpoints_and_outside_bounds(
    first: str,
    error_type: str | None,
    failure_codes: tuple[str, ...] | None,
    qualified: bool | None,
) -> None:
    values = (Decimal(first), Decimal("0.02"), Decimal("-0.01"))
    if error_type is not None:
        with pytest.raises(ValidationError) as caught:
            _evidence(fold_excess_returns=values)
        assert (("fold_excess_returns", 0), error_type) in {
            (error["loc"], error["type"]) for error in caught.value.errors()
        }
        return

    evidence = _evidence(fold_excess_returns=values)
    result = evaluate_alpha_qualification(evidence)

    assert evidence.fold_excess_returns == values
    assert result.failure_codes == failure_codes
    assert result.qualified is qualified


def test_regime_excess_rejects_nonfinite_values_at_each_position() -> None:
    for nonfinite in ("NaN", "sNaN", "Infinity", "-Infinity"):
        for index in range(3):
            values = [Decimal("0.03"), Decimal("0.01"), Decimal("-0.02")]
            values[index] = Decimal(nonfinite)
            with pytest.raises(ValidationError) as caught:
                _evidence(regime_excess_returns=tuple(values))
            assert (("regime_excess_returns", index), "finite_number") in {
                (error["loc"], error["type"]) for error in caught.value.errors()
            }


def test_single_total_return_keeps_original_floor() -> None:
    with pytest.raises(ValidationError) as caught:
        _metrics(total_return=Decimal("-1.0001"))
    assert (("total_return",), "greater_than_equal") in {
        (error["loc"], error["type"]) for error in caught.value.errors()
    }
