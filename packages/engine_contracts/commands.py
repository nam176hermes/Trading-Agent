"""Closed engine-neutral command contracts for protocol version 1."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from types import MappingProxyType
from typing import Annotated, Any, Literal, TypeAlias
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from packages.domain import (
    Currency,
    FiniteDecimal,
    OrderSide,
    OrderType,
    ProductType,
    TimeInForce,
)

from .serialization import CanonicalUtcDateTime, Sha256Hex
from .versions import SchemaVersion


CommandName: TypeAlias = Literal[
    "DescribeEngineCapabilities",
    "ValidateEngineConfiguration",
    "ValidateInstrumentCatalog",
    "ValidateStrategyConfiguration",
    "InspectEngineRun",
    "RunBacktest",
    "CancelBacktest",
    "ExportBacktestReport",
    "StartPaperEngine",
    "StopPaperEngine",
    "SubmitTargetPortfolio",
    "SubmitOrderIntent",
    "ModifyOrderIntent",
    "CancelOrderIntent",
    "CancelAllOrders",
    "ClosePositionIntent",
    "RequestExecutionReconciliation",
]


class EngineModel(BaseModel):
    """Common strict immutable engine-contract configuration."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )


class CommandModel(EngineModel):
    """Base for members of the closed command family."""


class ArtifactReference(EngineModel):
    """Content-addressed, engine-neutral input to a command."""

    artifact_id: UUID
    sha256: Sha256Hex
    media_type: Literal["application/json", "application/jsonl"]


class EngineInstrumentId(EngineModel):
    """Canonical engine-facing instrument identity."""

    symbol: Annotated[
        str,
        Field(
            min_length=1,
            max_length=32,
            pattern=r"^[A-Z0-9][A-Z0-9._-]{0,31}$",
        ),
    ]
    product_type: ProductType
    venue: Annotated[
        str,
        Field(
            min_length=1,
            max_length=32,
            pattern=r"^[A-Z0-9][A-Z0-9._-]{0,31}$",
        ),
    ]


class EngineQuantity(EngineModel):
    """Exact engine-facing quantity with bounded fractional precision."""

    value: FiniteDecimal
    precision: Annotated[StrictInt, Field(ge=0, le=18)]

    @model_validator(mode="after")
    def _validate_precision(self) -> "EngineQuantity":
        _, digits, raw_exponent = self.value.as_tuple()
        exponent = int(raw_exponent)
        trailing_zeros = 0
        for index in range(len(digits) - 1, 0, -1):
            if digits[index] != 0:
                break
            trailing_zeros += 1
        exponent += trailing_zeros
        if max(0, -exponent) > self.precision:
            raise ValueError("value has more fractional digits than precision")
        return self


class EnginePrice(EngineModel):
    """Exact strictly positive engine-facing price."""

    amount: FiniteDecimal
    currency: Currency

    @model_validator(mode="after")
    def _positive_amount(self) -> "EnginePrice":
        if self.amount <= 0:
            raise ValueError("price amount must be positive")
        return self


class EngineTargetPosition(EngineModel):
    instrument: EngineInstrumentId
    target_weight: FiniteDecimal


class EngineTargetPortfolio(EngineModel):
    """Canonical v1 target portfolio accepted by the engine boundary."""

    target_id: UUID
    positions: tuple[EngineTargetPosition, ...]
    source_signal_ids: tuple[UUID, ...] = Field(min_length=1)
    effective_at: CanonicalUtcDateTime
    schema_version: SchemaVersion

    @model_validator(mode="after")
    def _validate_portfolio(self) -> "EngineTargetPortfolio":
        instruments = [
            (
                position.instrument.product_type,
                position.instrument.venue,
                position.instrument.symbol,
            )
            for position in self.positions
        ]
        if len(instruments) != len(set(instruments)):
            raise ValueError("positions contain duplicate instruments")
        if len(self.source_signal_ids) != len(set(self.source_signal_ids)):
            raise ValueError("source_signal_ids contain duplicates")
        total_weight = sum(
            (abs(position.target_weight) for position in self.positions), Decimal(0)
        )
        if total_weight > Decimal(1):
            raise ValueError("total absolute target weight must be <= 1")
        return self


class EngineOrderIntent(EngineModel):
    """Canonical v1 order intent accepted by the engine boundary."""

    intent_id: UUID
    risk_decision_id: UUID
    instrument: EngineInstrumentId
    side: OrderSide
    order_type: OrderType
    time_in_force: TimeInForce
    quantity: EngineQuantity
    limit_price: EnginePrice | None = None
    requested_at: CanonicalUtcDateTime
    schema_version: SchemaVersion

    @model_validator(mode="after")
    def _validate_order(self) -> "EngineOrderIntent":
        if self.quantity.value <= 0:
            raise ValueError("quantity must be positive")
        if self.order_type is OrderType.LIMIT and self.limit_price is None:
            raise ValueError("limit orders require limit_price")
        if self.order_type is OrderType.MARKET and self.limit_price is not None:
            raise ValueError("market orders reject limit_price")
        return self


class DescribeEngineCapabilities(CommandModel):
    command_type: Literal["DescribeEngineCapabilities"]


class ValidateEngineConfiguration(CommandModel):
    command_type: Literal["ValidateEngineConfiguration"]
    engine_configuration: ArtifactReference


class ValidateInstrumentCatalog(CommandModel):
    command_type: Literal["ValidateInstrumentCatalog"]
    instrument_catalog: ArtifactReference


class ValidateStrategyConfiguration(CommandModel):
    command_type: Literal["ValidateStrategyConfiguration"]
    strategy_configuration: ArtifactReference


class InspectEngineRun(CommandModel):
    command_type: Literal["InspectEngineRun"]
    target_engine_run_id: UUID


class RunBacktest(CommandModel):
    command_type: Literal["RunBacktest"]
    engine_configuration: ArtifactReference
    instrument_catalog: ArtifactReference
    strategy_configuration: ArtifactReference
    market_data: ArtifactReference
    start_time: CanonicalUtcDateTime
    end_time: CanonicalUtcDateTime

    @model_validator(mode="after")
    def _validate_window(self) -> "RunBacktest":
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


class CancelBacktest(CommandModel):
    command_type: Literal["CancelBacktest"]
    target_engine_run_id: UUID


class ExportBacktestReport(CommandModel):
    command_type: Literal["ExportBacktestReport"]
    target_engine_run_id: UUID
    report_format: Literal["json"]


class StartPaperEngine(CommandModel):
    command_type: Literal["StartPaperEngine"]
    engine_configuration: ArtifactReference
    instrument_catalog: ArtifactReference
    strategy_configuration: ArtifactReference


class StopPaperEngine(CommandModel):
    command_type: Literal["StopPaperEngine"]
    target_engine_run_id: UUID


class SubmitTargetPortfolio(CommandModel):
    command_type: Literal["SubmitTargetPortfolio"]
    target_portfolio: EngineTargetPortfolio


class SubmitOrderIntent(CommandModel):
    command_type: Literal["SubmitOrderIntent"]
    order_intent: EngineOrderIntent


class ModifyOrderIntent(CommandModel):
    command_type: Literal["ModifyOrderIntent"]
    order_id: UUID
    replacement_order_intent: EngineOrderIntent


class CancelOrderIntent(CommandModel):
    command_type: Literal["CancelOrderIntent"]
    order_id: UUID


class CancelAllOrders(CommandModel):
    command_type: Literal["CancelAllOrders"]


class ClosePositionIntent(CommandModel):
    command_type: Literal["ClosePositionIntent"]
    position_id: UUID


class RequestExecutionReconciliation(CommandModel):
    command_type: Literal["RequestExecutionReconciliation"]


EngineCommand: TypeAlias = (
    DescribeEngineCapabilities
    | ValidateEngineConfiguration
    | ValidateInstrumentCatalog
    | ValidateStrategyConfiguration
    | InspectEngineRun
    | RunBacktest
    | CancelBacktest
    | ExportBacktestReport
    | StartPaperEngine
    | StopPaperEngine
    | SubmitTargetPortfolio
    | SubmitOrderIntent
    | ModifyOrderIntent
    | CancelOrderIntent
    | CancelAllOrders
    | ClosePositionIntent
    | RequestExecutionReconciliation
)

_COMMAND_MODELS = {
    model.model_fields["command_type"].annotation.__args__[0]: model
    for model in (
        DescribeEngineCapabilities,
        ValidateEngineConfiguration,
        ValidateInstrumentCatalog,
        ValidateStrategyConfiguration,
        InspectEngineRun,
        RunBacktest,
        CancelBacktest,
        ExportBacktestReport,
        StartPaperEngine,
        StopPaperEngine,
        SubmitTargetPortfolio,
        SubmitOrderIntent,
        ModifyOrderIntent,
        CancelOrderIntent,
        CancelAllOrders,
        ClosePositionIntent,
        RequestExecutionReconciliation,
    )
}
COMMAND_MODELS: Mapping[str, type[CommandModel]] = MappingProxyType(_COMMAND_MODELS)
COMMAND_TYPES = tuple(COMMAND_MODELS)


def parse_command(value: Mapping[str, Any]) -> EngineCommand:
    """Parse a command through the closed v1 registry."""

    if not isinstance(value, Mapping):
        raise ValueError("engine command must be an object")
    command_type = value.get("command_type")
    if not isinstance(command_type, str) or command_type not in COMMAND_MODELS:
        raise ValueError(f"unsupported engine command: {command_type!r}")
    return COMMAND_MODELS[command_type].model_validate(dict(value))
