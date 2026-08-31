"""Serial target execution for the fixed P1 Nautilus profile."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType, QuoteTick
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.events import OrderFilled, OrderRejected
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy

from .target_planner import TargetPlan, TargetPlanError, plan_target


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class StrategyState:
    WAITING_FOR_TARGET = "WAITING_FOR_TARGET"
    ORDER_WORKING = "ORDER_WORKING"
    TARGET_REACHED = "TARGET_REACHED"
    EXIT_ONLY = "EXIT_ONLY"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class QuoteFact:
    instrument_id: str
    bid: str
    ask: str
    bid_size: str
    ask_size: str
    ts_event: int


@dataclass(frozen=True, slots=True)
class SubmittedOrderFact:
    client_order_id: str
    target_id: str
    source_signal_ids: tuple[str, ...]
    side: str
    quantity: str
    order_type: str


@dataclass(frozen=True, slots=True)
class FilledOrderFact:
    client_order_id: str
    trade_id: str
    side: str
    quantity: str
    price: str
    commission: str
    commission_currency: str
    ts_event: int


@dataclass(frozen=True, slots=True)
class RejectedOrderFact:
    client_order_id: str
    reason: str
    ts_event: int


class StrategyEventCollector:
    """The only strategy-owned callback/output seam."""

    def __init__(self) -> None:
        self._records: list[tuple[str, object]] = []

    def record(self, kind: str, value: object) -> None:
        expected = {
            "order_filled": FilledOrderFact,
            "order_rejected": RejectedOrderFact,
            "order_submitted": SubmittedOrderFact,
            "quote": QuoteFact,
            "stop_pending": SubmittedOrderFact,
            "stopped": str,
            "strategy_failed": str,
            "target_planned": TargetPlan,
            "target_quantity_planned": str,
        }.get(kind)
        if expected is None or type(value) is not expected:
            raise ValueError("strategy collector fact is invalid")
        self._records.append((kind, value))

    def snapshot(self) -> tuple[tuple[str, object], ...]:
        return tuple(self._records)

    def reset(self) -> None:
        self._records.clear()


class TargetStrategyConfig(StrategyConfig, frozen=True):
    """Immutable inputs for the fixed single-instrument target schedule."""

    instrument_id: InstrumentId
    bar_type: BarType
    target_schedule: tuple[tuple[str, tuple[str, ...], str, str], ...]
    fee_rate: str
    leverage: str
    min_notional: str
    min_quantity: str
    step_size: str


def _timestamp_ns(value: str) -> int:
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except (AttributeError, ValueError) as exc:
        raise ValueError("target effective time is invalid") from exc
    if parsed.isoformat().replace("+00:00", "Z") != value:
        raise ValueError("target effective time is invalid")
    delta = parsed - _EPOCH
    result = (
        (delta.days * 86_400 + delta.seconds) * 1_000_000_000
        + delta.microseconds * 1_000
    )
    if not 0 <= result <= 2**64 - 1:
        raise ValueError("target effective time is outside the native range")
    return result


def _decimal(value: str) -> Decimal:
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("strategy decimal is invalid") from exc
    if not result.is_finite():
        raise ValueError("strategy decimal is invalid")
    return result


def _text(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("native decimal is invalid")
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered in {"", "-0"} else rendered


class TargetStrategy(Strategy):
    """Submit at most one native market order for each serial target."""

    def on_start(self) -> None:
        self._collector = StrategyEventCollector()
        self._state = StrategyState.WAITING_FOR_TARGET
        self._target_index = 0
        self._processed_target_ids: list[str] = []
        self._latest_quote: QuoteTick | None = None
        self._active_order: object | None = None
        self._active_order_fact: SubmittedOrderFact | None = None
        self._active_plan: TargetPlan | None = None
        self._planned_order_quantity = "0"
        self._filled_quantity = Decimal(0)
        self._instrument = None
        if (
            type(self.config.target_schedule) is not tuple
            or not self.config.target_schedule
        ):
            self._fail("target_schedule_invalid")
            return
        try:
            effective_times = tuple(
                _timestamp_ns(item[2]) for item in self.config.target_schedule
            )
        except (IndexError, TypeError, ValueError):
            self._fail("target_schedule_invalid")
            return
        if effective_times != tuple(sorted(effective_times)) or len(
            set(effective_times)
        ) != len(effective_times):
            self._fail("target_schedule_invalid")
            return
        self._effective_times = effective_times
        self._instrument = self.cache.instrument(self.config.instrument_id)
        if self._instrument is None:
            self._fail("instrument_unavailable")
            return
        self.subscribe_quote_ticks(self.config.instrument_id)
        self.subscribe_bars(self.config.bar_type)

    def on_quote_tick(self, tick: QuoteTick) -> None:
        if tick.instrument_id != self.config.instrument_id:
            self._fail("unexpected_quote")
            return
        self._latest_quote = tick
        self._collector.record(
            "quote",
            QuoteFact(
                instrument_id=str(tick.instrument_id),
                bid=_text(tick.bid_price.as_decimal()),
                ask=_text(tick.ask_price.as_decimal()),
                bid_size=_text(tick.bid_size.as_decimal()),
                ask_size=_text(tick.ask_size.as_decimal()),
                ts_event=tick.ts_event,
            ),
        )

    def on_bar(self, bar: Bar) -> None:
        if self._state in {
            StrategyState.COMPLETED,
            StrategyState.EXIT_ONLY,
            StrategyState.FAILED,
            StrategyState.ORDER_WORKING,
        }:
            return
        if bar.bar_type != self.config.bar_type:
            self._fail("unexpected_bar")
            return
        if self._state == StrategyState.TARGET_REACHED:
            self._state = StrategyState.WAITING_FOR_TARGET
        if self._target_index >= len(self.config.target_schedule):
            self._state = StrategyState.COMPLETED
            return
        if bar.ts_event < self._effective_times[self._target_index]:
            return
        quote = self._latest_quote
        if quote is None or quote.ts_event != bar.ts_event:
            self._fail("preceding_quote_unavailable")
            return
        try:
            plan = self._plan(quote)
        except (ArithmeticError, TargetPlanError, TypeError, ValueError):
            self._fail("planning_failed")
            return
        self._collector.record("target_planned", plan)
        self._planned_order_quantity = plan.delta.removeprefix("-")
        self._collector.record(
            "target_quantity_planned", self._planned_order_quantity
        )
        if plan.side is None:
            self._reach_target(plan.target_id)
            return
        side = OrderSide.BUY if plan.side == "BUY" else OrderSide.SELL
        order = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=self._instrument.make_qty(_decimal(self._planned_order_quantity)),
        )
        self._active_order = order
        self._active_order_fact = SubmittedOrderFact(
            client_order_id=str(order.client_order_id),
            target_id=plan.target_id,
            source_signal_ids=plan.source_signal_ids,
            side=plan.side,
            quantity=self._planned_order_quantity,
            order_type="MARKET",
        )
        self._active_plan = plan
        self._filled_quantity = Decimal(0)
        self._state = StrategyState.ORDER_WORKING
        self.submit_order(order)
        self._collector.record("order_submitted", self._active_order_fact)

    def _plan(self, quote: QuoteTick) -> TargetPlan:
        if self._instrument is None:
            raise ValueError("instrument is unavailable")
        account = self.cache.account_for_venue(self.config.instrument_id.venue)
        if account is None:
            raise ValueError("account is unavailable")
        positions = self.cache.positions_open(instrument_id=self.config.instrument_id)
        if len(positions) > 1:
            raise ValueError("multiple positions are unsupported")
        current = Decimal(0)
        if positions:
            position = positions[0]
            if position.signed_qty < 0:
                raise ValueError("short position is unsupported")
            current = position.quantity.as_decimal()
        total = account.balance_total(self._instrument.quote_currency)
        free = account.balance_free(self._instrument.quote_currency)
        if total is None or free is None:
            raise ValueError("quote balance is unavailable")
        total_decimal = total.as_decimal()
        target_id, source_signal_ids, effective_at, target_weight = (
            self.config.target_schedule[self._target_index]
        )
        return plan_target(
            target_id=target_id,
            source_signal_ids=source_signal_ids,
            effective_at=effective_at,
            target_instrument_id=str(self.config.instrument_id),
            instrument_id=str(self.config.instrument_id),
            target_weight=_decimal(target_weight),
            account_equity=total_decimal + current * quote.bid_price.as_decimal(),
            available_cash=free.as_decimal(),
            current_quantity=current,
            ask_price=(
                quote.ask_price.as_decimal()
                + self._instrument.price_increment.as_decimal()
            ),
            fee_rate=_decimal(self.config.fee_rate),
            step_size=_decimal(self.config.step_size),
            min_quantity=_decimal(self.config.min_quantity),
            min_notional=_decimal(self.config.min_notional),
            leverage=_decimal(self.config.leverage),
        )

    def on_order_filled(self, event: OrderFilled) -> None:
        order = self._active_order
        plan = self._active_plan
        if (
            self._state not in {StrategyState.ORDER_WORKING, StrategyState.EXIT_ONLY}
            or order is None
            or plan is None
            or event.client_order_id != order.client_order_id
            or event.instrument_id != self.config.instrument_id
            or event.order_side.name != plan.side
        ):
            self._fail("inconsistent_fill")
            return
        filled = event.last_qty.as_decimal()
        expected = _decimal(plan.delta.removeprefix("-"))
        if filled <= 0 or self._filled_quantity + filled > expected:
            self._fail("inconsistent_fill")
            return
        self._filled_quantity += filled
        commission = event.commission
        self._collector.record(
            "order_filled",
            FilledOrderFact(
                client_order_id=str(event.client_order_id),
                trade_id=str(event.trade_id),
                side=event.order_side.name,
                quantity=_text(event.last_qty.as_decimal()),
                price=_text(event.last_px.as_decimal()),
                commission=_text(commission.as_decimal()),
                commission_currency=str(commission.currency),
                ts_event=event.ts_event,
            ),
        )
        if self._filled_quantity != expected:
            return
        self._active_order = None
        self._active_order_fact = None
        self._active_plan = None
        if self._state == StrategyState.EXIT_ONLY:
            return
        self._reach_target(plan.target_id)

    def on_order_rejected(self, event: OrderRejected) -> None:
        self._collector.record(
            "order_rejected",
            RejectedOrderFact(
                client_order_id=str(event.client_order_id),
                reason=event.reason,
                ts_event=event.ts_event,
            ),
        )
        self._fail("order_rejected")

    def on_stop(self) -> None:
        self.unsubscribe_quote_ticks(self.config.instrument_id)
        self.unsubscribe_bars(self.config.bar_type)
        if self._state in {StrategyState.FAILED, StrategyState.COMPLETED}:
            self._collector.record("stopped", self._state)
            return
        if self._active_order is not None:
            self._state = StrategyState.EXIT_ONLY
            self._collector.record("stop_pending", self._active_order_fact)
            return
        self._state = StrategyState.EXIT_ONLY
        self._collector.record("stopped", self._state)

    def on_reset(self) -> None:
        self._collector.reset()
        self._state = StrategyState.WAITING_FOR_TARGET
        self._target_index = 0
        self._processed_target_ids.clear()
        self._latest_quote = None
        self._active_order = None
        self._active_order_fact = None
        self._active_plan = None
        self._planned_order_quantity = "0"
        self._filled_quantity = Decimal(0)
        self._instrument = None

    def finish_exit_only(self) -> None:
        if self._active_order is not None or not self._processed_target_ids:
            raise ValueError("exit-only completion is invalid")
        if self._state == StrategyState.COMPLETED:
            return
        if self._state != StrategyState.TARGET_REACHED:
            raise ValueError("exit-only completion is invalid")
        self._effective_times = self._effective_times[: self._target_index]
        self._state = StrategyState.COMPLETED

    def _reach_target(self, target_id: str) -> None:
        self._processed_target_ids.append(target_id)
        self._target_index += 1
        self._state = (
            StrategyState.COMPLETED
            if self._target_index == len(self.config.target_schedule)
            else StrategyState.TARGET_REACHED
        )

    def _fail(self, reason: str) -> None:
        self._state = StrategyState.FAILED
        self._collector.record("strategy_failed", reason)

    @property
    def state(self) -> str:
        return self._state

    @property
    def active_target_id(self) -> str | None:
        return None if self._active_plan is None else self._active_plan.target_id

    @property
    def pending_order(self) -> bool:
        return self._active_order is not None

    @property
    def planned_order_quantity(self) -> str:
        return self._planned_order_quantity

    @property
    def processed_target_ids(self) -> tuple[str, ...]:
        return tuple(self._processed_target_ids)

    @property
    def collector(self) -> StrategyEventCollector:
        return self._collector


__all__ = [
    "FilledOrderFact",
    "QuoteFact",
    "RejectedOrderFact",
    "StrategyEventCollector",
    "StrategyState",
    "SubmittedOrderFact",
    "TargetStrategy",
    "TargetStrategyConfig",
]
