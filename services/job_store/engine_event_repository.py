"""Repository boundary for atomic validated engine-event ingestion."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
import hashlib
import json
import re
from typing import Protocol
from uuid import UUID

from psycopg import Error as PostgresError

from packages.engine_event_ledger import (
    EngineEventBatchReceipt,
    EngineEventConflictError,
    EngineEventLedgerState,
    EngineJobResultBinding,
    EngineEventSequenceBlockedError,
    EngineRunProjection,
    InvalidEngineEventBatchError,
    StoredEngineEvent,
    project_engine_run,
)
from packages.engine_event_ledger.models import FIRST_ENGINE_EVENT_SEQUENCE
from packages.engine_contracts import (
    EngineEventEnvelope,
    EventFamily,
    canonical_json_bytes,
)
from services.job_worker.engine_results import ValidatedEngineEventBatch


__all__ = [
    "EngineEventLedgerRepository",
    "InMemoryEngineEventLedger",
    "PostgresEngineEventLedger",
    "PostgresEngineEventLedgerSql",
]


_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_GENERIC_ENGINE_EVENT_VALIDATOR = "engine-event-v1"
_NAUTILUS_BACKTEST_VALIDATOR = "nautilus-backtest-result-v1"
_ALLOWED_VALIDATORS = {
    _GENERIC_ENGINE_EVENT_VALIDATOR,
    _NAUTILUS_BACKTEST_VALIDATOR,
}
_JOB_ID = re.compile(r"^job_[0-9a-f]{32}$", re.ASCII)
_ATTEMPT_ID = re.compile(r"^attempt_[0-9a-f]{32}$", re.ASCII)
_SEQUENCE_DETAIL = re.compile(
    r"^engine_run_id=([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12});expected=([1-9][0-9]*);"
    r"actual=([1-9][0-9]*)$",
    re.ASCII,
)
_CONFLICT_SQLSTATE = "P2D01"
_SEQUENCE_GAP_SQLSTATE = "P2D02"
_SEQUENCE_REGRESSION_SQLSTATE = "P2D03"
_INVALID_BATCH_SQLSTATE = "P2D04"


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


def _validate_job_attempt_identity(
    job_id: object,
    attempt_id: object,
) -> tuple[str, str]:
    if (
        type(job_id) is not str
        or _JOB_ID.fullmatch(job_id) is None
        or type(attempt_id) is not str
        or _ATTEMPT_ID.fullmatch(attempt_id) is None
    ):
        raise ValueError("engine event job or attempt identity is invalid")
    return job_id, attempt_id


def _validate_job_identity(job_id: object) -> str:
    if type(job_id) is not str or _JOB_ID.fullmatch(job_id) is None:
        raise ValueError("engine event job identity is invalid")
    return job_id


def _validate_contiguous_records(
    records: tuple[StoredEngineEvent, ...],
    *,
    expected_sequence: int,
) -> None:
    run_id = records[0].engine_run_id
    for record in records:
        if record.stream_sequence != expected_sequence:
            raise EngineEventSequenceBlockedError(
                engine_run_id=run_id,
                expected_sequence=expected_sequence,
                actual_sequence=record.stream_sequence,
            )
        expected_sequence += 1


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
            or batch.validator_id not in _ALLOWED_VALIDATORS
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
        job_id, attempt_id = _validate_job_attempt_identity(
            metadata.get("job_id"),
            metadata.get("attempt_id"),
        )
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
            "validator_id": batch.validator_id,
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


def _receipt_for(
    batch: ValidatedEngineEventBatch,
    records: tuple[StoredEngineEvent, ...],
    ingestion_digest: str,
) -> EngineEventBatchReceipt:
    metadata = batch.validation_metadata
    return EngineEventBatchReceipt(
        batch_sha256=batch.sha256,
        ingestion_digest=ingestion_digest,
        job_id=metadata["job_id"],
        attempt_id=metadata["attempt_id"],
        engine_run_id=records[0].engine_run_id,
        event_count=len(records),
        first_sequence=records[0].stream_sequence,
        last_sequence=records[-1].stream_sequence,
        last_digest=records[-1].digest,
    )


class EngineEventLedgerRepository(Protocol):
    def ingest(self, batch: ValidatedEngineEventBatch) -> EngineEventBatchReceipt: ...

    def ingest_for_job(
        self, batch: ValidatedEngineEventBatch, *, claimed: object
    ) -> EngineEventBatchReceipt: ...

    def load_receipt(self, batch_sha256: str) -> EngineEventBatchReceipt | None: ...

    def load_job_receipt(self, job_id: str) -> EngineEventBatchReceipt | None: ...

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
        self._job_results: dict[str, EngineJobResultBinding] = {}
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
            if len({binding.job_id for binding in normalized.job_results}) != len(
                normalized.job_results
            ):
                raise EngineEventConflictError(
                    "restart state contains duplicate engine job results"
                )
            self._job_results = {
                binding.job_id: binding for binding in normalized.job_results
            }
            self.recover_projections()
            self._validate_recovered_receipts()
            self._validate_recovered_job_results()

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
        _validate_contiguous_records(
            records,
            expected_sequence=expected_sequence,
        )
        combined = self.load_events(run_id) + records
        projection = project_engine_run(combined)
        receipt = _receipt_for(batch, records, ingestion_digest)
        for record in records:
            self._events[record.message_id] = record
        self._receipts[batch.sha256] = receipt
        self._projections[run_id] = projection
        return receipt

    def ingest_for_job(
        self, batch: ValidatedEngineEventBatch, *, claimed: object
    ) -> EngineEventBatchReceipt:
        records, _ingestion_digest = _validated_records(batch)
        job_id = batch.validation_metadata["job_id"]
        attempt_id = batch.validation_metadata["attempt_id"]
        if (
            getattr(claimed, "job_id", None) != job_id
            or getattr(claimed, "attempt_id", None) != attempt_id
        ):
            raise EngineEventConflictError(
                "engine job result differs from the current claim"
            )
        prior = self.load_job_receipt(job_id)
        if prior is not None:
            if (
                prior.batch_sha256 == batch.sha256
                and prior.job_id == job_id
                and prior.attempt_id == attempt_id
            ):
                return self.ingest(batch)
            if prior.batch_sha256 == batch.sha256:
                raise EngineEventConflictError(
                    "job result receipt authority differs from the current claim"
                )
            raise EngineEventConflictError(
                f"job already has a different durable engine-event result: {job_id}"
            )
        receipt = self.ingest(batch)
        if receipt.engine_run_id != records[0].engine_run_id:
            raise EngineEventConflictError("job result receipt authority is invalid")
        self._job_results[job_id] = EngineJobResultBinding(
            job_id=job_id,
            attempt_id=attempt_id,
            batch_sha256=receipt.batch_sha256,
        )
        return receipt

    def load_receipt(self, batch_sha256: str) -> EngineEventBatchReceipt | None:
        return self._receipts.get(batch_sha256)

    def load_job_receipt(self, job_id: str) -> EngineEventBatchReceipt | None:
        _validate_job_identity(job_id)
        binding = self._job_results.get(job_id)
        if binding is None:
            return None
        receipt = self._receipts.get(binding.batch_sha256)
        if receipt is None:
            raise EngineEventConflictError(
                f"engine job result has no receipt: {job_id}"
            )
        return receipt

    def _validate_recovered_job_results(self) -> None:
        for job_id, binding in self._job_results.items():
            receipt = self._receipts.get(binding.batch_sha256)
            if (
                receipt is None
                or receipt.job_id != job_id
                or receipt.attempt_id != binding.attempt_id
            ):
                raise EngineEventConflictError(
                    f"restart engine job result is inconsistent: {job_id}"
                )

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
                _validate_job_attempt_identity(
                    receipt.job_id,
                    receipt.attempt_id,
                )
                _validate_contiguous_records(
                    records,
                    expected_sequence=first.stream_sequence,
                )
                envelopes = tuple(
                    EngineEventEnvelope.model_validate_json(record.canonical_json)
                    for record in records
                )
                envelope, _last_envelope = _validate_batch_envelopes(envelopes)
            except (
                AttributeError,
                EngineEventSequenceBlockedError,
                TypeError,
                ValueError,
            ) as exc:
                raise EngineEventConflictError(
                    f"restart batch authority is inconsistent for {batch_sha256}"
                ) from exc
            validator_id = (
                _NAUTILUS_BACKTEST_VALIDATOR
                if len(envelopes) == 1
                and envelopes[0].payload.event_type == "NautilusBacktestCompleted"
                else _GENERIC_ENGINE_EVENT_VALIDATOR
            )
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
                "validator_id": validator_id,
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
                    "validator_id": validator_id,
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
            job_results=tuple(
                self._job_results[job_id] for job_id in sorted(self._job_results)
            ),
        )


class _Cursor(Protocol):
    def fetchone(self) -> object: ...

    def fetchall(self) -> list[object]: ...


class _Connection(Protocol):
    def transaction(self) -> AbstractContextManager[object]: ...

    def execute(self, statement: str, params: Mapping[str, object]) -> _Cursor: ...


class _Pool(Protocol):
    def connection(self) -> AbstractContextManager[_Connection]: ...


class PostgresEngineEventLedgerSql:
    """Single-statement write authority plus protected ledger reads."""

    INGEST_BATCH = """SELECT batch_sha256, ingestion_digest, job_id, attempt_id,
       engine_run_id, event_count, first_sequence, last_sequence, last_digest
FROM public.ingest_engine_event_batch(%(batch_document)s);"""
    INGEST_JOB_RESULT = """SELECT batch_sha256, ingestion_digest, job_id, attempt_id,
       engine_run_id, event_count, first_sequence, last_sequence, last_digest
FROM job_plane.ingest_engine_job_result(
  %(job_id)s, %(attempt_id)s, %(worker_id)s, %(lease_token)s,
  %(batch_document)s
);"""
    LOAD_RECEIPT = """SELECT batch_sha256, ingestion_digest, job_id, attempt_id,
       engine_run_id, event_count, first_sequence, last_sequence, last_digest
FROM public.engine_event_batch_receipts
WHERE batch_sha256 = %(batch_sha256)s;"""
    LOAD_JOB_RECEIPT = """SELECT receipt.batch_sha256, receipt.ingestion_digest,
       receipt.job_id, receipt.attempt_id, receipt.engine_run_id,
       receipt.event_count, receipt.first_sequence, receipt.last_sequence,
       receipt.last_digest
FROM public.engine_job_results AS result
JOIN public.engine_event_batch_receipts AS receipt
  ON receipt.batch_sha256 = result.batch_sha256
WHERE result.job_id = %(job_id)s;"""
    LOAD_EVENTS = """SELECT message_id, engine_run_id, stream_sequence, event_type,
       event_family, canonical_json_text, digest, batch_sha256
FROM public.engine_events
WHERE engine_run_id = %(engine_run_id)s
ORDER BY stream_sequence, message_id;"""
    LOAD_PROJECTION = """SELECT engine_run_id, event_count, event_type_counts,
       last_sequence, last_digest
FROM public.engine_run_projections
WHERE engine_run_id = %(engine_run_id)s;"""
    RECOVER_PROJECTIONS = """SELECT engine_run_id, event_count, event_type_counts,
       last_sequence, last_digest
FROM public.recover_engine_run_projections()
ORDER BY engine_run_id;"""


class PostgresEngineEventLedger:
    """Durable ledger using database-owned atomic batch ingestion."""

    def __init__(self, pool: _Pool) -> None:
        self._pool = pool

    @staticmethod
    def _row_value(row: object, name: str, index: int) -> object:
        if isinstance(row, Mapping):
            return row[name]
        if isinstance(row, tuple):
            return row[index]
        raise EngineEventConflictError("database returned an unsupported row shape")

    @classmethod
    def _receipt_from_row(cls, row: object) -> EngineEventBatchReceipt:
        try:
            return EngineEventBatchReceipt(
                batch_sha256=cls._row_value(row, "batch_sha256", 0),
                ingestion_digest=cls._row_value(row, "ingestion_digest", 1),
                job_id=cls._row_value(row, "job_id", 2),
                attempt_id=cls._row_value(row, "attempt_id", 3),
                engine_run_id=cls._row_value(row, "engine_run_id", 4),
                event_count=cls._row_value(row, "event_count", 5),
                first_sequence=cls._row_value(row, "first_sequence", 6),
                last_sequence=cls._row_value(row, "last_sequence", 7),
                last_digest=cls._row_value(row, "last_digest", 8),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise EngineEventConflictError(
                "database returned an invalid engine-event receipt"
            ) from exc

    @classmethod
    def _event_from_row(cls, row: object) -> StoredEngineEvent:
        try:
            return StoredEngineEvent(
                message_id=cls._row_value(row, "message_id", 0),
                engine_run_id=cls._row_value(row, "engine_run_id", 1),
                stream_sequence=cls._row_value(row, "stream_sequence", 2),
                event_type=cls._row_value(row, "event_type", 3),
                event_family=EventFamily(cls._row_value(row, "event_family", 4)),
                canonical_json=cls._row_value(row, "canonical_json_text", 5),
                digest=cls._row_value(row, "digest", 6),
                batch_sha256=cls._row_value(row, "batch_sha256", 7),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise EngineEventConflictError(
                "database returned an invalid stored engine event"
            ) from exc

    @classmethod
    def _projection_from_row(cls, row: object) -> EngineRunProjection:
        try:
            type_counts = cls._row_value(row, "event_type_counts", 2)
            if type(type_counts) is not list:
                raise TypeError
            return EngineRunProjection(
                engine_run_id=cls._row_value(row, "engine_run_id", 0),
                event_count=cls._row_value(row, "event_count", 1),
                event_type_counts=tuple(type_counts),
                last_sequence=cls._row_value(row, "last_sequence", 3),
                last_digest=cls._row_value(row, "last_digest", 4),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise EngineEventConflictError(
                "database returned an invalid engine-run projection"
            ) from exc

    @staticmethod
    def _raise_typed_database_error(exc: PostgresError) -> None:
        sqlstate = getattr(exc, "sqlstate", None)
        if sqlstate == _CONFLICT_SQLSTATE:
            raise EngineEventConflictError("durable engine-event identity conflict") from exc
        if sqlstate == _INVALID_BATCH_SQLSTATE:
            raise InvalidEngineEventBatchError(
                "database rejected the validated engine-event batch"
            ) from exc
        if sqlstate in {_SEQUENCE_GAP_SQLSTATE, _SEQUENCE_REGRESSION_SQLSTATE}:
            detail = getattr(getattr(exc, "diag", None), "message_detail", None)
            match = _SEQUENCE_DETAIL.fullmatch(detail or "")
            if match is None:
                raise EngineEventConflictError(
                    "database returned invalid engine-event sequence authority"
                ) from exc
            blocked = EngineEventSequenceBlockedError(
                engine_run_id=UUID(match.group(1)),
                expected_sequence=int(match.group(2)),
                actual_sequence=int(match.group(3)),
            )
            expected_sqlstate = (
                _SEQUENCE_GAP_SQLSTATE
                if blocked.actual_sequence > blocked.expected_sequence
                else _SEQUENCE_REGRESSION_SQLSTATE
            )
            if sqlstate != expected_sqlstate:
                raise EngineEventConflictError(
                    "database returned inconsistent engine-event sequence authority"
                ) from exc
            raise blocked from exc
        raise exc

    @staticmethod
    def _batch_document(
        batch: ValidatedEngineEventBatch,
        records: tuple[StoredEngineEvent, ...],
        ingestion_digest: str,
    ) -> str:
        receipt = _receipt_for(batch, records, ingestion_digest)
        document = {
            "attempt_id": receipt.attempt_id,
            "batch_sha256": receipt.batch_sha256,
            "engine_run_id": str(receipt.engine_run_id),
            "event_count": receipt.event_count,
            "events": [
                {
                    "batch_sha256": record.batch_sha256,
                    "canonical_json": record.canonical_json,
                    "digest": record.digest,
                    "engine_run_id": str(record.engine_run_id),
                    "event_family": record.event_family.value,
                    "event_type": record.event_type,
                    "message_id": str(record.message_id),
                    "stream_sequence": record.stream_sequence,
                }
                for record in records
            ],
            "first_sequence": receipt.first_sequence,
            "ingestion_digest": receipt.ingestion_digest,
            "job_id": receipt.job_id,
            "last_digest": receipt.last_digest,
            "last_sequence": receipt.last_sequence,
        }
        return json.dumps(
            document,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    def ingest(self, batch: ValidatedEngineEventBatch) -> EngineEventBatchReceipt:
        return self._ingest_with_statement(
            batch, PostgresEngineEventLedgerSql.INGEST_BATCH
        )

    def ingest_for_job(
        self, batch: ValidatedEngineEventBatch, *, claimed: object
    ) -> EngineEventBatchReceipt:
        if (
            getattr(claimed, "job_id", None)
            != batch.validation_metadata.get("job_id")
            or getattr(claimed, "attempt_id", None)
            != batch.validation_metadata.get("attempt_id")
        ):
            raise EngineEventConflictError(
                "engine job result differs from the current claim"
            )
        return self._ingest_with_statement(
            batch,
            PostgresEngineEventLedgerSql.INGEST_JOB_RESULT,
            extra_params={
                "job_id": claimed.job_id,
                "attempt_id": claimed.attempt_id,
                "worker_id": claimed.worker_id,
                "lease_token": claimed.lease_token,
            },
        )

    def _ingest_with_statement(
        self,
        batch: ValidatedEngineEventBatch,
        statement: str,
        extra_params: Mapping[str, object] | None = None,
    ) -> EngineEventBatchReceipt:
        records, ingestion_digest = _validated_records(batch)
        expected = _receipt_for(batch, records, ingestion_digest)
        try:
            with self._pool.connection() as connection:
                with connection.transaction():
                    row = connection.execute(
                        statement,
                        {
                            "batch_document": self._batch_document(
                                batch, records, ingestion_digest
                            ),
                            **(extra_params or {}),
                        },
                    ).fetchone()
                    if row is None:
                        raise EngineEventConflictError(
                            "engine-event write authority returned no receipt"
                        )
                    receipt = self._receipt_from_row(row)
                    if receipt != expected:
                        raise EngineEventConflictError(
                            "durable engine-event receipt differs from validated batch"
                        )
        except PostgresError as exc:
            self._raise_typed_database_error(exc)
            raise AssertionError("unreachable")
        return receipt

    def load_receipt(self, batch_sha256: str) -> EngineEventBatchReceipt | None:
        if type(batch_sha256) is not str or _SHA256.fullmatch(batch_sha256) is None:
            raise ValueError("batch_sha256 must be lowercase sha256 hex")
        with self._pool.connection() as connection:
            row = connection.execute(
                PostgresEngineEventLedgerSql.LOAD_RECEIPT,
                {"batch_sha256": batch_sha256},
            ).fetchone()
        return None if row is None else self._receipt_from_row(row)

    def load_job_receipt(self, job_id: str) -> EngineEventBatchReceipt | None:
        _validate_job_identity(job_id)
        with self._pool.connection() as connection:
            rows = connection.execute(
                PostgresEngineEventLedgerSql.LOAD_JOB_RECEIPT,
                {"job_id": job_id},
            ).fetchall()
        if len(rows) > 1:
            raise EngineEventConflictError(
                f"job has conflicting durable engine-event receipts: {job_id}"
            )
        return None if not rows else self._receipt_from_row(rows[0])

    def load_events(self, engine_run_id: UUID) -> tuple[StoredEngineEvent, ...]:
        if type(engine_run_id) is not UUID:
            raise TypeError("engine_run_id must be an exact UUID")
        with self._pool.connection() as connection:
            rows = connection.execute(
                PostgresEngineEventLedgerSql.LOAD_EVENTS,
                {"engine_run_id": engine_run_id},
            ).fetchall()
        events = tuple(self._event_from_row(row) for row in rows)
        if any(event.engine_run_id != engine_run_id for event in events):
            raise EngineEventConflictError(
                "stored engine events differ from requested run"
            )
        return events

    def load_projection(self, engine_run_id: UUID) -> EngineRunProjection | None:
        if type(engine_run_id) is not UUID:
            raise TypeError("engine_run_id must be an exact UUID")
        with self._pool.connection() as connection:
            row = connection.execute(
                PostgresEngineEventLedgerSql.LOAD_PROJECTION,
                {"engine_run_id": engine_run_id},
            ).fetchone()
        if row is None:
            return None
        projection = self._projection_from_row(row)
        if projection.engine_run_id != engine_run_id:
            raise EngineEventConflictError(
                "stored projection differs from requested run"
            )
        return projection

    def replay_projection(self, engine_run_id: UUID) -> EngineRunProjection | None:
        events = self.load_events(engine_run_id)
        return project_engine_run(events) if events else None

    def recover_projections(self) -> tuple[EngineRunProjection, ...]:
        try:
            with self._pool.connection() as connection:
                with connection.transaction():
                    rows = connection.execute(
                        PostgresEngineEventLedgerSql.RECOVER_PROJECTIONS,
                        {},
                    ).fetchall()
                    projections = tuple(
                        self._projection_from_row(row) for row in rows
                    )
                    if len(
                        {projection.engine_run_id for projection in projections}
                    ) != len(projections):
                        raise EngineEventConflictError(
                            "projection recovery returned duplicate engine runs"
                        )
        except PostgresError as exc:
            self._raise_typed_database_error(exc)
            raise AssertionError("unreachable")
        return projections
