"""Closed engine-neutral command contracts for protocol version 1."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated, Any, Literal, TypeAlias
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from packages.domain import OrderIntent, TargetPortfolio

from .serialization import CanonicalUtcDateTime, Sha256Hex


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


class CommandModel(BaseModel):
    """Common strict immutable command configuration."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )


class ArtifactReference(CommandModel):
    """Content-addressed, engine-neutral input to a command."""

    artifact_id: UUID
    sha256: Sha256Hex
    media_type: Literal["application/json", "application/jsonl"]


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
    target_portfolio: TargetPortfolio


class SubmitOrderIntent(CommandModel):
    command_type: Literal["SubmitOrderIntent"]
    order_intent: OrderIntent


class ModifyOrderIntent(CommandModel):
    command_type: Literal["ModifyOrderIntent"]
    order_id: UUID
    replacement_order_intent: OrderIntent


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
