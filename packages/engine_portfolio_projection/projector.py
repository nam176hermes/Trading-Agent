"""Pure typed P1 event to canonical portfolio projection."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from hashlib import sha256
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import TypeAdapter

from packages.domain import (
    FillEvent,
    FillReportStatus,
    InstrumentConstraints,
    InstrumentDefinition,
    Money,
    OrderEvent,
    OrderQuantity,
    OrderSide,
    OrderStatus,
    PortfolioFillEntry,
    PortfolioMarkEntry,
    PositionMark,
    Price,
)
from packages.engine_contracts import canonical_json_bytes
from packages.nautilus_runtime_contracts.events import (
    P1AccountObserved,
    P1Event,
    P1Fill,
    P1OrderSubmitted,
    P1PositionObserved,
    P1RunCompleted,
    P1RunStarted,
    event_message_id,
)

from .models import (
    PortfolioEntry,
    PortfolioAccountObservationEntry,
    PortfolioProjection,
    ProjectedAccounting,
    ProjectedPortfolioEntry,
    ProjectionAuthority,
)
from .validation import (
    ProjectionError,
    catalog_digest,
    canonical_authority,
    canonical_events,
    exact_fraction,
)


_INSTRUMENT_ADAPTER = TypeAdapter(InstrumentDefinition)


@dataclass(slots=True)
class _OrderProjectionState:
    order: P1OrderSubmitted
    order_id: UUID
    cumulative: Fraction = Fraction(0)
    average_notional: Fraction = Fraction(0)
    fill_count: int = 0


def _decimal(value: Fraction, *, field: str) -> Decimal:
    if value.numerator.bit_length() > 512 or value.denominator.bit_length() > 512:
        raise ProjectionError(f"{field} exceeds Decimal bounds")
    denominator = value.denominator
    twos = fives = 0
    while denominator % 2 == 0:
        denominator //= 2
        twos += 1
    while denominator % 5 == 0:
        denominator //= 5
        fives += 1
    if denominator != 1:
        raise ProjectionError(f"{field} cannot be represented exactly as Decimal")
    scale = max(twos, fives)
    if scale > 18:
        raise ProjectionError(f"{field} exceeds Decimal bounds")
    numerator = value.numerator * (2 ** (scale - twos)) * (5 ** (scale - fives))
    raw_digits = str(abs(numerator))
    if len(raw_digits) > 128:
        raise ProjectionError(f"{field} exceeds Decimal bounds")
    digits = tuple(int(character) for character in raw_digits) or (0,)
    return Decimal((1 if numerator < 0 else 0, digits, -scale))


def _round_half_even(value: Fraction, precision: int) -> Fraction:
    scale = 10**precision
    scaled = value * scale
    quotient, remainder = divmod(abs(scaled.numerator), scaled.denominator)
    doubled = remainder * 2
    if doubled > scaled.denominator or (
        doubled == scaled.denominator and quotient % 2
    ):
        quotient += 1
    return Fraction((-1 if scaled < 0 else 1) * quotient, scale)


def _business_id(stream_digest: str, kind: str, identity: object) -> UUID:
    return uuid5(NAMESPACE_URL, f"trading-agent:p1:{stream_digest}:{kind}:{identity}")


def _projected_entry(
    authority: ProjectionAuthority,
    event: P1Event,
    entry: PortfolioEntry,
) -> ProjectedPortfolioEntry:
    source = event_message_id(authority.request_message_id, event)
    return ProjectedPortfolioEntry(
        source_message_id=source,
        event_id=uuid5(source, f"portfolio:{type(entry).__name__}"),
        source_sequence=event.sequence,
        entry=entry,
    )


def _identity(
    events: tuple[P1Event, ...], authority: ProjectionAuthority
) -> str:
    completion = events[-1]
    if not isinstance(completion, P1RunCompleted):
        raise ProjectionError("P1 completion is missing")
    document = {
        "event_semantic_digest": completion.semantic_digest,
        "catalog_digest": catalog_digest(authority.catalog),
        "instrument": _INSTRUMENT_ADAPTER.dump_python(
            authority.instrument, mode="json"
        ),
        "liquidity_side": authority.liquidity_side.value,
        "opening": authority.opening.model_dump(mode="json"),
        "reconciliation_source": authority.reconciliation_source.value,
        "strategy_id": authority.strategy_id,
    }
    return sha256(canonical_json_bytes(document)).hexdigest()


def project_portfolio(
    events: tuple[P1Event, ...], authority: ProjectionAuthority
) -> PortfolioProjection:
    """Project one complete root-valid P1 run without I/O or synthetic fills."""

    source = canonical_events(events)
    exact = canonical_authority(authority)
    completion = source[-1]
    start = source[0]
    if not isinstance(completion, P1RunCompleted):
        raise ProjectionError("P1 completion is missing")
    if (
        not isinstance(start, P1RunStarted)
        or start.catalog_digest != catalog_digest(exact.catalog)
    ):
        raise ProjectionError("catalog digest does not match P1 run authority")
    projection_identity = _identity(source, exact)
    constraints = InstrumentConstraints(exact.instrument)
    precision = exact.instrument.size_increment.precision
    settlement = exact.instrument.settlement_currency
    opening_balance = next(
        balance for balance in exact.opening.balances if balance.currency is settlement
    )
    cash = exact_fraction(opening_balance.cash.amount, field="opening cash")
    fees = exact_fraction(opening_balance.fees.amount, field="opening fees")
    realized = exact_fraction(
        opening_balance.realized_pnl.amount, field="opening realized PnL"
    )
    position = Fraction(0)
    average: Fraction | None = None
    unrealized = exact_fraction(
        opening_balance.unrealized_pnl.amount, field="opening unrealized PnL"
    )
    multiplier = exact_fraction(
        exact.instrument.multiplier, field="instrument multiplier"
    )
    entries = [_projected_entry(exact, source[0], exact.opening)]
    order_events: list[OrderEvent] = []
    orders: dict[str, _OrderProjectionState] = {}
    execution_ids: set[UUID] = set()
    last_position_observation: P1PositionObserved | None = None

    for event in source[1:-1]:
        if isinstance(event, P1OrderSubmitted):
            order_id = _business_id(
                projection_identity, "order", event.client_order_id
            )
            source_id = event_message_id(exact.request_message_id, event)
            order_events.append(
                OrderEvent.create(
                    event_id=uuid5(source_id, "order:submitted"),
                    order_id=order_id,
                    sequence=1,
                    target_status=OrderStatus.SUBMITTED,
                    occurred_at=event.simulation_time,
                )
            )
            orders[event.client_order_id] = _OrderProjectionState(event, order_id)
            continue

        if isinstance(event, P1Fill):
            state = orders.get(event.client_order_id)
            if state is None:
                raise ProjectionError("fill has no canonical submitted order")
            order = state.order
            quantity = OrderQuantity(event.quantity, precision)
            price = Price(event.price, settlement)
            try:
                constraints.validate_price(price)
                constraints.validate_quantity(quantity)
                commission = Money(event.fee, settlement)
            except ValueError as exc:
                raise ProjectionError(str(exc)) from exc
            quantity_fraction = exact_fraction(event.quantity, field="fill quantity")
            price_fraction = exact_fraction(event.price, field="fill price")
            fee_fraction = exact_fraction(event.fee, field="fill fee")
            notional = quantity_fraction * price_fraction * multiplier
            if not (
                exact_fraction(
                    exact.instrument.minimum_notional.amount,
                    field="minimum notional",
                )
                <= notional
                <= exact_fraction(
                    exact.instrument.maximum_notional.amount,
                    field="maximum notional",
                )
            ):
                raise ProjectionError("fill notional is outside instrument bounds")
            cumulative = state.cumulative
            average_notional = state.average_notional
            cumulative += quantity_fraction
            average_notional += quantity_fraction * price_fraction
            order_quantity = exact_fraction(order.quantity, field="order quantity")
            if cumulative > order_quantity:
                raise ProjectionError("fill quantity exceeds submitted order")
            status = (
                FillReportStatus.FILLED
                if cumulative == order_quantity
                else FillReportStatus.PARTIALLY_FILLED
            )
            source_id = event_message_id(exact.request_message_id, event)
            fill_count = state.fill_count + 1
            execution_id = _business_id(
                projection_identity,
                "execution",
                f"{event.client_order_id}:{fill_count}:{event.sequence}",
            )
            if execution_id in execution_ids:
                raise ProjectionError("duplicate fill business identity")
            execution_ids.add(execution_id)
            fill = FillEvent(
                execution_id=execution_id,
                order_id=state.order_id,
                report_sequence=fill_count,
                venue_trade_id=event.native_fill_id,
                instrument_definition=exact.instrument,
                side=OrderSide.BUY if event.side == "BUY" else OrderSide.SELL,
                liquidity_side=exact.liquidity_side,
                status=status,
                quantity=quantity,
                cumulative_fill_quantity=OrderQuantity(
                    _decimal(cumulative, field="cumulative fill"), precision
                ),
                leaves_quantity=OrderQuantity(
                    _decimal(order_quantity - cumulative, field="leaves quantity"),
                    precision,
                ),
                order_quantity=OrderQuantity(order.quantity, precision),
                last_fill_price=price,
                average_fill_price=Price(
                    _decimal(
                        _round_half_even(
                            average_notional / cumulative,
                            exact.catalog.price_precision,
                        ),
                        field="average fill price",
                    ),
                    settlement,
                ),
                commission=commission,
                reconciliation_source=exact.reconciliation_source,
                filled_at=event.simulation_time,
                schema_version="2.0",
            )
            entry = PortfolioFillEntry(
                account_id=exact.opening.account_id,
                strategy_id=exact.strategy_id,
                fill=fill,
                effective_at=event.simulation_time,
                schema_version="portfolio-entry-v1",
            )
            entries.append(_projected_entry(exact, event, entry))
            order_events.append(
                OrderEvent.create(
                    event_id=uuid5(source_id, f"order:{status.value.lower()}"),
                    order_id=state.order_id,
                    sequence=fill_count + 1,
                    target_status=(
                        OrderStatus.FILLED
                        if status is FillReportStatus.FILLED
                        else OrderStatus.PARTIALLY_FILLED
                    ),
                    occurred_at=event.simulation_time,
                )
            )
            state.average_notional = average_notional
            state.cumulative = cumulative
            state.fill_count = fill_count

            gross = quantity_fraction * price_fraction * multiplier
            if event.side == "BUY":
                next_position = position + quantity_fraction
                if position == 0:
                    average = gross / quantity_fraction
                else:
                    if average is None:
                        raise ProjectionError(
                            "non-zero position has no average entry price"
                        )
                    average = ((position * average) + gross) / next_position
                cash = _round_half_even(
                    cash - gross - fee_fraction, settlement.precision
                )
            else:
                if average is None or quantity_fraction > position:
                    raise ProjectionError("sell fill would create an unsupported short position")
                next_position = position - quantity_fraction
                realized += (price_fraction - average) * quantity_fraction * multiplier
                cash = _round_half_even(
                    cash + gross - fee_fraction, settlement.precision
                )
                if next_position == 0:
                    average = None
            position = next_position
            fees = _round_half_even(fees + fee_fraction, settlement.precision)
            continue

        if isinstance(event, P1PositionObserved):
            observed_quantity = exact_fraction(
                event.quantity, field="position observation"
            )
            observed_average = exact_fraction(
                event.average_entry_price, field="position average"
            )
            observed_realized = exact_fraction(
                event.realized_pnl, field="position realized PnL"
            )
            if (
                observed_quantity != position
                or observed_average
                != _round_half_even(
                    average or Fraction(0), settlement.precision
                )
                or observed_realized
                != _round_half_even(realized, settlement.precision)
            ):
                raise ProjectionError("position observation is not explained by fills")
            unrealized = exact_fraction(
                event.unrealized_pnl, field="position unrealized PnL"
            )
            if unrealized != _round_half_even(unrealized, settlement.precision):
                raise ProjectionError("position observation exceeds currency precision")
            last_position_observation = event
            if position:
                if average is None:
                    raise ProjectionError("non-zero position has no average entry price")
                mark_value = average + (unrealized / (position * multiplier))
                mark_price = Price(
                    _decimal(mark_value, field="position mark"), settlement
                )
                try:
                    constraints.validate_price(mark_price)
                except ValueError as exc:
                    raise ProjectionError("position mark is outside catalog authority") from exc
                provenance = str(
                    _business_id(
                        projection_identity, "position-observation", event.sequence
                    )
                )
                mark = PositionMark(
                    price=mark_price,
                    marked_at=event.simulation_time,
                    provenance_id=provenance,
                )
                entries.append(
                    _projected_entry(
                        exact,
                        event,
                        PortfolioMarkEntry(
                            account_id=exact.opening.account_id,
                            instrument=exact.instrument.instrument_id,
                            mark=mark,
                            marked_at=event.simulation_time,
                            effective_at=event.simulation_time,
                            schema_version="portfolio-entry-v1",
                        ),
                    )
                )
            elif unrealized:
                raise ProjectionError("flat position cannot retain unrealized PnL")
            continue

        if isinstance(event, P1AccountObserved):
            if last_position_observation is None:
                raise ProjectionError("account observation has no position observation")
            if (
                exact_fraction(event.cash_balance, field="account cash")
                != _round_half_even(cash, settlement.precision)
                or exact_fraction(event.fees, field="account fees")
                != _round_half_even(fees, settlement.precision)
                or exact_fraction(event.realized_pnl, field="account realized PnL")
                != _round_half_even(realized, settlement.precision)
                or exact_fraction(event.unrealized_pnl, field="account unrealized PnL")
                != _round_half_even(unrealized, settlement.precision)
            ):
                raise ProjectionError("account observation is not explained by fills")
            if event is source[-2]:
                try:
                    account = PortfolioAccountObservationEntry(
                        account_id=exact.opening.account_id,
                        currency=settlement,
                        cash_balance=Money(event.cash_balance, settlement),
                        fees=Money(event.fees, settlement),
                        realized_pnl=Money(event.realized_pnl, settlement),
                        unrealized_pnl=Money(event.unrealized_pnl, settlement),
                        effective_at=event.simulation_time,
                        schema_version="portfolio-entry-v1",
                    )
                except ValueError as exc:
                    raise ProjectionError(str(exc)) from exc
                entries.append(_projected_entry(exact, event, account))

    accounting = ProjectedAccounting(
        cash_balance=_decimal(
            _round_half_even(cash, settlement.precision), field="final cash"
        ),
        position_quantity=_decimal(position, field="final position"),
        fees=_decimal(
            _round_half_even(fees, settlement.precision), field="final fees"
        ),
        realized_pnl=_decimal(
            _round_half_even(realized, settlement.precision),
            field="final realized PnL",
        ),
        unrealized_pnl=_decimal(
            _round_half_even(unrealized, settlement.precision),
            field="final unrealized PnL",
        ),
    )
    return PortfolioProjection(
        order_events=tuple(order_events),
        entries=tuple(entries),
        accounting=accounting,
        canonical_identity=projection_identity,
    )
