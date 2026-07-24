"""Sanitized DTOs for the loopback Job API boundary."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, GetJsonSchemaHandler, model_validator
from pydantic.json_schema import JsonSchemaValue

from .enums import ActorType, JobState, JobType
from .payloads import JobPayload, parse_payload


OpaqueId = str
Sha256Hex = str
_RESERVED_SCHEDULE_PREFIX_PATTERN = r"^schedule:"
_GREGORIAN_YEAR_PATTERN = (
    r"(?:[0-9]{3}[1-9]|[0-9]{2}[1-9][0-9]|"
    r"[0-9][1-9][0-9]{2}|[1-9][0-9]{3})"
)
_GREGORIAN_MONTH_DAY_PATTERN = (
    r"(?:(?:0[13578]|1[02])-(?:0[1-9]|[12][0-9]|3[01])|"
    r"(?:0[469]|11)-(?:0[1-9]|[12][0-9]|30)|"
    r"02-(?:0[1-9]|1[0-9]|2[0-8]))"
)
_GREGORIAN_LEAP_YEAR_PATTERN = (
    r"(?:[0-9]{2}(?:0[48]|[2468][048]|[13579][26])|"
    r"(?:0[48]|[2468][048]|[13579][26])00)"
)
_SCHEDULE_KEY_PATTERN = (
    rf"^schedule:snapshot:(?:{_GREGORIAN_YEAR_PATTERN}-"
    rf"{_GREGORIAN_MONTH_DAY_PATTERN}|"
    rf"{_GREGORIAN_LEAP_YEAR_PATTERN}-02-29)T"
    r"(?:[01][0-9]|2[0-3]):[0-5][0-9]Z$"
)
_SCHEDULE_KEY = re.compile(_SCHEDULE_KEY_PATTERN, re.ASCII)


def _valid_schedule_key(value: str) -> bool:
    if _SCHEDULE_KEY.fullmatch(value) is None:
        return False
    try:
        datetime.strptime(
            value.removeprefix("schedule:snapshot:"), "%Y-%m-%dT%H:%MZ"
        )
    except ValueError:
        return False
    return True


class StrictApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _parse_typed_payload(value: Any) -> Any:
    """Cross-validate a job payload through the job-type registry parser."""

    if not isinstance(value, Mapping):
        return value
    data = dict(value)
    if "job_type" in data and "payload" in data:
        payload = data["payload"]
        if isinstance(payload, BaseModel):
            payload = payload.model_dump()
        data["payload"] = parse_payload(data["job_type"], payload)
    return data


class ActorIdentity(StrictApiModel):
    actor_type: ActorType
    actor_id: OpaqueId = Field(min_length=1, max_length=128)


class EnqueueJobBody(StrictApiModel):
    """Client-controlled enqueue fields; actor identity comes from authentication."""

    job_type: JobType
    payload: JobPayload
    idempotency_key: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$",
    )
    priority: int = Field(default=0, ge=0, le=100)

    @model_validator(mode="before")
    @classmethod
    def parse_typed_payload(cls, value: Any) -> Any:
        return _parse_typed_payload(value)

    @model_validator(mode="after")
    def reserve_scheduler_namespace(self) -> "EnqueueJobBody":
        if self.idempotency_key.startswith("schedule:"):
            raise ValueError("idempotency namespace is reserved")
        return self

    @classmethod
    def __get_pydantic_json_schema__(
        cls, core_schema: Any, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        schema = handler(core_schema)
        properties = schema["properties"]
        payload_options = properties["payload"]["anyOf"]
        shared = {
            "idempotency_key": {
                **properties["idempotency_key"],
                "not": {"pattern": _RESERVED_SCHEDULE_PREFIX_PATTERN},
            },
            "priority": properties["priority"],
        }
        variants = []
        for job_type, payload_schema in zip(JobType, payload_options, strict=True):
            variants.append(
                {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "job_type": {"type": "string", "const": job_type.value},
                        "payload": payload_schema,
                        **shared,
                    },
                    "required": ["job_type", "payload", "idempotency_key"],
                    "title": f"{job_type.value.title()}JobBody",
                }
            )
        return {
            key: value
            for key, value in schema.items()
            if key not in {"properties", "required", "additionalProperties", "type"}
        } | {"oneOf": variants}


def _scheduler_coupling_json_schema() -> JsonSchemaValue:
    return {
        "if": {
            "properties": {
                "idempotency_key": {"pattern": _RESERVED_SCHEDULE_PREFIX_PATTERN}
            },
            "required": ["idempotency_key"],
        },
        "then": {
            "properties": {
                "idempotency_key": {"pattern": _SCHEDULE_KEY_PATTERN},
                "actor": {
                    "properties": {
                        "actor_type": {"const": ActorType.SCHEDULER.value}
                    },
                    "required": ["actor_type"],
                },
                "job_type": {"const": JobType.SNAPSHOT.value},
                "priority": {"const": 0},
            }
        },
        "else": {
            "properties": {
                "actor": {
                    "properties": {
                        "actor_type": {
                            "not": {"const": ActorType.SCHEDULER.value}
                        }
                    },
                    "required": ["actor_type"],
                }
            }
        },
    }


class EnqueueJobRequest(StrictApiModel):
    job_type: JobType
    payload: JobPayload
    idempotency_key: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$",
    )
    actor: ActorIdentity
    priority: int = Field(default=0, ge=0, le=100)

    @model_validator(mode="before")
    @classmethod
    def parse_typed_payload(cls, value: Any) -> Any:
        return _parse_typed_payload(value)

    @model_validator(mode="after")
    def validate_scheduler_identity(self) -> "EnqueueJobRequest":
        scheduled = self.idempotency_key.startswith("schedule:")
        scheduler_actor = self.actor.actor_type is ActorType.SCHEDULER
        if scheduled:
            if not (
                _valid_schedule_key(self.idempotency_key)
                and scheduler_actor
                and self.job_type is JobType.SNAPSHOT
                and self.priority == 0
            ):
                raise ValueError("scheduler enqueue identity is invalid")
        elif scheduler_actor:
            raise ValueError("scheduler enqueue identity is invalid")
        return self

    @classmethod
    def __get_pydantic_json_schema__(
        cls, core_schema: Any, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        """Publish the same closed job-type/payload pairing enforced at runtime."""

        schema = handler(core_schema)
        properties = schema["properties"]
        payload_options = properties["payload"]["anyOf"]
        shared = {
            name: properties[name]
            for name in ("idempotency_key", "actor", "priority")
        }
        variants = []
        for job_type, payload_schema in zip(JobType, payload_options, strict=True):
            variants.append(
                {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "job_type": {"type": "string", "const": job_type.value},
                        "payload": payload_schema,
                        **shared,
                    },
                    "required": ["job_type", "payload", "idempotency_key", "actor"],
                    "title": f"{job_type.value.title()}JobRequest",
                }
            )
        base_schema = {
            key: value
            for key, value in schema.items()
            if key not in {"properties", "required", "additionalProperties", "type"}
        }
        return base_schema | {
            "oneOf": variants,
            "allOf": [_scheduler_coupling_json_schema()],
        }


class JobMetadata(StrictApiModel):
    job_id: OpaqueId = Field(min_length=1, max_length=128)
    job_type: JobType
    state: JobState
    payload: JobPayload
    payload_fingerprint: Sha256Hex = Field(pattern=r"^[0-9a-f]{64}$")
    actor: ActorIdentity
    priority: int = Field(ge=0, le=100)
    requested_at: datetime
    updated_at: datetime
    attempt_count: int = Field(ge=0)
    reason_code: str | None = Field(
        default=None,
        max_length=128,
        pattern=r"^[A-Z][A-Z0-9_]{0,127}$",
    )
    result_hash: Sha256Hex | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )

    @model_validator(mode="before")
    @classmethod
    def parse_typed_payload(cls, value: Any) -> Any:
        return _parse_typed_payload(value)


class AttemptMetadata(StrictApiModel):
    attempt_id: OpaqueId = Field(min_length=1, max_length=128)
    attempt_number: int = Field(ge=1)
    worker_id: OpaqueId | None = Field(default=None, max_length=128)
    claimed_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    exit_code: int | None = None
    termination_reason: str | None = Field(default=None, max_length=128)
    artifact_count: int = Field(default=0, ge=0)


class EventMetadata(StrictApiModel):
    event_id: OpaqueId = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=1)
    from_state: JobState | None
    to_state: JobState
    reason_code: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Z][A-Z0-9_]{0,127}$",
    )
    actor: ActorIdentity
    trace_id: OpaqueId = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$",
    )
    created_at: datetime


class ArtifactMetadata(StrictApiModel):
    artifact_id: OpaqueId = Field(min_length=1, max_length=128)
    attempt_id: OpaqueId = Field(min_length=1, max_length=128)
    artifact_type: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Z][A-Z0-9_]{0,63}$",
    )
    validator_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$",
    )
    sha256: Sha256Hex = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    created_at: datetime


class JobDetail(StrictApiModel):
    job: JobMetadata
    attempts: tuple[AttemptMetadata, ...] = ()
    events: tuple[EventMetadata, ...] = ()
    artifacts: tuple[ArtifactMetadata, ...] = ()


# Explicit response aliases keep later repository/API adapters descriptive
# without creating duplicate models that might drift.
JobResponse = JobMetadata
JobAttemptResponse = AttemptMetadata
JobEventResponse = EventMetadata
JobArtifactResponse = ArtifactMetadata
JobDetailResponse = JobDetail
