"""The single fixed Nautilus strategy permitted by the simulation launcher.

It is copied as a sealed launcher file and has no root-project imports.  All
values in its config originate from the launcher after its five mounted inputs
have passed the closed semantic grammar.
"""

from __future__ import annotations

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.events import OrderFilled, OrderRejected
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.trading.strategy import Strategy


class TargetPortfolioStrategyConfig(StrategyConfig, frozen=True):
    """No ambient provider, client, module, output, or network settings."""

    instrument_id: InstrumentId
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
        self._awaiting_exit_fill = False

    def on_start(self) -> None:
        self._instrument = self.cache.instrument(self.config.instrument_id)
        if self._instrument is None:
            raise RuntimeError("validated simulation instrument is unavailable")

    def on_bar(self, bar: Bar) -> None:
        if self._event_index >= len(self.config.execution_plan):
            return
        instruction = self.config.execution_plan[self._event_index]
        self._event_index += 1
        if instruction["eligible"] is not True:
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
            self._exit_filled_quantity = (
                event.last_qty
                if self._exit_filled_quantity is None
                else self._exit_filled_quantity + event.last_qty
            )
            self._awaiting_exit_fill = False
            return
        self._entry_filled_quantity = (
            event.last_qty
            if self._entry_filled_quantity is None
            else self._entry_filled_quantity + event.last_qty
        )
        instruction = self._active_instruction
        if instruction is not None and instruction["exit_price"] is not None:
            self._awaiting_exit_fill = True
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
