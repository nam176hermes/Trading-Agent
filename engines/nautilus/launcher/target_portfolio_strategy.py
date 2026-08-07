"""The single fixed Nautilus strategy permitted by the simulation launcher.

It is copied as a sealed launcher file and has no root-project imports.  All
values in its config originate from the launcher after its five mounted inputs
have passed the closed semantic grammar.
"""

from __future__ import annotations

from decimal import Decimal

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.events import OrderFilled, OrderRejected
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.trading.strategy import Strategy


def _fixed_point_text(value: Price | Quantity) -> str:
    parsed = value.as_decimal()
    if not isinstance(parsed, Decimal) or not parsed.is_finite():
        raise RuntimeError("Nautilus fill value is not a finite Decimal")
    if parsed.is_zero():
        return "0"
    rendered = format(parsed, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


class TargetPortfolioStrategyConfig(StrategyConfig, frozen=True):
    """No ambient provider, client, module, output, or network settings."""

    instrument_id: InstrumentId
    bar_type: BarType
    target_quantity: str
    scenario_id: str
    execution_plan: tuple[dict[str, object], ...]
    event_semantics: tuple[dict[str, object], ...]
    fee_rate: str
    slippage_bps: str
    liquidity_limit: str
    stale_quote_threshold_seconds: int
    stop_price: str | None
    take_profit_price: str | None
    stop_take_profit_precedence: str


class TargetPortfolioStrategy(Strategy):
    """Submit only scenario-eligible, price-bound Nautilus orders."""

    def __init__(self, config: TargetPortfolioStrategyConfig) -> None:
        super().__init__(config)
        self._submitted = False
        self._entry_filled_quantity: Quantity | None = None
        self._exit_filled_quantity: Quantity | None = None
        self._rejected = False
        self._event_index = 0
        self._active_instruction: dict[str, object] | None = None
        self._exit_instruction: dict[str, object] | None = None
        self._awaiting_exit_fill = False
        self._semantic_events: list[dict[str, object]] = []

    def on_start(self) -> None:
        self._instrument = self.cache.instrument(self.config.instrument_id)
        if self._instrument is None:
            raise RuntimeError("validated simulation instrument is unavailable")
        self.subscribe_bars(self.config.bar_type)
        self._semantic_events.append(
            {
                "event_type": "order-created",
                "quantity": self.config.target_quantity,
                "sequence": 0,
            }
        )

    def on_bar(self, bar: Bar) -> None:
        if self._event_index >= len(self.config.execution_plan):
            return
        instruction = self.config.execution_plan[self._event_index]
        self._event_index += 1
        if instruction["eligible"] is not True:
            skip_reason = instruction["skip_reason"]
            if skip_reason == "session-closed":
                record = {
                    "event_type": "session-closed",
                    "market_sequence": instruction["market_sequence"],
                    "sequence": len(self._semantic_events),
                }
            elif skip_reason == "stale-quote":
                record = {
                    "event_type": "quote-rejected",
                    "market_sequence": instruction["market_sequence"],
                    "reason": "stale",
                    "sequence": len(self._semantic_events),
                }
            elif skip_reason == "zero-liquidity":
                record = {
                    "event_type": "liquidity-rejected",
                    "market_sequence": instruction["market_sequence"],
                    "reason": "zero",
                    "sequence": len(self._semantic_events),
                }
            else:
                raise RuntimeError("validated execution skip reason is unsupported")
            self._semantic_events.append(record)
            return
        target = Quantity.from_str(str(instruction["fill_quantity"]))
        price = Price.from_str(str(instruction["entry_price"]))
        side = (
            OrderSide.BUY
            if not self.config.target_quantity.startswith("-")
            else OrderSide.SELL
        )
        self._active_instruction = instruction
        self.submit_order(
            self.order_factory.limit(
                instrument_id=self.config.instrument_id,
                order_side=side,
                quantity=target,
                price=price,
            )
        )
        self._submitted = True

    def on_order_filled(self, event: OrderFilled) -> None:
        if self._awaiting_exit_fill:
            instruction = self._exit_instruction
            if instruction is None:
                raise RuntimeError("exit fill has no validated instruction")
            self._exit_filled_quantity = (
                event.last_qty
                if self._exit_filled_quantity is None
                else self._exit_filled_quantity + event.last_qty
            )
            exit_quantity = _fixed_point_text(event.last_qty)
            if not self.config.target_quantity.startswith("-"):
                exit_quantity = f"-{exit_quantity}"
            self._semantic_events.append(
                {
                    "event_time": instruction["event_time"],
                    "event_type": "fill",
                    "price": _fixed_point_text(event.last_px),
                    "quantity": exit_quantity,
                    "sequence": len(self._semantic_events),
                }
            )
            self._semantic_events.append(
                {
                    "event_type": "position-closed",
                    "sequence": len(self._semantic_events),
                }
            )
            self._awaiting_exit_fill = False
            self._exit_instruction = None
            return
        instruction = self._active_instruction
        if instruction is None:
            raise RuntimeError("entry fill has no validated instruction")
        self._entry_filled_quantity = (
            event.last_qty
            if self._entry_filled_quantity is None
            else self._entry_filled_quantity + event.last_qty
        )
        entry_quantity = _fixed_point_text(event.last_qty)
        if self.config.target_quantity.startswith("-"):
            entry_quantity = f"-{entry_quantity}"
        self._semantic_events.append(
            {
                "event_time": instruction["event_time"],
                "event_type": "fill",
                "price": _fixed_point_text(event.last_px),
                "quantity": entry_quantity,
                "sequence": len(self._semantic_events),
            }
        )
        if instruction["exit_price"] is not None:
            self._awaiting_exit_fill = True
            self._exit_instruction = instruction
            self._semantic_events.append(
                {
                    "event_type": "exit-order-created",
                    "reason": instruction["exit_reason"],
                    "sequence": len(self._semantic_events),
                }
            )
            side = (
                OrderSide.SELL
                if not self.config.target_quantity.startswith("-")
                else OrderSide.BUY
            )
            self.submit_order(
                self.order_factory.limit(
                    instrument_id=self.config.instrument_id,
                    order_side=side,
                    quantity=event.last_qty,
                    price=Price.from_str(str(instruction["exit_price"])),
                )
            )
        self._active_instruction = None

    def on_order_rejected(self, event: OrderRejected) -> None:
        self._rejected = True

    @property
    def semantic_events(self) -> list[dict[str, object]]:
        """Return JSON-native scenario events in deterministic callback order."""

        return [dict(record) for record in self._semantic_events]

    @property
    def entry_filled_quantity(self) -> Quantity | None:
        """Return the actual entry quantity observed from fill callbacks."""

        return self._entry_filled_quantity

    @property
    def rejected(self) -> bool:
        """Report whether the engine rejected any strategy order."""

        return self._rejected
