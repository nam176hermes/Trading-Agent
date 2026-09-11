"""One-call database boundary for fenced P3 publication and reconciliation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, ContextManager, Literal, Protocol
from uuid import UUID

from pydantic import BeforeValidator, Field
from psycopg import OperationalError
from psycopg.errors import DeadlockDetected, SerializationFailure

from packages.alpha_lifecycle.baseline_campaign import ArtifactStore
from packages.alpha_lifecycle.sandbox_policy import MAX_OUTPUT_INVENTORY_BYTES
from packages.alpha_lifecycle.contracts.base import DigestModel, Sha256, Text, Token, StrictModel
from packages.alpha_lifecycle.contracts.lifecycle import PublicationReceipt, PublicationRequest
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import canonical_json_bytes
from services.job_store.p3_sql import DomainAppendEntry, PublicationTransport
from services.job_store.records import ClaimedJob


def _tuple(value: object) -> tuple[object, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("value must be a JSON array")
    return tuple(value)


class JobCommitResult(DigestModel):
    schema_version: Literal["p3-job-commit-result-v1"]
    job_id: Text
    idempotency_key: Token
    semantic_request_digest: Sha256
    prepublication_ref: ArtifactRefV1
    ledger_event_ids: Annotated[
        tuple[UUID, ...], BeforeValidator(_tuple), Field(min_length=1, max_length=8)
    ]
    registry_event_refs: Annotated[
        tuple[ArtifactRefV1, ...], BeforeValidator(_tuple), Field(min_length=1, max_length=8)
    ]
    alpha_outcome: Literal["NOT_EVALUATED", "PASS", "FAIL"]

    def bound_to(
        self, request: PublicationRequest, *, ledger_event_ids: tuple[UUID, ...] | None = None
    ) -> "JobCommitResult":
        """Validate response identity; a valid digest alone does not bind a commit."""
        if (
            self.job_id != request.job_id
            or self.idempotency_key != request.idempotency_key
            or self.semantic_request_digest != request.semantic_request_digest
            or self.prepublication_ref != request.evidence_ref
            or self.registry_event_refs != request.proposed_event_refs
            or len(self.ledger_event_ids) != len(self.registry_event_refs)
            or len(set(self.ledger_event_ids)) != len(self.ledger_event_ids)
            or (self.alpha_outcome == "NOT_EVALUATED") != (request.stage == "REGISTER")
            or (ledger_event_ids is not None and self.ledger_event_ids != ledger_event_ids)
        ):
            raise ValueError("P3 commit result does not match publication request")
        return self


class _OutputCommit(StrictModel):
    result: JobCommitResult
    output_attempt_id: Text
    output_inventory_ref: ArtifactRefV1


class _Pool(Protocol):
    def connection(self) -> ContextManager[Any]: ...


class P3PublicationRepository:
    COMMIT_SQL = """SELECT job_plane.worker_commit_alpha_campaign(
        %s,%s,%s,%s,%s,%s
    ) AS result"""
    READ_SQL = """SELECT job_plane.read_alpha_commit(%s,%s,%s) AS result"""

    def __init__(self, pool: _Pool, store: ArtifactStore) -> None:
        self._pool = pool
        self._store = store

    def publish(
        self,
        request: PublicationRequest,
        claim: ClaimedJob,
        entries: tuple[DomainAppendEntry, ...],
        *,
        trace_id: str,
        output_inventory_ref: ArtifactRefV1 | None = None,
    ) -> JobCommitResult:
        request = PublicationRequest.model_validate(request)
        if claim.job_id != request.job_id:
            raise ValueError("claim does not match publication request")
        for ref in (request.evidence_ref, *request.proposed_event_refs):
            self._store.read_bytes(ref)
        if output_inventory_ref is not None:
            output_inventory_ref = ArtifactRefV1.model_validate(output_inventory_ref)
            self._read_inventory(output_inventory_ref)
        transport = PublicationTransport(
            job_id=claim.job_id,
            attempt_id=claim.attempt_id,
            worker_id=claim.worker_id,
            lease_token=claim.lease_token,
            request=request,
            output_inventory_ref=output_inventory_ref,
            entries=entries,
        )
        parameters = (
            claim.job_id,
            claim.attempt_id,
            claim.worker_id,
            claim.lease_token,
            transport.canonical_bytes().decode(),
            trace_id,
        )
        for attempt in range(3):
            try:
                with self._pool.connection() as connection:
                    with connection.transaction():
                        row = connection.execute(self.COMMIT_SQL, parameters).fetchone()
                        if row is None or row["result"] is None:
                            raise RuntimeError("P3 commit capability returned no result")
                        result = self._commit_result(row["result"],request,
                            output_inventory_ref=output_inventory_ref,attempt_id=claim.attempt_id if output_inventory_ref is not None else None
                        ).bound_to(request, ledger_event_ids=tuple(entry.event_id for entry in entries))
                return result
            except (DeadlockDetected, SerializationFailure):
                # PostgreSQL has aborted this transaction. The same SQL capability
                # checks the current fence again on every bounded retry.
                if attempt == 2:
                    raise
            except OperationalError:
                recovered = self.read_commit(request,output_inventory_ref=output_inventory_ref,attempt_id=claim.attempt_id if output_inventory_ref is not None else None)
                if recovered is not None:
                    return recovered.bound_to(
                        request, ledger_event_ids=tuple(entry.event_id for entry in entries)
                    )
                raise
        raise RuntimeError("P3 commit retries exhausted")

    def recover_receipt(self, job_id: str) -> PublicationReceipt:
        from packages.alpha_lifecycle.publication import recover_publication_receipt
        return recover_publication_receipt(job_id, repository=self, store=self._store)

    def read_publication(self, job_id: str) -> tuple[PublicationRequest, JobCommitResult, datetime]:
        """Read immutable SQL custody after commit, including after lease expiry."""
        with self._pool.connection() as connection:
            row = connection.execute(
                "SELECT * FROM job_plane.worker_read_alpha_publication(%s)", (job_id,)
            ).fetchone()
        if row is None or row["request_text"] is None:
            raise RuntimeError("HELD: canonical publication custody unavailable")
        raw = row["request_text"]
        request = PublicationRequest.model_validate_json(raw)
        committed_at = row["committed_at"]
        if (
            canonical_json_bytes(request).decode() != raw or request.job_id != job_id
            or not isinstance(committed_at, datetime) or committed_at.tzinfo is not UTC
        ):
            raise ValueError("invalid publication custody identity or timestamp")
        result = JobCommitResult.model_validate_json(row["result_text"]).bound_to(request)
        if canonical_json_bytes(result).decode() != row["result_text"]:
            raise ValueError("publication custody result is not canonical")
        ref_text=row.get("output_inventory_ref_text")
        attempt_id=row.get("output_attempt_id")
        if not isinstance(ref_text,str) or not isinstance(attempt_id,str) or not attempt_id:
            raise ValueError("P3 output custody binding is unavailable")
        ref=ArtifactRefV1.model_validate_json(ref_text)
        if canonical_json_bytes(ref).decode() != ref_text:
            raise ValueError("P3 output custody reference is not canonical")
        self._read_inventory(ref)
        return request, result, committed_at

    def _read_inventory(self, ref: ArtifactRefV1) -> None:
        import json
        if ref.media_type != 'application/json' or not 0 < ref.size_bytes <= MAX_OUTPUT_INVENTORY_BYTES:
            raise ValueError('P3 output custody inventory bound differs')
        raw = self._store.read_bytes(ref)
        value = json.loads(raw)
        if not isinstance(value,list) or canonical_json_bytes(value) != raw:
            raise ValueError('P3 output custody inventory is not canonical')

    def _commit_result(self, value, request, *, output_inventory_ref=None, attempt_id=None):
        if isinstance(value,dict) and 'result' in value:
            bound = _OutputCommit.model_validate_json(canonical_json_bytes(value))
            if (output_inventory_ref is None or attempt_id is None
                or bound.output_inventory_ref != output_inventory_ref
                or bound.output_attempt_id != attempt_id):
                raise ValueError('P3 output custody differs from committed attempt')
            self._read_inventory(bound.output_inventory_ref)
            result = bound.result
        else:
            if output_inventory_ref is not None or attempt_id is not None:
                raise ValueError('P3 output custody is absent from canonical commit')
            # Historical fixture result remains readable; it does not prove output custody.
            result = JobCommitResult.model_validate_json(canonical_json_bytes(value))
        return result.bound_to(request)

    def read_commit(self, request: PublicationRequest, *,
        output_inventory_ref: ArtifactRefV1 | None = None, attempt_id: str | None = None,
    ) -> JobCommitResult | None:
        request = PublicationRequest.model_validate(request)
        with self._pool.connection() as connection:
            row = connection.execute(
                self.READ_SQL,
                (request.job_id, request.idempotency_key, request.semantic_request_digest),
            ).fetchone()
        if row is None or row["result"] is None:
            return None
        return self._commit_result(row["result"],request,
            output_inventory_ref=output_inventory_ref,attempt_id=attempt_id)


__all__ = ["JobCommitResult", "P3PublicationRepository"]
