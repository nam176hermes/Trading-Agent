from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from packages.domain import Currency, InstrumentId, Money, Price, ProductType, Quantity
from packages.domain.portfolio import (
    AccountBalanceSnapshot,
    AccountPositionSnapshot,
    PositionMark,
)


NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
INSTRUMENT = InstrumentId("BTC-USD", ProductType.CRYPTO_SPOT, "ALPACA")


def money(amount: str, currency: Currency = Currency.USD) -> Money:
    return Money(Decimal(amount), currency)


def account_balance(**changes: object) -> AccountBalanceSnapshot:
    values: dict[str, object] = {
        "account_id": "account-1",
        "currency": Currency.USD,
        "cash": money("100"),
        "locked_funds": money("3"),
        "margin_used": money("2"),
        "realized_pnl": money("4"),
        "unrealized_pnl": money("5"),
        "fees": money("1"),
        "funding": money("0"),
        "observed_at": NOW,
        "schema_version": "account-balance-v1",
    }
    values.update(changes)
    return AccountBalanceSnapshot(**values)


def position_mark(**changes: object) -> PositionMark:
    values: dict[str, object] = {
        "price": Price(Decimal("100"), Currency.USD),
        "marked_at": NOW,
        "provenance_id": "venue-mark-1",
    }
    values.update(changes)
    return PositionMark(**values)


def future_mark() -> PositionMark:
    return position_mark(marked_at=NOW + timedelta(seconds=1))


def account_position(**changes: object) -> AccountPositionSnapshot:
    values: dict[str, object] = {
        "account_id": "account-1",
        "strategy_id": "strategy-1",
        "instrument": INSTRUMENT,
        "settlement_currency": Currency.USD,
        "quantity": Quantity(Decimal("1"), 0),
        "mark": position_mark(),
        "realized_pnl": money("4"),
        "unrealized_pnl": money("5"),
        "fees": money("1"),
        "funding": money("0"),
        "observed_at": NOW,
        "schema_version": "account-position-v1",
    }
    values.update(changes)
    return AccountPositionSnapshot(**values)


def test_account_balance_requires_one_currency_and_nonnegative_locked_margin() -> None:
    balance = account_balance()

    assert balance.locked_funds.amount == Decimal("3")
    with pytest.raises(ValidationError, match="currency"):
        account_balance(fees=Money(Decimal("1"), Currency.USDT))
    with pytest.raises(ValidationError, match="locked_funds"):
        account_balance(locked_funds=Money(Decimal("-1"), Currency.USD))
    with pytest.raises(ValidationError, match="margin_used"):
        account_balance(margin_used=Money(Decimal("-1"), Currency.USD))


def test_nonzero_position_requires_current_provenance_mark() -> None:
    position = account_position()

    assert position.mark is not None
    with pytest.raises(ValidationError, match="non-zero position"):
        account_position(mark=None)
    with pytest.raises(ValidationError, match="mark timestamp"):
        account_position(mark=future_mark())


def test_position_allows_zero_quantity_without_a_mark() -> None:
    position = account_position(quantity=Quantity(Decimal("0"), 0), mark=None)

    assert position.mark is None


def test_account_position_requires_settlement_currency_for_money_and_mark() -> None:
    with pytest.raises(ValidationError, match="settlement currency"):
        account_position(settlement_currency=Currency.USDT)
    with pytest.raises(ValidationError, match="settlement currency"):
        account_position(
            mark=position_mark(price=Price(Decimal("100"), Currency.USDT))
        )


@pytest.mark.parametrize(
    ("factory", "changes"),
    [
        (account_balance, {"observed_at": datetime(2026, 8, 9, 12, 0)}),
        (position_mark, {"marked_at": datetime(2026, 8, 9, 12, 0)}),
        (account_position, {"observed_at": datetime(2026, 8, 9, 12, 0)}),
    ],
)
def test_account_portfolio_contracts_require_utc_timestamps(
    factory: object, changes: dict[str, object]
) -> None:
    with pytest.raises(ValidationError, match="UTC"):
        factory(**changes)  # type: ignore[operator]


@pytest.mark.parametrize(
    ("factory", "changes"),
    [
        (account_balance, {"account_id": "invalid account"}),
        (position_mark, {"provenance_id": "invalid provenance"}),
        (account_position, {"strategy_id": "invalid strategy"}),
    ],
)
def test_account_portfolio_contracts_require_canonical_identifiers(
    factory: object, changes: dict[str, object]
) -> None:
    with pytest.raises(ValidationError):
        factory(**changes)  # type: ignore[operator]


def test_account_portfolio_contracts_forbid_extra_fields_and_are_frozen() -> None:
    balance = account_balance()

    with pytest.raises(ValidationError, match="extra_forbidden"):
        account_balance(unexpected="value")
    with pytest.raises(ValidationError, match="frozen"):
        balance.account_id = "account-2"  # type: ignore[misc]
