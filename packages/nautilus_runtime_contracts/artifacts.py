"""Strict, immutable P1 input artifact contracts."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Annotated, Any, ClassVar, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from packages.domain import FiniteDecimal
from packages.engine_contracts import (
    CanonicalUtcDateTime,
    EngineTargetPortfolio,
    Sha256Hex,
    canonical_json_bytes,
)

from .versions import (
    MAX_ENGINE_CONFIGURATION_BYTES,
    MAX_INSTRUMENT_CATALOG_BYTES,
    MAX_MARKET_DATA_MANIFEST_BYTES,
    MAX_TARGET_SCHEDULE_BYTES,
)


class P1Artifact(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )
    MAX_BYTES: ClassVar[int]


class P1EngineConfigurationV1(P1Artifact):
    MAX_BYTES = MAX_ENGINE_CONFIGURATION_BYTES

    schema_version: Literal["nautilus-p1-engine-configuration-v1"]
    venue: Literal["BINANCE"]
    account_type: Literal["CASH"]
    oms_type: Literal["NETTING"]
    starting_currency: Literal["USDT"]
    starting_balance: FiniteDecimal
    fill_model: Literal["deterministic"]
    fee_model: Literal["fixed-rate"]
    fee_rate: FiniteDecimal
    bar_execution: Literal[False]
    allow_leverage: Literal[False]
    allow_short: Literal[False]
    network_access: Literal[False]
    load_state: Literal[False]
    save_state: Literal[False]
    run_analysis: Literal[False]
    logging_bypass: Literal[True]

    @model_validator(mode="after")
    def _validate_amounts(self) -> "P1EngineConfigurationV1":
        if self.starting_balance != Decimal("1000000"):
            raise ValueError("starting_balance must match the P1 acceptance profile")
        if self.fee_rate != Decimal("0.001"):
            raise ValueError("fee_rate must match the P1 acceptance profile")
        return self


class P1InstrumentCatalogV1(P1Artifact):
    MAX_BYTES = MAX_INSTRUMENT_CATALOG_BYTES

    schema_version: Literal["nautilus-p1-instrument-catalog-v1"]
    instrument_id: Literal["BTCUSDT.BINANCE"]
    product_type: Literal["crypto_spot"]
    symbol: Literal["BTCUSDT"]
    base_currency: Literal["BTC"]
    quote_currency: Literal["USDT"]
    venue: Literal["BINANCE"]
    price_precision: Annotated[StrictInt, Field(ge=0, le=18)]
    size_precision: Annotated[StrictInt, Field(ge=0, le=18)]
    tick_size: FiniteDecimal
    step_size: FiniteDecimal
    min_quantity: FiniteDecimal
    min_notional: FiniteDecimal
    provenance_sha256: Sha256Hex

    @model_validator(mode="after")
    def _validate_increments(self) -> "P1InstrumentCatalogV1":
        if min(self.tick_size, self.step_size, self.min_quantity, self.min_notional) <= 0:
            raise ValueError("instrument increments and minimums must be positive")
        return self


class P1TargetScheduleV1(P1Artifact):
    MAX_BYTES = MAX_TARGET_SCHEDULE_BYTES

    schema_version: Literal["nautilus-p1-target-schedule-v1"]
    targets: tuple[EngineTargetPortfolio, ...] = Field(min_length=1, max_length=4_096)

    @model_validator(mode="after")
    def _validate_schedule(self) -> "P1TargetScheduleV1":
        target_ids = [target.target_id for target in self.targets]
        effective_times = [target.effective_at for target in self.targets]
        if len(target_ids) != len(set(target_ids)):
            raise ValueError("target schedule contains duplicate target IDs")
        if len(effective_times) != len(set(effective_times)):
            raise ValueError("target schedule contains duplicate effective times")
        if effective_times != sorted(effective_times):
            raise ValueError("target schedule must be ordered by effective_at")
        for target in self.targets:
            if len(target.positions) != 1:
                raise ValueError("P1 targets require exactly one position")
            position = target.positions[0]
            if (
                position.instrument.product_type.value != "crypto_spot"
                or position.instrument.symbol != "BTCUSDT"
                or position.instrument.venue != "BINANCE"
            ):
                raise ValueError("P1 target instrument is unsupported")
            if not Decimal(0) <= position.target_weight <= Decimal(1):
                raise ValueError("P1 target weight must be between zero and one")
        return self


class P1MarketDataManifestV1(P1Artifact):
    MAX_BYTES = MAX_MARKET_DATA_MANIFEST_BYTES

    schema_version: Literal["nautilus-p1-market-data-manifest-v1"]
    media_type: Literal["application/jsonl"]
    row_count: Annotated[StrictInt, Field(gt=0, le=10_000_000)]
    first_timestamp: CanonicalUtcDateTime
    last_timestamp: CanonicalUtcDateTime
    quote_bar_pair_policy: Literal["quote-then-bar"]
    timeframe: Literal["1m"]
    timestamp_policy: Literal["close"]
    data_sha256: Sha256Hex
    catalog_sha256: Sha256Hex
    normalization_version: Literal["market-normalization-v1"]

    @model_validator(mode="after")
    def _validate_window(self) -> "P1MarketDataManifestV1":
        if self.last_timestamp < self.first_timestamp:
            raise ValueError("last_timestamp must not precede first_timestamp")
        return self


P1ArtifactT = TypeVar("P1ArtifactT", bound=P1Artifact)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_float(_: str) -> Any:
    raise ValueError("float JSON values are forbidden")


def parse_canonical_artifact(
    model: type[P1ArtifactT], raw: bytes
) -> P1ArtifactT:
    """Validate bounded canonical JSON and return its immutable contract."""

    if len(raw) > model.MAX_BYTES:
        raise ValueError("artifact exceeds maximum byte size")
    if not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        raise ValueError("artifact must have one trailing newline")
    body = raw[:-1]
    try:
        json.loads(
            body,
            object_pairs_hook=_reject_duplicate_keys,
            parse_float=_reject_float,
            parse_constant=_reject_float,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("artifact is not valid UTF-8 JSON") from exc
    value = model.model_validate_json(body)
    if canonical_json_bytes(value) != body:
        raise ValueError("artifact JSON is not canonical")
    return value
