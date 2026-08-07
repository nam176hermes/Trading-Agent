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
from decimal import Decimal
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
    ) -> "BacktestScenarioV1":
        if any(type(value) is not bytes for value in (scenario_bytes, catalog_bytes, strategy_bytes, market_data_bytes)):
            raise BacktestScenarioError("mounted artifacts must be exact bytes")
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
        for name in ("fee_rate", "liquidity_limit", "slippage_bps"):
            _decimal_text(value.get(name), label=name)
        for name in ("stop_price", "take_profit_price"):
            if value[name] is not None:
                _decimal_text(value[name], label=name)
        events = value.get("events")
        if type(events) is not list or not events:
            raise BacktestScenarioError("scenario events are invalid")
        for index, event in enumerate(events, start=1):
            if not isinstance(event, dict) or set(event) != _EVENT_FIELDS:
                raise BacktestScenarioError("scenario event fields are missing or unknown")
            if event.get("sequence") != index or type(event.get("session_open")) is not bool:
                raise BacktestScenarioError("scenario event sequence is invalid")
            for name in ("ask", "bid", "close", "high", "low", "open", "volume"):
                _decimal_text(event.get(name), label=f"event {name}")
        try:
            parsed = cls.model_validate(value)
        except (TypeError, ValueError) as exc:
            raise BacktestScenarioError("scenario contract is invalid") from exc
        object.__setattr__(parsed, "_mounted_bytes", scenario_bytes)
        object.__setattr__(parsed, "_scenario_digest", hashlib.sha256(scenario_bytes).hexdigest())
        object.__setattr__(parsed, "_target_quantity", target_quantity)
        return parsed
