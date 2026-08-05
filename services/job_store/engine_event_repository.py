"""Repository boundary for atomic validated engine-event ingestion."""

from __future__ import annotations

import hashlib
import re
from typing import Protocol
from uuid import UUID

from packages.engine_event_ledger import (
    EngineEventBatchReceipt,
    EngineEventConflictError,
    EngineEventLedgerState,
    EngineEventSequenceBlockedError,
    EngineRunProjection,
    InvalidEngineEventBatchError,
    StoredEngineEvent,
    project_engine_run,
)
from packages.engine_event_ledger.models import FIRST_ENGINE_EVENT_SEQUENCE
from packages.engine_contracts import EngineEventEnvelope, canonical_json_bytes
from services.job_worker.engine_results import ValidatedEngineEventBatch


__all__ = ["EngineEventLedgerRepository", "InMemoryEngineEventLedger"]


_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_JOB_ID = re.compile(r"^job_[0-9a-f]{32}$", re.ASCII)
_ATTEMPT_ID = re.compile(r"^attempt_[0-9a-f]{32}$", re.ASCII)


def _validate_batch_envelopes(
    events: object,
) -> tuple[EngineEventEnvelope, EngineEventEnvelope]:
    if (
        type(events) is not tuple
        or not 1 <= len(events) <= 4_096
        or any(type(event) is not EngineEventEnvelope for event in events)
    ):
        raise ValueError("engine event batch members are invalid")
    first = events[0]
    authority = (
        first.engine_run_id,
        first.correlation_id,
        first.causation_id,
        first.initialization_time,
        first.schema_version,
        first.producer_identity,
        first.source_commit,
        first.config_digest,
    )
    if any(
        (
            event.engine_run_id,
            event.correlation_id,
            event.causation_id,
            event.initialization_time,
            event.schema_version,
            event.producer_identity,
            event.source_commit,
            event.config_digest,
        )
        != authority
        for event in events
    ) or len({event.message_id for event in events}) != len(events):
        raise ValueError("engine event batch authority is inconsistent")
    return first, events[-1]


def _validated_records(
    batch: ValidatedEngineEventBatch,
) -> tuple[tuple[StoredEngineEvent, ...], str]:
    if type(batch) is not ValidatedEngineEventBatch:
        raise InvalidEngineEventBatchError(
            "ingest requires an exact ValidatedEngineEventBatch"
        )
    try:
        if (
            batch.artifact_type != "engine_event_batch"
            or batch.media_type != "application/x-ndjson"
            or batch.truncated is not False
            or batch.validator_id != "engine-event-v1"
            or type(batch.size_bytes) is not int
            or batch.size_bytes <= 0
            or type(batch.sha256) is not str
            or _SHA256.fullmatch(batch.sha256) is None
            or type(batch.validation_metadata) is not dict
        ):
            raise ValueError
        first, last = _validate_batch_envelopes(batch.events)
        raw = b"".join(
            canonical_json_bytes(event) + b"\n" for event in batch.events
        )
        if (
            len(raw) != batch.size_bytes
            or hashlib.sha256(raw).hexdigest() != batch.sha256
        ):
            raise ValueError
        metadata = batch.validation_metadata
        job_id = metadata.get("job_id")
        attempt_id = metadata.get("attempt_id")
        if (
            type(job_id) is not str
            or _JOB_ID.fullmatch(job_id) is None
            or type(attempt_id) is not str
            or _ATTEMPT_ID.fullmatch(attempt_id) is None
        ):
            raise ValueError
        expected_metadata = {
            "attempt_id": attempt_id,
            "config_digest": first.config_digest,
            "engine_run_id": str(first.engine_run_id),
            "event_count": len(batch.events),
            "first_sequence": first.stream_sequence,
            "job_id": job_id,
            "last_sequence": last.stream_sequence,
            "request_message_id": str(first.causation_id),
            "source_commit": first.source_commit,
            "validator_id": "engine-event-v1",
        }
        if metadata != expected_metadata:
            raise ValueError
        expected_ref = (
            f"engine-results/{job_id}/{attempt_id}/{batch.sha256}.jsonl"
        )
        if batch.relative_ref != expected_ref:
            raise ValueError

        identity_bytes = canonical_json_bytes(
            {
                "artifact_type": batch.artifact_type,
                "media_type": batch.media_type,
                "relative_ref": batch.relative_ref,
                "sha256": batch.sha256,
                "size_bytes": batch.size_bytes,
                "truncated": batch.truncated,
                "validation_metadata": metadata,
                "validator_id": batch.validator_id,
            }
        )
        ingestion_digest = hashlib.sha256(identity_bytes).hexdigest()
        records = tuple(
            StoredEngineEvent.from_envelope(event, batch_sha256=batch.sha256)
            for event in batch.events
        )
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise InvalidEngineEventBatchError(
            "validated engine event batch seal or authority is invalid"
        ) from exc
    return records, ingestion_digest


class EngineEventLedgerRepository(Protocol):
    def ingest(self, batch: ValidatedEngineEventBatch) -> EngineEventBatchReceipt: ...

    def load_receipt(self, batch_sha256: str) -> EngineEventBatchReceipt | None: ...

    def load_events(self, engine_run_id: UUID) -> tuple[StoredEngineEvent, ...]: ...

    def load_projection(self, engine_run_id: UUID) -> EngineRunProjection | None: ...

    def replay_projection(self, engine_run_id: UUID) -> EngineRunProjection | None: ...

    def recover_projections(self) -> tuple[EngineRunProjection, ...]: ...


class InMemoryEngineEventLedger:
    """Hermetic fake whose ingest operation updates all state at once."""

    def __init__(self, state: EngineEventLedgerState | None = None) -> None:
        self._events: dict[UUID, StoredEngineEvent] = {}
        self._receipts: dict[str, EngineEventBatchReceipt] = {}
        self._projections: dict[UUID, EngineRunProjection] = {}
        if state is not None:
            if type(state) is not EngineEventLedgerState:
                raise TypeError("restart state must be an exact EngineEventLedgerState")
            normalized = EngineEventLedgerState.model_validate(
                state.model_dump(mode="python")
            )
            if len({event.message_id for event in normalized.events}) != len(
                normalized.events
            ) or len({receipt.batch_sha256 for receipt in normalized.receipts}) != len(
                normalized.receipts
            ):
                raise EngineEventConflictError("restart state contains duplicate identities")
            self._events = {event.message_id: event for event in normalized.events}
            self._receipts = {
                receipt.batch_sha256: receipt for receipt in normalized.receipts
            }
            self.recover_projections()
            self._validate_recovered_receipts()

    def ingest(self, batch: ValidatedEngineEventBatch) -> EngineEventBatchReceipt:
        records, ingestion_digest = _validated_records(batch)
        prior = self._receipts.get(batch.sha256)
        if prior is not None:
            if prior.ingestion_digest != ingestion_digest:
                raise EngineEventConflictError(
                    f"conflicting receipt authority for batch {batch.sha256}"
                )
            return prior
        for record in records:
            existing = self._events.get(record.message_id)
            if existing is not None and existing.digest != record.digest:
                raise EngineEventConflictError(
                    f"conflicting canonical content for message_id {record.message_id}"
                )
        run_id = records[0].engine_run_id
        prior_projection = self._projections.get(run_id)
        expected_sequence = (
            FIRST_ENGINE_EVENT_SEQUENCE
            if prior_projection is None
            else prior_projection.last_sequence + 1
        )
        for record in records:
            if record.stream_sequence != expected_sequence:
                raise EngineEventSequenceBlockedError(
                    engine_run_id=run_id,
                    expected_sequence=expected_sequence,
                    actual_sequence=record.stream_sequence,
                )
            expected_sequence += 1
        combined = self.load_events(run_id) + records
        projection = project_engine_run(combined)
        metadata = batch.validation_metadata
        receipt = EngineEventBatchReceipt(
            batch_sha256=batch.sha256,
            ingestion_digest=ingestion_digest,
            job_id=metadata["job_id"],
            attempt_id=metadata["attempt_id"],
            engine_run_id=run_id,
            event_count=len(records),
            first_sequence=records[0].stream_sequence,
            last_sequence=records[-1].stream_sequence,
            last_digest=records[-1].digest,
        )
        for record in records:
            self._events[record.message_id] = record
        self._receipts[batch.sha256] = receipt
        self._projections[run_id] = projection
        return receipt

    def load_receipt(self, batch_sha256: str) -> EngineEventBatchReceipt | None:
        return self._receipts.get(batch_sha256)

    def load_events(self, engine_run_id: UUID) -> tuple[StoredEngineEvent, ...]:
        return tuple(
            sorted(
                (
                    event
                    for event in self._events.values()
                    if event.engine_run_id == engine_run_id
                ),
                key=lambda event: event.stream_sequence,
            )
        )

    def load_projection(self, engine_run_id: UUID) -> EngineRunProjection | None:
        return self._projections.get(engine_run_id)

    def replay_projection(self, engine_run_id: UUID) -> EngineRunProjection | None:
        events = self.load_events(engine_run_id)
        return project_engine_run(events) if events else None

    def recover_projections(self) -> tuple[EngineRunProjection, ...]:
        run_ids = sorted(
            {event.engine_run_id for event in self._events.values()},
            key=lambda value: value.bytes,
        )
        recovered = tuple(
            projection
            for run_id in run_ids
            if (projection := self.replay_projection(run_id)) is not None
        )
        self._projections = {
            projection.engine_run_id: projection for projection in recovered
        }
        return recovered

    def _validate_recovered_receipts(self) -> None:
        event_batches = {event.batch_sha256 for event in self._events.values()}
        if event_batches != set(self._receipts):
            raise EngineEventConflictError(
                "restart state does not atomically pair events and receipts"
            )
        for batch_sha256, receipt in self._receipts.items():
            records = tuple(
                sorted(
                    (
                        event
                        for event in self._events.values()
                        if event.batch_sha256 == batch_sha256
                    ),
                    key=lambda event: event.stream_sequence,
                )
            )
            raw = b"".join(
                event.canonical_json.encode("utf-8") + b"\n" for event in records
            )
            first = records[0]
            last = records[-1]
            try:
                envelopes = tuple(
                    EngineEventEnvelope.model_validate_json(record.canonical_json)
                    for record in records
                )
                envelope, _last_envelope = _validate_batch_envelopes(envelopes)
            except (AttributeError, TypeError, ValueError) as exc:
                raise EngineEventConflictError(
                    f"restart batch authority is inconsistent for {batch_sha256}"
                ) from exc
            metadata = {
                "attempt_id": receipt.attempt_id,
                "config_digest": envelope.config_digest,
                "engine_run_id": str(envelope.engine_run_id),
                "event_count": len(records),
                "first_sequence": first.stream_sequence,
                "job_id": receipt.job_id,
                "last_sequence": last.stream_sequence,
                "request_message_id": str(envelope.causation_id),
                "source_commit": envelope.source_commit,
                "validator_id": "engine-event-v1",
            }
            relative_ref = (
                f"engine-results/{receipt.job_id}/{receipt.attempt_id}/"
                f"{batch_sha256}.jsonl"
            )
            identity_bytes = canonical_json_bytes(
                {
                    "artifact_type": "engine_event_batch",
                    "media_type": "application/x-ndjson",
                    "relative_ref": relative_ref,
                    "sha256": batch_sha256,
                    "size_bytes": len(raw),
                    "truncated": False,
                    "validation_metadata": metadata,
                    "validator_id": "engine-event-v1",
                }
            )
            if (
                hashlib.sha256(raw).hexdigest() != batch_sha256
                or receipt.ingestion_digest
                != hashlib.sha256(identity_bytes).hexdigest()
                or receipt.engine_run_id != first.engine_run_id
                or receipt.event_count != len(records)
                or receipt.first_sequence != first.stream_sequence
                or receipt.last_sequence != last.stream_sequence
                or receipt.last_digest != last.digest
            ):
                raise EngineEventConflictError(
                    f"restart receipt is inconsistent for batch {batch_sha256}"
                )

    def export_state(self) -> EngineEventLedgerState:
        return EngineEventLedgerState(
            events=tuple(
                sorted(
                    self._events.values(),
                    key=lambda event: (
                        event.engine_run_id.bytes,
                        event.stream_sequence,
                        event.message_id.bytes,
                    ),
                )
            ),
            receipts=tuple(
                self._receipts[digest] for digest in sorted(self._receipts)
            ),
        )
