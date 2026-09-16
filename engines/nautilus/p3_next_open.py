"""Pinned-engine next-open calculation, without custody or publication authority."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, localcontext
from importlib.metadata import version as package_version
import json
import sys

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import FeeModel, FillModel, LatencyModel
from nautilus_trader.common.config import LoggingConfig
from nautilus_trader.config import BacktestEngineConfig, StrategyConfig
from nautilus_trader.model.data import Bar, BarType, QuoteTick
from nautilus_trader.model.enums import AccountType, OmsType, OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Currency, Money, Price, Quantity
from nautilus_trader.trading.strategy import Strategy


INSTRUMENT = InstrumentId.from_str("BTCUSDT.BINANCE")
BAR_TYPE = BarType.from_str("BTCUSDT.BINANCE-1-DAY-LAST-EXTERNAL")
QUOTE = Currency.from_str("USDT")


def _text(value: Decimal) -> str:
    text = format(value, "f")
    return (text.rstrip("0").rstrip(".") if "." in text else text) if value else "0"


def _price(opening: Decimal, target: int) -> Decimal:
    return (opening * Decimal("1.001" if target else ".999")).quantize(
        Decimal(".01"), rounding=ROUND_CEILING if target else ROUND_FLOOR)


def _steps(value: object) -> tuple[tuple[int, Decimal, int, str], ...]:
    if type(value) is not list or not 2 <= len(value) <= 366:
        raise ValueError("E_NATIVE_RECIPE: bounded daily step array required")
    result = []
    for item in value:
        if type(item) is not dict or set(item) != {"target", "open", "boundary_ns", "source_day"}:
            raise ValueError("E_NATIVE_RECIPE: exact step fields required")
        target, opening, boundary, day = (item[k] for k in ("target", "open", "boundary_ns", "source_day"))
        if (type(target) is not int or target not in (0, 1)
            or type(opening) is not str or not 1 <= len(opening) <= 32
            or type(boundary) is not int or not 1 < boundary < 2**63 - 2
            or type(day) is not str or date.fromisoformat(day).isoformat() != day):
            raise ValueError("E_NATIVE_RECIPE: invalid step")
        price = Decimal(opening)
        if not price.is_finite() or not Decimal(".02") <= price <= Decimal("100000000"):
            raise ValueError("E_NATIVE_RECIPE: open outside native range")
        if boundary != ((date.fromisoformat(day) - date(1970, 1, 1)).days + 1) * 86400000000000:
            raise ValueError("E_NATIVE_RECIPE: signal must follow its completed UTC source day")
        if result and (boundary - result[-1][2] != 86400000000000
            or (date.fromisoformat(day) - date.fromisoformat(result[-1][3])).days != 1):
            raise ValueError("E_NATIVE_RECIPE: steps must be consecutive days")
        result.append((target, price, boundary, day))
    if result[-1][0] != 0:
        raise ValueError("E_NATIVE_RECIPE: terminal liquidation required")
    return tuple(result)


class _QuoteFee(FeeModel):
    def get_commission(self, order, fill_qty, fill_px, instrument):
        fee = (fill_qty.as_decimal() * fill_px.as_decimal() * Decimal(".001")).quantize(
            Decimal(".01"), rounding=ROUND_HALF_EVEN)
        return Money(fee, instrument.quote_currency)


class _NextOpenConfig(StrategyConfig, frozen=True):
    steps: tuple[tuple[int, Decimal, int, str], ...]


class _NextOpen(Strategy):
    def on_start(self):
        self.steps = self.config.steps
        self.rows = []
        self.index = -1
        self.pending = None
        self.fill = None
        self.quote_seen = False
        self.errors = []
        self.current = 0
        self.submitted = False

        self.subscribe_bars(BAR_TYPE)
        self.subscribe_quote_ticks(INSTRUMENT)

    def _balances(self):
        account = self.cache.account_for_venue(INSTRUMENT.venue)
        positions = self.cache.positions_open(instrument_id=INSTRUMENT)
        if account is None or len(positions) > 1 or any(p.signed_qty < 0 for p in positions):
            raise ValueError("E_NATIVE_RECIPE: invalid native account/position")
        cash = account.balance_total(QUOTE)
        free = account.balance_free(QUOTE)
        if cash is None or free is None or cash.as_decimal() < 0:
            raise ValueError("E_NATIVE_RECIPE: invalid native cash")
        return cash.as_decimal(), free.as_decimal(), positions[0].quantity.as_decimal() if positions else Decimal(0)

    def _add(self, kind, side, time_ns, *, price=None, quantity=None, fee=None):
        cash, _, position = self._balances()
        self.rows.append({
            "sequence": len(self.rows), "event_time_ns": time_ns, "init_time_ns": time_ns,
            "kind": kind, "side": side, "source_day": self.steps[self.index][3],
            "price": None if price is None else _text(price),
            "quantity": None if quantity is None else _text(quantity),
            "fee_quote": None if fee is None else _text(fee),
            "cash_after": _text(cash), "position_after": _text(position),
        })

    def finish_step(self):
        if self.index < 0:
            return
        target, opening, boundary, _ = self.steps[self.index]
        if self.errors or not self.quote_seen or (self.pending is not None and self.fill is None):
            raise ValueError("E_NATIVE_RECIPE: native order/quote did not complete")
        if self.fill is not None:
            event = self.fill
            self._add("FILL", event.order_side.name, event.ts_event,
                price=event.last_px.as_decimal(), quantity=event.last_qty.as_decimal(),
                fee=event.commission.as_decimal())
        cash, _, position = self._balances()
        if int(position > 0) != target or cash < 0:
            raise ValueError("E_NATIVE_RECIPE: native ending state differs")
        kind = "LIQUIDATION" if self.current and not target and self.index == len(self.steps) - 1 else "MARK"
        self._add(kind, "NONE", boundary + 1,
            price=opening.quantize(Decimal(".01"), rounding=ROUND_HALF_EVEN))

    def on_bar(self, bar):
        self.finish_step()
        self.index += 1
        target, opening, boundary, _ = self.steps[self.index]
        if bar.ts_event != boundary or bar.bar_type != BAR_TYPE:
            raise ValueError("E_NATIVE_RECIPE: unexpected signal clock")
        self.pending, self.fill, self.quote_seen, self.submitted = None, None, False, False
        _, free, position = self._balances()
        self.current = int(position > 0)
        self._add("SIGNAL", "NONE", boundary)
        if target == self.current:
            return
        price = _price(opening, target)
        quantity = ((free * Decimal(".9975") / (price * Decimal("1.001"))).quantize(
            Decimal(".00001"), rounding=ROUND_FLOOR) if target else position)
        if quantity <= 0 or quantity * price < 10:
            raise ValueError("E_NATIVE_RECIPE: quantity/minimum notional policy failed")
        self.pending = self.order_factory.market(instrument_id=INSTRUMENT,
            order_side=OrderSide.BUY if target else OrderSide.SELL,
            quantity=Quantity(quantity, 5))
        self.submit_order(self.pending)

    def on_order_submitted(self, event):
        if (self.pending is None or self.submitted or self.quote_seen
            or event.client_order_id != self.pending.client_order_id
            or event.ts_event != self.steps[self.index][2] or event.ts_init != event.ts_event):
            raise ValueError("E_NATIVE_RECIPE: native submission order differs")
        self.submitted = True
        self._add("ORDER", self.pending.side.name, event.ts_event)

    def on_quote_tick(self, tick):
        # Zero-size ticks withdraw liquidity; only the +1 ns tick is executable.
        if tick.bid_size.as_decimal() == 0 and tick.ask_size.as_decimal() == 0:
            return
        if (self.index < 0 or tick.ts_event != self.steps[self.index][2] + 1
            or tick.ts_init != tick.ts_event or self.quote_seen):
            raise ValueError("E_NATIVE_RECIPE: unexpected executable quote")
        self.quote_seen = True
        if self.pending is not None:
            if not self.submitted:
                raise ValueError("E_NATIVE_RECIPE: quote precedes native submission")
            self._add("QUOTE", self.pending.side.name, tick.ts_event,
                price=tick.ask_price.as_decimal() if self.steps[self.index][0] else tick.bid_price.as_decimal())

    def on_order_filled(self, event):
        target, opening, boundary, _ = self.steps[self.index]
        if (self.pending is None or self.fill is not None or not self.quote_seen
            or event.client_order_id != self.pending.client_order_id
            or event.instrument_id != INSTRUMENT or event.order_side != self.pending.side
            or event.ts_event != boundary + 1 or event.ts_init != event.ts_event
            or event.last_qty != self.pending.quantity
            or event.last_px.as_decimal() != _price(opening, target)
            or event.commission.currency != QUOTE):
            raise ValueError("E_NATIVE_RECIPE: early, partial or inconsistent native fill")
        self.fill = event

    def on_order_denied(self, event):
        self.errors.append(str(event))

    def on_order_rejected(self, event):
        self.errors.append(str(event))

    def on_order_canceled(self, event):
        self.errors.append(str(event))

    def on_order_expired(self, event):
        self.errors.append(str(event))


def run_next_open(value: object) -> list[dict[str, object]]:
    if package_version("nautilus_trader") != "1.231.0":
        raise ValueError("E_NATIVE_RECIPE: pinned engine required")
    with localcontext(prec=50, rounding=ROUND_HALF_EVEN):
        steps = _steps(value)
        engine = BacktestEngine(BacktestEngineConfig(load_state=False, save_state=False,
            run_analysis=False, logging=LoggingConfig(bypass_logging=True)))
        try:
            instrument = CurrencyPair(instrument_id=INSTRUMENT, raw_symbol=INSTRUMENT.symbol,
                base_currency=Currency.from_str("BTC"), quote_currency=QUOTE,
                price_precision=2, size_precision=5, price_increment=Price.from_str(".01"),
                size_increment=Quantity.from_str(".00001"), ts_event=0, ts_init=0,
                min_quantity=Quantity.from_str(".00001"), min_notional=Money(10, QUOTE),
                maker_fee=Decimal(0), taker_fee=Decimal(".001"))
            engine.add_venue(venue=INSTRUMENT.venue, oms_type=OmsType.NETTING,
                account_type=AccountType.CASH, starting_balances=[Money(100000, QUOTE)],
                fill_model=FillModel(), fee_model=_QuoteFee(),
                latency_model=LatencyModel(base_latency_nanos=1), bar_execution=False,
                allow_cash_borrowing=False, use_random_ids=False, liquidity_consumption=True)
            engine.add_instrument(instrument)
            data = []
            for _, opening, boundary, _ in steps:
                bid, ask = Price(_price(opening, 0), 2), Price(_price(opening, 1), 2)
                # Internal book control, not observed market data. The bar only clocks
                # a precomputed target; native bar execution is disabled.
                for offset, size in ((-1, "0.00000"), (1, "100000000.00000")):
                    data.append(QuoteTick(INSTRUMENT, bid, ask, Quantity.from_str(size),
                        Quantity.from_str(size), boundary + offset, boundary + offset))
                price = Price(opening.quantize(Decimal(".01")), 2)
                data.append(Bar(BAR_TYPE, price, price, price, price,
                    Quantity.from_str("0.00000"), boundary, boundary))
            strategy = _NextOpen(_NextOpenConfig(steps=steps))
            engine.add_strategy(strategy)
            engine.add_data(data, sort=True)
            engine.run()
            strategy.finish_step()
            if strategy.index != len(steps) - 1 or strategy._balances()[2] != 0:
                raise ValueError("E_NATIVE_RECIPE: incomplete native run")
            return strategy.rows
        finally:
            engine.dispose()


if __name__ == "__main__":
    raw = sys.stdin.buffer.read(131073)
    if len(raw) > 131072:
        raise ValueError("E_NATIVE_RECIPE: oversized input")
    value = json.loads(raw)
    if json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode() != raw:
        raise ValueError("E_NATIVE_RECIPE: canonical input required")
    print(json.dumps(run_next_open(value), sort_keys=True, separators=(",", ":"), allow_nan=False))
