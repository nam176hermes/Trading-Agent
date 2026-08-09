from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from packages.domain import (
    AccountBalanceSnapshot as PublicAccountBalanceSnapshot,
    AccountPortfolioSnapshot as PublicAccountPortfolioSnapshot,
    AccountPositionSnapshot as PublicAccountPositionSnapshot,
    Currency,
    ExposureSnapshot as PublicExposureSnapshot,
    InstrumentExposureSnapshot as PublicInstrumentExposureSnapshot,
    InstrumentId,
    Money,
    PositionMark as PublicPositionMark,
    Price,
    ProductType,
    Quantity,
    StrategyExposureSnapshot as PublicStrategyExposureSnapshot,
    VenueExposureSnapshot as PublicVenueExposureSnapshot,
)
from packages.domain.portfolio import (
    AccountBalanceSnapshot,
    AccountPortfolioSnapshot,
    AccountPositionSnapshot,
    ExposureSnapshot,
    InstrumentExposureSnapshot,
    PositionMark,
    StrategyExposureSnapshot,
    VenueExposureSnapshot,
)


NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
INSTRUMENT = InstrumentId("BTC-USD", ProductType.CRYPTO_SPOT, "ALPACA")
SECOND_INSTRUMENT = InstrumentId(
    "ETH-USDT", ProductType.CRYPTO_SPOT, "BINANCE"
)


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


def balance_in(currency: Currency, **changes: object) -> AccountBalanceSnapshot:
    values: dict[str, object] = {
        "currency": currency,
        "cash": money("100", currency),
        "locked_funds": money("3", currency),
        "margin_used": money("2", currency),
        "realized_pnl": money("4", currency),
        "unrealized_pnl": money("5", currency),
        "fees": money("1", currency),
        "funding": money("0", currency),
    }
    values.update(changes)
    return account_balance(**values)


def position_for(
    strategy_id: str,
    instrument: InstrumentId,
    currency: Currency,
    **changes: object,
) -> AccountPositionSnapshot:
    values: dict[str, object] = {
        "strategy_id": strategy_id,
        "instrument": instrument,
        "settlement_currency": currency,
        "mark": position_mark(price=Price(Decimal("100"), currency)),
        "realized_pnl": money("4", currency),
        "unrealized_pnl": money("5", currency),
        "fees": money("1", currency),
        "funding": money("0", currency),
    }
    values.update(changes)
    return account_position(**values)


def exposure(
    currency: Currency = Currency.USD, **changes: object
) -> ExposureSnapshot:
    values: dict[str, object] = {
        "currency": currency,
        "gross": money("2", currency),
        "net": money("1", currency),
        "pending": money("0", currency),
    }
    values.update(changes)
    return ExposureSnapshot(**values)


def account_portfolio(**changes: object) -> AccountPortfolioSnapshot:
    values: dict[str, object] = {
        "snapshot_id": UUID("11111111-1111-4111-8111-111111111111"),
        "account_id": "account-1",
        "reporting_currency": Currency.USD,
        "balances": (balance_in(Currency.USD), balance_in(Currency.USDT)),
        "positions": (
            position_for("strategy-1", INSTRUMENT, Currency.USD),
            position_for(
                "strategy-2", SECOND_INSTRUMENT, Currency.USDT
            ),
        ),
        "total_exposure": exposure(),
        "instrument_exposures": (
            InstrumentExposureSnapshot(
                instrument=INSTRUMENT, exposure=exposure()
            ),
            InstrumentExposureSnapshot(
                instrument=SECOND_INSTRUMENT, exposure=exposure()
            ),
        ),
        "strategy_exposures": (
            StrategyExposureSnapshot(
                strategy_id="strategy-1", exposure=exposure()
            ),
            StrategyExposureSnapshot(
                strategy_id="strategy-2", exposure=exposure()
            ),
        ),
        "venue_exposures": (
            VenueExposureSnapshot(venue_id="ALPACA", exposure=exposure()),
            VenueExposureSnapshot(venue_id="BINANCE", exposure=exposure()),
        ),
        "observed_at": NOW,
        "schema_version": "account-portfolio-v1",
    }
    values.update(changes)
    return AccountPortfolioSnapshot(**values)


def test_account_portfolio_contracts_are_public_domain_exports() -> None:
    assert PublicAccountBalanceSnapshot is AccountBalanceSnapshot
    assert PublicPositionMark is PositionMark
    assert PublicAccountPositionSnapshot is AccountPositionSnapshot
    assert PublicExposureSnapshot is ExposureSnapshot
    assert PublicInstrumentExposureSnapshot is InstrumentExposureSnapshot
    assert PublicStrategyExposureSnapshot is StrategyExposureSnapshot
    assert PublicVenueExposureSnapshot is VenueExposureSnapshot
    assert PublicAccountPortfolioSnapshot is AccountPortfolioSnapshot


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


def test_account_portfolio_aggregate_requires_utc_and_canonical_account_id() -> None:
    with pytest.raises(ValidationError, match="UTC"):
        account_portfolio(observed_at=datetime(2026, 8, 9, 12, 0))
    with pytest.raises(ValidationError):
        account_portfolio(account_id="invalid account")


def test_account_portfolio_wrappers_require_canonical_identifiers() -> None:
    with pytest.raises(ValidationError):
        StrategyExposureSnapshot(
            strategy_id="invalid strategy", exposure=exposure()
        )
    with pytest.raises(ValidationError):
        VenueExposureSnapshot(venue_id="invalid venue", exposure=exposure())


def test_account_portfolio_aggregate_and_wrappers_are_strict_and_frozen() -> None:
    models = (
        exposure(),
        InstrumentExposureSnapshot(instrument=INSTRUMENT, exposure=exposure()),
        StrategyExposureSnapshot(strategy_id="strategy-1", exposure=exposure()),
        VenueExposureSnapshot(venue_id="ALPACA", exposure=exposure()),
        account_portfolio(),
    )

    for model in models:
        with pytest.raises(ValidationError, match="extra_forbidden"):
            type(model).model_validate({**model.model_dump(), "unexpected": "value"})
        field = next(iter(type(model).model_fields))
        with pytest.raises(ValidationError, match="frozen"):
            setattr(model, field, getattr(model, field))


def test_account_portfolio_requires_canonical_unique_ordered_members() -> None:
    snapshot = account_portfolio()

    assert (
        AccountPortfolioSnapshot.model_validate_json(snapshot.model_dump_json())
        == snapshot
    )
    with pytest.raises(ValidationError, match="balances must be ordered"):
        account_portfolio(balances=tuple(reversed(snapshot.balances)))
    with pytest.raises(ValidationError, match="duplicate position"):
        account_portfolio(positions=snapshot.positions * 2)


def test_exposure_rejects_cross_currency_negative_and_impossible_net() -> None:
    with pytest.raises(ValidationError, match="currency"):
        exposure(net=Money(Decimal("1"), Currency.USDT))
    with pytest.raises(ValidationError, match="pending"):
        ExposureSnapshot(
            currency=Currency.USD,
            gross=Money(Decimal("2"), Currency.USD),
            net=Money(Decimal("1"), Currency.USD),
            pending=Money(Decimal("-1"), Currency.USD),
        )
    with pytest.raises(ValidationError, match="gross"):
        ExposureSnapshot(
            currency=Currency.USD,
            gross=Money(Decimal("1"), Currency.USD),
            net=Money(Decimal("2"), Currency.USD),
            pending=Money(Decimal("0"), Currency.USD),
        )


def test_account_portfolio_rejects_duplicate_balance_and_position_keys() -> None:
    snapshot = account_portfolio()

    with pytest.raises(ValidationError, match="duplicate balance currency"):
        account_portfolio(balances=(snapshot.balances[0], snapshot.balances[0]))
    with pytest.raises(ValidationError, match="duplicate position"):
        account_portfolio(positions=(snapshot.positions[0], snapshot.positions[0]))


@pytest.mark.parametrize(
    ("field", "duplicate", "message"),
    [
        (
            "instrument_exposures",
            lambda snapshot: snapshot.instrument_exposures[:1] * 2,
            "duplicate instrument exposure",
        ),
        (
            "strategy_exposures",
            lambda snapshot: snapshot.strategy_exposures[:1] * 2,
            "duplicate strategy exposure",
        ),
        (
            "venue_exposures",
            lambda snapshot: snapshot.venue_exposures[:1] * 2,
            "duplicate venue exposure",
        ),
    ],
)
def test_account_portfolio_rejects_duplicate_partition_keys(
    field: str, duplicate: object, message: str
) -> None:
    snapshot = account_portfolio()

    with pytest.raises(ValidationError, match=message):
        account_portfolio(**{field: duplicate(snapshot)})  # type: ignore[operator]


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("positions", "positions must be ordered"),
        ("instrument_exposures", "instrument_exposures must be ordered"),
        ("strategy_exposures", "strategy_exposures must be ordered"),
        ("venue_exposures", "venue_exposures must be ordered"),
    ],
)
def test_account_portfolio_rejects_noncanonical_partition_order(
    field: str, message: str
) -> None:
    snapshot = account_portfolio()
    members = getattr(snapshot, field)

    with pytest.raises(ValidationError, match=message):
        account_portfolio(**{field: tuple(reversed(members))})


@pytest.mark.parametrize(
    ("field", "members", "message"),
    [
        (
            "balances",
            lambda snapshot: (
                snapshot.balances[0],
                balance_in(Currency.USDT, observed_at=NOW + timedelta(seconds=1)),
            ),
            "balance timestamp",
        ),
        (
            "positions",
            lambda snapshot: (
                snapshot.positions[0],
                position_for(
                    "strategy-2",
                    SECOND_INSTRUMENT,
                    Currency.USDT,
                    observed_at=NOW + timedelta(seconds=1),
                    mark=position_mark(
                        price=Price(Decimal("100"), Currency.USDT),
                        marked_at=NOW + timedelta(seconds=1),
                    ),
                ),
            ),
            "position timestamp",
        ),
    ],
)
def test_account_portfolio_rejects_child_timestamps_after_observation(
    field: str, members: object, message: str
) -> None:
    snapshot = account_portfolio()

    with pytest.raises(ValidationError, match=message):
        account_portfolio(**{field: members(snapshot)})  # type: ignore[operator]


@pytest.mark.parametrize(
    ("field", "members", "message"),
    [
        (
            "balances",
            lambda snapshot: (
                balance_in(Currency.USD, account_id="account-2"),
                snapshot.balances[1],
            ),
            "balance account",
        ),
        (
            "positions",
            lambda snapshot: (
                position_for(
                    "strategy-1",
                    INSTRUMENT,
                    Currency.USD,
                    account_id="account-2",
                ),
                snapshot.positions[1],
            ),
            "position account",
        ),
    ],
)
def test_account_portfolio_rejects_cross_account_members(
    field: str, members: object, message: str
) -> None:
    snapshot = account_portfolio()

    with pytest.raises(ValidationError, match=message):
        account_portfolio(**{field: members(snapshot)})  # type: ignore[operator]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("total_exposure", lambda: exposure(Currency.USDT), "total exposure currency"),
        (
            "instrument_exposures",
            lambda: (
                InstrumentExposureSnapshot(
                    instrument=INSTRUMENT, exposure=exposure(Currency.USDT)
                ),
            ),
            "instrument exposure currency",
        ),
        (
            "strategy_exposures",
            lambda: (
                StrategyExposureSnapshot(
                    strategy_id="strategy-1", exposure=exposure(Currency.USDT)
                ),
            ),
            "strategy exposure currency",
        ),
        (
            "venue_exposures",
            lambda: (
                VenueExposureSnapshot(
                    venue_id="ALPACA", exposure=exposure(Currency.USDT)
                ),
            ),
            "venue exposure currency",
        ),
    ],
)
def test_account_portfolio_requires_reporting_currency_exposures(
    field: str, value: object, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        account_portfolio(**{field: value()})  # type: ignore[operator]


@pytest.mark.parametrize(
    "field",
    [
        "balances",
        "positions",
        "instrument_exposures",
        "strategy_exposures",
        "venue_exposures",
    ],
)
def test_account_portfolio_sequences_accept_only_tuples(field: str) -> None:
    snapshot = account_portfolio()

    with pytest.raises(ValidationError):
        account_portfolio(**{field: list(getattr(snapshot, field))})
