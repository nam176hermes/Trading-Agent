from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from packages.domain import (
    AccountBalanceSnapshot,
    AccountPortfolioSnapshot,
    AssetClass,
    Currency,
    CurrencyConversion,
    EventEnvelope,
    ExposureSnapshot,
    FillEvent,
    FillReportStatus,
    InstrumentDefinition,
    InstrumentId,
    InstrumentProvenance,
    LiquiditySide,
    Money,
    OrderQuantity,
    OrderSide,
    PortfolioConversionEntry,
    PortfolioFillEntry,
    PortfolioFundingEntry,
    PortfolioMarkEntry,
    PortfolioOpeningEntry,
    PortfolioReconciliationEntry,
    PortfolioReconciliationSource,
    PortfolioValuationRateEntry,
    PositionMark,
    Price,
    ProductType,
    ReconciliationSource,
)
from packages.event_ledger import deserialize_event, serialize_event


NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
INSTRUMENT = InstrumentId("BTC-USD", ProductType.CRYPTO_SPOT, "ALPACA")


def money(amount: str, currency: Currency = Currency.USD) -> Money:
    return Money(Decimal(amount), currency)


def balance(currency: Currency = Currency.USD, **changes: object) -> AccountBalanceSnapshot:
    values: dict[str, object] = dict(
        account_id="account-1", currency=currency, cash=money("100", currency),
        locked_funds=money("0", currency), margin_used=money("0", currency),
        realized_pnl=money("0", currency), unrealized_pnl=money("0", currency),
        fees=money("0", currency), funding=money("0", currency), observed_at=NOW,
        schema_version="balance-v1",
    )
    values.update(changes)
    return AccountBalanceSnapshot(**values)


def opening(**changes: object) -> PortfolioOpeningEntry:
    values: dict[str, object] = {
        "account_id": "account-1", "reporting_currency": Currency.USD,
        "balances": (balance(Currency.USD), balance(Currency.USDT)),
        "source_id": "opening-source", "source_revision": "revision-1",
        "effective_at": NOW, "schema_version": "portfolio-entry-v1",
    }
    values.update(changes)
    return PortfolioOpeningEntry(**values)


def definition() -> InstrumentDefinition:
    return InstrumentDefinition(
        instrument_id=INSTRUMENT, raw_symbol="BTCUSD", asset_class=AssetClass.CRYPTO,
        base_currency=Currency.BTC, quote_currency=Currency.USD,
        settlement_currency=Currency.USD, tick_size=Price(Decimal("0.01"), Currency.USD),
        size_increment=OrderQuantity(Decimal("0.01"), 2),
        minimum_quantity=OrderQuantity(Decimal("0.01"), 2),
        maximum_quantity=OrderQuantity(Decimal("100"), 2),
        minimum_notional=money("1"), maximum_notional=money("100000"),
        multiplier=Decimal("1"), margin=None, session_calendar="24X7",
        provenance=InstrumentProvenance(source_id="catalog", source_revision="r1", observed_at=NOW),
    )


def fill() -> FillEvent:
    return FillEvent(
        execution_id=uuid4(), order_id=uuid4(), report_sequence=1, venue_trade_id="trade-1",
        instrument_definition=definition(), side=OrderSide.BUY, liquidity_side=LiquiditySide.MAKER,
        status=FillReportStatus.FILLED, quantity=OrderQuantity(Decimal("1"), 2),
        cumulative_fill_quantity=OrderQuantity(Decimal("1"), 2), leaves_quantity=OrderQuantity(Decimal("0"), 2),
        order_quantity=OrderQuantity(Decimal("1"), 2), last_fill_price=Price(Decimal("100"), Currency.USD),
        average_fill_price=Price(Decimal("100"), Currency.USD), commission=money("0.01"),
        reconciliation_source=ReconciliationSource.VENUE, filled_at=NOW, schema_version="2.0",
    )


def fill_entry(**changes: object) -> PortfolioFillEntry:
    values: dict[str, object] = {
        "account_id": "account-1", "strategy_id": "strategy-1", "fill": fill(),
        "effective_at": NOW, "schema_version": "portfolio-entry-v1",
    }
    values.update(changes)
    return PortfolioFillEntry(**values)


def mark() -> PositionMark:
    return PositionMark(price=Price(Decimal("100"), Currency.USD), marked_at=NOW, provenance_id="mark-source")


def snapshot(**changes: object) -> AccountPortfolioSnapshot:
    values: dict[str, object] = {
        "snapshot_id": uuid4(), "account_id": "account-1", "reporting_currency": Currency.USD,
        "balances": (balance(),), "positions": (),
        "total_exposure": ExposureSnapshot(currency=Currency.USD, gross=money("0"), net=money("0"), pending=money("0")),
        "instrument_exposures": (), "strategy_exposures": (), "venue_exposures": (),
        "observed_at": NOW, "schema_version": "snapshot-v1",
    }
    values.update(changes)
    return AccountPortfolioSnapshot(**values)


def envelope(payload: object) -> EventEnvelope[object]:
    return EventEnvelope[object](
        event_id=uuid4(), event_type=type(payload).__name__, schema_version="1.0", source="domain-test",
        stream_id=uuid4(), sequence=1, observed_at=NOW, ingested_at=NOW + timedelta(seconds=1),
        produced_at=NOW + timedelta(seconds=2), effective_at=NOW + timedelta(seconds=2),
        expires_at=NOW + timedelta(minutes=5), correlation_id=uuid4(), causation_id=uuid4(), trace_id=uuid4(), payload=payload,
    )


def test_portfolio_opening_requires_ordered_balances_and_nonempty_provenance() -> None:
    assert opening().balances[0].currency is Currency.USD
    with pytest.raises(ValidationError, match="opening balances must be ordered by currency"):
        opening(balances=(balance(Currency.USDT), balance(Currency.USD)))
    with pytest.raises(ValidationError, match="String should have at least 1 character"):
        opening(source_id="")
    with pytest.raises(ValidationError, match="String should have at least 1 character"):
        opening(source_revision="")


def test_portfolio_fill_entry_is_registered_and_requires_an_exact_fill() -> None:
    entry = fill_entry()
    event = envelope(entry)
    assert event.event_type == "PortfolioFillEntry"
    assert deserialize_event(serialize_event(event)) == event
    assert type(entry.fill) is FillEvent


def test_portfolio_mark_requires_matching_mark_time_and_utc_effective_time() -> None:
    entry = PortfolioMarkEntry(account_id="account-1", instrument=INSTRUMENT, mark=mark(), marked_at=NOW, effective_at=NOW, schema_version="portfolio-entry-v1")
    assert entry.marked_at == entry.mark.marked_at
    with pytest.raises(ValidationError, match="marked_at must match mark.marked_at"):
        PortfolioMarkEntry(account_id="account-1", instrument=INSTRUMENT, mark=mark(), marked_at=NOW + timedelta(seconds=1), effective_at=NOW, schema_version="portfolio-entry-v1")
    with pytest.raises(ValidationError, match="UTC"):
        PortfolioMarkEntry(account_id="account-1", instrument=INSTRUMENT, mark=mark(), marked_at=NOW, effective_at=datetime(2026, 8, 9, 12, 0), schema_version="portfolio-entry-v1")


def test_portfolio_funding_requires_a_complete_optional_position_key() -> None:
    entry = PortfolioFundingEntry(funding_id=uuid4(), account_id="account-1", strategy_id=None, instrument=None, amount=money("1"), provenance_id="funding-source", effective_at=NOW, schema_version="portfolio-entry-v1")
    assert entry.strategy_id is None
    with pytest.raises(ValidationError, match="funding position key must provide strategy_id and instrument together"):
        PortfolioFundingEntry(funding_id=uuid4(), account_id="account-1", strategy_id="strategy-1", instrument=None, amount=money("1"), provenance_id="funding-source", effective_at=NOW, schema_version="portfolio-entry-v1")


def test_portfolio_conversion_and_valuation_require_exact_positive_cross_currency_data() -> None:
    conversion = CurrencyConversion(source=money("10"), target_currency=Currency.USDT, rate=Decimal("1"), target=money("10", Currency.USDT))
    assert PortfolioConversionEntry(account_id="account-1", conversion=conversion, provenance_id="conversion-source", effective_at=NOW, schema_version="portfolio-entry-v1").conversion == conversion
    with pytest.raises(ValidationError, match="valuation rate must be positive"):
        PortfolioValuationRateEntry(account_id="account-1", source_currency=Currency.USD, target_currency=Currency.USDT, rate=Decimal("0"), quoted_at=NOW, provenance_id="valuation-source", effective_at=NOW, schema_version="portfolio-entry-v1")
    with pytest.raises(ValidationError, match="valuation rate currencies must differ"):
        PortfolioValuationRateEntry(account_id="account-1", source_currency=Currency.USD, target_currency=Currency.USD, rate=Decimal("1"), quoted_at=NOW, provenance_id="valuation-source", effective_at=NOW, schema_version="portfolio-entry-v1")
    with pytest.raises(ValidationError, match="UTC"):
        PortfolioValuationRateEntry(account_id="account-1", source_currency=Currency.USD, target_currency=Currency.USDT, rate=Decimal("1"), quoted_at=datetime(2026, 8, 9, 12, 0), provenance_id="valuation-source", effective_at=NOW, schema_version="portfolio-entry-v1")


def test_portfolio_reconciliation_requires_matching_account_and_nonempty_source_revision() -> None:
    entry = PortfolioReconciliationEntry(reconciliation_id=uuid4(), account_id="account-1", source=PortfolioReconciliationSource.VENUE, source_revision="revision-1", snapshot=snapshot(), effective_at=NOW, schema_version="portfolio-entry-v1")
    assert entry.snapshot.account_id == entry.account_id
    with pytest.raises(ValidationError, match="reconciliation snapshot account must match entry account"):
        PortfolioReconciliationEntry(reconciliation_id=uuid4(), account_id="account-1", source=PortfolioReconciliationSource.VENUE, source_revision="revision-1", snapshot=snapshot(account_id="account-2", balances=(balance(account_id="account-2"),)), effective_at=NOW, schema_version="portfolio-entry-v1")
    with pytest.raises(ValidationError, match="String should have at least 1 character"):
        PortfolioReconciliationEntry(reconciliation_id=uuid4(), account_id="account-1", source=PortfolioReconciliationSource.VENUE, source_revision="", snapshot=snapshot(), effective_at=NOW, schema_version="portfolio-entry-v1")
