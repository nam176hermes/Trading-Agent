"""Sanitized HTTP envelopes for the loopback Operator API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from packages.operator_control.contracts import (
    CommandExecutionResultV1,
    OperatorSourceStateV1,
)


class HttpContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EnvelopeMetadata(HttpContractModel):
    schema_version: Literal["1.0.0"]
    trace_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$",
    )
    generated_at: datetime


class OperatorApiError(HttpContractModel):
    code: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Z][A-Z0-9_]{0,127}$",
    )
    message: str = Field(min_length=1, max_length=512)
    details: dict[str, Any]


class OperatorApiErrorEnvelope(EnvelopeMetadata):
    error: OperatorApiError


class OperatorStateData(HttpContractModel):
    state: OperatorSourceStateV1


class OperatorStateEnvelope(EnvelopeMetadata):
    data: OperatorStateData


class OperatorCommandData(HttpContractModel):
    result: CommandExecutionResultV1


class OperatorCommandEnvelope(EnvelopeMetadata):
    data: OperatorCommandData


class OperatorHealthData(HttpContractModel):
    status: Literal["UP", "READY", "NOT_READY"]


class OperatorHealthEnvelope(EnvelopeMetadata):
    data: OperatorHealthData


__all__ = [
    "OperatorApiError",
    "OperatorApiErrorEnvelope",
    "OperatorCommandData",
    "OperatorCommandEnvelope",
    "OperatorHealthData",
    "OperatorHealthEnvelope",
    "OperatorStateData",
    "OperatorStateEnvelope",
]
