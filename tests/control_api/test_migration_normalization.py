from datetime import UTC

import pytest

from trading_control.normalization import MigrationValidationError, normalize_decision


def decision(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "ticker": "BTC",
        "suggestion": "STRONG SELL",
        "confidence": 0.5,
        "stored_at": "2026-06-25T04:54:37Z",
        "price_at_decision": 100.0,
        "signals": {},
    }
    value.update(overrides)
    return value


def test_normalizes_approved_alias_and_preserves_missing_known_at() -> None:
    result = normalize_decision(decision(), source_hash="a" * 64, record_index=1)
    assert result.action == "STRONG_SELL"
    assert result.known_at is None
    assert result.provenance_quality == "LEGACY_ESTIMATED"
    assert result.as_of.tzinfo is UTC
    assert [event.code for event in result.audit_events] == ["NORMALIZED_ACTION_ALIAS"]


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"confidence": "0.5"}, "INVALID_CONFIDENCE"),
        ({"confidence": 1.5}, "INVALID_CONFIDENCE"),
        ({"ticker": "UNKNOWN"}, "UNKNOWN_ASSET"),
        ({"suggestion": "MOON"}, "INVALID_ENUM"),
        ({"ticker": ""}, "MISSING_REQUIRED_FIELD"),
        ({"stored_at": "2026-06-25T04:54:37"}, "SCHEMA_VALIDATION_FAILED"),
    ],
)
def test_invalid_legacy_values_are_not_silently_coerced(
    overrides: dict[str, object], code: str
) -> None:
    with pytest.raises(MigrationValidationError) as captured:
        normalize_decision(decision(**overrides), source_hash="a" * 64, record_index=1)
    assert captured.value.code == code
