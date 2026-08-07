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
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy


class TargetPortfolioStrategyConfig(StrategyConfig, frozen=True):
    """No ambient provider, client, module, output, or network settings."""

    instrument_id: InstrumentId
    target_quantity: str


class TargetPortfolioStrategy(Strategy):
    """Submit one actual market order and retain only Nautilus callback state."""

    def __init__(self, config: TargetPortfolioStrategyConfig) -> None:
        super().__init__(config)
        self._submitted = False
        self._filled_quantity: Quantity | None = None
        self._rejected = False

    def on_start(self) -> None:
        self._instrument = self.cache.instrument(self.config.instrument_id)
        if self._instrument is None:
            raise RuntimeError("validated simulation instrument is unavailable")

    def on_bar(self, bar: Bar) -> None:
        if self._submitted:
            return
        target = Quantity.from_str(self.config.target_quantity.lstrip("-"))
        side = OrderSide.BUY if not self.config.target_quantity.startswith("-") else OrderSide.SELL
        self.submit_order(self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=target,
        ))
        self._submitted = True

    def on_order_filled(self, event: OrderFilled) -> None:
        self._filled_quantity = event.last_qty if self._filled_quantity is None else self._filled_quantity + event.last_qty

    def on_order_rejected(self, event: OrderRejected) -> None:
        self._rejected = True
