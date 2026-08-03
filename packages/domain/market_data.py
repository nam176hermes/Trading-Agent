"""Strict, deterministic, paper-only market-data domain contracts."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal
from enum import Enum
from typing import Annotated

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    WithJsonSchema,
    field_validator,
    model_validator,
)

from .clock import require_utc
from .instruments import InstrumentId, ProductType
from .primitives import CANONICAL_DECIMAL_POLICY_VERSION, FiniteDecimal


_ASCII_WHITESPACE = " \t\n\r\f\v"
_MARKET_DECIMAL_CONTEXT = Context(prec=128, Emax=127, Emin=-128)
_MAX_MARKET_DECIMAL_COEFFICIENT_DIGITS = 128
_MAX_MARKET_SYMBOL_ALIASES = 256
_MAX_CONTINUITY_CANDLES = 4_096
_MAX_CONTINUITY_ISSUES = 4_096
_SAFE_VERSION_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,63}$"
_SAFE_PROVIDER_PATTERN = r"^[a-z0-9][a-z0-9.-]{0,63}$"
_SAFE_MARKET_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,31}$", re.ASCII)
_SAFE_MARKET_ALIAS = re.compile(r"^[A-Z0-9]+(?:[._/-][A-Z0-9]+)*$", re.ASCII)
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_PROHIBITED_PROVIDER_TEXT = frozenset(
    {
        "account",
        "apikey",
        "authorization",
        "balance",
        "broker",
        "credential",
        "execution",
        "execute",
        "order",
        "password",
        "position",
        "routing",
        "secret",
        "token",
    }
)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

BoundedVersion = Annotated[
    str,
    Field(min_length=1, max_length=64, pattern=_SAFE_VERSION_PATTERN),
]
BoundedProvider = Annotated[
    str,
    Field(min_length=1, max_length=64, pattern=_SAFE_PROVIDER_PATTERN),
]
Sha256Hex = Annotated[str, Field(pattern=_SHA256_PATTERN)]


def _market_decimal_wire_schema(*, allow_zero: bool) -> dict[str, object]:
    positive_normal = [
        {
            "type": "string",
            "pattern": r"^[1-9]\d{0,127}$",
            "maxLength": 128,
        },
        {
            "type": "string",
            "pattern": r"^[1-9]\d*\.\d*[1-9]$",
            "minLength": 3,
            "maxLength": 129,
        },
        {
            "type": "string",
            "pattern": r"^0\.0{0,127}(?:[1-9]|[1-9]\d{0,126}[1-9])$",
            "minLength": 3,
            "maxLength": 257,
        },
    ]
    choices: list[dict[str, object]] = []
    if allow_zero:
        choices.append({"type": "string", "const": "0"})
    choices.extend(positive_normal)
    return {
        "type": "string",
        "anyOf": choices,
        "x-canonical-decimal-policy": CANONICAL_DECIMAL_POLICY_VERSION,
        "x-market-decimal-policy": (
            "non-negative-normal-v1" if allow_zero else "positive-normal-v1"
        ),
    }


PositiveMarketDecimal = Annotated[
    FiniteDecimal,
    WithJsonSchema(_market_decimal_wire_schema(allow_zero=False), mode="validation"),
]
NonNegativeMarketDecimal = Annotated[
    FiniteDecimal,
    WithJsonSchema(_market_decimal_wire_schema(allow_zero=True), mode="validation"),
]


def _validate_canonical_instrument_input(value: object) -> object:
    if isinstance(value, InstrumentId):
        return value
    if not isinstance(value, dict):
        raise ValueError("instrument must be a canonical object")
    if set(value) != {"symbol", "product_type", "venue"}:
        raise ValueError("instrument must contain exactly symbol, product_type, and venue")

    symbol = value["symbol"]
    venue = value["venue"]
    if not isinstance(symbol, str) or _SAFE_MARKET_SYMBOL.fullmatch(symbol) is None:
        raise ValueError("instrument symbol must already be canonical")
    if not isinstance(venue, str) or _SAFE_MARKET_SYMBOL.fullmatch(venue) is None:
        raise ValueError("instrument venue must already be canonical")

    product_type = value["product_type"]
    if not isinstance(product_type, str) or product_type not in {
        member.value for member in ProductType
    }:
        raise ValueError("instrument product_type must be canonical")
    return value


CanonicalInstrumentId = Annotated[
    InstrumentId,
    BeforeValidator(
        _validate_canonical_instrument_input,
        json_schema_input_type=dict[str, object],
    ),
    WithJsonSchema(
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "symbol": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 32,
                    "pattern": r"^[A-Z0-9][A-Z0-9._-]*$",
                },
                "product_type": {
                    "type": "string",
                    "enum": [member.value for member in ProductType],
                },
                "venue": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 32,
                    "pattern": r"^[A-Z0-9][A-Z0-9._-]*$",
                },
            },
            "required": ["symbol", "product_type", "venue"],
        },
        mode="validation",
    ),
]


class DomainModel(BaseModel):
    """Common strict immutable configuration for market-data payloads."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )


class MarketTimeframe(str, Enum):
    """Closed candle intervals represented by canonical compact names."""

    ONE_MINUTE = "1m"
    FIVE_MINUTES = "5m"
    FIFTEEN_MINUTES = "15m"
    ONE_HOUR = "1h"
    FOUR_HOURS = "4h"
    ONE_DAY = "1d"

    @property
    def interval_seconds(self) -> int:
        return {
            MarketTimeframe.ONE_MINUTE: 60,
            MarketTimeframe.FIVE_MINUTES: 300,
            MarketTimeframe.FIFTEEN_MINUTES: 900,
            MarketTimeframe.ONE_HOUR: 3_600,
            MarketTimeframe.FOUR_HOURS: 14_400,
            MarketTimeframe.ONE_DAY: 86_400,
        }[self]

    @classmethod
    def normalize(cls, value: object) -> "MarketTimeframe":
        """Resolve only the explicitly listed, bounded timeframe spellings."""

        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            raise ValueError("timeframe must be a supported string alias")
        normalized = value.strip(_ASCII_WHITESPACE)
        aliases = {
            "1m": cls.ONE_MINUTE,
            "60s": cls.ONE_MINUTE,
            "5m": cls.FIVE_MINUTES,
            "300s": cls.FIVE_MINUTES,
            "15m": cls.FIFTEEN_MINUTES,
            "900s": cls.FIFTEEN_MINUTES,
            "1h": cls.ONE_HOUR,
            "3600s": cls.ONE_HOUR,
            "4h": cls.FOUR_HOURS,
            "14400s": cls.FOUR_HOURS,
            "1d": cls.ONE_DAY,
            "86400s": cls.ONE_DAY,
        }
        try:
            return aliases[normalized]
        except KeyError as exc:
            raise ValueError("timeframe must be an explicitly supported alias") from exc


def _require_canonical_market_timeframe(value: object) -> MarketTimeframe:
    if isinstance(value, MarketTimeframe):
        return value
    if not isinstance(value, str):
        raise ValueError("timeframe must be a canonical string")
    try:
        return MarketTimeframe(value)
    except ValueError as exc:
        raise ValueError("timeframe must use a canonical wire value") from exc


def _normalized_market_alias(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("market symbol alias must be a string")
    normalized = value.strip(_ASCII_WHITESPACE).upper()
    if not normalized or len(normalized) > 32 or not normalized.isascii():
        raise ValueError("market symbol must be a bounded ASCII alias")
    return normalized


def _provider_market_alias(value: object) -> str:
    normalized = _normalized_market_alias(value)
    if _SAFE_MARKET_ALIAS.fullmatch(normalized) is None:
        raise ValueError("market symbol alias contains unsafe characters")
    compact = "".join(
        character for character in normalized.lower() if character.isalnum()
    )
    if any(term in compact for term in _PROHIBITED_PROVIDER_TEXT):
        raise ValueError("market symbol alias contains prohibited text")
    return normalized


def _canonical_market_symbol(value: object) -> str:
    normalized = _normalized_market_alias(value)
    if _SAFE_MARKET_SYMBOL.fullmatch(normalized) is None:
        raise ValueError("canonical market symbol contains unsafe characters")
    compact = "".join(
        character for character in normalized.lower() if character.isalnum()
    )
    if any(term in compact for term in _PROHIBITED_PROVIDER_TEXT):
        raise ValueError("canonical market symbol contains prohibited text")
    return normalized


def normalize_market_symbol(
    value: object,
    *,
    aliases: Mapping[str, str],
) -> str:
    """Resolve one provider alias through an explicit adapter-owned vocabulary."""

    if not isinstance(aliases, Mapping) or not aliases:
        raise ValueError("aliases must be a non-empty mapping")
    normalized_aliases: dict[str, str] = {}
    for index, (raw_alias, raw_canonical) in enumerate(aliases.items()):
        if index >= _MAX_MARKET_SYMBOL_ALIASES:
            raise ValueError("alias map exceeds the supported range")
        alias = _provider_market_alias(raw_alias)
        canonical = _canonical_market_symbol(raw_canonical)
        previous = normalized_aliases.setdefault(alias, canonical)
        if previous != canonical:
            raise ValueError("aliases contain conflicting normalized keys")
    normalized = _provider_market_alias(value)
    try:
        return normalized_aliases[normalized]
    except KeyError as exc:
        raise ValueError("market symbol is not an explicitly supported alias") from exc


def _require_market_decimal(value: Decimal, field_name: str, *, allow_zero: bool) -> Decimal:
    if not isinstance(value, Decimal):
        raise ValueError(f"{field_name} must be a Decimal instance")
    if not value.is_finite():
        raise ValueError(f"{field_name} must be finite")
    if len(value.as_tuple().digits) > _MAX_MARKET_DECIMAL_COEFFICIENT_DIGITS:
        raise ValueError(
            f"{field_name} must contain at most "
            f"{_MAX_MARKET_DECIMAL_COEFFICIENT_DIGITS} coefficient digits"
        )
    if value.is_zero() and allow_zero:
        return value
    if value <= 0:
        raise ValueError(f"{field_name} must be positive")
    if not (
        _MARKET_DECIMAL_CONTEXT.Emin
        <= value.adjusted()
        <= _MARKET_DECIMAL_CONTEXT.Emax
    ):
        raise ValueError(f"{field_name} must be a positive normal Decimal")
    if not value.is_normal(_MARKET_DECIMAL_CONTEXT):
        raise ValueError(f"{field_name} must be a positive normal Decimal")
    return value


def _require_interval_aligned(
    value: datetime,
    timeframe: MarketTimeframe,
    field_name: str = "open_time",
) -> datetime:
    require_utc(value)
    delta = value - _EPOCH
    if value.microsecond or (delta.days * 86_400 + delta.seconds) % timeframe.interval_seconds:
        raise ValueError(f"{field_name} must be aligned to the candle timeframe")
    return value


class MarketCandle(DomainModel):
    """One UTC-aligned OHLCV observation for a canonical instrument identity."""

    instrument: CanonicalInstrumentId
    timeframe: MarketTimeframe
    open_time: datetime
    open: PositiveMarketDecimal
    high: PositiveMarketDecimal
    low: PositiveMarketDecimal
    close: PositiveMarketDecimal
    volume: NonNegativeMarketDecimal

    @field_validator("timeframe", mode="before")
    @classmethod
    def _timeframe(cls, value: object) -> MarketTimeframe:
        return _require_canonical_market_timeframe(value)

    @field_validator("open_time")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return require_utc(value)

    @field_validator("open", "high", "low", "close")
    @classmethod
    def _positive_price(cls, value: Decimal, info: object) -> Decimal:
        return _require_market_decimal(value, getattr(info, "field_name", "price"), allow_zero=False)

    @field_validator("volume")
    @classmethod
    def _non_negative_volume(cls, value: Decimal) -> Decimal:
        return _require_market_decimal(value, "volume", allow_zero=True)

    @model_validator(mode="after")
    def _valid_candle(self) -> "MarketCandle":
        _require_interval_aligned(self.open_time, self.timeframe)
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("high must be >= open, close, and low")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("low must be <= open, close, and high")
        return self

    @property
    def identity(self) -> tuple[str, MarketTimeframe, datetime]:
        """Stable identity used for duplicate detection."""

        return (self.instrument.canonical, self.timeframe, self.open_time)


class MarketDataProvenance(DomainModel):
    """Bounded public-provider provenance with evidence integrity metadata."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
        json_schema_extra={
            "x-prohibited-content": [
                "credentials",
                "account-routing",
                "order-text",
                "broker-text",
                "execution-text",
            ]
        },
    )

    provider: BoundedProvider
    observed_at: datetime
    fetched_at: datetime
    raw_evidence_sha256: Sha256Hex
    schema_version: BoundedVersion
    normalization_version: BoundedVersion

    @field_validator("provider")
    @classmethod
    def _safe_provider(cls, value: str) -> str:
        compact = re.sub(r"[^a-z0-9]", "", value)
        if any(term in compact for term in _PROHIBITED_PROVIDER_TEXT):
            raise ValueError("provider contains prohibited account or execution text")
        return value

    @field_validator("observed_at", "fetched_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _temporal_order(self) -> "MarketDataProvenance":
        if self.fetched_at < self.observed_at:
            raise ValueError("fetched_at must not be before observed_at")
        return self


class MarketContinuity(DomainModel):
    """Exact continuity observation; it reports and never invents candles."""

    model_config = ConfigDict(
        json_schema_extra={
            "x-temporal-invariants": [
                "duplicate_open_times and missing_open_times are sorted and unique",
                "all issue timestamps are UTC and aligned to timeframe",
            ]
        }
    )

    timeframe: MarketTimeframe
    duplicate_open_times: tuple[datetime, ...] = Field(
        default=(),
        max_length=_MAX_CONTINUITY_ISSUES,
        json_schema_extra={"uniqueItems": True},
    )
    missing_open_times: tuple[datetime, ...] = Field(
        default=(),
        max_length=_MAX_CONTINUITY_ISSUES,
        json_schema_extra={"uniqueItems": True},
    )

    @field_validator("timeframe", mode="before")
    @classmethod
    def _timeframe(cls, value: object) -> MarketTimeframe:
        return _require_canonical_market_timeframe(value)

    @field_validator("duplicate_open_times", "missing_open_times")
    @classmethod
    def _utc_times(cls, values: tuple[datetime, ...]) -> tuple[datetime, ...]:
        return tuple(require_utc(value) for value in values)

    @model_validator(mode="after")
    def _canonical_issue_times(self) -> "MarketContinuity":
        for field_name in ("duplicate_open_times", "missing_open_times"):
            values = getattr(self, field_name)
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{field_name} must be sorted and unique")
            for value in values:
                _require_interval_aligned(value, self.timeframe, field_name)
        return self

    @property
    def is_continuous(self) -> bool:
        return not self.duplicate_open_times and not self.missing_open_times

    @classmethod
    def analyze(
        cls,
        candles: Iterable[MarketCandle],
        timeframe: MarketTimeframe | str,
    ) -> "MarketContinuity":
        normalized_timeframe = MarketTimeframe.normalize(timeframe)
        open_times: list[datetime] = []
        instrument_identity: str | None = None
        for candle in candles:
            if len(open_times) >= _MAX_CONTINUITY_CANDLES:
                raise ValueError("candle series exceeds the supported range")
            if not isinstance(candle, MarketCandle):
                raise ValueError("candles must be MarketCandle instances")
            if candle.timeframe is not normalized_timeframe:
                raise ValueError("all candles must use the requested timeframe")
            if instrument_identity is None:
                instrument_identity = candle.instrument.canonical
            elif candle.instrument.canonical != instrument_identity:
                raise ValueError("all candles must use the same instrument")
            open_times.append(candle.open_time)
        ordered = sorted(open_times)
        duplicate_open_times = tuple(
            sorted(
                {
                    value
                    for index, value in enumerate(ordered[1:], start=1)
                    if value == ordered[index - 1]
                }
            )
        )
        unique = tuple(sorted(set(ordered)))
        missing: list[datetime] = []
        interval_seconds = normalized_timeframe.interval_seconds
        interval = timedelta(seconds=interval_seconds)
        for previous, actual in zip(unique, unique[1:]):
            delta = actual - previous
            delta_seconds = delta.days * 86_400 + delta.seconds
            missing_count = (delta_seconds // interval_seconds) - 1
            if len(missing) + missing_count > _MAX_CONTINUITY_ISSUES:
                raise ValueError("continuity gap exceeds the supported range")
            missing.extend(
                previous + interval * offset
                for offset in range(1, missing_count + 1)
            )
        return cls(
            timeframe=normalized_timeframe,
            duplicate_open_times=duplicate_open_times,
            missing_open_times=tuple(missing),
        )


class MarketSnapshot(DomainModel):
    """A deterministic, provenance-bound canonical market-data observation."""

    model_config = ConfigDict(
        json_schema_extra={
            "x-temporal-invariants": [
                "candles[*].instrument == instrument",
                "candles[*].timeframe == timeframe",
                "candles[*].open_time + timeframe.interval <= provenance.observed_at",
                "provenance.observed_at <= provenance.fetched_at <= known_at",
                "normalization_version == provenance.normalization_version",
            ]
        }
    )

    instrument: CanonicalInstrumentId
    timeframe: MarketTimeframe
    candles: tuple[MarketCandle, ...] = Field(min_length=1, max_length=4096)
    provenance: MarketDataProvenance
    known_at: datetime
    schema_version: BoundedVersion
    normalization_version: BoundedVersion

    @field_validator("timeframe", mode="before")
    @classmethod
    def _timeframe(cls, value: object) -> MarketTimeframe:
        return _require_canonical_market_timeframe(value)

    @field_validator("known_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _canonicalize_candles(self) -> "MarketSnapshot":
        identities = [candle.identity for candle in self.candles]
        if len(identities) != len(set(identities)):
            raise ValueError("snapshot contains duplicate candle identities")
        if any(
            candle.instrument != self.instrument or candle.timeframe is not self.timeframe
            for candle in self.candles
        ):
            raise ValueError("snapshot candles must match its instrument and timeframe")
        if self.known_at < self.provenance.fetched_at:
            raise ValueError("known_at must not be before provenance fetched_at")
        if self.normalization_version != self.provenance.normalization_version:
            raise ValueError(
                "normalization_version must match provenance normalization_version"
            )
        interval = timedelta(seconds=self.timeframe.interval_seconds)
        if any(
            self.provenance.observed_at - candle.open_time < interval
            for candle in self.candles
        ):
            raise ValueError(
                "candle interval must close no later than provenance observed_at"
            )
        object.__setattr__(self, "candles", tuple(sorted(self.candles, key=lambda candle: candle.open_time)))
        return self

    @property
    def continuity(self) -> MarketContinuity:
        return MarketContinuity.analyze(self.candles, self.timeframe)

    def _canonical_payload(self) -> dict[str, object]:
        """Exclude derived content identity from the payload it authenticates."""

        return self.model_dump(mode="json")

    @property
    def canonical_payload_bytes(self) -> bytes:
        """Stable UTF-8 JSON bytes with sorted keys and decimal-v1 strings."""

        return json.dumps(
            self._canonical_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.canonical_payload_bytes).hexdigest()

    @property
    def snapshot_digest(self) -> str:
        return self.digest


__all__ = [
    "MarketCandle",
    "MarketContinuity",
    "MarketDataProvenance",
    "MarketSnapshot",
    "MarketTimeframe",
    "normalize_market_symbol",
]
