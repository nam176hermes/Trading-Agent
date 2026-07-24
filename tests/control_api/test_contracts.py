from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from control_api import SCHEMA_VERSION
from control_api.contracts import (
    ApiEnvelope,
    AssetClass,
    DecisionAction,
    DecisionRecord,
    DecisionSignals,
    FreshnessStatus,
    HealthData,
    SystemStatus,
)


def decision_signals() -> DecisionSignals:
    return DecisionSignals(
        symbol="BTC", close=1.0, rsi_14=50.0, macd_line=0.0,
        macd_signal_line=0.0, macd_histogram=0.0, sma_200=1.0,
        price_vs_sma200="above", volume_24h=1.0, volume_30d_avg=1.0,
        volume_trend_ratio=1.0, signal=None, calculated_at=None,
    )


def test_decision_contract_rejects_string_confidence() -> None:
    with pytest.raises(ValidationError):
        DecisionRecord(
            decision_id="decision-1",
            asset="BTC",
            action=DecisionAction.BUY,
            confidence="0.5",
            decision_at=datetime.now(UTC),
            price_at_decision=1.0,
            reflected=False,
            signals=decision_signals(),
        )


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_decision_contract_rejects_out_of_range_confidence(confidence: float) -> None:
    with pytest.raises(ValidationError):
        DecisionRecord(
            decision_id="decision-1",
            asset="BTC",
            action=DecisionAction.BUY,
            confidence=confidence,
            decision_at=datetime.now(UTC),
            price_at_decision=1.0,
            reflected=False,
            signals=decision_signals(),
        )


def test_domain_contract_rejects_legacy_enum_spellings() -> None:
    with pytest.raises(ValidationError):
        DecisionRecord(
            decision_id="decision-1",
            asset="BTC",
            action="STRONG SELL",
            confidence=0.5,
            decision_at=datetime.now(UTC),
            price_at_decision=1.0,
            reflected=False,
            signals=decision_signals(),
        )


def test_envelope_requires_version_trace_and_generation_time() -> None:
    envelope = ApiEnvelope[HealthData](
        schema_version="1.0.0",
        trace_id="trace_test",
        generated_at=datetime.now(UTC),
        data=HealthData(status="UP"),
    )

    assert envelope.schema_version == "1.0.0"
    assert envelope.data.status == "UP"

    with pytest.raises(ValidationError):
        ApiEnvelope[HealthData].model_validate({"data": {"status": "UP"}})


def test_nullable_current_order_counts_use_major_schema_version() -> None:
    assert SCHEMA_VERSION == "2.0.0"
    required = SystemStatus.model_json_schema()["required"]
    assert "orders_count" in required
    assert "trades_count" in required


def test_canonical_enums_expose_only_contract_values() -> None:
    assert FreshnessStatus.STALE.value == "STALE"
    assert AssetClass.CRYPTO.value == "CRYPTO"
    assert DecisionAction.STRONG_SELL.value == "STRONG_SELL"
