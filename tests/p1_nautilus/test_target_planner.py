from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal
from pathlib import Path
import sys

from hypothesis import given, strategies as st
import pytest


ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "engines/nautilus"))

from runtime_v1.target_planner import (  # noqa: E402
    TargetPlanError,
    plan_target,
)


D = Decimal


def plan(**updates: object):
    values: dict[str, object] = {
        "target_id": "11111111-1111-4111-8111-111111111111",
        "source_signal_ids": ("22222222-2222-4222-8222-222222222222",),
        "effective_at": "2026-08-05T12:00:00Z",
        "target_instrument_id": "BTCUSDT.BINANCE",
        "instrument_id": "BTCUSDT.BINANCE",
        "target_weight": D("0.5"),
        "account_equity": D("1000"),
        "available_cash": D("1000"),
        "current_quantity": D("0"),
        "ask_price": D("100"),
        "fee_rate": D("0.001"),
        "step_size": D("0.001"),
        "min_quantity": D("0.001"),
        "min_notional": D("10"),
        "leverage": D("1"),
    }
    values.update(updates)
    return plan_target(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("updates", "expected"),
    (
        ({}, ("BUY", "5", "5", "500", "ORDER")),
        (
            {"current_quantity": D("2"), "target_weight": D("0.8")},
            ("BUY", "8", "6", "600", "ORDER"),
        ),
        (
            {"current_quantity": D("8")},
            ("SELL", "5", "-3", "300", "ORDER"),
        ),
        (
            {"current_quantity": D("2"), "target_weight": D("0")},
            ("SELL", "0", "-2", "200", "ORDER"),
        ),
        (
            {"current_quantity": D("5")},
            (None, "5", "0", "0", "ALREADY_AT_TARGET"),
        ),
        (
            {"available_cash": D("0.05")},
            (None, "0", "0", "0", "INSUFFICIENT_CASH"),
        ),
        (
            {"available_cash": D("500")},
            ("BUY", "4.995", "4.995", "499.5", "ORDER"),
        ),
        (
            {"account_equity": D("5"), "available_cash": D("5"), "target_weight": D("1")},
            (None, "0", "0", "0", "BELOW_MIN_NOTIONAL"),
        ),
        (
            {"account_equity": D("100"), "ask_price": D("30"), "target_weight": D("1")},
            ("BUY", "3.333", "3.333", "99.99", "ORDER"),
        ),
    ),
)
def test_long_flat_planning_table(
    updates: dict[str, object], expected: tuple[str | None, str, str, str, str]
) -> None:
    result = plan(**updates)

    assert (
        result.side,
        result.target_quantity,
        result.delta,
        result.notional,
        result.reason,
    ) == expected
    assert result.target_id == "11111111-1111-4111-8111-111111111111"
    assert result.source_signal_ids == ("22222222-2222-4222-8222-222222222222",)
    assert result.effective_at == "2026-08-05T12:00:00Z"
    assert result.current_quantity == str(updates.get("current_quantity", D("0")))
    assert result.price_basis == str(updates.get("ask_price", D("100")))
    with pytest.raises(FrozenInstanceError):
        result.reason = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("updates", "code"),
    (
        ({"target_weight": D("-0.1")}, "TARGET_WEIGHT_OUT_OF_RANGE"),
        ({"target_weight": D("1.1")}, "TARGET_WEIGHT_OUT_OF_RANGE"),
        ({"current_quantity": D("-1")}, "SHORT_POSITION"),
        ({"available_cash": D("-1")}, "NEGATIVE_BALANCE"),
        ({"account_equity": D("-1")}, "NEGATIVE_BALANCE"),
        ({"leverage": D("0")}, "UNSUPPORTED_LEVERAGE"),
        ({"leverage": D("-1")}, "UNSUPPORTED_LEVERAGE"),
        ({"leverage": D("2")}, "UNSUPPORTED_LEVERAGE"),
        ({"target_instrument_id": "ETHUSDT.BINANCE"}, "CROSS_INSTRUMENT"),
        ({"ask_price": D("0")}, "INVALID_PRICE"),
        ({"fee_rate": D("1")}, "INVALID_FEE_RATE"),
        ({"step_size": D("0.00000000000000001")}, "PRECISION_LIMIT"),
        ({"target_weight": 0.5}, "INVALID_DECIMAL"),
    ),
)
def test_rejects_unsafe_inputs_with_stable_codes(
    updates: dict[str, object], code: str
) -> None:
    with pytest.raises(TargetPlanError) as caught:
        plan(**updates)

    assert caught.value.code == code
    assert str(caught.value) == code


@given(
    equity=st.integers(min_value=0, max_value=10**9),
    cash=st.integers(min_value=0, max_value=10**9),
    weight=st.integers(min_value=0, max_value=10_000),
)
def test_buy_never_exceeds_target_notional_or_available_cash(
    equity: int, cash: int, weight: int
) -> None:
    target_weight = D(weight) / D(10_000)
    result = plan(
        account_equity=D(equity),
        available_cash=D(cash),
        target_weight=target_weight,
        min_notional=D("0.001"),
    )
    target_quantity = D(result.target_quantity)
    ask = D(result.price_basis)

    assert target_quantity * ask <= D(equity) * target_weight
    assert D(result.delta) * ask * D("1.001") <= D(cash)


def test_source_has_no_native_runtime_or_ambient_state_dependency() -> None:
    source = (
        ROOT / "engines/nautilus/runtime_v1/target_planner.py"
    ).read_text(encoding="utf-8")

    assert "nautilus_trader" not in source
    assert "os.environ" not in source
    assert "open(" not in source
    assert "float(" not in source
