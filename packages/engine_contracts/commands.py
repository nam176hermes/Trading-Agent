"""Closed engine-neutral command contracts for protocol version 1."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from types import MappingProxyType
from typing import Annotated, Any, Literal, TypeAlias
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    WithJsonSchema,
    model_validator,
)

from packages.domain import (
    CANONICAL_DECIMAL_POLICY_VERSION,
    Currency,
    FiniteDecimal,
    OrderSide,
    OrderType,
    ProductType,
    TimeInForce,
)

from .serialization import CanonicalUtcDateTime, Sha256Hex, canonical_json
from .versions import SchemaVersion


_MAX_ENGINE_QUANTITY_COEFFICIENT_DIGITS = 128
_CANONICAL_DECIMAL_PATTERN = r"^(?:0|[1-9]\d*|(?:0|[1-9]\d*)\.\d*[1-9])$"

CommandName: TypeAlias = Literal[
    "DescribeEngineCapabilities",
    "ValidateEngineConfiguration",
    "ValidateInstrumentCatalog",
    "ValidateStrategyConfiguration",
    "InspectEngineRun",
    "RunBacktest",
    "RunBacktestSimulation",
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
EngineQuantityValue = Annotated[
    FiniteDecimal,
    WithJsonSchema(
        {
            "type": "string",
            "pattern": _CANONICAL_DECIMAL_PATTERN,
            "maxLength": 129,
            "x-canonical-decimal-policy": CANONICAL_DECIMAL_POLICY_VERSION,
        },
        mode="validation",
    ),
]
CanonicalOrderIdentifier = Annotated[
    str,
    Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$",
    ),
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
    """Exact unsigned engine-facing order quantity."""

    value: EngineQuantityValue
    precision: Annotated[StrictInt, Field(ge=0, le=18)]

    @model_validator(mode="after")
    def _validate_precision(self) -> "EngineQuantity":
        if self.value < 0:
            raise ValueError("value must be non-negative")
        target_exponent = -self.precision
        if self.value.is_zero():
            object.__setattr__(
                self,
                "value",
                Decimal((0, (0,), target_exponent)),
            )
            return self

        sign, raw_digits, raw_exponent = self.value.as_tuple()
        digits = raw_digits
        exponent = int(raw_exponent)
        trailing_zero_count = 0
        for index in range(len(digits) - 1, 0, -1):
            if digits[index] != 0:
                break
            trailing_zero_count += 1
        if trailing_zero_count:
            digits = digits[:-trailing_zero_count]
            exponent += trailing_zero_count

        fractional_digits = max(0, -exponent)
        if fractional_digits > self.precision:
            raise ValueError("value has more fractional digits than precision")

        expanded_digit_count = len(digits) + (exponent - target_exponent)
        if expanded_digit_count > _MAX_ENGINE_QUANTITY_COEFFICIENT_DIGITS:
            raise ValueError("value exceeds maximum quantity magnitude")

        canonical_digits = digits + ((0,) * (exponent - target_exponent))
        object.__setattr__(
            self,
            "value",
            Decimal((sign, canonical_digits, target_exponent)),
        )
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
    client_order_id: CanonicalOrderIdentifier
    venue_order_id: CanonicalOrderIdentifier | None = None
    strategy_id: CanonicalOrderIdentifier
    trader_id: CanonicalOrderIdentifier
    account_id: CanonicalOrderIdentifier
    execution_client_id: CanonicalOrderIdentifier
    order_list_id: CanonicalOrderIdentifier | None = None
    instrument: EngineInstrumentId
    side: OrderSide
    order_type: OrderType
    time_in_force: TimeInForce
    quantity: EngineQuantity
    limit_price: EnginePrice | None = None
    trigger_price: EnginePrice | None = None
    trailing_offset: EnginePrice | None = None
    gtd_expiry: CanonicalUtcDateTime | None = None
    post_only: bool = False
    reduce_only: bool = False
    requested_at: CanonicalUtcDateTime
    schema_version: SchemaVersion

    @model_validator(mode="after")
    def _validate_order(self) -> "EngineOrderIntent":
        if self.quantity.value <= 0:
            raise ValueError("quantity must be positive")
        limit_types = {
            OrderType.LIMIT,
            OrderType.STOP_LIMIT,
            OrderType.LIMIT_IF_TOUCHED,
        }
        trigger_types = {
            OrderType.STOP_MARKET,
            OrderType.STOP_LIMIT,
            OrderType.MARKET_IF_TOUCHED,
            OrderType.LIMIT_IF_TOUCHED,
        }
        if self.order_type in limit_types and self.limit_price is None:
            raise ValueError(f"{self.order_type.value} orders require limit_price")
        if self.order_type not in limit_types and self.limit_price is not None:
            raise ValueError(f"{self.order_type.value} orders forbid limit_price")
        if self.order_type in trigger_types and self.trigger_price is None:
            raise ValueError(f"{self.order_type.value} orders require trigger_price")
        if self.order_type not in trigger_types and self.trigger_price is not None:
            raise ValueError(f"{self.order_type.value} orders forbid trigger_price")
        if self.order_type is OrderType.TRAILING_STOP and self.trailing_offset is None:
            raise ValueError("trailing_stop orders require trailing_offset")
        if self.order_type is not OrderType.TRAILING_STOP and self.trailing_offset is not None:
            raise ValueError(f"{self.order_type.value} orders forbid trailing_offset")
        if self.time_in_force is TimeInForce.GTD:
            if self.gtd_expiry is None:
                raise ValueError("GTD orders require gtd_expiry")
            if self.gtd_expiry <= self.requested_at:
                raise ValueError("gtd_expiry must be after requested_at")
        elif self.gtd_expiry is not None:
            raise ValueError("gtd_expiry is valid only for GTD orders")
        if self.post_only and (
            self.order_type not in limit_types
            or self.time_in_force
            not in {TimeInForce.GTC, TimeInForce.DAY, TimeInForce.GTD}
        ):
            raise ValueError("post_only requires a resting limit instruction")
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


class ValidatePaperCompatibility(CommandModel):
    """Finite research-only compatibility request excluded from job parsing."""

    command_type: Literal["ValidatePaperCompatibility"]
    engine_configuration: ArtifactReference
    instrument_catalog: ArtifactReference
    strategy_configuration: ArtifactReference
    strategy_source_sha256: Sha256Hex
    scenario_campaign_sha256: Sha256Hex

    @model_validator(mode="after")
    def _validate_artifact_authority(self) -> "ValidatePaperCompatibility":
        references = (
            self.engine_configuration,
            self.instrument_catalog,
            self.strategy_configuration,
        )
        identities = tuple(
            (reference.artifact_id, reference.sha256, reference.media_type)
            for reference in references
        )
        if len(set(identities)) != len(identities):
            raise ValueError("paper compatibility contains a duplicate artifact reference")
        return self


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


class RunBacktestSimulation(CommandModel):
    """One fixture-only execution simulation with a separately bound scenario."""

    command_type: Literal["RunBacktestSimulation"]
    engine_configuration: ArtifactReference
    instrument_catalog: ArtifactReference
    strategy_configuration: ArtifactReference
    market_data: ArtifactReference
    simulation_scenario: ArtifactReference
    start_time: CanonicalUtcDateTime
    end_time: CanonicalUtcDateTime

    @model_validator(mode="after")
    def _validate_simulation(self) -> "RunBacktestSimulation":
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        references = (
            self.engine_configuration,
            self.instrument_catalog,
            self.strategy_configuration,
            self.market_data,
            self.simulation_scenario,
        )
        identities = tuple(
            (reference.artifact_id, reference.sha256, reference.media_type)
            for reference in references
        )
        if len(set(identities)) != len(identities):
            raise ValueError("simulation contains a duplicate artifact reference")
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
    | RunBacktestSimulation
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
        RunBacktestSimulation,
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
    """Parse native values or a JSON-decoded command through the closed registry."""

    if not isinstance(value, Mapping):
        raise ValueError("engine command must be an object")
    command_type = value.get("command_type")
    if not isinstance(command_type, str) or command_type not in COMMAND_MODELS:
        raise ValueError(f"unsupported engine command: {command_type!r}")
    model = COMMAND_MODELS[command_type]
    try:
        wire_json = canonical_json(value)
    except ValueError as exc:
        if "unsupported canonical engine JSON value" not in str(exc):
            raise
        return model.model_validate(dict(value))
    return model.model_validate_json(wire_json)
