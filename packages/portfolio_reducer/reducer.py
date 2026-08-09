"""Exact, side-effect-free reduction for portfolio opening and fill entries."""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from fractions import Fraction
from hashlib import sha256
import json
from typing import TypeAlias

from packages.domain.events import EventEnvelope
from packages.domain.orders import FillEvent, FillReportStatus, OrderSide
from packages.domain.portfolio import AccountBalanceSnapshot
from packages.domain.portfolio_events import PortfolioFillEntry, PortfolioOpeningEntry
from packages.domain.primitives import Money, Price, Quantity

from .models import (
    PortfolioAppliedEvent,
    PortfolioExecutionEffect,
    PortfolioPositionState,
    PortfolioReplayError,
    PortfolioReplayState,
    PortfolioStreamCursor,
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


def _validate_event(event: object) -> PortfolioEvent:
    if not isinstance(event, EventEnvelope):
        raise PortfolioReplayError("event must be an EventEnvelope")
    if type(event.payload) not in (PortfolioOpeningEntry, PortfolioFillEntry):
        raise PortfolioReplayError("event payload is not supported by execution accounting")
    try:
        return EventEnvelope[type(event.payload)].model_validate(event)
    except ValueError as exc:
        raise PortfolioReplayError("event envelope is invalid") from exc


def _sorted_applied(items: dict) -> tuple[PortfolioAppliedEvent, ...]:
    return tuple(
        PortfolioAppliedEvent(event_id=event_id, digest=digest)
        for event_id, digest in sorted(items.items(), key=lambda item: item[0].bytes)
    )


def _next_state(
    state: PortfolioReplayState,
    *,
    snapshot: PortfolioWorkingSnapshot | None = None,
    event: PortfolioEvent,
    active_effects: tuple[PortfolioExecutionEffect, ...] | None = None,
) -> PortfolioReplayState:
    digest = _event_digest(event)
    applied = {item.event_id: item.digest for item in state.applied_events}
    applied[event.event_id] = digest
    cursor = {item.stream_id: item.sequence for item in state.cursor}
    cursor[event.stream_id] = event.sequence
    return PortfolioReplayState(
        snapshot=snapshot or state.snapshot,
        cursor=tuple(
            PortfolioStreamCursor(stream_id=stream_id, sequence=sequence)
            for stream_id, sequence in sorted(cursor.items(), key=lambda item: item[0].bytes)
        ),
        applied_events=_sorted_applied(applied),
        valuation_rates=state.valuation_rates,
        active_effects=active_effects if active_effects is not None else state.active_effects,
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
        applied_events=(PortfolioAppliedEvent(event_id=event.event_id, digest=digest),),
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


def _fill_effect(
    state: PortfolioReplayState,
    entry: PortfolioFillEntry,
    *,
    logical_sequence: int,
) -> tuple[PortfolioExecutionEffect, PortfolioPositionState]:
    fill = entry.fill
    existing = _find_position(state, entry.strategy_id, fill.instrument_definition.instrument_id.canonical)
    position = existing or _new_position(entry, fill)
    definition = fill.instrument_definition
    if position.instrument_definition != definition:
        raise PortfolioReplayError("fill instrument definition conflicts with active position")
    signed_fill = fill.quantity.value if fill.side is OrderSide.BUY else -fill.quantity.value
    current = position.quantity.value
    residual = _sum(current, signed_fill, field="position quantity")
    closed = min(abs(current), abs(signed_fill))
    realized_delta = Decimal(0)
    if current > 0 and signed_fill < 0:
        assert position.average_entry_price is not None
        realized_delta = _product(
            _sum(fill.last_fill_price.amount, -position.average_entry_price.amount, field="realized PnL"),
            closed,
            definition.multiplier,
            field="realized PnL",
        )
    elif current < 0 and signed_fill > 0:
        assert position.average_entry_price is not None
        realized_delta = _product(
            _sum(position.average_entry_price.amount, -fill.last_fill_price.amount, field="realized PnL"),
            closed,
            definition.multiplier,
            field="realized PnL",
        )
    if residual == 0:
        next_average = None
    elif current == 0 or current * residual < 0:
        next_average = fill.last_fill_price
    elif current * signed_fill > 0:
        assert position.average_entry_price is not None
        numerator = _sum(
            _product(abs(current), position.average_entry_price.amount, field="weighted average"),
            _product(abs(signed_fill), fill.last_fill_price.amount, field="weighted average"),
            field="weighted average",
        )
        next_average = Price(_decimal(_fraction(numerator) / _fraction(abs(residual)), field="weighted average"), definition.settlement_currency)
    else:
        next_average = position.average_entry_price
    fee_delta = fill.commission.amount if fill.commission.currency is definition.settlement_currency else Decimal(0)
    next_position = PortfolioPositionState(
        account_id=position.account_id,
        strategy_id=position.strategy_id,
        instrument=position.instrument,
        instrument_definition=position.instrument_definition,
        settlement_currency=position.settlement_currency,
        quantity=_quantity(residual, fill.quantity.precision),
        average_entry_price=next_average,
        realized_pnl=_money(_sum(position.realized_pnl.amount, realized_delta, field="realized PnL"), position.settlement_currency, field="realized PnL"),
        unrealized_pnl=position.unrealized_pnl,
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
        logical_sequence=logical_sequence,
        cash_deltas=_cash_deltas(fill),
        balance_realized_pnl_deltas=(
            _money(realized_delta, position.settlement_currency, field="balance realized PnL"),
        ) if realized_delta != 0 else (),
        balance_fee_deltas=(fill.commission,) if fill.commission.amount != 0 else (),
        position_key=(entry.strategy_id, position.instrument.canonical),
        quantity_delta=_quantity(signed_fill, fill.quantity.precision),
        realized_pnl_delta=_money(realized_delta, position.settlement_currency, field="realized PnL"),
        fees_delta=_money(fee_delta, position.settlement_currency, field="position fees"),
        average_before=position.average_entry_price,
        average_after=next_average,
    )
    return effect, next_position


def _apply_balances(
    balances: tuple[AccountBalanceSnapshot, ...],
    cash_deltas: tuple[Money, ...],
    realized_pnl_deltas: tuple[Money, ...],
    fee_deltas: tuple[Money, ...],
    observed_at,
) -> tuple[AccountBalanceSnapshot, ...]:
    by_currency = {balance.currency: balance for balance in balances}
    deltas_by_currency: dict = {}
    for index, deltas, field in (
        (0, cash_deltas, "cash delta"),
        (1, realized_pnl_deltas, "balance realized PnL delta"),
        (2, fee_deltas, "balance fee delta"),
    ):
        for delta in deltas:
            current = deltas_by_currency.setdefault(
                delta.currency, [Decimal(0), Decimal(0), Decimal(0)]
            )
            current[index] = _sum(current[index], delta.amount, field=field)
    for currency, (cash_delta, realized_pnl_delta, fee_delta) in deltas_by_currency.items():
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
                unrealized_pnl=balance.unrealized_pnl,
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
    snapshot = PortfolioWorkingSnapshot(
        account_id=state.snapshot.account_id,
        reporting_currency=state.snapshot.reporting_currency,
        balances=_apply_balances(
            state.snapshot.balances,
            effect.cash_deltas,
            effect.balance_realized_pnl_deltas,
            effect.balance_fee_deltas,
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
    next_position = PortfolioPositionState(
        account_id=position.account_id,
        strategy_id=position.strategy_id,
        instrument=position.instrument,
        instrument_definition=position.instrument_definition,
        settlement_currency=position.settlement_currency,
        quantity=_quantity(next_quantity, position.quantity.precision),
        average_entry_price=effect.average_before,
        realized_pnl=_money(_sum(position.realized_pnl.amount, -effect.realized_pnl_delta.amount, field="reversed realized PnL"), position.settlement_currency, field="reversed realized PnL"),
        unrealized_pnl=position.unrealized_pnl,
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
    return PortfolioWorkingSnapshot(
        account_id=state.snapshot.account_id,
        reporting_currency=state.snapshot.reporting_currency,
        balances=_apply_balances(
            state.snapshot.balances,
            reverse_cash,
            reverse_realized_pnl,
            reverse_fees,
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
    )


def _rebase_later_effects(
    state: PortfolioReplayState,
    effect: PortfolioExecutionEffect,
    entry: PortfolioFillEntry,
) -> PortfolioReplayState:
    """Replace or remove one effect while preserving subsequent same-position fills."""

    later = tuple(
        sorted(
            (
                item
                for item in state.active_effects
                if item.position_key == effect.position_key
                and item.logical_sequence > effect.logical_sequence
            ),
            key=lambda item: item.logical_sequence,
        )
    )
    rebased = state
    for item in reversed((effect, *later)):
        rebased = _remove_effect(rebased, item, observed_at=entry.effective_at)
    if entry.fill.status is FillReportStatus.CORRECTION:
        replacement, next_position = _fill_effect(
            rebased,
            entry,
            logical_sequence=effect.logical_sequence,
        )
        rebased = _with_effect(rebased, replacement, next_position)
    for item in later:
        replayed, next_position = _fill_effect(
            rebased,
            item.entry,
            logical_sequence=item.logical_sequence,
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
    return _next_state(_rebase_later_effects(state, effect, entry), event=event)


def apply_portfolio_event(state: PortfolioReplayState, event: object) -> PortfolioReplayState:
    """Apply one opening/fill envelope without persistence or external authority."""

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
    assert type(payload) is PortfolioFillEntry
    if payload.account_id != state.snapshot.account_id:
        raise PortfolioReplayError("portfolio event account does not match state account")
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


def reduce_portfolio_events(events: Iterable[PortfolioEvent]) -> PortfolioReplayState:
    """Reduce a supplied opening followed by portfolio fills in caller order."""

    batch = tuple(_validate_event(event) for event in events)
    if not batch:
        raise PortfolioReplayError("portfolio event history requires an opening entry")
    state = _opening_state(batch[0])
    for event in batch[1:]:
        state = apply_portfolio_event(state, event)
    return state
