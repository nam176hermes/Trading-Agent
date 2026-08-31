"""Injected PostgreSQL boundary for immutable security-master revisions."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast
from uuid import UUID

from pydantic import ValidationError

from packages.domain import require_utc

from .models import (
    CashDividendPayloadV1,
    DelistingPayloadV1,
    PersistedSecurityMasterRevisionV1,
    SecurityMasterIdentityKind,
    SecurityMasterOperation,
    SecurityMasterRevisionV1,
    SplitPayloadV1,
    SymbolChangePayloadV1,
    SymbolMappingPayloadV1,
)
from .resolver import (
    SecurityMasterAmbiguityError,
    SecurityMasterIntegrityError,
    SecurityMasterResolver,
)


class SecurityMasterPersistenceError(ValueError):
    """Stored security-master data is unavailable, unbounded, or invalid."""


class _Cursor(Protocol):
    def fetchone(self) -> object: ...
    def fetchall(self) -> list[object]: ...


class _Connection(Protocol):
    def transaction(self) -> AbstractContextManager[object]: ...
    def execute(self, statement: str, params: Mapping[str, object]) -> _Cursor: ...


class _Pool(Protocol):
    def connection(self) -> AbstractContextManager[_Connection]: ...


@dataclass(frozen=True, slots=True)
class SecurityMasterPersistenceOutcome:
    revision_id: UUID
    revision_digest: str
    inserted: bool


_SELECT_COLUMNS = """canonical_revision_text, revision_digest,
revision_id, fact_id, subject_id, subject_kind, revision_ordinal, operation,
effective_from, effective_to, known_at, recorded_at, supersedes_revision_id,
lookup_provider, lookup_symbol, related_security_id"""


class SecurityMasterPostgresSql:
    APPEND_REVISION = """SELECT revision_id, revision_digest, inserted
FROM public.append_security_master_revision(%(canonical_revision_text)s);"""
    LOAD_REVISION = f"""SELECT {_SELECT_COLUMNS}
FROM public.security_master_revisions
WHERE revision_id = %(revision_id)s;"""
    LOAD_SUBJECT_HISTORY = f"""SELECT {_SELECT_COLUMNS}
FROM public.security_master_revisions
WHERE subject_kind = %(subject_kind)s
  AND subject_id = %(subject_id)s
  AND recorded_at <= %(knowledge_cutoff)s
ORDER BY fact_id, revision_ordinal
LIMIT 4097;"""
    LOAD_SYMBOL_HISTORY = f"""SELECT {_SELECT_COLUMNS}
FROM public.security_master_revisions
WHERE subject_kind = 'SYMBOL_MAPPING'
  AND lookup_provider = %(provider)s
  AND lookup_symbol = %(raw_symbol)s
  AND recorded_at <= %(knowledge_cutoff)s
ORDER BY fact_id, revision_ordinal
LIMIT 4097;"""
    LOAD_CORPORATE_ACTION_HISTORY = f"""SELECT {_SELECT_COLUMNS}
FROM public.security_master_revisions
WHERE subject_kind = 'CORPORATE_ACTION'
  AND related_security_id = %(security_id)s
  AND recorded_at <= %(knowledge_cutoff)s
ORDER BY fact_id, revision_ordinal
LIMIT 4097;"""
    EXPORT_VISIBLE_REVISIONS = f"""SELECT {_SELECT_COLUMNS}
FROM public.security_master_revisions
WHERE recorded_at <= %(knowledge_cutoff)s
ORDER BY recorded_at, revision_id
    LIMIT 4097;"""
    READ_TRANSACTION_ISOLATION = (
        "SELECT pg_catalog.current_setting('transaction_isolation', false) "
        "AS transaction_isolation;"
    )
    ITER_VISIBLE_REVISIONS = f"""SELECT {_SELECT_COLUMNS}
FROM public.security_master_revisions
WHERE recorded_at <= %(knowledge_cutoff)s
  AND (recorded_at, revision_id) > (%(after_recorded_at)s, %(after_revision_id)s)
ORDER BY recorded_at, revision_id
LIMIT %(limit)s;"""


_ROW_NAMES = (
    "canonical_revision_text",
    "revision_digest",
    "revision_id",
    "fact_id",
    "subject_id",
    "subject_kind",
    "revision_ordinal",
    "operation",
    "effective_from",
    "effective_to",
    "known_at",
    "recorded_at",
    "supersedes_revision_id",
    "lookup_provider",
    "lookup_symbol",
    "related_security_id",
)
_OUTCOME_NAMES = ("revision_id", "revision_digest", "inserted")
_ISOLATION_NAMES = ("transaction_isolation",)
_ACTION_PAYLOADS = (
    SplitPayloadV1,
    CashDividendPayloadV1,
    SymbolChangePayloadV1,
    DelistingPayloadV1,
)


def _utc(value: datetime, label: str) -> datetime:
    try:
        return require_utc(value)
    except ValueError as exc:
        raise SecurityMasterPersistenceError(f"{label} must be an explicit UTC datetime") from exc


def _row_value(row: object, name: str) -> object:
    if isinstance(row, Mapping):
        try:
            return row[name]
        except KeyError as exc:
            raise SecurityMasterPersistenceError("database row is missing a required field") from exc
    if isinstance(row, tuple):
        for names in (_ROW_NAMES, _OUTCOME_NAMES, _ISOLATION_NAMES):
            if len(row) == len(names) and name in names:
                return row[names.index(name)]
    raise SecurityMasterPersistenceError("database row shape is invalid")


def _exact_row_shape(row: object, names: tuple[str, ...]) -> bool:
    if isinstance(row, Mapping):
        return set(row) == set(names)
    return isinstance(row, tuple) and len(row) == len(names)


def _db_recorded_at(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise SecurityMasterPersistenceError("stored recorded_at mirror is invalid")
    try:
        offset = value.utcoffset()
    except Exception:
        raise SecurityMasterPersistenceError(
            "stored recorded_at mirror is invalid"
        ) from None
    if offset != timedelta(0):
        raise SecurityMasterPersistenceError("stored recorded_at mirror is invalid")
    return value.astimezone(UTC)


def _decode_row(row: object) -> PersistedSecurityMasterRevisionV1:
    if not _exact_row_shape(row, _ROW_NAMES):
        raise SecurityMasterPersistenceError("database row shape is invalid")
    canonical = _row_value(row, "canonical_revision_text")
    digest = _row_value(row, "revision_digest")
    if not isinstance(canonical, str) or not isinstance(digest, str):
        raise SecurityMasterPersistenceError("stored canonical revision has invalid types")
    try:
        revision = SecurityMasterRevisionV1.model_validate_json(canonical)
    except ValidationError as exc:
        raise SecurityMasterPersistenceError("stored canonical revision is invalid") from exc
    if revision.canonical_revision_bytes != canonical.encode("utf-8") or revision.digest != digest:
        raise SecurityMasterPersistenceError("stored canonical revision seal is invalid")
    expected = {
        "revision_id": revision.revision_id,
        "fact_id": revision.fact_id,
        "subject_id": revision.subject_id,
        "subject_kind": revision.subject_kind.value,
        "revision_ordinal": revision.revision_ordinal,
        "operation": revision.operation.value,
        "effective_from": revision.effective_from,
        "effective_to": revision.effective_to,
        "known_at": revision.known_at,
        "supersedes_revision_id": revision.supersedes_revision_id,
    }
    if any(_row_value(row, name) != value for name, value in expected.items()):
        raise SecurityMasterPersistenceError("stored scalar mirror is invalid")
    payload = revision.payload
    provider = payload.provider if isinstance(payload, SymbolMappingPayloadV1) else None
    symbol = payload.raw_symbol if isinstance(payload, SymbolMappingPayloadV1) else None
    related_security = payload.security_id if isinstance(payload, _ACTION_PAYLOADS) else None
    if revision.operation.value == "ASSERT" and (
        _row_value(row, "lookup_provider") != provider
        or _row_value(row, "lookup_symbol") != symbol
        or _row_value(row, "related_security_id") != related_security
    ):
        raise SecurityMasterPersistenceError("stored lookup mirror is invalid")
    if revision.operation.value == "RETRACT":
        if revision.subject_kind is SecurityMasterIdentityKind.SYMBOL_MAPPING:
            if not isinstance(_row_value(row, "lookup_provider"), str) or not isinstance(
                _row_value(row, "lookup_symbol"), str
            ):
                raise SecurityMasterPersistenceError("stored retraction lookup mirror is invalid")
        elif revision.subject_kind is SecurityMasterIdentityKind.CORPORATE_ACTION:
            if not isinstance(_row_value(row, "related_security_id"), UUID):
                raise SecurityMasterPersistenceError("stored retraction relation mirror is invalid")
        elif any(
            _row_value(row, name) is not None
            for name in ("lookup_provider", "lookup_symbol", "related_security_id")
        ):
            raise SecurityMasterPersistenceError("stored definition lookup mirror is invalid")
    try:
        return PersistedSecurityMasterRevisionV1(
            revision=revision,
            recorded_at=_db_recorded_at(_row_value(row, "recorded_at")),
        )
    except ValidationError as exc:
        raise SecurityMasterPersistenceError("stored recorded_at mirror is invalid") from exc


def _decode_history(
    rows: list[object],
) -> tuple[PersistedSecurityMasterRevisionV1, ...]:
    if len(rows) > 4096:
        raise SecurityMasterPersistenceError("security-master history exceeds 4096 revisions")
    revisions = tuple(_decode_row(row) for row in rows)
    ordering = tuple(
        (item.revision.fact_id, item.revision.revision_ordinal)
        for item in revisions
    )
    if ordering != tuple(sorted(ordering)):
        raise SecurityMasterPersistenceError("stored security-master history order is invalid")
    try:
        SecurityMasterResolver(revisions)
    except SecurityMasterIntegrityError as exc:
        raise SecurityMasterPersistenceError("stored security-master history is invalid") from exc
    return revisions


def _database_error(exc: Exception) -> Exception:
    sqlstate = getattr(exc, "sqlstate", None)
    if sqlstate == "P2S03":
        return SecurityMasterAmbiguityError("security-master database authority rejected ambiguity")
    if sqlstate in {"P2S01", "P2S02", "P2S04"}:
        return SecurityMasterIntegrityError("security-master database authority rejected revision")
    return SecurityMasterPersistenceError("security-master database authority failed")


class PostgresSecurityMasterRepository:
    """Persist through one DB authority and load bounded immutable histories."""

    def __init__(self, pool: _Pool) -> None:
        self._pool = pool

    def append(self, revision: SecurityMasterRevisionV1) -> SecurityMasterPersistenceOutcome:
        try:
            canonical_revision = SecurityMasterRevisionV1.model_validate(revision)
        except ValidationError as exc:
            raise SecurityMasterIntegrityError("revision is not canonical") from exc
        try:
            with self._pool.connection() as connection:
                with connection.transaction():
                    row = connection.execute(
                        SecurityMasterPostgresSql.APPEND_REVISION,
                        {
                            "canonical_revision_text": canonical_revision.canonical_revision_bytes.decode(
                                "utf-8"
                            )
                        },
                    ).fetchone()
        except Exception as exc:
            raise _database_error(exc) from None
        if row is None:
            raise SecurityMasterPersistenceError("security-master database authority returned no outcome")
        if not _exact_row_shape(row, _OUTCOME_NAMES):
            raise SecurityMasterPersistenceError("security-master database authority returned invalid outcome")
        revision_id = _row_value(row, "revision_id")
        revision_digest = _row_value(row, "revision_digest")
        inserted = _row_value(row, "inserted")
        if (
            revision_id != canonical_revision.revision_id
            or revision_digest != canonical_revision.digest
            or type(inserted) is not bool
        ):
            raise SecurityMasterPersistenceError("security-master database authority returned invalid outcome")
        return SecurityMasterPersistenceOutcome(revision_id, cast(str, revision_digest), cast(bool, inserted))

    def load_revision(
        self, revision_id: UUID
    ) -> PersistedSecurityMasterRevisionV1 | None:
        try:
            with self._pool.connection() as connection:
                row = connection.execute(
                    SecurityMasterPostgresSql.LOAD_REVISION,
                    {"revision_id": revision_id},
                ).fetchone()
        except Exception as exc:
            raise _database_error(exc) from None
        if row is None:
            return None
        revision = _decode_row(row)
        if revision.revision.revision_id != revision_id:
            raise SecurityMasterPersistenceError("database result does not bind the exact request")
        return revision

    def _history(
        self, statement: str, params: dict[str, object]
    ) -> tuple[PersistedSecurityMasterRevisionV1, ...]:
        try:
            with self._pool.connection() as connection:
                rows = connection.execute(statement, params).fetchall()
        except Exception as exc:
            raise _database_error(exc) from None
        return _decode_history(rows)

    def load_subject_history(
        self,
        kind: SecurityMasterIdentityKind,
        subject_id: UUID,
        *,
        knowledge_cutoff: datetime,
    ) -> tuple[PersistedSecurityMasterRevisionV1, ...]:
        cutoff = _utc(knowledge_cutoff, "knowledge_cutoff")
        revisions = self._history(
            SecurityMasterPostgresSql.LOAD_SUBJECT_HISTORY,
            {
                "subject_kind": kind.value,
                "subject_id": subject_id,
                "knowledge_cutoff": cutoff,
            },
        )
        if any(
            item.recorded_at > cutoff
            or item.revision.subject_kind is not kind
            or item.revision.subject_id != subject_id
            for item in revisions
        ):
            raise SecurityMasterPersistenceError("database history does not bind its selector or cutoff")
        return revisions

    def load_symbol_history(
        self,
        provider: str,
        raw_symbol: str,
        *,
        knowledge_cutoff: datetime,
    ) -> tuple[PersistedSecurityMasterRevisionV1, ...]:
        cutoff = _utc(knowledge_cutoff, "knowledge_cutoff")
        revisions = self._history(
            SecurityMasterPostgresSql.LOAD_SYMBOL_HISTORY,
            {
                "provider": provider,
                "raw_symbol": raw_symbol,
                "knowledge_cutoff": cutoff,
            },
        )
        if any(
            item.recorded_at > cutoff
            or item.revision.subject_kind
            is not SecurityMasterIdentityKind.SYMBOL_MAPPING
            or (
                item.revision.operation is SecurityMasterOperation.ASSERT
                and (
                    not isinstance(item.revision.payload, SymbolMappingPayloadV1)
                    or item.revision.payload.provider != provider
                    or item.revision.payload.raw_symbol != raw_symbol
                )
            )
            for item in revisions
        ):
            raise SecurityMasterPersistenceError("database history does not bind its selector or cutoff")
        return revisions

    def load_corporate_action_history(
        self,
        security_id: UUID,
        *,
        knowledge_cutoff: datetime,
    ) -> tuple[PersistedSecurityMasterRevisionV1, ...]:
        cutoff = _utc(knowledge_cutoff, "knowledge_cutoff")
        revisions = self._history(
            SecurityMasterPostgresSql.LOAD_CORPORATE_ACTION_HISTORY,
            {
                "security_id": security_id,
                "knowledge_cutoff": cutoff,
            },
        )
        if any(
            item.recorded_at > cutoff
            or item.revision.subject_kind
            is not SecurityMasterIdentityKind.CORPORATE_ACTION
            or (
                item.revision.operation is SecurityMasterOperation.ASSERT
                and (
                    not isinstance(item.revision.payload, _ACTION_PAYLOADS)
                    or item.revision.payload.security_id != security_id
                )
            )
            for item in revisions
        ):
            raise SecurityMasterPersistenceError("database history does not bind its selector or cutoff")
        return revisions

    def export_visible_revisions(
        self,
        *,
        knowledge_cutoff: datetime,
    ) -> tuple[PersistedSecurityMasterRevisionV1, ...]:
        """Load the complete bounded revision log visible at one DB cutoff."""

        cutoff = _utc(knowledge_cutoff, "knowledge_cutoff")
        try:
            with self._pool.connection() as connection:
                isolation_row = connection.execute(
                    SecurityMasterPostgresSql.READ_TRANSACTION_ISOLATION,
                    {},
                ).fetchone()
                if (
                    not _exact_row_shape(isolation_row, _ISOLATION_NAMES)
                    or _row_value(isolation_row, "transaction_isolation")
                    != "read committed"
                ):
                    raise SecurityMasterPersistenceError(
                        "security-master export requires read committed isolation"
                    )
                rows = connection.execute(
                    SecurityMasterPostgresSql.EXPORT_VISIBLE_REVISIONS,
                    {"knowledge_cutoff": cutoff},
                ).fetchall()
        except SecurityMasterPersistenceError:
            raise
        except Exception as exc:
            raise _database_error(exc) from None
        if len(rows) > 4096:
            raise SecurityMasterPersistenceError(
                "security-master export exceeds 4096 revisions"
            )
        revisions = tuple(_decode_row(row) for row in rows)
        ordering = tuple(
            (item.recorded_at, item.revision.revision_id) for item in revisions
        )
        if (
            any(item.recorded_at > cutoff for item in revisions)
            or ordering != tuple(sorted(ordering))
            or len(ordering) != len(set(ordering))
        ):
            raise SecurityMasterPersistenceError(
                "security-master export does not bind its cutoff or order"
            )
        try:
            SecurityMasterResolver(revisions)
        except SecurityMasterIntegrityError as exc:
            raise SecurityMasterPersistenceError(
                "security-master export history is invalid"
            ) from exc
        return revisions

    def iter_visible_revisions(
        self,
        *,
        knowledge_cutoff: datetime,
        after: tuple[datetime, UUID] | None,
        limit: int,
    ) -> tuple[PersistedSecurityMasterRevisionV1, ...]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise SecurityMasterPersistenceError("snapshot page limit must be 1 through 1000")
        cutoff = _utc(knowledge_cutoff, "knowledge_cutoff")
        after_recorded_at, after_revision_id = after or (
            datetime.min.replace(tzinfo=UTC),
            UUID(int=0),
        )
        after_recorded_at = _utc(after_recorded_at, "after recorded_at")
        try:
            with self._pool.connection() as connection:
                rows = connection.execute(
                    SecurityMasterPostgresSql.ITER_VISIBLE_REVISIONS,
                    {
                        "knowledge_cutoff": cutoff,
                        "after_recorded_at": after_recorded_at,
                        "after_revision_id": after_revision_id,
                        "limit": limit,
                    },
                ).fetchall()
        except Exception as exc:
            raise _database_error(exc) from None
        if len(rows) > limit:
            raise SecurityMasterPersistenceError("snapshot page exceeds its requested limit")
        revisions = tuple(_decode_row(row) for row in rows)
        ordering = tuple(
            (item.recorded_at, item.revision.revision_id) for item in revisions
        )
        if (
            any(item.recorded_at > cutoff for item in revisions)
            or any(
                key <= (after_recorded_at, after_revision_id) for key in ordering
            )
            or ordering != tuple(sorted(ordering))
            or len(ordering) != len(set(ordering))
        ):
            raise SecurityMasterPersistenceError("snapshot page does not bind its cutoff or cursor")
        return revisions


__all__ = [
    "PostgresSecurityMasterRepository",
    "SecurityMasterPersistenceError",
    "SecurityMasterPersistenceOutcome",
    "SecurityMasterPostgresSql",
]
