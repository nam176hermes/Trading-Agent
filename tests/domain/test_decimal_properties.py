from __future__ import annotations

import json
from decimal import Decimal

import pytest
from hypothesis import given, settings, strategies as st
from pydantic import TypeAdapter, ValidationError

from packages.domain import Currency, Money, Price, TargetPosition
from packages.domain.primitives import (
    CANONICAL_DECIMAL_POLICY_VERSION,
    FiniteDecimal,
    Quantity,
)
from packages.event_ledger import deserialize_event, serialize_event
from packages.event_ledger.replay import event_digest
from tests.event_ledger.test_reducer import INSTRUMENT, envelope, signal


FINITE_DECIMAL_ADAPTER = TypeAdapter(FiniteDecimal)


@st.composite
def equivalent_decimal_pairs(draw: st.DrawFn) -> tuple[Decimal, Decimal]:
    coefficient = draw(st.integers(min_value=-(10**36), max_value=10**36))
    places = draw(st.integers(min_value=0, max_value=18))
    trailing_zeros = draw(st.integers(min_value=1, max_value=8))
    value = Decimal(coefficient).scaleb(-places)
    plain = format(value, "f")
    equivalent = Decimal(
        plain + ("" if "." in plain else ".") + ("0" * trailing_zeros)
    )
    return value, equivalent


@st.composite
def equivalent_unit_interval_pairs(draw: st.DrawFn) -> tuple[Decimal, Decimal]:
    coefficient = draw(st.integers(min_value=0, max_value=10**18))
    value = Decimal(coefficient).scaleb(-18)
    plain = format(value, "f")
    equivalent = Decimal(plain + ("" if "." in plain else ".") + "000")
    return value, equivalent


def _canonical_json(value: Decimal) -> bytes:
    validated = FINITE_DECIMAL_ADAPTER.validate_python(value)
    return FINITE_DECIMAL_ADAPTER.dump_json(validated)


@given(equivalent_decimal_pairs())  # type: ignore[call-arg]
@settings(max_examples=150, deadline=None, database=None)
def test_equal_decimals_have_identical_canonical_json_bytes(
    pair: tuple[Decimal, Decimal],
) -> None:
    value, equivalent = pair

    assert value == equivalent
    canonical = _canonical_json(value)
    assert canonical == _canonical_json(equivalent)
    assert FINITE_DECIMAL_ADAPTER.validate_json(canonical) == value


@given(equivalent_decimal_pairs())  # type: ignore[call-arg]
@settings(max_examples=100, deadline=None, database=None)
def test_money_price_quantity_and_weight_share_decimal_v1_bytes(
    pair: tuple[Decimal, Decimal],
) -> None:
    value, equivalent = pair
    positive = abs(value) or Decimal("1")
    positive_text = format(positive, "f")
    positive_equivalent = Decimal(
        positive_text + ("0" if "." in positive_text else ".0")
    )
    whole_money = Decimal(int(value))
    equivalent_whole_money = Decimal(f"{int(value)}.0")

    equivalent_models = (
        (
            Money(whole_money, Currency.USD),
            Money(equivalent_whole_money, Currency.USD),
        ),
        (Price(positive, Currency.USDT), Price(positive_equivalent, Currency.USDT)),
        (Quantity(value, 18), Quantity(equivalent, 18)),
        (
            TargetPosition(instrument=INSTRUMENT, target_weight=value),
            TargetPosition(instrument=INSTRUMENT, target_weight=equivalent),
        ),
    )
    for original, variant in equivalent_models:
        adapter = TypeAdapter(type(original))
        original_json = adapter.dump_json(original)
        variant_json = adapter.dump_json(variant)
        assert original == variant
        assert original_json == variant_json
        assert adapter.validate_json(original_json) == original


def test_quantity_accepts_equivalent_trailing_zero_scale() -> None:
    assert Quantity(Decimal("1.230000"), 2).value.as_tuple().exponent == -2


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("0"), b'"0"'),
        (Decimal("-0"), b'"0"'),
        (Decimal("0.0000"), b'"0"'),
        (Decimal("-0.0000"), b'"0"'),
        (Decimal("0.2500"), b'"0.25"'),
        (Decimal("-10.5000"), b'"-10.5"'),
        (Decimal("1E+3"), b'"1000"'),
        (Decimal("1E-20"), b'"0.00000000000000000001"'),
    ],
)
def test_decimal_policy_uses_one_plain_canonical_spelling(
    value: Decimal, expected: bytes
) -> None:
    assert _canonical_json(value) == expected


@pytest.mark.parametrize(
    "raw",
    [b'"-0"', b'"-0.0"', b'"0.0"', b'"0.250"', b'"1.0"', b'"1E-1"'],
)
def test_json_input_rejects_noncanonical_decimal_spellings(raw: bytes) -> None:
    with pytest.raises(ValidationError, match="canonical Decimal string"):
        FINITE_DECIMAL_ADAPTER.validate_json(raw)


def test_decimal_policy_is_explicitly_versioned() -> None:
    assert CANONICAL_DECIMAL_POLICY_VERSION == "decimal-v1"
    schema = FINITE_DECIMAL_ADAPTER.json_schema(mode="validation")
    assert schema["x-canonical-decimal-policy"] == CANONICAL_DECIMAL_POLICY_VERSION


@given(
    coefficient=st.integers(min_value=-(10**24), max_value=10**24),
    precision=st.integers(min_value=0, max_value=18),
)
@settings(max_examples=150, deadline=None, database=None)
def test_quantity_normalizes_value_to_declared_precision(
    coefficient: int, precision: int
) -> None:
    value = Decimal(coefficient).scaleb(-precision)
    quantity = Quantity(value=value, precision=precision)

    assert quantity.value == value
    assert quantity.value.as_tuple().exponent == -precision
    if quantity.value.is_zero():
        assert quantity.value.as_tuple().sign == 0


@given(equivalent_unit_interval_pairs())  # type: ignore[call-arg]
@settings(max_examples=75, deadline=None, database=None)
def test_equivalent_signal_decimals_have_identical_event_bytes_digest_and_round_trip(
    pair: tuple[Decimal, Decimal],
) -> None:
    bounded, bounded_equivalent = pair
    original_signal = signal().model_copy(
        update={"score": bounded, "confidence": bounded}
    )
    equivalent_signal = signal().model_copy(
        update={"score": bounded_equivalent, "confidence": bounded_equivalent}
    )
    original_event = envelope(original_signal, event_number=501)
    equivalent_event = original_event.model_copy(update={"payload": equivalent_signal})

    assert original_event == equivalent_event
    original_bytes = serialize_event(original_event)
    equivalent_bytes = serialize_event(equivalent_event)
    assert original_bytes == equivalent_bytes
    assert event_digest(original_bytes) == event_digest(equivalent_bytes)
    assert deserialize_event(original_bytes) == original_event
    assert json.loads(original_bytes)["payload"]["score"] == format(
        bounded.normalize() if bounded else Decimal(0), "f"
    )
