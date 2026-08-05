"""Strict client-controlled portions of durable research jobs."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Annotated, Any, Literal, Mapping, TypeAlias

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    TypeAdapter,
    WithJsonSchema,
    model_serializer,
    model_validator,
)

from packages.domain.clock import require_utc
from packages.engine_contracts import ArtifactReference, CanonicalUtcDateTime

from .asset_registry import APPROVED_ASSET_SYMBOLS
from .enums import JobType
from .fingerprint import validate_canonical_input_size

_SAFE_SYMBOL = re.compile(r"^[A-Za-z0-9]{1,16}$", re.ASCII)
_ASSET_WHITESPACE = " \t\r\n\f\v"
_ASSET_JSON_WHITESPACE = rf"[{_ASSET_WHITESPACE.encode('unicode_escape').decode('ascii')}]*"
_ASSET_JSON_ALTERNATIVES = "|".join(
    "".join(f"[{character.lower()}{character.upper()}]" for character in symbol)
    for symbol in sorted(APPROVED_ASSET_SYMBOLS)
)
_ASSET_JSON_PATTERN = (
    rf"^{_ASSET_JSON_WHITESPACE}(?:{_ASSET_JSON_ALTERNATIVES})"
    rf"{_ASSET_JSON_WHITESPACE}$"
)


def _canonical_asset(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("asset must be a string")
    stripped = value.strip(_ASSET_WHITESPACE)
    if not _SAFE_SYMBOL.fullmatch(stripped):
        raise ValueError("asset contains unsafe characters")
    canonical = stripped.upper()
    if canonical not in APPROVED_ASSET_SYMBOLS:
        raise ValueError("unknown asset")
    return canonical


AssetSymbol = Annotated[
    str,
    BeforeValidator(_canonical_asset),
    WithJsonSchema(
        {
            "type": "string",
            "pattern": _ASSET_JSON_PATTERN,
            "description": (
                "Case-insensitive canonical Phase 1 asset symbol; surrounding "
                "ASCII whitespace is normalized by the service."
            ),
            "x-canonical-values": sorted(APPROVED_ASSET_SYMBOLS),
        }
    ),
]
SessionId = Annotated[
    str,
    Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$", min_length=1, max_length=128),
]


def _canonical_utc_request_time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("requested_at must be a canonical UTC timestamp string")
    if not value.endswith("Z"):
        raise ValueError("requested_at must use the canonical UTC Z suffix")
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise ValueError("requested_at must be an ISO-8601 UTC timestamp") from exc
    return require_utc(parsed)


CanonicalUtcRequestTime = Annotated[
    datetime,
    BeforeValidator(_canonical_utc_request_time),
    WithJsonSchema(
        {
            "type": "string",
            "format": "date-time",
            "pattern": r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$",
        }
    ),
]


class StrictPayload(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class SnapshotPayload(StrictPayload):
    scope: Literal["default"]
    requested_as_of: None
    market_data: "MarketDataSnapshotRequest | None" = None

    @model_serializer(mode="wrap")
    def _legacy_compatible_serialization(self, handler: Any) -> Any:
        serialized = handler(self)
        if self.market_data is None:
            serialized.pop("market_data", None)
        return serialized


class MarketDataSnapshotRequest(StrictPayload):
    """Closed, paper-only acquisition intent for the injected P10 fixture."""

    provider: Literal["deterministic-provider-free-fixture-v1"]
    instrument: Literal["crypto_spot:FIXTURE:BTC"]
    timeframe: Literal["1m"]
    interval_seconds: Literal[60]
    requested_at: CanonicalUtcRequestTime
    provider_retry_limit: Literal[1]


class DebatePayload(StrictPayload):
    asset: AssetSymbol
    horizon: Literal["1d"]


class ReplayPayload(StrictPayload):
    session_id: SessionId


class BacktestPayload(StrictPayload):
    asset: AssetSymbol
    strategy_id: Literal["legacy-binary-report-v1"]
    date_from: None
    date_to: None


class EngineBacktestInput(StrictPayload):
    """Closed engine-neutral input from which a worker may derive RunBacktest."""

    engine_configuration: ArtifactReference
    instrument_catalog: ArtifactReference
    strategy_configuration: ArtifactReference
    market_data: ArtifactReference
    start_time: CanonicalUtcDateTime
    end_time: CanonicalUtcDateTime

    @model_validator(mode="after")
    def _validate_window(self) -> "EngineBacktestInput":
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


class EngineBacktestPayload(StrictPayload):
    """Explicit engine-authority form kept separate from the legacy payload."""

    engine_backtest: EngineBacktestInput


BacktestJobPayload: TypeAlias = BacktestPayload | EngineBacktestPayload
JobPayload: TypeAlias = (
    SnapshotPayload | DebatePayload | ReplayPayload | BacktestJobPayload
)

_PAYLOAD_MODELS: dict[JobType, type[StrictPayload]] = {
    JobType.SNAPSHOT: SnapshotPayload,
    JobType.DEBATE: DebatePayload,
    JobType.REPLAY: ReplayPayload,
    JobType.BACKTEST: BacktestPayload,
}
_BACKTEST_PAYLOAD_ADAPTER = TypeAdapter(BacktestJobPayload)


def parse_payload(job_type: JobType | str, value: Mapping[str, Any]) -> JobPayload:
    """Parse a payload through the model selected by the closed job type."""

    try:
        selected_type = JobType(job_type)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"unknown job type: {job_type!r}") from exc
    if not isinstance(value, Mapping):
        raise ValueError("payload must be an object")
    plain_value = dict(value)
    validate_canonical_input_size(plain_value)
    if selected_type is JobType.BACKTEST:
        return _BACKTEST_PAYLOAD_ADAPTER.validate_json(
            json.dumps(plain_value, allow_nan=False, separators=(",", ":"))
        )
    return _PAYLOAD_MODELS[selected_type].model_validate(plain_value)
