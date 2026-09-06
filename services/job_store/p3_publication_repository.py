"""One-call database boundary for fenced P3 publication and reconciliation."""

from __future__ import annotations

from typing import Annotated, Literal, Protocol
from uuid import UUID

from pydantic import BeforeValidator, Field
from psycopg import OperationalError

from packages.alpha_lifecycle.baseline_campaign import ArtifactStore
from packages.alpha_lifecycle.contracts.base import DigestModel, Sha256, Text, Token
from packages.alpha_lifecycle.contracts.lifecycle import PublicationRequest
from packages.data_contracts import ArtifactRefV1
from services.job_store.p3_sql import DomainAppendEntry, PublicationTransport
from services.job_store.worker_repository import ClaimedJob


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


class _Pool(Protocol):
    def connection(self): ...


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
    ) -> JobCommitResult:
        request = PublicationRequest.model_validate(request)
        if claim.job_id != request.job_id:
            raise ValueError("claim does not match publication request")
        for ref in (request.evidence_ref, *request.proposed_event_refs):
            self._store.read_bytes(ref)
        transport = PublicationTransport(
            job_id=claim.job_id,
            attempt_id=claim.attempt_id,
            worker_id=claim.worker_id,
            lease_token=claim.lease_token,
            request=request,
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
        try:
            with self._pool.connection() as connection:
                with connection.transaction():
                    row = connection.execute(self.COMMIT_SQL, parameters).fetchone()
        except OperationalError:
            recovered = self.read_commit(request)
            if recovered is not None:
                return recovered
            raise
        if row is None or row["result"] is None:
            raise RuntimeError("P3 commit capability returned no result")
        return JobCommitResult.model_validate(row["result"])

    def read_commit(self, request: PublicationRequest) -> JobCommitResult | None:
        request = PublicationRequest.model_validate(request)
        with self._pool.connection() as connection:
            row = connection.execute(
                self.READ_SQL,
                (request.job_id, request.idempotency_key, request.semantic_request_digest),
            ).fetchone()
        if row is None or row["result"] is None:
            return None
        return JobCommitResult.model_validate(row["result"])


__all__ = ["JobCommitResult", "P3PublicationRepository"]
