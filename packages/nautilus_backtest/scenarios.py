"""Strict, root-owned reconstruction of mounted simulation scenario bytes.

This module deliberately uses only the standard library and Pydantic.  It is
the controller-side boundary for the five sealed artifacts and must never
import the external Nautilus launcher or runtime.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import datetime, timedelta
from decimal import Context, Decimal, localcontext
from typing import Literal

from pydantic import BaseModel, ConfigDict, PrivateAttr, StrictInt


ScenarioId = Literal[
    "long-accounting",
    "short-accounting",
    "partial-fill",
    "same-bar-stop-take-profit",
    "stale-quote",
    "zero-liquidity",
    "session-boundary",
    "event-digest",
]
Sha256Hex = str

_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_DECIMAL = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]*[1-9])?$", re.ASCII)
_SCENARIO_FIELDS = {
    "catalog_sha256", "events", "fee_rate", "instrument", "liquidity_limit",
    "market_data_sha256", "scenario_id", "schema_version", "session_policy",
    "slippage_bps", "stale_quote_threshold_seconds", "stop_price",
    "stop_take_profit_precedence", "strategy_sha256", "take_profit_price",
}
_EVENT_FIELDS = {
    "ask", "bid", "close", "event_time", "high", "low", "open", "quote_time",
    "sequence", "session_open", "volume",
}
_INSTRUMENT = {"product_type": "crypto_spot", "symbol": "BTCUSDT", "venue": "BINANCE"}
_CATALOG_FIELDS = {"canonical_rows_sha256", "content_digest", "continuity", "fetched_at", "first_event_at", "importer_version", "instrument", "known_at", "last_event_at", "normalization_version", "observed_at", "parquet_sha256", "provider", "provenance_schema_version", "raw_evidence_sha256", "row_count", "schema_version", "snapshot_schema_version", "timeframe"}
_MARKET_FIELDS = {"close", "high", "low", "open", "open_time", "volume"}
_PRICE_MAX = Decimal("17014118346046")
_QUANTITY_MAX = Decimal("34028236692093")
_DECIMAL_CONTEXT = Context(prec=80, Emin=-999, Emax=999)


class BacktestScenarioError(ValueError):
    """Mounted simulation bytes are not an exact approved scenario."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _loads_exact(raw: bytes) -> dict[str, object]:
    def no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise BacktestScenarioError("scenario contains a duplicate key")
            result[key] = value
        return result

    def reject_float(_value: str) -> object:
        raise BacktestScenarioError("scenario float input is forbidden")

    try:
        value = json.loads(raw, object_pairs_hook=no_duplicates, parse_float=reject_float)
    except BacktestScenarioError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BacktestScenarioError("scenario bytes are invalid JSON") from exc
    if not isinstance(value, dict) or _canonical(value) != raw:
        raise BacktestScenarioError("scenario bytes are not canonical")
    return value


def _decimal_text(value: object, *, label: str) -> None:
    if not isinstance(value, str) or _DECIMAL.fullmatch(value) is None or value == "-0":
        raise BacktestScenarioError(f"{label} is not a canonical Decimal string")


def _decimal(value: object, *, label: str) -> Decimal:
    _decimal_text(value, label=label)
    parsed = Decimal(str(value))
    digits, exponent = len(parsed.as_tuple().digits), parsed.as_tuple().exponent
    if digits > 38 or not isinstance(exponent, int) or abs(exponent) > 38:
        raise BacktestScenarioError(f"{label} exceeds the simulation decimal bound")
    return parsed


def _timestamp(value: object, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise BacktestScenarioError(f"{label} timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise BacktestScenarioError(f"{label} timestamp is invalid") from exc
    if parsed.isoformat().replace("+00:00", "Z") != value:
        raise BacktestScenarioError(f"{label} timestamp is not canonical")
    return parsed


def _bounded(value: Decimal, *, maximum: Decimal, precision: int, label: str) -> None:
    if abs(value) > maximum:
        raise BacktestScenarioError(f"{label} exceeds the Nautilus fixed-point bound")
    if value.as_tuple().exponent < -precision:
        raise BacktestScenarioError(f"{label} exceeds fixed instrument precision")


class ScenarioEventV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    ask: str
    bid: str
    close: str
    event_time: str
    high: str
    low: str
    open: str
    quote_time: str
    sequence: StrictInt
    session_open: bool
    volume: str


class BacktestScenarioV1(BaseModel):
    """The exact v2 semantic scenario reconstructed from its mounted bytes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    catalog_sha256: Sha256Hex
    events: tuple[ScenarioEventV1, ...]
    fee_rate: Decimal
    instrument: dict[str, str]
    liquidity_limit: Decimal
    market_data_sha256: Sha256Hex
    scenario_id: ScenarioId
    schema_version: Literal["nautilus-execution-scenario-v1"]
    session_policy: Literal["explicit-open-flag-v1"]
    slippage_bps: Decimal
    stale_quote_threshold_seconds: StrictInt
    stop_price: Decimal | None
    stop_take_profit_precedence: Literal["stop-first"]
    strategy_sha256: Sha256Hex
    take_profit_price: Decimal | None

    _mounted_bytes: bytes = PrivateAttr()
    _scenario_digest: str = PrivateAttr()
    _target_quantity: Decimal = PrivateAttr()

    @property
    def mounted_bytes(self) -> bytes:
        return self._mounted_bytes

    @property
    def scenario_digest(self) -> str:
        return self._scenario_digest

    @property
    def target_quantity(self) -> Decimal:
        return self._target_quantity

    def to_mounted_bytes(self) -> bytes:
        return self._mounted_bytes

    @classmethod
    def from_mounted_artifacts(
        cls,
        *,
        scenario_bytes: bytes,
        catalog_bytes: bytes,
        strategy_bytes: bytes,
        market_data_bytes: bytes,
        start_time: datetime,
        end_time: datetime,
    ) -> "BacktestScenarioV1":
        if any(type(value) is not bytes for value in (scenario_bytes, catalog_bytes, strategy_bytes, market_data_bytes)):
            raise BacktestScenarioError("mounted artifacts must be exact bytes")
        if type(start_time) is not datetime or type(end_time) is not datetime or start_time.tzinfo is None or end_time.tzinfo is None or end_time <= start_time:
            raise BacktestScenarioError("command window is invalid")
        value = _loads_exact(scenario_bytes)
        if set(value) != _SCENARIO_FIELDS:
            raise BacktestScenarioError("scenario fields are missing or unknown")
        if value.get("schema_version") != "nautilus-execution-scenario-v1" or value.get("session_policy") != "explicit-open-flag-v1":
            raise BacktestScenarioError("scenario semantic identity is invalid")
        if value.get("instrument") != _INSTRUMENT or value.get("stop_take_profit_precedence") != "stop-first":
            raise BacktestScenarioError("scenario identity is invalid")
        for name, artifact in (("catalog_sha256", catalog_bytes), ("strategy_sha256", strategy_bytes), ("market_data_sha256", market_data_bytes)):
            observed = value.get(name)
            expected = hashlib.sha256(artifact).hexdigest()
            if not isinstance(observed, str) or _SHA256.fullmatch(observed) is None or not hmac.compare_digest(observed, expected):
                raise BacktestScenarioError(f"scenario {name} binding is invalid")
        strategy = _loads_exact(strategy_bytes)
        positions = strategy.get("positions") if isinstance(strategy, dict) else None
        if (
            set(strategy) != {"effective_at", "positions", "schema_version"}
            or strategy.get("schema_version") != "nautilus-execution-target-v1"
            or type(positions) is not list
            or len(positions) != 1
            or not isinstance(positions[0], dict)
            or set(positions[0]) != {"instrument", "target_quantity"}
            or positions[0].get("instrument") != _INSTRUMENT
        ):
            raise BacktestScenarioError("strategy target contract is invalid")
        _decimal_text(positions[0].get("target_quantity"), label="target quantity")
        target_quantity = Decimal(str(positions[0]["target_quantity"]))
        if target_quantity == 0:
            raise BacktestScenarioError("strategy target must be non-zero")
        _bounded(target_quantity, maximum=_QUANTITY_MAX, precision=6, label="target quantity")
        effective_at = _timestamp(strategy.get("effective_at"), label="strategy")
        if not start_time <= effective_at < end_time:
            raise BacktestScenarioError("strategy target is outside command window")
        catalog = _loads_exact(catalog_bytes)
        if set(catalog) != _CATALOG_FIELDS or catalog.get("schema_version") != "market-dataset-manifest-v1" or catalog.get("timeframe") != "1m" or catalog.get("instrument") != _INSTRUMENT:
            raise BacktestScenarioError("catalog contract is invalid")
        row_count = catalog.get("row_count")
        if type(row_count) is not int or not 0 < row_count <= 32:
            raise BacktestScenarioError("catalog row count is invalid")
        for name in ("canonical_rows_sha256", "content_digest", "parquet_sha256", "raw_evidence_sha256"):
            if not isinstance(catalog.get(name), str) or _SHA256.fullmatch(str(catalog[name])) is None:
                raise BacktestScenarioError("catalog digest is invalid")
        for name in ("first_event_at", "last_event_at", "observed_at", "fetched_at", "known_at"):
            _timestamp(catalog.get(name), label=f"catalog {name}")
        continuity = catalog.get("continuity")
        if not isinstance(continuity, dict) or continuity != {"duplicate_report": [], "gap_report": [], "timeframe": "1m"}:
            raise BacktestScenarioError("catalog continuity is invalid")
        if not market_data_bytes.endswith(b"\n"):
            raise BacktestScenarioError("market data must be canonical JSONL")
        lines = market_data_bytes[:-1].split(b"\n")
        if len(lines) != row_count or any(not line for line in lines):
            raise BacktestScenarioError("market data row count is invalid")
        market_rows = tuple(_loads_exact(line) for line in lines)
        if any(set(row) != _MARKET_FIELDS for row in market_rows):
            raise BacktestScenarioError("market data fields are invalid")
        for row in market_rows:
            _timestamp(row["open_time"], label="market data")
            prices = {name: _decimal(row[name], label=f"market {name}") for name in _MARKET_FIELDS - {"open_time"}}
            for name in ("open", "high", "low", "close"):
                _bounded(prices[name], maximum=_PRICE_MAX, precision=2, label=f"market {name}")
            _bounded(prices["volume"], maximum=_QUANTITY_MAX, precision=6, label="market volume")
            if prices["open"] <= 0 or prices["high"] <= 0 or prices["low"] <= 0 or prices["close"] <= 0 or prices["volume"] < 0 or prices["low"] > min(prices["open"], prices["close"]) or prices["high"] < max(prices["open"], prices["close"]):
                raise BacktestScenarioError("market data range is invalid")
        if hashlib.sha256(_canonical(list(market_rows))).hexdigest() != catalog["canonical_rows_sha256"]:
            raise BacktestScenarioError("market data does not match catalog")
        fee_rate = _decimal(value.get("fee_rate"), label="fee rate")
        liquidity_limit = _decimal(value.get("liquidity_limit"), label="liquidity limit")
        slippage_bps = _decimal(value.get("slippage_bps"), label="slippage bps")
        _bounded(liquidity_limit, maximum=_QUANTITY_MAX, precision=6, label="liquidity limit")
        if not Decimal(0) <= fee_rate < Decimal(1) or not Decimal(0) <= slippage_bps < Decimal(10_000) or liquidity_limit < 0:
            raise BacktestScenarioError("scenario decimal parameter is invalid")
        for name in ("stop_price", "take_profit_price"):
            if value[name] is not None:
                _bounded(_decimal(value[name], label=name), maximum=_PRICE_MAX, precision=2, label=name)
        threshold = value.get("stale_quote_threshold_seconds")
        if type(threshold) is not int or not 0 <= threshold <= 86_400:
            raise BacktestScenarioError("stale quote threshold is invalid")
        events = value.get("events")
        if type(events) is not list or not events:
            raise BacktestScenarioError("scenario events are invalid")
        if len(events) != len(market_rows):
            raise BacktestScenarioError("scenario events do not match market data")
        validated_events: list[dict[str, object]] = []
        previous_time: datetime | None = None
        for index, event in enumerate(events, start=1):
            if not isinstance(event, dict) or set(event) != _EVENT_FIELDS:
                raise BacktestScenarioError("scenario event fields are missing or unknown")
            if event.get("sequence") != index or type(event.get("session_open")) is not bool:
                raise BacktestScenarioError("scenario event sequence is invalid")
            event_time = _timestamp(event.get("event_time"), label="event")
            quote_time = _timestamp(event.get("quote_time"), label="quote")
            if not start_time <= event_time < end_time or quote_time > event_time or (previous_time is not None and event_time <= previous_time):
                raise BacktestScenarioError("event ordering or command window is invalid")
            previous_time = event_time
            decimals = {name: _decimal(event.get(name), label=f"event {name}") for name in ("ask", "bid", "close", "high", "low", "open", "volume")}
            for name in ("ask", "bid", "close", "high", "low", "open"):
                _bounded(decimals[name], maximum=_PRICE_MAX, precision=2, label=f"event {name}")
            _bounded(decimals["volume"], maximum=_QUANTITY_MAX, precision=6, label="event volume")
            if decimals["ask"] <= 0 or decimals["bid"] <= 0 or decimals["bid"] > decimals["ask"] or decimals["open"] <= 0 or decimals["high"] <= 0 or decimals["low"] <= 0 or decimals["close"] <= 0 or decimals["volume"] < 0 or decimals["low"] > min(decimals["open"], decimals["close"]) or decimals["high"] < max(decimals["open"], decimals["close"]):
                raise BacktestScenarioError("event range is invalid")
            if {"close": event["close"], "high": event["high"], "low": event["low"], "open": event["open"], "open_time": event["event_time"], "volume": event["volume"]} != market_rows[index - 1]:
                raise BacktestScenarioError("event does not match catalog market data")
            validated_events.append({**decimals, "event_time": event_time, "quote_time": quote_time, "session_open": event["session_open"]})
        if catalog["first_event_at"] != events[0]["event_time"] or catalog["last_event_at"] != events[-1]["event_time"]:
            raise BacktestScenarioError("catalog event boundaries are invalid")
        with localcontext(_DECIMAL_CONTEXT):
            rate = slippage_bps / Decimal(10_000)
            is_long = target_quantity > 0
            for event in validated_events:
                quote = event["ask"] if is_long else event["bid"]
                assert isinstance(quote, Decimal)
                entry = quote * (
                    Decimal(1) + rate if is_long else Decimal(1) - rate
                )
                _bounded(
                    entry,
                    maximum=_PRICE_MAX,
                    precision=38,
                    label="executable entry price",
                )
            for trigger in (value["stop_price"], value["take_profit_price"]):
                if trigger is not None:
                    exit_price = Decimal(str(trigger)) * (
                        Decimal(1) - rate if is_long else Decimal(1) + rate
                    )
                    _bounded(
                        exit_price,
                        maximum=_PRICE_MAX,
                        precision=38,
                        label="executable exit price",
                    )
        def fresh(event: dict[str, object]) -> bool:
            return event["event_time"] - event["quote_time"] <= timedelta(seconds=threshold)
        def capacity(event: dict[str, object]) -> Decimal:
            return min(event["volume"], liquidity_limit)  # type: ignore[arg-type]
        def capacity_sum(selected: list[dict[str, object]]) -> Decimal:
            with localcontext(_DECIMAL_CONTEXT):
                return sum((capacity(event) for event in selected), Decimal(0))
        scenario_id = value["scenario_id"]
        stop = None if value["stop_price"] is None else Decimal(str(value["stop_price"]))
        take = None if value["take_profit_price"] is None else Decimal(str(value["take_profit_price"]))
        if scenario_id != "same-bar-stop-take-profit" and (stop is not None or take is not None):
            raise BacktestScenarioError("scenario trigger semantic precondition is invalid")
        if scenario_id == "short-accounting" and target_quantity >= 0:
            raise BacktestScenarioError("short scenario semantic precondition is invalid")
        if scenario_id != "short-accounting" and target_quantity <= 0:
            raise BacktestScenarioError("long scenario semantic precondition is invalid")
        size = abs(target_quantity)
        if scenario_id in {"long-accounting", "short-accounting", "event-digest"} and (not all(event["session_open"] is True and fresh(event) for event in validated_events) or capacity_sum(validated_events) < size):
            raise BacktestScenarioError("scenario semantic precondition is invalid")
        if scenario_id == "partial-fill":
            available = capacity_sum([event for event in validated_events if event["session_open"] is True and fresh(event)])
            if not all(event["session_open"] is True and fresh(event) for event in validated_events) or not Decimal(0) < available < size:
                raise BacktestScenarioError("partial-fill scenario semantic precondition is invalid")
        if scenario_id == "stale-quote" and not all(event["session_open"] is True and not fresh(event) and capacity(event) > 0 for event in validated_events):
            raise BacktestScenarioError("stale-quote scenario semantic precondition is invalid")
        if scenario_id == "zero-liquidity" and (liquidity_limit != 0 or not all(event["session_open"] is True and fresh(event) and event["volume"] > 0 for event in validated_events)):
            raise BacktestScenarioError("zero-liquidity scenario semantic precondition is invalid")
        if scenario_id == "session-boundary" and (len(validated_events) < 2 or validated_events[0]["session_open"] is not False or not all(event["session_open"] is True and fresh(event) for event in validated_events[1:]) or capacity_sum(validated_events[1:]) < size):
            raise BacktestScenarioError("session-boundary scenario semantic precondition is invalid")
        if scenario_id == "same-bar-stop-take-profit":
            first = validated_events[0]
            with localcontext(_DECIMAL_CONTEXT):
                entry = first["ask"] * (Decimal(1) + slippage_bps / Decimal(10_000))  # type: ignore[operator]
            if len(validated_events) != 1 or first["session_open"] is not True or not fresh(first) or capacity(first) < size or stop is None or take is None or not stop < entry < take or not first["low"] <= stop <= first["high"] or not first["low"] <= take <= first["high"]:
                raise BacktestScenarioError("same-bar scenario semantic precondition is invalid")
        try:
            parsed = cls.model_validate(value)
        except (TypeError, ValueError) as exc:
            raise BacktestScenarioError("scenario contract is invalid") from exc
        object.__setattr__(parsed, "_mounted_bytes", scenario_bytes)
        object.__setattr__(parsed, "_scenario_digest", hashlib.sha256(scenario_bytes).hexdigest())
        object.__setattr__(parsed, "_target_quantity", target_quantity)
        return parsed
