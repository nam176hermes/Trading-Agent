"""Exact, sanitized HTTP envelopes published by the loopback Job API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from packages.job_contracts import JobDetail, JobMetadata


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


class HealthLiveData(HttpContractModel):
    status: Literal["UP"]


class HealthReadyData(HttpContractModel):
    status: Literal["READY", "NOT_READY"]


class HealthLiveEnvelope(EnvelopeMetadata):
    data: HealthLiveData


class HealthReadyEnvelope(EnvelopeMetadata):
    data: HealthReadyData


class JobEnqueuedData(HttpContractModel):
    outcome: Literal["ENQUEUED"]
    job: JobMetadata


class JobDeduplicatedData(HttpContractModel):
    outcome: Literal["DEDUPLICATED"]
    job: JobMetadata


class JobEnqueuedEnvelope(EnvelopeMetadata):
    data: JobEnqueuedData


class JobDeduplicatedEnvelope(EnvelopeMetadata):
    data: JobDeduplicatedData


class JobListData(HttpContractModel):
    items: tuple[JobMetadata, ...]
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)


class JobListEnvelope(EnvelopeMetadata):
    data: JobListData


class JobDetailEnvelope(EnvelopeMetadata):
    data: JobDetail


class JobEnvelope(EnvelopeMetadata):
    data: JobMetadata


class JobApiError(HttpContractModel):
    code: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Z][A-Z0-9_]{0,127}$",
    )
    message: str = Field(min_length=1, max_length=512)
    details: dict[str, Any]


class JobApiErrorEnvelope(EnvelopeMetadata):
    error: JobApiError
