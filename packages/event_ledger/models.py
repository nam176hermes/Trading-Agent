"""Strict, immutable types shared by the append-only event ledger."""
from __future__ import annotations

from enum import Enum
import json
from typing import Annotated, Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
NonEmptyText = Annotated[str, Field(min_length=1, max_length=256)]
POSTGRES_BIGINT_MAX: Final = 9223372036854775807
LedgerPositiveInt = Annotated[int, Field(gt=0, le=POSTGRES_BIGINT_MAX)]
LedgerNonNegativeInt = Annotated[int, Field(ge=0, le=POSTGRES_BIGINT_MAX)]
REPLAY_SCHEMA_VERSION: Final = "event-ledger-replay-v1"
REDUCER_VERSION: Final = "event-ledger-reducer-v1"


def _validate_postgres_json_strings(value: object) -> None:
    """Reject strings PostgreSQL jsonb cannot represent losslessly."""
    if isinstance(value, str):
        if "\x00" in value or any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            raise ValueError("string cannot be represented by PostgreSQL jsonb")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _validate_postgres_json_strings(key)
            _validate_postgres_json_strings(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _validate_postgres_json_strings(item)


class LedgerModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, revalidate_instances="always")


class StoredEvent(LedgerModel):
    """A canonical envelope plus the digest used for identity comparisons."""

    event_id: UUID
    stream_id: UUID
    sequence: LedgerPositiveInt
    event_type: NonEmptyText
    canonical_json: Annotated[str, Field(min_length=2)]
    digest: Sha256

    @classmethod
    def from_envelope(cls, event: object) -> "StoredEvent":
        from .replay import event_digest, serialize_event

        canonical_json = serialize_event(event)
        return cls(
            event_id=event.event_id,  # type: ignore[union-attr]
            stream_id=event.stream_id,  # type: ignore[union-attr]
            sequence=event.sequence,  # type: ignore[union-attr]
            event_type=event.event_type,  # type: ignore[union-attr]
            canonical_json=canonical_json,
            digest=event_digest(canonical_json),
        )


class ReplayStatus(str, Enum):
    COMPLETE = "COMPLETE"
    DEGRADED = "DEGRADED"


class ReplayIssueCode(str, Enum):
    SEQUENCE_GAP = "SEQUENCE_GAP"
    SEQUENCE_REGRESSION = "SEQUENCE_REGRESSION"


class ReplayIssue(LedgerModel):
    code: ReplayIssueCode
    stream_id: UUID
    event_id: UUID
    sequence: LedgerPositiveInt
    expected_sequence: LedgerPositiveInt
    digest: Sha256


class AppliedEvent(LedgerModel):
    event_id: UUID
    digest: Sha256


class EventTypeCount(LedgerModel):
    event_type: NonEmptyText
    count: LedgerNonNegativeInt


class StreamProjection(LedgerModel):
    stream_id: UUID
    event_count: LedgerNonNegativeInt
    last_sequence: LedgerNonNegativeInt
    last_digest: Sha256 | None = None


class AggregateReplayState(LedgerModel):
    event_count: LedgerNonNegativeInt
    type_counts: tuple[EventTypeCount, ...]
    streams: tuple[StreamProjection, ...]
    applied_events: tuple[AppliedEvent, ...]


class ReplayResult(LedgerModel):
    schema_version: Literal[REPLAY_SCHEMA_VERSION]
    reducer_version: Literal[REDUCER_VERSION]
    status: ReplayStatus
    state: AggregateReplayState
    issues: tuple[ReplayIssue, ...]
    canonical_state_json: Annotated[str, Field(min_length=2)]
    state_hash: Sha256


class SnapshotRecord(LedgerModel):
    schema_version: Literal[REPLAY_SCHEMA_VERSION]
    reducer_version: Literal[REDUCER_VERSION]
    state: AggregateReplayState
    status: ReplayStatus
    issues: tuple[ReplayIssue, ...]
    canonical_state_json: Annotated[str, Field(min_length=2)]
    state_hash: Sha256


class OutboxIntent(LedgerModel):
    event_id: UUID
    topic: NonEmptyText
    payload_json: str = Field(
        default="{}",
        min_length=2,
        max_length=65536,
        json_schema_extra={
            "contentMediaType": "application/json",
            "x-canonical-json": True,
        },
    )

    @field_validator("payload_json")
    @classmethod
    def _canonical_payload_json(cls, value: str) -> str:
        def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, item in pairs:
                if key in result:
                    raise ValueError(f"duplicate JSON key: {key}")
                result[key] = item
            return result

        def reject_non_finite(value: str) -> None:
            raise ValueError(f"non-finite JSON value: {value}")

        def reject_fractional(value: str) -> None:
            raise ValueError("fractional JSON numbers are not canonical domain values")

        try:
            decoded = json.loads(
                value,
                object_pairs_hook=reject_duplicate_keys,
                parse_constant=reject_non_finite,
                parse_float=reject_fractional,
            )
            _validate_postgres_json_strings(decoded)
            canonical = json.dumps(
                decoded,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("payload_json must be canonical JSON") from exc
        if value != canonical:
            raise ValueError("payload_json must use canonical JSON encoding")
        return value


class AppendOutcome(LedgerModel):
    event_id: UUID
    inserted: bool
