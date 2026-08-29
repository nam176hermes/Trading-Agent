"""Convert the fixed P1 canonical JSONL feed into native Nautilus data."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from nautilus_trader.model.data import Bar, BarType, QuoteTick
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Price, Quantity

from .generated_protocol import ProtocolValidationError, canonical_json_bytes
from .input_loader import RuntimeInputs
from .instrument_factory import _PRICE_MAX, _QUANTITY_MAX


_ROW_KEYS = {
    "ask",
    "bid",
    "close",
    "event_time",
    "high",
    "low",
    "open",
    "quote_time",
    "sequence",
    "volume",
}
_SEMANTIC_DOMAIN = b"nautilus-p1-market-data-semantic-v1\0"
_DECIMAL = re.compile(r"(?:0|[1-9]\d*|(?:0|[1-9]\d*)\.\d*[1-9])", re.ASCII)
_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", re.ASCII
)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class MarketDataError(ValueError):
    """The P1 market-data batch failed closed validation."""


@dataclass(frozen=True, slots=True)
class MarketDataBatch:
    data: tuple[object, ...]
    row_count: int
    raw_sha256: str
    semantic_sha256: str


def _is_exact_type(value: object, expected: type[object]) -> bool:
    return type(value) is expected


def _reject_number(_value: str) -> object:
    raise MarketDataError("market-data numbers must use canonical strings")


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in items:
        if key in value:
            raise MarketDataError("market-data row contains a duplicate key")
        value[key] = item
    return value


def _timestamp(value: object, label: str) -> datetime:
    if type(value) is not str or _TIMESTAMP.fullmatch(value) is None:
        raise MarketDataError(f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise MarketDataError(f"{label} is invalid") from exc
    if parsed.isoformat().replace("+00:00", "Z") != value:
        raise MarketDataError(f"{label} is invalid")
    return parsed


def _native_decimal(
    value: object,
    *,
    label: str,
    precision: int,
    maximum: Decimal,
    allow_zero: bool,
) -> tuple[Decimal, str]:
    if type(value) is not str or _DECIMAL.fullmatch(value) is None:
        raise MarketDataError(f"{label} is not a canonical decimal")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise MarketDataError(f"{label} is invalid") from exc
    if (
        not number.is_finite()
        or number < 0
        or (not allow_zero and number == 0)
        or number > maximum
        or max(0, -number.as_tuple().exponent) > precision
    ):
        raise MarketDataError(f"{label} is outside the catalog range")
    return number, f"{number:.{precision}f}"


def _nanoseconds(value: datetime) -> int:
    delta = value - _EPOCH
    result = (
        (delta.days * 86_400 + delta.seconds) * 1_000_000_000
        + delta.microseconds * 1_000
    )
    if not 0 <= result <= 2**64 - 1:
        raise MarketDataError("timestamp is outside the native range")
    return result


def _rows(raw: bytes) -> list[dict[str, object]]:
    if not raw or not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        raise MarketDataError("market-data JSONL framing is invalid")
    rows: list[dict[str, object]] = []
    for line in raw.splitlines(keepends=True):
        try:
            value = json.loads(
                line,
                object_pairs_hook=_pairs,
                parse_float=_reject_number,
                parse_constant=_reject_number,
            )
        except MarketDataError:
            raise
        except (RecursionError, UnicodeDecodeError, ValueError) as exc:
            raise MarketDataError("market-data row JSON is invalid") from exc
        if type(value) is not dict or set(value) != _ROW_KEYS:
            raise MarketDataError("market-data row shape is invalid")
        try:
            if canonical_json_bytes(value) + b"\n" != line:
                raise MarketDataError("market-data row is not canonical")
        except ProtocolValidationError as exc:
            raise MarketDataError("market-data row JSON is invalid") from exc
        rows.append(value)
    return rows


def load_market_data(
    inputs: RuntimeInputs, instrument: CurrencyPair
) -> MarketDataBatch:
    """Validate one bounded fixed-profile feed and build quote/bar pairs."""

    if type(inputs) is not RuntimeInputs or not _is_exact_type(
        instrument, CurrencyPair
    ):
        raise MarketDataError("exact runtime inputs and native instrument are required")
    request = inputs.request
    raw_sha256 = hashlib.sha256(inputs.market_data).hexdigest()
    if (
        request.market_data.media_type != "application/jsonl"
        or not hmac.compare_digest(raw_sha256, request.market_data.sha256)
    ):
        raise MarketDataError("market-data digest binding is invalid")
    try:
        catalog_raw = canonical_json_bytes(dict(inputs.instrument_catalog)) + b"\n"
    except ProtocolValidationError as exc:
        raise MarketDataError("instrument catalog projection is invalid") from exc
    if not hmac.compare_digest(
        hashlib.sha256(catalog_raw).hexdigest(), request.instrument_catalog.sha256
    ):
        raise MarketDataError("instrument catalog digest binding is invalid")

    catalog = dict(inputs.instrument_catalog)
    configuration = dict(inputs.engine_configuration)
    price_precision = catalog.get("price_precision")
    size_precision = catalog.get("size_precision")
    if (
        configuration.get("bar_execution") is not False
        or type(price_precision) is not int
        or type(size_precision) is not int
        or not 0 <= price_precision <= 16
        or not 0 <= size_precision <= 16
        or str(instrument.id) != catalog.get("instrument_id")
        or instrument.price_precision != price_precision
        or instrument.size_precision != size_precision
    ):
        raise MarketDataError("market-data catalog or execution profile is invalid")
    tick_size = _native_decimal(
        catalog.get("tick_size"),
        label="catalog tick size",
        precision=price_precision,
        maximum=_PRICE_MAX,
        allow_zero=False,
    )[0]
    step_size = _native_decimal(
        catalog.get("step_size"),
        label="catalog step size",
        precision=size_precision,
        maximum=_QUANTITY_MAX,
        allow_zero=False,
    )[0]
    min_quantity = _native_decimal(
        catalog.get("min_quantity"),
        label="catalog minimum quantity",
        precision=size_precision,
        maximum=_QUANTITY_MAX,
        allow_zero=False,
    )[0]
    try:
        min_notional = Decimal(str(catalog.get("min_notional")))
    except InvalidOperation as exc:
        raise MarketDataError("catalog minimum notional is invalid") from exc
    if (
        str(instrument.id.venue) != catalog.get("venue")
        or str(instrument.raw_symbol) != catalog.get("symbol")
        or str(instrument.base_currency) != catalog.get("base_currency")
        or str(instrument.quote_currency) != catalog.get("quote_currency")
        or str(instrument.get_settlement_currency()) != catalog.get("quote_currency")
        or instrument.price_increment.as_decimal() != tick_size
        or instrument.size_increment.as_decimal() != step_size
        or instrument.min_quantity is None
        or instrument.min_notional is None
        or instrument.min_quantity.as_decimal() != min_quantity
        or instrument.min_notional.as_decimal() != min_notional
        or str(instrument.min_notional.currency) != catalog.get("quote_currency")
    ):
        raise MarketDataError("native instrument does not match the catalog")

    start = _timestamp(request.start_time, "command start time")
    end = _timestamp(request.end_time, "command end time")
    rows = _rows(inputs.market_data)
    window = end - start
    if window % timedelta(minutes=1):
        raise MarketDataError("market-data command window is not minute-aligned")
    expected_count = int(window // timedelta(minutes=1)) + 1
    if len(rows) != expected_count:
        raise MarketDataError("market-data row count or cadence is invalid")

    bar_type = BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL")
    data: list[object] = []
    previous_quote: datetime | None = None
    for index, row in enumerate(rows, start=1):
        sequence = row["sequence"]
        quote_time = _timestamp(row["quote_time"], "quote time")
        event_time = _timestamp(row["event_time"], "bar close time")
        expected_time = start + timedelta(minutes=index - 1)
        if (
            type(sequence) is not int
            or sequence != index
            or event_time != expected_time
            or not start <= quote_time <= event_time <= end
            or (previous_quote is not None and quote_time <= previous_quote)
        ):
            raise MarketDataError("market-data ordering or window is invalid")
        previous_quote = quote_time

        values: dict[str, tuple[Decimal, str]] = {}
        for name in ("bid", "ask", "open", "high", "low", "close"):
            values[name] = _native_decimal(
                row[name],
                label=name,
                precision=price_precision,
                maximum=_PRICE_MAX,
                allow_zero=False,
            )
        volume = _native_decimal(
            row["volume"],
            label="volume",
            precision=size_precision,
            maximum=_QUANTITY_MAX,
            allow_zero=True,
        )
        if any(value[0] % tick_size for value in values.values()) or volume[0] % step_size:
            raise MarketDataError("market-data value is off the catalog increment")
        bid, ask = values["bid"][0], values["ask"][0]
        open_price, high, low, close = (
            values["open"][0],
            values["high"][0],
            values["low"][0],
            values["close"][0],
        )
        if bid > ask or low > min(open_price, close) or high < max(open_price, close) or low > high:
            raise MarketDataError("market-data price relationships are invalid")

        quote_ns = _nanoseconds(quote_time)
        close_ns = _nanoseconds(event_time)
        native_volume = Quantity.from_str(volume[1])
        data.append(
            QuoteTick(
                instrument.id,
                Price.from_str(values["bid"][1]),
                Price.from_str(values["ask"][1]),
                native_volume,
                native_volume,
                quote_ns,
                close_ns,
            )
        )
        data.append(
            Bar(
                bar_type,
                Price.from_str(values["open"][1]),
                Price.from_str(values["high"][1]),
                Price.from_str(values["low"][1]),
                Price.from_str(values["close"][1]),
                native_volume,
                close_ns,
                close_ns,
            )
        )

    return MarketDataBatch(
        data=tuple(data),
        row_count=len(rows),
        raw_sha256=raw_sha256,
        semantic_sha256=hashlib.sha256(_SEMANTIC_DOMAIN + inputs.market_data).hexdigest(),
    )


__all__ = ["MarketDataBatch", "MarketDataError", "load_market_data"]
