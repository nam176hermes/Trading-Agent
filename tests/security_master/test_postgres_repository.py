from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from packages.security_master import (
    PersistedSecurityMasterRevisionV1,
    PostgresSecurityMasterRepository,
    SecurityMasterAmbiguityError,
    SecurityMasterIntegrityError,
    SecurityMasterPersistenceError,
    SecurityMasterPostgresSql,
    SecurityMasterRevisionV1,
)

from .test_models import KNOWN, evidence, mapping_payload, revision


class Cursor:
    def __init__(self, rows: object) -> None:
        self.rows = rows

    def fetchone(self) -> object:
        if isinstance(self.rows, list):
            return self.rows[0] if self.rows else None
        return self.rows

    def fetchall(self) -> list[object]:
        if isinstance(self.rows, list):
            return self.rows
        return [] if self.rows is None else [self.rows]


class Connection:
    def __init__(self, results: list[object]) -> None:
        self.results = results
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.transactions = 0
        self.rollbacks = 0

    @contextmanager
    def transaction(self):
        self.transactions += 1
        try:
            yield self
        except BaseException:
            self.rollbacks += 1
            raise
        finally:
            self.transactions -= 1

    def execute(self, statement: str, params: dict[str, object]) -> Cursor:
        self.calls.append((statement, params))
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return Cursor(result)


class Pool:
    def __init__(self, connection: Connection) -> None:
        self.connection_value = connection

    @contextmanager
    def connection(self):
        yield self.connection_value


class DatabaseError(RuntimeError):
    def __init__(self, sqlstate: str) -> None:
        super().__init__("raw database details must not escape")
        self.sqlstate = sqlstate


def stored_row(
    document: SecurityMasterRevisionV1 | None = None,
    *,
    recorded_at=None,
    **updates: object,
) -> dict[str, object]:
    document = document or revision()
    recorded_at = recorded_at or document.known_at
    payload = document.payload
    row: dict[str, object] = {
        "canonical_revision_text": document.canonical_revision_bytes.decode("utf-8"),
        "revision_digest": document.digest,
        "revision_id": document.revision_id,
        "fact_id": document.fact_id,
        "subject_id": document.subject_id,
        "subject_kind": document.subject_kind.value,
        "revision_ordinal": document.revision_ordinal,
        "operation": document.operation.value,
        "effective_from": document.effective_from,
        "effective_to": document.effective_to,
        "known_at": document.known_at,
        "recorded_at": recorded_at,
        "supersedes_revision_id": document.supersedes_revision_id,
        "lookup_provider": payload.provider if payload is not None else None,
        "lookup_symbol": payload.raw_symbol if payload is not None else None,
        "related_security_id": None,
    }
    row.update(updates)
    return row


def persisted(
    document: SecurityMasterRevisionV1 | None = None,
    *,
    recorded_at=None,
) -> PersistedSecurityMasterRevisionV1:
    document = document or revision()
    return PersistedSecurityMasterRevisionV1(
        revision=document,
        recorded_at=recorded_at or document.known_at,
    )


def test_append_uses_one_parameterized_database_function_and_exact_outcome() -> None:
    document = revision()
    connection = Connection(
        [
            {
                "revision_id": document.revision_id,
                "revision_digest": document.digest,
                "inserted": True,
            }
        ]
    )

    outcome = PostgresSecurityMasterRepository(Pool(connection)).append(document)

    assert outcome.inserted is True
    assert outcome.revision_id == document.revision_id
    assert outcome.revision_digest == document.digest
    assert connection.calls == [
        (
            SecurityMasterPostgresSql.APPEND_REVISION,
            {"canonical_revision_text": document.canonical_revision_bytes.decode("utf-8")},
        )
    ]
    assert "INSERT" not in SecurityMasterPostgresSql.APPEND_REVISION
    assert connection.transactions == 0


def test_append_accepts_the_default_positional_psycopg_outcome() -> None:
    document = revision()
    repository = PostgresSecurityMasterRepository(
        Pool(Connection([(document.revision_id, document.digest, True)]))
    )

    assert repository.append(document).inserted is True


def test_append_exact_retry_and_typed_sqlstate_mapping() -> None:
    document = revision()
    retry = Connection(
        [{"revision_id": document.revision_id, "revision_digest": document.digest, "inserted": False}]
    )
    assert PostgresSecurityMasterRepository(Pool(retry)).append(document).inserted is False

    for sqlstate, error_type in (
        ("P2S01", SecurityMasterIntegrityError),
        ("P2S02", SecurityMasterIntegrityError),
        ("P2S03", SecurityMasterAmbiguityError),
        ("P2S04", SecurityMasterIntegrityError),
    ):
        connection = Connection([DatabaseError(sqlstate)])
        with pytest.raises(error_type, match="security-master database authority") as caught:
            PostgresSecurityMasterRepository(Pool(connection)).append(document)
        assert "raw database" not in str(caught.value)


def test_load_revision_revalidates_canonical_bytes_digest_and_all_scalar_mirrors() -> None:
    document = revision()
    repository = PostgresSecurityMasterRepository(Pool(Connection([[stored_row()]])))

    assert repository.load_revision(document.revision_id) == persisted(document)

    for update in (
        {"revision_digest": "0" * 64},
        {"subject_id": UUID("60000000-0000-4000-8000-000000000099")},
        {"lookup_symbol": "btcusdt"},
    ):
        tampered = PostgresSecurityMasterRepository(Pool(Connection([[stored_row(**update)]])))
        with pytest.raises(SecurityMasterPersistenceError):
            tampered.load_revision(document.revision_id)


def test_history_queries_are_bounded_deterministic_and_never_resolve_in_sql() -> None:
    document = revision()
    connection = Connection([[stored_row()]])
    repository = PostgresSecurityMasterRepository(Pool(connection))

    assert repository.load_subject_history(
        document.subject_kind,
        document.subject_id,
        knowledge_cutoff=KNOWN,
    ) == (persisted(document),)
    statement, params = connection.calls[0]
    assert "recorded_at <= %(knowledge_cutoff)s" in statement
    assert "known_at <= %(knowledge_cutoff)s" not in statement
    assert "ORDER BY fact_id, revision_ordinal" in statement
    assert "LIMIT 4097" in statement
    assert "DISTINCT ON" not in statement
    assert "operation = 'ASSERT'" not in statement
    assert params["knowledge_cutoff"] == KNOWN


def test_history_over_bound_fails_closed() -> None:
    rows = [stored_row() for _ in range(4097)]
    repository = PostgresSecurityMasterRepository(Pool(Connection([rows])))

    with pytest.raises(SecurityMasterPersistenceError, match="4096"):
        repository.load_symbol_history("BINANCE", "BTCUSDT", knowledge_cutoff=KNOWN)


def test_snapshot_iteration_requires_explicit_cutoff_cursor_and_bounded_limit() -> None:
    document = revision()
    connection = Connection([[stored_row()]])
    repository = PostgresSecurityMasterRepository(Pool(connection))

    assert repository.iter_visible_revisions(
        knowledge_cutoff=KNOWN,
        after=(KNOWN - timedelta(seconds=1), UUID(int=0)),
        limit=1000,
    ) == (persisted(document),)
    statement, params = connection.calls[0]
    assert "(recorded_at, revision_id) > (%(after_recorded_at)s, %(after_revision_id)s)" in statement
    assert "ORDER BY recorded_at, revision_id" in statement
    assert params["limit"] == 1000

    with pytest.raises(SecurityMasterPersistenceError, match="limit"):
        repository.iter_visible_revisions(knowledge_cutoff=KNOWN, after=None, limit=1001)


def test_database_queries_reject_non_utc_cutoffs_before_sql() -> None:
    connection = Connection([])
    repository = PostgresSecurityMasterRepository(Pool(connection))
    naive = KNOWN.replace(tzinfo=None)

    with pytest.raises(SecurityMasterPersistenceError, match="UTC"):
        repository.load_symbol_history("BINANCE", "BTCUSDT", knowledge_cutoff=naive)
    with pytest.raises(SecurityMasterPersistenceError, match="UTC"):
        repository.iter_visible_revisions(
            knowledge_cutoff=KNOWN,
            after=(naive, UUID(int=0)),
            limit=1,
        )
    assert connection.calls == []


def test_database_read_errors_are_typed_and_redacted() -> None:
    repository = PostgresSecurityMasterRepository(
        Pool(Connection([DatabaseError("08006")]))
    )

    with pytest.raises(SecurityMasterPersistenceError) as caught:
        repository.load_revision(revision().revision_id)
    assert "raw database" not in str(caught.value)


def test_database_reads_bind_exact_request_cutoff_cursor_and_limit() -> None:
    requested = revision()
    other = revision(
        revision_id=UUID("90000000-0000-4000-8000-000000000099")
    )
    wrong = PostgresSecurityMasterRepository(
        Pool(Connection([[stored_row(other)]]))
    )
    with pytest.raises(SecurityMasterPersistenceError, match="request"):
        wrong.load_revision(requested.revision_id)

    future = revision(
        revision_id=UUID("90000000-0000-4000-8000-000000000098"),
        known_at=KNOWN + timedelta(minutes=1),
        evidence=(evidence(known_at=KNOWN + timedelta(minutes=1)),),
    )
    future_page = PostgresSecurityMasterRepository(
        Pool(Connection([[stored_row(future)]]))
    )
    with pytest.raises(SecurityMasterPersistenceError, match="cutoff"):
        future_page.iter_visible_revisions(
            knowledge_cutoff=KNOWN,
            after=None,
            limit=1,
        )

    oversized_page = PostgresSecurityMasterRepository(
        Pool(Connection([[stored_row(requested), stored_row(other)]]))
    )
    with pytest.raises(SecurityMasterPersistenceError, match="limit"):
        oversized_page.iter_visible_revisions(
            knowledge_cutoff=KNOWN,
            after=None,
            limit=1,
        )


def test_database_mapping_rows_have_exact_query_shape_and_selector() -> None:
    extra = stored_row(unexpected="field")
    repository = PostgresSecurityMasterRepository(Pool(Connection([[extra]])))
    with pytest.raises(SecurityMasterPersistenceError, match="shape"):
        repository.load_revision(revision().revision_id)

    wrong_selector = revision(
        revision_id=UUID("90000000-0000-4000-8000-000000000097"),
        payload=mapping_payload().model_copy(update={"provider": "OTHER"}),
    )
    repository = PostgresSecurityMasterRepository(
        Pool(Connection([[stored_row(wrong_selector)]]))
    )
    with pytest.raises(SecurityMasterPersistenceError, match="selector"):
        repository.load_symbol_history(
            "BINANCE", "BTCUSDT", knowledge_cutoff=KNOWN
        )


def test_database_reads_use_db_recorded_at_without_historical_lookahead() -> None:
    document = revision()
    recorded_at = KNOWN + timedelta(hours=2)
    late_row = stored_row(document, recorded_at=recorded_at)

    historical = PostgresSecurityMasterRepository(
        Pool(Connection([[late_row]]))
    )
    with pytest.raises(SecurityMasterPersistenceError, match="cutoff"):
        historical.load_subject_history(
            document.subject_kind,
            document.subject_id,
            knowledge_cutoff=KNOWN + timedelta(hours=1),
        )

    current = PostgresSecurityMasterRepository(Pool(Connection([[late_row]])))
    assert current.load_subject_history(
        document.subject_kind,
        document.subject_id,
        knowledge_cutoff=recorded_at,
    ) == (persisted(document, recorded_at=recorded_at),)

    page = PostgresSecurityMasterRepository(Pool(Connection([[late_row]])))
    assert page.iter_visible_revisions(
        knowledge_cutoff=recorded_at,
        after=(KNOWN + timedelta(hours=1), UUID(int=0)),
        limit=1,
    ) == (persisted(document, recorded_at=recorded_at),)


def test_database_rejects_recorded_at_mirror_before_evidence_knowledge() -> None:
    document = revision()
    repository = PostgresSecurityMasterRepository(
        Pool(
            Connection(
                [[stored_row(document, recorded_at=document.known_at - timedelta(seconds=1))]]
            )
        )
    )

    with pytest.raises(SecurityMasterPersistenceError, match="recorded_at"):
        repository.load_revision(document.revision_id)


def test_database_normalizes_psycopg_zoneinfo_utc_recorded_at() -> None:
    document = revision()
    psycopg_recorded_at = document.known_at.astimezone(ZoneInfo("UTC"))
    assert psycopg_recorded_at.tzinfo is not document.known_at.tzinfo
    repository = PostgresSecurityMasterRepository(
        Pool(Connection([[stored_row(document, recorded_at=psycopg_recorded_at)]]))
    )

    assert repository.load_revision(document.revision_id) == persisted(document)


def test_authoritative_export_is_complete_bounded_and_recorded_at_visible() -> None:
    root = revision()
    root_recorded_at = KNOWN + timedelta(hours=1)
    connection = Connection(
        [
            {"transaction_isolation": "read committed"},
            [stored_row(root, recorded_at=root_recorded_at)],
        ]
    )
    repository = PostgresSecurityMasterRepository(Pool(connection))
    export = getattr(repository, "export_visible_revisions", None)
    assert callable(export), "authoritative complete export is missing"

    assert export(
        knowledge_cutoff=KNOWN + timedelta(hours=1, minutes=30)
    ) == (persisted(root, recorded_at=root_recorded_at),)
    assert connection.calls[0] == (
        SecurityMasterPostgresSql.READ_TRANSACTION_ISOLATION,
        {},
    )
    assert connection.calls[0][0] == (
        "SELECT pg_catalog.current_setting('transaction_isolation', false) "
        "AS transaction_isolation;"
    )
    statement, params = connection.calls[1]
    assert statement == SecurityMasterPostgresSql.EXPORT_VISIBLE_REVISIONS
    assert "recorded_at <= %(knowledge_cutoff)s" in statement
    assert "ORDER BY recorded_at, revision_id" in statement
    assert "LIMIT 4097" in statement
    assert params == {"knowledge_cutoff": KNOWN + timedelta(hours=1, minutes=30)}

    late = PostgresSecurityMasterRepository(
        Pool(
            Connection(
                [
                    {"transaction_isolation": "read committed"},
                    [stored_row(root, recorded_at=KNOWN + timedelta(hours=2))],
                ]
            )
        )
    )
    with pytest.raises(SecurityMasterPersistenceError, match="cutoff"):
        late.export_visible_revisions(
            knowledge_cutoff=KNOWN + timedelta(hours=1)
        )

    over_bound = PostgresSecurityMasterRepository(
        Pool(
            Connection(
                [
                    {"transaction_isolation": "read committed"},
                    [stored_row(root)] * 4097,
                ]
            )
        )
    )
    with pytest.raises(SecurityMasterPersistenceError, match="4096"):
        over_bound.export_visible_revisions(knowledge_cutoff=KNOWN)


@pytest.mark.parametrize(
    "isolation_row",
    (
        {"transaction_isolation": "read committed"},
        ("read committed",),
    ),
    ids=("mapping-row", "positional-row"),
)
def test_authoritative_export_accepts_exact_isolation_rows_and_empty_result(
    isolation_row: object,
) -> None:
    repository = PostgresSecurityMasterRepository(
        Pool(Connection([isolation_row, []]))
    )

    assert repository.export_visible_revisions(knowledge_cutoff=KNOWN) == ()


@pytest.mark.parametrize(
    "isolation_row",
    (
        {"transaction_isolation": "repeatable read"},
        {},
        {
            "transaction_isolation": "read committed",
            "unexpected": "field",
        },
        (),
    ),
    ids=("wrong-level", "missing-field", "extra-field", "wrong-tuple-shape"),
)
def test_authoritative_export_fails_closed_for_non_exact_read_committed_isolation(
    isolation_row: object,
) -> None:
    connection = Connection([isolation_row, []])
    repository = PostgresSecurityMasterRepository(Pool(connection))

    with pytest.raises(SecurityMasterPersistenceError, match="isolation"):
        repository.export_visible_revisions(knowledge_cutoff=KNOWN)
    assert len(connection.calls) == 1


@pytest.mark.parametrize(
    "results",
    (
        [DatabaseError("08006")],
        [
            {"transaction_isolation": "read committed"},
            DatabaseError("08006"),
        ],
    ),
    ids=("isolation-query", "export-query"),
)
def test_authoritative_export_database_errors_are_typed_and_redacted(
    results: list[object],
) -> None:
    repository = PostgresSecurityMasterRepository(Pool(Connection(results)))

    with pytest.raises(SecurityMasterPersistenceError) as caught:
        repository.export_visible_revisions(knowledge_cutoff=KNOWN)
    assert "raw database" not in str(caught.value)
