"""Exact, side-effect-free reduction for portfolio opening and fill entries."""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from fractions import Fraction
from hashlib import sha256
import json
from typing import TypeAlias
from uuid import NAMESPACE_URL, UUID, uuid5

from packages.domain.events import EventEnvelope
from packages.domain.orders import FillEvent, FillReportStatus, OrderSide
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
from packages.domain.portfolio_events import (
    PortfolioAccountObservationEntry,
    PortfolioConversionEntry,
    PortfolioFillEntry,
    PortfolioFundingEntry,
    PortfolioMarkEntry,
    PortfolioOpeningEntry,
    PortfolioReconciliationEntry,
    PortfolioValuationRateEntry,
)
from packages.domain.primitives import Money, Price, Quantity

from .models import (
    PortfolioAppliedEvent,
    PortfolioBusinessIdentity,
    PortfolioExecutionEffect,
    PortfolioPositionState,
    PortfolioReplayError,
    PortfolioReplayState,
    PortfolioStreamCursor,
    PortfolioValuationRateState,
    PortfolioWorkingSnapshot,
)


PortfolioEvent: TypeAlias = EventEnvelope[object]


def _fraction(value: Decimal) -> Fraction:
    sign, digits, exponent = value.as_tuple()
    numerator = int("".join(str(digit) for digit in digits))
    if sign:
        numerator = -numerator
    if exponent >= 0:
        return Fraction(numerator * (10**exponent), 1)
    return Fraction(numerator, 10 ** (-exponent))


def _decimal(value: Fraction, *, field: str) -> Decimal:
    denominator = value.denominator
    twos = fives = 0
    while denominator % 2 == 0:
        denominator //= 2
        twos += 1
    while denominator % 5 == 0:
        denominator //= 5
        fives += 1
    if denominator != 1:
        raise PortfolioReplayError(f"{field} cannot be represented exactly as a Decimal")
    scale = max(twos, fives)
    numerator = value.numerator * (2 ** (scale - twos)) * (5 ** (scale - fives))
    sign = 1 if numerator < 0 else 0
    digits = tuple(int(character) for character in str(abs(numerator))) or (0,)
    return Decimal((sign, digits, -scale))


def _sum(*values: Decimal, field: str) -> Decimal:
    return _decimal(sum((_fraction(value) for value in values), Fraction(0)), field=field)


def _product(*values: Decimal, field: str) -> Decimal:
    result = Fraction(1)
    for value in values:
        result *= _fraction(value)
    return _decimal(result, field=field)


def _money(amount: Decimal, currency, *, field: str) -> Money:
    try:
        return Money(amount, currency)
    except ValueError as exc:
        raise PortfolioReplayError(f"{field} is not exact at currency precision") from exc


def _quantity(amount: Decimal, precision: int) -> Quantity:
    try:
        return Quantity(amount, precision)
    except ValueError as exc:
        raise PortfolioReplayError("position quantity is not exact") from exc


def _event_digest(event: PortfolioEvent) -> str:
    if not isinstance(event, EventEnvelope):
        raise PortfolioReplayError("event must be an EventEnvelope")
    try:
        canonical = json.dumps(
            event.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise PortfolioReplayError("event cannot be canonically represented") from exc
    return sha256(canonical.encode("utf-8")).hexdigest()


def _payload_digest(payload: object) -> str:
    try:
        document = payload.model_dump(mode="json")
        canonical = json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise PortfolioReplayError("portfolio payload cannot be canonically represented") from exc
    return sha256(canonical.encode("utf-8")).hexdigest()


def _identity_record(event: PortfolioEvent, identity_id) -> PortfolioBusinessIdentity:
    return PortfolioBusinessIdentity(
        identity_id=identity_id,
        payload_digest=_payload_digest(event.payload),
        event_id=event.event_id,
        event_digest=_event_digest(event),
        stream_id=event.stream_id,
        sequence=event.sequence,
    )


def _business_identity(payload: object) -> UUID | None:
    if type(payload) is PortfolioFillEntry:
        return payload.fill.execution_id
    if type(payload) is PortfolioFundingEntry:
        return payload.funding_id
    if type(payload) is PortfolioReconciliationEntry:
        return payload.reconciliation_id
    return None


def _updated_identity_tuple(
    identities: tuple[PortfolioBusinessIdentity, ...],
    record: PortfolioBusinessIdentity,
    *,
    label: str,
) -> tuple[PortfolioBusinessIdentity, ...]:
    existing = next(
        (item for item in identities if item.identity_id == record.identity_id), None
    )
    if existing is not None:
        if existing.payload_digest == record.payload_digest:
            raise PortfolioReplayError(f"duplicate {label} identity")
        raise PortfolioReplayError(f"conflicting {label} identity")
    return tuple(
        sorted((*identities, record), key=lambda item: item.identity_id.bytes)
    )


def _business_identities(
    state: PortfolioReplayState,
    event: PortfolioEvent,
) -> tuple[
    tuple[PortfolioBusinessIdentity, ...],
    tuple[PortfolioBusinessIdentity, ...],
    tuple[PortfolioBusinessIdentity, ...],
]:
    execution = state.execution_identities
    funding = state.funding_identities
    reconciliation = state.reconciliation_identities
    payload = event.payload
    if type(payload) is PortfolioFillEntry:
        execution = _updated_identity_tuple(
            execution,
            _identity_record(event, payload.fill.execution_id),
            label="execution",
        )
    elif type(payload) is PortfolioFundingEntry:
        funding = _updated_identity_tuple(
            funding,
            _identity_record(event, payload.funding_id),
            label="funding",
        )
    elif type(payload) is PortfolioReconciliationEntry:
        reconciliation = _updated_identity_tuple(
            reconciliation,
            _identity_record(event, payload.reconciliation_id),
            label="reconciliation",
        )
    return execution, funding, reconciliation


def _validate_event(event: object) -> PortfolioEvent:
    if not isinstance(event, EventEnvelope):
        raise PortfolioReplayError("event must be an EventEnvelope")
    if type(event.payload) not in (
        PortfolioOpeningEntry,
        PortfolioAccountObservationEntry,
        PortfolioFillEntry,
        PortfolioMarkEntry,
        PortfolioFundingEntry,
        PortfolioConversionEntry,
        PortfolioValuationRateEntry,
        PortfolioReconciliationEntry,
    ):
        raise PortfolioReplayError("event payload is not supported by portfolio accounting")
    try:
        return EventEnvelope[type(event.payload)].model_validate(event)
    except ValueError as exc:
        raise PortfolioReplayError("event envelope is invalid") from exc


def _applied_event(event: PortfolioEvent, digest: str) -> PortfolioAppliedEvent:
    return PortfolioAppliedEvent(
        event_id=event.event_id,
        digest=digest,
        stream_id=event.stream_id,
        sequence=event.sequence,
        event_type=type(event.payload).__name__,
        business_identity_id=_business_identity(event.payload),
    )


def _sorted_applied(
    items: dict[UUID, PortfolioAppliedEvent],
) -> tuple[PortfolioAppliedEvent, ...]:
    return tuple(item for _, item in sorted(items.items(), key=lambda item: item[0].bytes))


def _next_state(
    state: PortfolioReplayState,
    *,
    snapshot: PortfolioWorkingSnapshot | None = None,
    event: PortfolioEvent,
    active_effects: tuple[PortfolioExecutionEffect, ...] | None = None,
    valuation_rates: tuple[PortfolioValuationRateState, ...] | None = None,
    reconciliation: PortfolioReconciliationEntry | None = None,
) -> PortfolioReplayState:
    digest = _event_digest(event)
    execution_identities, funding_identities, reconciliation_identities = (
        _business_identities(state, event)
    )
    applied = {item.event_id: item for item in state.applied_events}
    applied[event.event_id] = _applied_event(event, digest)
    cursor = {item.stream_id: item.sequence for item in state.cursor}
    cursor[event.stream_id] = event.sequence
    return PortfolioReplayState(
        snapshot=snapshot or state.snapshot,
        cursor=tuple(
            PortfolioStreamCursor(stream_id=stream_id, sequence=sequence)
            for stream_id, sequence in sorted(cursor.items(), key=lambda item: item[0].bytes)
        ),
        applied_events=_sorted_applied(applied),
        valuation_rates=state.valuation_rates if valuation_rates is None else valuation_rates,
        active_effects=active_effects if active_effects is not None else state.active_effects,
        execution_identities=execution_identities,
        funding_identities=funding_identities,
        reconciliation_identities=reconciliation_identities,
        reconciliation=state.reconciliation if reconciliation is None else reconciliation,
    )


def _opening_state(event: PortfolioEvent) -> PortfolioReplayState:
    payload = event.payload
    if type(payload) is not PortfolioOpeningEntry:
        raise PortfolioReplayError("first portfolio event must be an opening entry")
    snapshot = PortfolioWorkingSnapshot(
        account_id=payload.account_id,
        reporting_currency=payload.reporting_currency,
        balances=payload.balances,
        positions=(),
        observed_at=payload.effective_at,
        schema_version="portfolio-working-v1",
    )
    digest = _event_digest(event)
    return PortfolioReplayState(
        snapshot=snapshot,
        cursor=(PortfolioStreamCursor(stream_id=event.stream_id, sequence=event.sequence),),
        applied_events=(_applied_event(event, digest),),
    )


def _find_position(state: PortfolioReplayState, strategy_id: str, instrument: str) -> PortfolioPositionState | None:
    return next(
        (
            position
            for position in state.snapshot.positions
            if position.strategy_id == strategy_id and position.instrument.canonical == instrument
        ),
        None,
    )


def _new_position(entry: PortfolioFillEntry, fill: FillEvent) -> PortfolioPositionState:
    currency = fill.instrument_definition.settlement_currency
    return PortfolioPositionState(
        account_id=entry.account_id,
        strategy_id=entry.strategy_id,
        instrument=fill.instrument_definition.instrument_id,
        instrument_definition=fill.instrument_definition,
        settlement_currency=currency,
        quantity=_quantity(Decimal(0), fill.quantity.precision),
        average_entry_price=None,
        realized_pnl=_money(Decimal(0), currency, field="initial realized PnL"),
        unrealized_pnl=_money(Decimal(0), currency, field="initial unrealized PnL"),
        fees=_money(Decimal(0), currency, field="initial fees"),
        funding=_money(Decimal(0), currency, field="initial funding"),
        observed_at=entry.effective_at,
        schema_version="portfolio-working-v1",
    )


def _cash_deltas(fill: FillEvent) -> tuple[Money, ...]:
    definition = fill.instrument_definition
    gross = _money(
        _product(fill.quantity.value, fill.last_fill_price.amount, definition.multiplier, field="fill notional"),
        definition.settlement_currency,
        field="fill notional",
    )
    trade_delta = -gross.amount if fill.side is OrderSide.BUY else gross.amount
    by_currency = {definition.settlement_currency: trade_delta}
    by_currency[fill.commission.currency] = _sum(
        by_currency.get(fill.commission.currency, Decimal(0)),
        -fill.commission.amount,
        field="cash delta",
    )
    return tuple(
        _money(amount, currency, field="cash delta")
        for currency, amount in sorted(by_currency.items(), key=lambda item: item[0].code)
        if amount != 0
    )


def _fill_accounting(
    fill: FillEvent,
    *,
    current: Decimal,
    average_before: Price | None,
) -> tuple[Decimal, Decimal, Price | None, Decimal, Decimal]:
    definition = fill.instrument_definition
    signed_fill = fill.quantity.value if fill.side is OrderSide.BUY else -fill.quantity.value
    residual = _sum(current, signed_fill, field="position quantity")
    closed = min(abs(current), abs(signed_fill))
    realized_delta = Decimal(0)
    if current > 0 and signed_fill < 0:
        if average_before is None:
            raise PortfolioReplayError("long position requires an average entry price")
        realized_delta = _product(
            _sum(fill.last_fill_price.amount, -average_before.amount, field="realized PnL"),
            closed,
            definition.multiplier,
            field="realized PnL",
        )
    elif current < 0 and signed_fill > 0:
        if average_before is None:
            raise PortfolioReplayError("short position requires an average entry price")
        realized_delta = _product(
            _sum(average_before.amount, -fill.last_fill_price.amount, field="realized PnL"),
            closed,
            definition.multiplier,
            field="realized PnL",
        )
    if residual == 0:
        next_average = None
    elif current == 0 or current * residual < 0:
        next_average = fill.last_fill_price
    elif current * signed_fill > 0:
        if average_before is None:
            raise PortfolioReplayError("open position requires an average entry price")
        numerator = _sum(
            _product(abs(current), average_before.amount, field="weighted average"),
            _product(abs(signed_fill), fill.last_fill_price.amount, field="weighted average"),
            field="weighted average",
        )
        next_average = Price(
            _decimal(_fraction(numerator) / _fraction(abs(residual)), field="weighted average"),
            definition.settlement_currency,
        )
    else:
        next_average = average_before
    fee_delta = (
        fill.commission.amount
        if fill.commission.currency is definition.settlement_currency
        else Decimal(0)
    )
    return signed_fill, residual, next_average, realized_delta, fee_delta


def _mark_and_unrealized(
    position: PortfolioPositionState,
    *,
    quantity: Quantity,
    average_entry_price: Price | None,
    retained_mark: PositionMark | None = None,
) -> tuple[PositionMark | None, Money]:
    if quantity.value == 0:
        return None, _money(
            Decimal(0), position.settlement_currency, field="flat position unrealized PnL"
        )
    selected_mark = position.mark if retained_mark is None else retained_mark
    if selected_mark is None:
        return None, _money(
            Decimal(0), position.settlement_currency, field="unmarked position unrealized PnL"
        )
    if average_entry_price is None or position.instrument_definition is None:
        raise PortfolioReplayError("marked position requires cost basis and instrument definition")
    unrealized = _product(
        _sum(
            selected_mark.price.amount,
            -average_entry_price.amount,
            field="unrealized PnL",
        ),
        quantity.value,
        position.instrument_definition.multiplier,
        field="unrealized PnL",
    )
    return selected_mark, _money(
        unrealized, position.settlement_currency, field="unrealized PnL"
    )


def _fill_effect(
    state: PortfolioReplayState,
    entry: PortfolioFillEntry,
    *,
    source_event: PortfolioEvent,
    logical_sequence: int,
    retained_mark: PositionMark | None = None,
) -> tuple[PortfolioExecutionEffect, PortfolioPositionState]:
    fill = entry.fill
    existing = _find_position(state, entry.strategy_id, fill.instrument_definition.instrument_id.canonical)
    position = existing or _new_position(entry, fill)
    definition = fill.instrument_definition
    if position.instrument_definition is not None and position.instrument_definition != definition:
        raise PortfolioReplayError("fill instrument definition conflicts with active position")
    current = position.quantity.value
    signed_fill, residual, next_average, realized_delta, fee_delta = _fill_accounting(
        fill,
        current=current,
        average_before=position.average_entry_price,
    )
    next_quantity = _quantity(residual, fill.quantity.precision)
    next_mark, next_unrealized = _mark_and_unrealized(
        position,
        quantity=next_quantity,
        average_entry_price=next_average,
        retained_mark=retained_mark,
    )
    next_position = PortfolioPositionState(
        account_id=position.account_id,
        strategy_id=position.strategy_id,
        instrument=position.instrument,
        instrument_definition=definition,
        settlement_currency=position.settlement_currency,
        quantity=next_quantity,
        mark=next_mark,
        average_entry_price=next_average,
        realized_pnl=_money(_sum(position.realized_pnl.amount, realized_delta, field="realized PnL"), position.settlement_currency, field="realized PnL"),
        unrealized_pnl=next_unrealized,
        fees=_money(_sum(position.fees.amount, fee_delta, field="position fees"), position.settlement_currency, field="position fees"),
        funding=position.funding,
        observed_at=entry.effective_at,
        schema_version=position.schema_version,
    )
    effect = PortfolioExecutionEffect(
        execution_id=fill.execution_id,
        account_id=entry.account_id,
        strategy_id=entry.strategy_id,
        fill=fill,
        entry=entry,
        source_event=source_event,
        logical_sequence=logical_sequence,
        cash_deltas=_cash_deltas(fill),
        balance_realized_pnl_deltas=(
            _money(realized_delta, position.settlement_currency, field="balance realized PnL"),
        ) if realized_delta != 0 else (),
        balance_fee_deltas=(fill.commission,) if fill.commission.amount != 0 else (),
        position_key=(entry.strategy_id, position.instrument.canonical),
        quantity_before=position.quantity,
        quantity_delta=_quantity(signed_fill, fill.quantity.precision),
        realized_pnl_delta=_money(realized_delta, position.settlement_currency, field="realized PnL"),
        fees_delta=_money(fee_delta, position.settlement_currency, field="position fees"),
        average_before=position.average_entry_price,
        average_after=next_average,
    )
    return effect, next_position


def validate_execution_effect(effect: PortfolioExecutionEffect) -> None:
    """Reject any retained effect that is not the exact result of its source fill."""

    fill = effect.fill
    if fill.status not in (
        FillReportStatus.PARTIALLY_FILLED,
        FillReportStatus.FILLED,
        FillReportStatus.CORRECTION,
    ):
        raise PortfolioReplayError("snapshot effect source is not an active execution")
    signed_fill, _residual, average_after, realized_delta, fee_delta = _fill_accounting(
        fill,
        current=effect.quantity_before.value,
        average_before=effect.average_before,
    )
    settlement = fill.instrument_definition.settlement_currency
    expected_realized = (
        (_money(realized_delta, settlement, field="balance realized PnL"),)
        if realized_delta != 0
        else ()
    )
    expected_fees = (fill.commission,) if fill.commission.amount != 0 else ()
    expected = (
        effect.position_key
        == (effect.strategy_id, fill.instrument_definition.instrument_id.canonical)
        and effect.quantity_delta
        == _quantity(signed_fill, fill.quantity.precision)
        and effect.cash_deltas == _cash_deltas(fill)
        and effect.balance_realized_pnl_deltas == expected_realized
        and effect.balance_fee_deltas == expected_fees
        and effect.realized_pnl_delta
        == _money(realized_delta, settlement, field="realized PnL")
        and effect.fees_delta == _money(fee_delta, settlement, field="position fees")
        and effect.average_after == average_after
    )
    if not expected:
        raise PortfolioReplayError("snapshot execution effect does not match source fill economics")


def _apply_balances(
    balances: tuple[AccountBalanceSnapshot, ...],
    cash_deltas: tuple[Money, ...],
    realized_pnl_deltas: tuple[Money, ...],
    fee_deltas: tuple[Money, ...],
    unrealized_pnl_deltas: tuple[Money, ...],
    observed_at,
) -> tuple[AccountBalanceSnapshot, ...]:
    by_currency = {balance.currency: balance for balance in balances}
    deltas_by_currency: dict = {}
    for index, deltas, field in (
        (0, cash_deltas, "cash delta"),
        (1, realized_pnl_deltas, "balance realized PnL delta"),
        (2, fee_deltas, "balance fee delta"),
        (3, unrealized_pnl_deltas, "balance unrealized PnL delta"),
    ):
        for delta in deltas:
            current = deltas_by_currency.setdefault(
                delta.currency, [Decimal(0), Decimal(0), Decimal(0), Decimal(0)]
            )
            current[index] = _sum(current[index], delta.amount, field=field)
    for currency, (
        cash_delta,
        realized_pnl_delta,
        fee_delta,
        unrealized_pnl_delta,
    ) in deltas_by_currency.items():
        balance = by_currency.get(currency)
        if balance is None:
            raise PortfolioReplayError(f"missing balance for currency {currency.code}")
        try:
            by_currency[currency] = AccountBalanceSnapshot(
                account_id=balance.account_id,
                currency=balance.currency,
                cash=_money(_sum(balance.cash.amount, cash_delta, field="cash"), balance.currency, field="cash"),
                locked_funds=balance.locked_funds,
                margin_used=balance.margin_used,
                realized_pnl=_money(_sum(balance.realized_pnl.amount, realized_pnl_delta, field="balance realized PnL"), balance.currency, field="balance realized PnL"),
                unrealized_pnl=_money(
                    _sum(
                        balance.unrealized_pnl.amount,
                        unrealized_pnl_delta,
                        field="balance unrealized PnL",
                    ),
                    balance.currency,
                    field="balance unrealized PnL",
                ),
                fees=_money(_sum(balance.fees.amount, fee_delta, field="balance fees"), balance.currency, field="balance fees"),
                funding=balance.funding,
                observed_at=observed_at,
                schema_version=balance.schema_version,
            )
        except ValueError as exc:
            raise PortfolioReplayError("cash balance cannot be reconstructed") from exc
    return tuple(by_currency[currency] for currency in sorted(by_currency, key=lambda item: item.code))


def _replace_position(snapshot: PortfolioWorkingSnapshot, replacement: PortfolioPositionState) -> tuple[PortfolioPositionState, ...]:
    positions = [
        replacement
        if (position.strategy_id, position.instrument.canonical) == (replacement.strategy_id, replacement.instrument.canonical)
        else position
        for position in snapshot.positions
    ]
    if not any(
        (position.strategy_id, position.instrument.canonical) == (replacement.strategy_id, replacement.instrument.canonical)
        for position in snapshot.positions
    ):
        positions.append(replacement)
    return tuple(sorted(positions, key=lambda item: (item.strategy_id, item.instrument.canonical)))


def _with_effect(
    state: PortfolioReplayState,
    effect: PortfolioExecutionEffect,
    next_position: PortfolioPositionState,
    *,
    observed_at=None,
) -> PortfolioReplayState:
    applied_at = effect.entry.effective_at if observed_at is None else observed_at
    position_fields = {
        name: getattr(next_position, name)
        for name in PortfolioPositionState.model_fields
    }
    position_fields["observed_at"] = applied_at
    applied_position = PortfolioPositionState(**position_fields)
    previous_position = _find_position(
        state,
        effect.strategy_id,
        effect.fill.instrument_definition.instrument_id.canonical,
    ) or _new_position(effect.entry, effect.fill)
    unrealized_delta = _sum(
        applied_position.unrealized_pnl.amount,
        -previous_position.unrealized_pnl.amount,
        field="balance unrealized PnL delta",
    )
    snapshot = PortfolioWorkingSnapshot(
        account_id=state.snapshot.account_id,
        reporting_currency=state.snapshot.reporting_currency,
        balances=_apply_balances(
            state.snapshot.balances,
            effect.cash_deltas,
            effect.balance_realized_pnl_deltas,
            effect.balance_fee_deltas,
            (
                _money(
                    unrealized_delta,
                    applied_position.settlement_currency,
                    field="balance unrealized PnL delta",
                ),
            )
            if unrealized_delta != 0
            else (),
            applied_at,
        ),
        positions=_replace_position(state.snapshot, applied_position),
        observed_at=applied_at,
        schema_version=state.snapshot.schema_version,
    )
    active = tuple(sorted((*state.active_effects, effect), key=lambda item: item.execution_id.bytes))
    return PortfolioReplayState(
        snapshot=snapshot,
        cursor=state.cursor,
        applied_events=state.applied_events,
        valuation_rates=state.valuation_rates,
        active_effects=active,
        execution_identities=state.execution_identities,
        funding_identities=state.funding_identities,
        reconciliation_identities=state.reconciliation_identities,
        reconciliation=state.reconciliation,
    )


def _apply_normal(
    state: PortfolioReplayState,
    event: PortfolioEvent,
    entry: PortfolioFillEntry,
    *,
    logical_sequence: int | None = None,
) -> PortfolioReplayState:
    effect, next_position = _fill_effect(
        state,
        entry,
        source_event=event,
        logical_sequence=event.sequence if logical_sequence is None else logical_sequence,
    )
    return _next_state(_with_effect(state, effect, next_position), event=event)


def _same_economics(left: FillEvent, right: FillEvent) -> bool:
    return (
        left.instrument_definition == right.instrument_definition
        and left.side is right.side
        and left.quantity == right.quantity
        and left.last_fill_price == right.last_fill_price
        and left.average_fill_price == right.average_fill_price
        and left.commission == right.commission
    )


def _reverse_effect(state: PortfolioReplayState, effect: PortfolioExecutionEffect, observed_at) -> PortfolioWorkingSnapshot:
    position = _find_position(state, effect.strategy_id, effect.fill.instrument_definition.instrument_id.canonical)
    if position is None:
        raise PortfolioReplayError("active normal execution has no position to reverse")
    next_quantity = _sum(position.quantity.value, -effect.quantity_delta.value, field="reversed position quantity")
    next_quantity_value = _quantity(next_quantity, position.quantity.precision)
    next_mark, next_unrealized = _mark_and_unrealized(
        position,
        quantity=next_quantity_value,
        average_entry_price=effect.average_before,
    )
    next_position = PortfolioPositionState(
        account_id=position.account_id,
        strategy_id=position.strategy_id,
        instrument=position.instrument,
        instrument_definition=position.instrument_definition,
        settlement_currency=position.settlement_currency,
        quantity=next_quantity_value,
        mark=next_mark,
        average_entry_price=effect.average_before,
        realized_pnl=_money(_sum(position.realized_pnl.amount, -effect.realized_pnl_delta.amount, field="reversed realized PnL"), position.settlement_currency, field="reversed realized PnL"),
        unrealized_pnl=next_unrealized,
        fees=_money(_sum(position.fees.amount, -effect.fees_delta.amount, field="reversed fees"), position.settlement_currency, field="reversed fees"),
        funding=position.funding,
        observed_at=observed_at,
        schema_version=position.schema_version,
    )
    reverse_cash = tuple(_money(-delta.amount, delta.currency, field="reversed cash") for delta in effect.cash_deltas)
    reverse_realized_pnl = tuple(
        _money(-delta.amount, delta.currency, field="reversed balance realized PnL")
        for delta in effect.balance_realized_pnl_deltas
    )
    reverse_fees = tuple(
        _money(-delta.amount, delta.currency, field="reversed balance fees")
        for delta in effect.balance_fee_deltas
    )
    unrealized_delta = _sum(
        next_position.unrealized_pnl.amount,
        -position.unrealized_pnl.amount,
        field="reversed balance unrealized PnL",
    )
    return PortfolioWorkingSnapshot(
        account_id=state.snapshot.account_id,
        reporting_currency=state.snapshot.reporting_currency,
        balances=_apply_balances(
            state.snapshot.balances,
            reverse_cash,
            reverse_realized_pnl,
            reverse_fees,
            (
                _money(
                    unrealized_delta,
                    position.settlement_currency,
                    field="reversed balance unrealized PnL",
                ),
            )
            if unrealized_delta != 0
            else (),
            observed_at,
        ),
        positions=_replace_position(state.snapshot, next_position),
        observed_at=observed_at,
        schema_version=state.snapshot.schema_version,
    )


def _remove_effect(
    state: PortfolioReplayState,
    effect: PortfolioExecutionEffect,
    *,
    observed_at,
) -> PortfolioReplayState:
    return PortfolioReplayState(
        snapshot=_reverse_effect(state, effect, observed_at),
        cursor=state.cursor,
        applied_events=state.applied_events,
        valuation_rates=state.valuation_rates,
        active_effects=tuple(
            item for item in state.active_effects if item.execution_id != effect.execution_id
        ),
        execution_identities=state.execution_identities,
        funding_identities=state.funding_identities,
        reconciliation_identities=state.reconciliation_identities,
        reconciliation=state.reconciliation,
    )


def _rebase_later_effects(
    state: PortfolioReplayState,
    effect: PortfolioExecutionEffect,
    event: PortfolioEvent,
    entry: PortfolioFillEntry,
) -> PortfolioReplayState:
    """Replace or remove one effect while preserving affected position lineages."""

    replacement_definition = entry.fill.instrument_definition
    replacement_key = (
        entry.strategy_id,
        replacement_definition.instrument_id.canonical,
    )
    affected_definitions = {
        effect.position_key: effect.fill.instrument_definition,
    }
    if entry.fill.status is FillReportStatus.CORRECTION:
        affected_definitions[replacement_key] = replacement_definition
    affected_keys = set(affected_definitions)
    later = tuple(
        sorted(
            (
                item
                for item in state.active_effects
                if item.position_key in affected_keys
                and item.logical_sequence > effect.logical_sequence
            ),
            key=lambda item: item.logical_sequence,
        )
    )
    retained_marks: dict[tuple[str, str], PositionMark | None] = {}
    for position_key in affected_keys:
        current_position = _find_position(state, *position_key)
        expected_definition = affected_definitions[position_key]
        retained_marks[position_key] = (
            current_position.mark
            if current_position is not None
            and current_position.instrument == expected_definition.instrument_id
            and current_position.settlement_currency
            is expected_definition.settlement_currency
            and current_position.mark is not None
            and current_position.mark.price.currency
            is current_position.settlement_currency
            else None
        )
    rebased = state
    for item in sorted(
        (effect, *later), key=lambda item: item.logical_sequence, reverse=True
    ):
        rebased = _remove_effect(rebased, item, observed_at=entry.effective_at)
    if entry.fill.status is FillReportStatus.CORRECTION:
        replacement, next_position = _fill_effect(
            rebased,
            entry,
            source_event=event,
            logical_sequence=effect.logical_sequence,
            retained_mark=retained_marks[replacement_key],
        )
        rebased = _with_effect(rebased, replacement, next_position)
    for item in later:
        replayed, next_position = _fill_effect(
            rebased,
            item.entry,
            source_event=item.source_event,
            logical_sequence=item.logical_sequence,
            retained_mark=retained_marks[item.position_key],
        )
        rebased = _with_effect(
            rebased,
            replayed,
            next_position,
            observed_at=entry.effective_at,
        )
    return rebased


def _consume_effect(state: PortfolioReplayState, event: PortfolioEvent, entry: PortfolioFillEntry, reference) -> PortfolioReplayState:
    effect = next((item for item in state.active_effects if item.execution_id == reference), None)
    if effect is None:
        raise PortfolioReplayError("reference must name an active normal execution")
    if effect.account_id != entry.account_id or effect.strategy_id != entry.strategy_id:
        raise PortfolioReplayError("reference must name an active normal execution in the same account and strategy")
    return _next_state(_rebase_later_effects(state, effect, event, entry), event=event)


def _working_snapshot(
    state: PortfolioReplayState,
    *,
    balances: tuple[AccountBalanceSnapshot, ...] | None = None,
    positions: tuple[PortfolioPositionState, ...] | None = None,
    observed_at,
) -> PortfolioWorkingSnapshot:
    return PortfolioWorkingSnapshot(
        account_id=state.snapshot.account_id,
        reporting_currency=state.snapshot.reporting_currency,
        balances=state.snapshot.balances if balances is None else balances,
        positions=state.snapshot.positions if positions is None else positions,
        observed_at=observed_at,
        schema_version=state.snapshot.schema_version,
    )


def _updated_balance(
    balance: AccountBalanceSnapshot,
    *,
    cash_delta: Decimal = Decimal(0),
    funding_delta: Decimal = Decimal(0),
    observed_at,
) -> AccountBalanceSnapshot:
    return AccountBalanceSnapshot(
        account_id=balance.account_id,
        currency=balance.currency,
        cash=_money(_sum(balance.cash.amount, cash_delta, field="cash"), balance.currency, field="cash"),
        locked_funds=balance.locked_funds,
        margin_used=balance.margin_used,
        realized_pnl=balance.realized_pnl,
        unrealized_pnl=balance.unrealized_pnl,
        fees=balance.fees,
        funding=_money(_sum(balance.funding.amount, funding_delta, field="funding"), balance.currency, field="funding"),
        observed_at=observed_at,
        schema_version=balance.schema_version,
    )


def _apply_mark(
    state: PortfolioReplayState, event: PortfolioEvent, entry: PortfolioMarkEntry
) -> PortfolioReplayState:
    if entry.marked_at > entry.effective_at:
        raise PortfolioReplayError("mark time must not be after event effective time")
    if any(
        position.instrument == entry.instrument
        and position.quantity.value != 0
        and position.mark is not None
        and entry.marked_at < position.mark.marked_at
        for position in state.snapshot.positions
    ):
        raise PortfolioReplayError("mark time is older than retained mark")
    positions: list[PortfolioPositionState] = []
    unrealized_by_currency: dict = {}
    for position in state.snapshot.positions:
        if position.instrument != entry.instrument or position.quantity.value == 0:
            positions.append(position)
            continue
        if entry.mark.price.currency is not position.settlement_currency:
            raise PortfolioReplayError("mark currency must match position settlement currency")
        assert position.average_entry_price is not None
        assert position.instrument_definition is not None
        unrealized = _product(
            _sum(entry.mark.price.amount, -position.average_entry_price.amount, field="unrealized PnL"),
            position.quantity.value,
            position.instrument_definition.multiplier,
            field="unrealized PnL",
        )
        unrealized_delta = _sum(
            unrealized,
            -position.unrealized_pnl.amount,
            field="balance unrealized PnL delta",
        )
        unrealized_by_currency[position.settlement_currency] = _sum(
            unrealized_by_currency.get(position.settlement_currency, Decimal(0)),
            unrealized_delta,
            field="balance unrealized PnL delta",
        )
        positions.append(
            PortfolioPositionState(
                account_id=position.account_id,
                strategy_id=position.strategy_id,
                instrument=position.instrument,
                instrument_definition=position.instrument_definition,
                settlement_currency=position.settlement_currency,
                quantity=position.quantity,
                mark=entry.mark,
                average_entry_price=position.average_entry_price,
                realized_pnl=position.realized_pnl,
                unrealized_pnl=_money(unrealized, position.settlement_currency, field="unrealized PnL"),
                fees=position.fees,
                funding=position.funding,
                observed_at=entry.effective_at,
                schema_version=position.schema_version,
            )
        )
    return _next_state(
        state,
        snapshot=_working_snapshot(
            state,
            balances=_apply_balances(
                state.snapshot.balances,
                (),
                (),
                (),
                tuple(
                    _money(
                        amount,
                        currency,
                        field="balance unrealized PnL delta",
                    )
                    for currency, amount in sorted(
                        unrealized_by_currency.items(), key=lambda item: item[0].code
                    )
                    if amount != 0
                ),
                entry.effective_at,
            ),
            positions=tuple(positions),
            observed_at=entry.effective_at,
        ),
        event=event,
    )


def _apply_funding(
    state: PortfolioReplayState, event: PortfolioEvent, entry: PortfolioFundingEntry
) -> PortfolioReplayState:
    balance_by_currency = {balance.currency: balance for balance in state.snapshot.balances}
    balance = balance_by_currency.get(entry.amount.currency)
    if balance is None:
        raise PortfolioReplayError(f"missing balance for funding currency {entry.amount.currency.code}")
    if entry.strategy_id is None:
        balance_by_currency[entry.amount.currency] = _updated_balance(
            balance,
            cash_delta=entry.amount.amount,
            funding_delta=entry.amount.amount,
            observed_at=entry.effective_at,
        )
        return _next_state(
            state,
            snapshot=_working_snapshot(
                state,
                balances=tuple(balance_by_currency[currency] for currency in sorted(balance_by_currency, key=lambda item: item.code)),
                observed_at=entry.effective_at,
            ),
            event=event,
        )
    assert entry.instrument is not None
    position = _find_position(state, entry.strategy_id, entry.instrument.canonical)
    if position is None:
        raise PortfolioReplayError("funding position key does not name an existing position")
    if entry.amount.currency is not position.settlement_currency:
        raise PortfolioReplayError("funding currency must match position settlement currency")
    replacement = PortfolioPositionState(
        account_id=position.account_id,
        strategy_id=position.strategy_id,
        instrument=position.instrument,
        instrument_definition=position.instrument_definition,
        settlement_currency=position.settlement_currency,
        quantity=position.quantity,
        mark=position.mark,
        average_entry_price=position.average_entry_price,
        realized_pnl=position.realized_pnl,
        unrealized_pnl=position.unrealized_pnl,
        fees=position.fees,
        funding=_money(_sum(position.funding.amount, entry.amount.amount, field="position funding"), position.settlement_currency, field="position funding"),
        observed_at=entry.effective_at,
        schema_version=position.schema_version,
    )
    balance_by_currency[entry.amount.currency] = _updated_balance(
        balance,
        cash_delta=entry.amount.amount,
        funding_delta=entry.amount.amount,
        observed_at=entry.effective_at,
    )
    return _next_state(
        state,
        snapshot=_working_snapshot(
            state,
            balances=tuple(balance_by_currency[currency] for currency in sorted(balance_by_currency, key=lambda item: item.code)),
            positions=_replace_position(state.snapshot, replacement),
            observed_at=entry.effective_at,
        ),
        event=event,
    )


def _apply_conversion(
    state: PortfolioReplayState, event: PortfolioEvent, entry: PortfolioConversionEntry
) -> PortfolioReplayState:
    conversion = entry.conversion
    if conversion.source.amount <= 0:
        raise PortfolioReplayError("conversion source amount must be positive")
    by_currency = {balance.currency: balance for balance in state.snapshot.balances}
    source = by_currency.get(conversion.source.currency)
    target = by_currency.get(conversion.target.currency)
    if source is None or target is None:
        raise PortfolioReplayError("conversion requires balances for both currencies")
    if source.cash.amount < conversion.source.amount:
        raise PortfolioReplayError("insufficient source cash for conversion")
    by_currency[source.currency] = _updated_balance(
        source, cash_delta=-conversion.source.amount, observed_at=entry.effective_at
    )
    by_currency[target.currency] = _updated_balance(
        target, cash_delta=conversion.target.amount, observed_at=entry.effective_at
    )
    return _next_state(
        state,
        snapshot=_working_snapshot(
            state,
            balances=tuple(by_currency[currency] for currency in sorted(by_currency, key=lambda item: item.code)),
            observed_at=entry.effective_at,
        ),
        event=event,
    )


def _apply_valuation_rate(
    state: PortfolioReplayState, event: PortfolioEvent, entry: PortfolioValuationRateEntry
) -> PortfolioReplayState:
    rates = {(rate.source_currency, rate.target_currency): rate for rate in state.valuation_rates}
    key = (entry.source_currency, entry.target_currency)
    existing = rates.get(key)
    if existing is None or entry.quoted_at > existing.quoted_at:
        rates[key] = PortfolioValuationRateState(
            source_currency=entry.source_currency,
            target_currency=entry.target_currency,
            rate=entry.rate,
            quoted_at=entry.quoted_at,
            provenance_id=entry.provenance_id,
        )
    return _next_state(
        state,
        event=event,
        valuation_rates=tuple(
            rates[key] for key in sorted(rates, key=lambda item: (item[0].code, item[1].code))
        ),
    )


def _apply_reconciliation(
    state: PortfolioReplayState, event: PortfolioEvent, entry: PortfolioReconciliationEntry
) -> PortfolioReplayState:
    definitions = {
        (position.strategy_id, position.instrument.canonical): position.instrument_definition
        for position in state.snapshot.positions
    }
    positions: list[PortfolioPositionState] = []
    for position in entry.snapshot.positions:
        definition = definitions.get((position.strategy_id, position.instrument.canonical))
        if definition is None and position.quantity.value != 0:
            raise PortfolioReplayError("reconciliation position has no retained instrument definition")
        positions.append(
            PortfolioPositionState(
                account_id=position.account_id,
                strategy_id=position.strategy_id,
                instrument=position.instrument,
                instrument_definition=definition,
                settlement_currency=position.settlement_currency,
                quantity=position.quantity,
                mark=position.mark,
                average_entry_price=position.average_entry_price,
                realized_pnl=position.realized_pnl,
                unrealized_pnl=position.unrealized_pnl,
                fees=position.fees,
                funding=position.funding,
                observed_at=position.observed_at,
                schema_version=position.schema_version,
            )
        )
    snapshot = PortfolioWorkingSnapshot(
        account_id=entry.snapshot.account_id,
        reporting_currency=entry.snapshot.reporting_currency,
        balances=entry.snapshot.balances,
        positions=tuple(positions),
        observed_at=entry.snapshot.observed_at,
        schema_version="portfolio-working-v1",
    )
    return _next_state(
        state,
        snapshot=snapshot,
        event=event,
        active_effects=(),
        reconciliation=entry,
    )


def apply_portfolio_event(state: PortfolioReplayState, event: object) -> PortfolioReplayState:
    """Apply one portfolio ledger envelope without persistence or external authority."""

    if not isinstance(state, PortfolioReplayState):
        raise PortfolioReplayError("state must be a PortfolioReplayState")
    canonical = _validate_event(event)
    digest = _event_digest(canonical)
    known = {item.event_id: item.digest for item in state.applied_events}
    previous = known.get(canonical.event_id)
    if previous is not None:
        if previous != digest:
            raise PortfolioReplayError("conflicting event identity")
        return state
    if canonical.stream_id not in {cursor.stream_id for cursor in state.cursor}:
        raise PortfolioReplayError("event stream does not match portfolio state")
    payload = canonical.payload
    if type(payload) is PortfolioOpeningEntry:
        raise PortfolioReplayError("portfolio opening may only appear once")
    if payload.account_id != state.snapshot.account_id:
        raise PortfolioReplayError("portfolio event account does not match state account")
    _business_identities(state, canonical)
    if type(payload) is PortfolioMarkEntry:
        return _apply_mark(state, canonical, payload)
    if type(payload) is PortfolioFundingEntry:
        return _apply_funding(state, canonical, payload)
    if type(payload) is PortfolioConversionEntry:
        return _apply_conversion(state, canonical, payload)
    if type(payload) is PortfolioValuationRateEntry:
        return _apply_valuation_rate(state, canonical, payload)
    if type(payload) is PortfolioReconciliationEntry:
        return _apply_reconciliation(state, canonical, payload)
    if type(payload) is PortfolioAccountObservationEntry:
        balance = next(
            (
                item
                for item in state.snapshot.balances
                if item.currency is payload.currency
            ),
            None,
        )
        if balance is None or (
            balance.cash != payload.cash_balance
            or balance.fees != payload.fees
            or balance.realized_pnl != payload.realized_pnl
            or balance.unrealized_pnl != payload.unrealized_pnl
        ):
            raise PortfolioReplayError(
                "account observation does not match reduced portfolio state"
            )
        if payload.effective_at < state.snapshot.observed_at:
            raise PortfolioReplayError("account observation time cannot regress")
        snapshot = state.snapshot.model_copy(
            update={"observed_at": payload.effective_at}
        )
        return _next_state(state, snapshot=snapshot, event=canonical)
    assert type(payload) is PortfolioFillEntry
    fill = payload.fill
    if fill.status in (FillReportStatus.PARTIALLY_FILLED, FillReportStatus.FILLED):
        if fill.execution_id in state.active_execution_ids:
            raise PortfolioReplayError("normal execution is already active")
        return _apply_normal(state, canonical, payload)
    if fill.status is FillReportStatus.DUPLICATE:
        reference = fill.duplicate_of_execution_id
        effect = next((item for item in state.active_effects if item.execution_id == reference), None)
        if effect is None or effect.account_id != payload.account_id or effect.strategy_id != payload.strategy_id:
            raise PortfolioReplayError("duplicate must reference an active normal execution")
        if not _same_economics(effect.fill, fill):
            raise PortfolioReplayError("duplicate economics do not match referenced execution")
        return _next_state(state, event=canonical)
    if fill.status is FillReportStatus.CORRECTION:
        assert fill.correction_of_execution_id is not None
        return _consume_effect(state, canonical, payload, fill.correction_of_execution_id)
    if fill.status is FillReportStatus.BUST:
        assert fill.bust_of_execution_id is not None
        return _consume_effect(state, canonical, payload, fill.bust_of_execution_id)
    raise PortfolioReplayError("unsupported fill status")


def _exposure(gross_amount: Decimal, net_amount: Decimal, currency) -> ExposureSnapshot:
    return ExposureSnapshot(
        currency=currency,
        gross=_money(gross_amount, currency, field="exposure gross"),
        net=_money(net_amount, currency, field="exposure net"),
        pending=_money(Decimal(0), currency, field="exposure pending"),
    )


def _valuation_rate(
    state: PortfolioReplayState, source_currency, target_currency, *, observed_at
) -> Decimal:
    if source_currency is target_currency:
        return Decimal(1)
    rate = next(
        (
            item
            for item in state.valuation_rates
            if item.source_currency is source_currency and item.target_currency is target_currency
        ),
        None,
    )
    if rate is None or rate.quoted_at > observed_at:
        raise PortfolioReplayError(
            f"explicit valuation rate is required for {source_currency.code}/{target_currency.code}"
        )
    return rate.rate


def derive_account_snapshot(
    state: PortfolioReplayState, observed_at
) -> AccountPortfolioSnapshot:
    """Derive an exact reporting-currency account snapshot from reducer state."""

    if not isinstance(state, PortfolioReplayState):
        raise PortfolioReplayError("state must be a PortfolioReplayState")
    if not isinstance(observed_at, type(state.snapshot.observed_at)):
        raise PortfolioReplayError("snapshot observation time must be a datetime")
    if observed_at < state.snapshot.observed_at:
        raise PortfolioReplayError("snapshot observation time cannot predate reducer state")
    reporting = state.snapshot.reporting_currency
    instrument_amounts: dict = {}
    strategy_amounts: dict = {}
    venue_amounts: dict = {}
    positions: list[AccountPositionSnapshot] = []
    for position in state.snapshot.positions:
        positions.append(
            AccountPositionSnapshot(
                account_id=position.account_id,
                strategy_id=position.strategy_id,
                instrument=position.instrument,
                settlement_currency=position.settlement_currency,
                quantity=position.quantity,
                mark=position.mark,
                average_entry_price=position.average_entry_price,
                realized_pnl=position.realized_pnl,
                unrealized_pnl=position.unrealized_pnl,
                fees=position.fees,
                funding=position.funding,
                observed_at=position.observed_at,
                schema_version=position.schema_version,
            )
        )
        if position.quantity.value == 0:
            continue
        if position.mark is None:
            raise PortfolioReplayError("non-zero position requires a mark for exposure derivation")
        rate = _valuation_rate(
            state, position.settlement_currency, reporting, observed_at=observed_at
        )
        notional = _product(
            abs(position.quantity.value),
            position.mark.price.amount,
            position.instrument_definition.multiplier,
            rate,
            field="marked exposure",
        )
        signed = notional if position.quantity.value > 0 else -notional
        for amounts, key, field in (
            (instrument_amounts, position.instrument, "instrument exposure"),
            (strategy_amounts, position.strategy_id, "strategy exposure"),
            (venue_amounts, position.instrument.venue, "venue exposure"),
        ):
            gross, net = amounts.get(key, (Decimal(0), Decimal(0)))
            amounts[key] = (
                _sum(gross, notional, field=f"{field} gross"),
                _sum(net, signed, field=f"{field} net"),
            )
    total_gross = _sum(
        *(amounts[0] for amounts in instrument_amounts.values()), field="total exposure gross"
    )
    total_net = _sum(
        *(amounts[1] for amounts in instrument_amounts.values()), field="total exposure net"
    )
    try:
        state_document = json.dumps(
            {"state": state.model_dump(mode="json"), "observed_at": observed_at.isoformat()},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise PortfolioReplayError("snapshot state cannot be canonically represented") from exc
    return AccountPortfolioSnapshot(
        snapshot_id=uuid5(NAMESPACE_URL, state_document),
        account_id=state.snapshot.account_id,
        reporting_currency=reporting,
        balances=state.snapshot.balances,
        positions=tuple(sorted(positions, key=lambda item: (item.strategy_id, item.instrument.canonical))),
        total_exposure=_exposure(total_gross, total_net, reporting),
        instrument_exposures=tuple(
            InstrumentExposureSnapshot(
                instrument=instrument,
                exposure=_exposure(*instrument_amounts[instrument], reporting),
            )
            for instrument in sorted(instrument_amounts, key=lambda item: item.canonical)
        ),
        strategy_exposures=tuple(
            StrategyExposureSnapshot(
                strategy_id=strategy_id,
                exposure=_exposure(*strategy_amounts[strategy_id], reporting),
            )
            for strategy_id in sorted(strategy_amounts)
        ),
        venue_exposures=tuple(
            VenueExposureSnapshot(
                venue_id=venue_id,
                exposure=_exposure(*venue_amounts[venue_id], reporting),
            )
            for venue_id in sorted(venue_amounts)
        ),
        observed_at=observed_at,
        schema_version="portfolio-snapshot-v1",
    )


def reduce_portfolio_events(events: Iterable[PortfolioEvent]) -> PortfolioReplayState:
    """Reduce a supplied opening followed by portfolio entries in caller order."""

    batch = tuple(_validate_event(event) for event in events)
    if not batch:
        raise PortfolioReplayError("portfolio event history requires an opening entry")
    state = _opening_state(batch[0])
    for event in batch[1:]:
        state = apply_portfolio_event(state, event)
    return state
