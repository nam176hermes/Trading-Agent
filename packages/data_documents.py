"""Immutable document registry and provider-free PageIndex-compatible retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
import re
from uuid import UUID

from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.data_contracts import ArtifactRefV1
from packages.domain import require_utc


class DocumentRegistryError(ValueError):
    """Document identity, visibility, or content is invalid."""


@dataclass(frozen=True, slots=True)
class DocumentRecordV1:
    document_id: UUID
    title: str
    artifact: ArtifactRefV1
    source_available_at: datetime
    system_observed_at: datetime
    ingested_at: datetime


@dataclass(frozen=True, slots=True)
class RetrievalHitV1:
    document_id: UUID
    title: str
    score: int


@dataclass(frozen=True, slots=True)
class RetrievalCaseV1:
    query: str
    expected_document_id: UUID
    cutoff: datetime


@dataclass(frozen=True, slots=True)
class RetrievalBenchmarkV1:
    case_count: int
    recall_at_k: Fraction
    mean_reciprocal_rank: Fraction


class DocumentRegistry:
    def __init__(self, store: LocalArtifactStore) -> None:
        self._store = store
        self._records: dict[UUID, DocumentRecordV1] = {}

    def register(
        self,
        *,
        document_id: UUID,
        title: str,
        content: bytes,
        source_available_at: datetime,
        system_observed_at: datetime,
        ingested_at: datetime,
    ) -> DocumentRecordV1:
        if document_id in self._records:
            raise DocumentRegistryError("document identifier already exists")
        if (
            not title
            or title != title.strip()
            or not title.isprintable()
            or len(title) > 256
            or not content
            or len(content) > 16 * 1024 * 1024
        ):
            raise DocumentRegistryError("document title or content is invalid")
        try:
            source_available_at = require_utc(source_available_at)
            system_observed_at = require_utc(system_observed_at)
            ingested_at = require_utc(ingested_at)
        except ValueError as exc:
            raise DocumentRegistryError("document times must use canonical UTC") from exc
        if not source_available_at <= system_observed_at <= ingested_at:
            raise DocumentRegistryError("document visibility times are not ordered")
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DocumentRegistryError("document content must be valid UTF-8") from exc
        artifact = self._store.put_bytes(content, media_type="text/plain; charset=utf-8")
        record = DocumentRecordV1(
            document_id,
            title,
            artifact,
            source_available_at,
            system_observed_at,
            ingested_at,
        )
        self._records[document_id] = record
        return record

    def visible_at(self, cutoff: datetime) -> tuple[DocumentRecordV1, ...]:
        return tuple(
            sorted(
                (record for record in self._records.values() if record.ingested_at <= cutoff),
                key=lambda record: record.document_id.bytes,
            )
        )

    def content(self, record: DocumentRecordV1) -> str:
        try:
            return self._store.read_bytes(record.artifact).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DocumentRegistryError("document is not valid UTF-8") from exc


_TERM = re.compile(r"[A-Za-z0-9]+", re.ASCII)


def _terms(value: str) -> set[str]:
    return {match.group(0).lower() for match in _TERM.finditer(value)}


class LocalPageIndex:
    """Deterministic local fallback for the PageIndex retrieval seam."""

    def __init__(self, registry: DocumentRegistry) -> None:
        self._registry = registry

    def search(self, query: str, *, cutoff: datetime, limit: int) -> tuple[RetrievalHitV1, ...]:
        terms = _terms(query)
        if not terms or not 1 <= limit <= 100:
            raise DocumentRegistryError("retrieval query or limit is invalid")
        hits: list[RetrievalHitV1] = []
        for record in self._registry.visible_at(cutoff):
            score = len(terms & _terms(f"{record.title} {self._registry.content(record)}"))
            if score:
                hits.append(RetrievalHitV1(record.document_id, record.title, score))
        return tuple(sorted(hits, key=lambda hit: (-hit.score, hit.document_id.bytes))[:limit])


def benchmark_retrieval(
    index: LocalPageIndex, cases: tuple[RetrievalCaseV1, ...], *, k: int
) -> RetrievalBenchmarkV1:
    if not cases or not 1 <= k <= 100:
        raise DocumentRegistryError("benchmark cases or k are invalid")
    found = 0
    reciprocal = Fraction(0)
    for case in cases:
        ids = tuple(hit.document_id for hit in index.search(case.query, cutoff=case.cutoff, limit=k))
        if case.expected_document_id in ids:
            found += 1
            reciprocal += Fraction(1, ids.index(case.expected_document_id) + 1)
    return RetrievalBenchmarkV1(
        case_count=len(cases),
        recall_at_k=Fraction(found, len(cases)),
        mean_reciprocal_rank=reciprocal / len(cases),
    )


__all__ = [
    "DocumentRecordV1",
    "DocumentRegistry",
    "DocumentRegistryError",
    "LocalPageIndex",
    "RetrievalBenchmarkV1",
    "RetrievalCaseV1",
    "RetrievalHitV1",
    "benchmark_retrieval",
]
