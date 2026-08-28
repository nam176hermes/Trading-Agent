"""Construct the fixed P1 spot instrument from the validated catalog."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Currency, Money, Price, Quantity


_CATALOG_KEYS = {
    "schema_version",
    "instrument_id",
    "product_type",
    "symbol",
    "base_currency",
    "quote_currency",
    "venue",
    "price_precision",
    "size_precision",
    "tick_size",
    "step_size",
    "min_quantity",
    "min_notional",
    "provenance_sha256",
}
_DIGEST = re.compile(r"[0-9a-f]{64}", re.ASCII)
_DECIMAL = re.compile(r"(?:0|-?[1-9]\d*|-?(?:0|[1-9]\d*)\.\d*[1-9])", re.ASCII)
_CURRENCY_METADATA = {
    "BTC": (8, 0, "Bitcoin", 1),
    "USDT": (8, 0, "Tether", 1),
}
_PRICE_MAX = Decimal("9223372036")
_QUANTITY_MAX = Decimal("18446744073")


class InstrumentFactoryError(ValueError):
    """The catalog cannot produce the fixed P1 native instrument."""


def _positive_decimal(
    value: object, *, label: str, maximum: Decimal, precision: int | None = None
) -> Decimal:
    if type(value) is not str or _DECIMAL.fullmatch(value) is None:
        raise InstrumentFactoryError(f"{label} is not a canonical decimal")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise InstrumentFactoryError(f"{label} is invalid") from exc
    if not number.is_finite() or number <= 0 or number > maximum:
        raise InstrumentFactoryError(f"{label} is outside the accepted range")
    if precision is not None and -number.as_tuple().exponent != precision:
        raise InstrumentFactoryError(f"{label} does not match catalog precision")
    return number


def _currency(code: object) -> Currency:
    if type(code) is not str or code not in _CURRENCY_METADATA:
        raise InstrumentFactoryError("catalog currency is unsupported")
    precision, iso4217, name, currency_type = _CURRENCY_METADATA[code]
    currency = Currency(code, precision, iso4217, name, currency_type)
    if (
        currency.code != code
        or currency.precision != precision
        or currency.iso4217 != iso4217
        or currency.name != name
        or int(currency.currency_type) != currency_type
    ):
        raise InstrumentFactoryError("native currency identity drifted")
    return currency


def build_instrument(catalog: tuple[tuple[str, object], ...]) -> CurrencyPair:
    """Build one deterministic BTCUSDT spot pair from immutable catalog values."""

    if type(catalog) is not tuple or any(
        type(item) is not tuple or len(item) != 2 or type(item[0]) is not str
        for item in catalog
    ):
        raise InstrumentFactoryError("immutable catalog tuple is required")
    values = dict(catalog)
    if len(values) != len(catalog) or set(values) != _CATALOG_KEYS:
        raise InstrumentFactoryError("catalog shape is invalid")
    if (
        values["schema_version"] != "nautilus-p1-instrument-catalog-v1"
        or values["product_type"] != "crypto_spot"
        or values["instrument_id"] != "BTCUSDT.BINANCE"
        or values["symbol"] != "BTCUSDT"
        or values["venue"] != "BINANCE"
        or values["base_currency"] != "BTC"
        or values["quote_currency"] != "USDT"
        or type(values["provenance_sha256"]) is not str
        or _DIGEST.fullmatch(values["provenance_sha256"]) is None
    ):
        raise InstrumentFactoryError("catalog identity is unsupported")
    price_precision = values["price_precision"]
    size_precision = values["size_precision"]
    if (
        type(price_precision) is not int
        or type(size_precision) is not int
        or not 0 <= price_precision <= 16
        or not 0 <= size_precision <= 16
    ):
        raise InstrumentFactoryError("catalog precision is invalid")

    tick_size = _positive_decimal(
        values["tick_size"],
        label="tick size",
        maximum=_PRICE_MAX,
        precision=price_precision,
    )
    step_size = _positive_decimal(
        values["step_size"],
        label="step size",
        maximum=_QUANTITY_MAX,
        precision=size_precision,
    )
    min_quantity = _positive_decimal(
        values["min_quantity"],
        label="minimum quantity",
        maximum=_QUANTITY_MAX,
        precision=size_precision,
    )
    min_notional = _positive_decimal(
        values["min_notional"],
        label="minimum notional",
        maximum=_PRICE_MAX,
    )
    base = _currency(values["base_currency"])
    quote = _currency(values["quote_currency"])
    instrument_id = InstrumentId.from_str(values["instrument_id"])
    instrument = CurrencyPair(
        instrument_id=instrument_id,
        raw_symbol=instrument_id.symbol,
        base_currency=base,
        quote_currency=quote,
        price_precision=price_precision,
        size_precision=size_precision,
        price_increment=Price.from_str(str(tick_size)),
        size_increment=Quantity.from_str(str(step_size)),
        ts_event=0,
        ts_init=0,
        min_quantity=Quantity.from_str(str(min_quantity)),
        min_notional=Money(min_notional, quote),
        maker_fee=Decimal(0),
        taker_fee=Decimal(0),
    )
    if (
        str(instrument.id) != values["instrument_id"]
        or str(instrument.raw_symbol) != values["symbol"]
        or str(instrument.base_currency) != values["base_currency"]
        or str(instrument.quote_currency) != values["quote_currency"]
        or str(instrument.get_settlement_currency()) != values["quote_currency"]
        or instrument.price_precision != price_precision
        or instrument.size_precision != size_precision
        or instrument.price_increment.as_decimal() != tick_size
        or instrument.size_increment.as_decimal() != step_size
        or instrument.min_quantity.as_decimal() != min_quantity
        or instrument.min_notional.as_decimal() != min_notional
        or instrument.asset_class.name != "CRYPTOCURRENCY"
        or instrument.instrument_class.name != "SPOT"
    ):
        raise InstrumentFactoryError("native instrument identity drifted")
    return instrument


__all__ = ["InstrumentFactoryError", "build_instrument"]
