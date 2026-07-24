from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import os
from typing import Any
from uuid import UUID

import psycopg
import pytest

from tests.jobs._postgres import (
    _upgrade_to_revision,
    disposable_database,
    disposable_role_settings,
)


pytestmark = pytest.mark.runtime_postgres

EXACT_0008_HEAD = "0008_trading_domain_ledger"
OPERATION_ID = "event-ledger-durability-runtime-green-v1"
BIGINT_MAX = 9223372036854775807
EVENT_ID = "00000000-0000-0000-0000-000000000001"
ISSUE_ID = "00000000-0000-0000-0000-000000000002"
STREAM_ID = "00000000-0000-0000-0000-000000000010"
DIGEST = "a" * 64
TOPIC = "domain.signal"
PAYLOAD_TEXT = '{"attempt":1}'
UNKNOWN_EVENT_ID = "00000000-0000-0000-0000-000000000099"
CHAIN_EVENT_ID_1 = "00000000-0000-0000-0000-000000000021"
CHAIN_EVENT_ID_2 = "00000000-0000-0000-0000-000000000022"
CHAIN_GAP_EVENT_ID = "00000000-0000-0000-0000-000000000024"
CHAIN_STREAM_ID = "00000000-0000-0000-0000-000000000020"
ROLLBACK_EVENT_ID = "00000000-0000-0000-0000-000000000031"
ROLLBACK_STREAM_ID = "00000000-0000-0000-0000-000000000030"
NON_OWNER_ROLES = (
    "trading_migrator",
    "trading_reader",
    "trading_job_api",
    "trading_job_worker",
    "trading_job_scheduler",
)


def _canonical(document: object) -> str:
    return json.dumps(
        document,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _empty_snapshot_document() -> dict[str, Any]:
    return {
        "issues": [],
        "reducer_version": "event-ledger-reducer-v1",
        "schema_version": "event-ledger-replay-v1",
        "state": {
            "applied_events": [],
            "event_count": 0,
            "streams": [],
            "type_counts": [],
        },
        "status": "COMPLETE",
    }


def _applied_snapshot_document() -> dict[str, Any]:
    document = _empty_snapshot_document()
    document["state"] = {
        "applied_events": [{"digest": DIGEST, "event_id": EVENT_ID}],
        "event_count": 1,
        "streams": [
            {
                "event_count": 1,
                "last_digest": DIGEST,
                "last_sequence": 1,
                "stream_id": STREAM_ID,
            }
        ],
        "type_counts": [{"count": 1, "event_type": "SignalProposal"}],
    }
    return document


def _degraded_snapshot_document() -> dict[str, Any]:
    document = _empty_snapshot_document()
    document["issues"] = [
        {
            "code": "SEQUENCE_GAP",
            "digest": DIGEST,
            "event_id": ISSUE_ID,
            "expected_sequence": 1,
            "sequence": 2,
            "stream_id": STREAM_ID,
        }
    ]
    document["status"] = "DEGRADED"
    return document


@pytest.fixture(scope="module")
def snapshot_database():
    if (
        os.environ.get("TRADING_TEST_ALLOW_DISPOSABLE_POSTGRES") != "YES"
        or os.environ.get("TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE")
        != "DISPOSABLE_PG_GREEN"
    ):
        pytest.skip("exact disposable PostgreSQL GREEN authority is not present")
    with disposable_database(operation_id=OPERATION_ID, planned=True) as owner:
        _upgrade_to_revision(owner, EXACT_0008_HEAD)
        yield owner


def _snapshot_count(connection: psycopg.Connection) -> int:
    return connection.execute(
        "SELECT count(*) FROM public.aggregate_snapshots"
    ).fetchone()[0]


def _save(connection: psycopg.Connection, document: object) -> bool:
    return connection.execute(
        "SELECT public.save_domain_snapshot(%s)",
        (_canonical(document),),
    ).fetchone()[0]


def _append_event(
    connection: psycopg.Connection,
    *,
    event_id: str = EVENT_ID,
    stream_id: str = STREAM_ID,
    sequence: int = 1,
    topic: str = TOPIC,
    payload_text: str = PAYLOAD_TEXT,
    source: str | None = None,
) -> bool:
    document = {
        "event_id": event_id,
        "event_type": "SignalProposal",
        "sequence": sequence,
        "stream_id": stream_id,
    }
    if source is not None:
        document["source"] = source
    event_text = _canonical(document)
    return connection.execute(
        "SELECT public.append_domain_event(%s, %s, %s, %s, %s, %s, %s)",
        (
            event_id,
            stream_id,
            sequence,
            "SignalProposal",
            event_text,
            topic,
            payload_text,
        ),
    ).fetchone()[0]


def _append_request_digest(event_text: str) -> str:
    parts = (
        UUID(EVENT_ID).bytes,
        UUID(STREAM_ID).bytes,
        b"1",
        b"SignalProposal",
        event_text.encode("utf-8"),
        TOPIC.encode("utf-8"),
        PAYLOAD_TEXT.encode("utf-8"),
    )
    digest = sha256()
    for part in parts:
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    return digest.hexdigest()


def _assert_rejected(
    connection: psycopg.Connection,
    document: object,
    message: str,
) -> None:
    before = _snapshot_count(connection)
    with pytest.raises(psycopg.Error, match=message) as raised:
        _save(connection, document)
    assert raised.value.sqlstate == "23514"
    assert _snapshot_count(connection) == before


def test_runtime_postgres_accepts_unicode_and_global_snapshot_golden_vectors(
    snapshot_database,
) -> None:
    unicode_document = {"astral": "😀", "bmp": "é"}
    expected_unicode = '{"astral":"\\ud83d\\ude00","bmp":"\\u00e9"}'
    with psycopg.connect(snapshot_database.conninfo(), autocommit=True) as connection:
        assert connection.execute(
            "SELECT public.canonical_domain_json(%s::jsonb)",
            (_canonical(unicode_document),),
        ).fetchone()[0] == expected_unicode

        for document in (
            _empty_snapshot_document(),
            _applied_snapshot_document(),
            _degraded_snapshot_document(),
        ):
            assert _save(connection, document) is True
            assert _save(connection, document) is False

        rows = connection.execute(
            """
            SELECT state_hash, canonical_state_json, status, issues, state
            FROM public.aggregate_snapshots
            ORDER BY state_hash
            """
        ).fetchall()
        assert len(rows) == 3
        for state_hash, canonical_text, status, issues, state in rows:
            assert len(state_hash) == 64
            assert _canonical(json.loads(canonical_text)) == canonical_text
            wrapper = json.loads(canonical_text)
            assert wrapper["status"] == status
            assert wrapper["issues"] == issues
            assert wrapper["state"] == state


def test_runtime_postgres_rejects_recomputed_forged_snapshot_vectors(
    snapshot_database,
) -> None:
    overflow = _empty_snapshot_document()
    overflow["state"]["event_count"] = BIGINT_MAX + 1

    wrong_event_count = _applied_snapshot_document()
    wrong_event_count["state"]["event_count"] = 2

    wrong_type_total = _applied_snapshot_document()
    wrong_type_total["state"]["type_counts"][0]["count"] = 0

    wrong_stream_total = _applied_snapshot_document()
    wrong_stream_total["state"]["streams"][0]["event_count"] = 2
    wrong_stream_total["state"]["streams"][0]["last_sequence"] = 2

    invalid_issue = _degraded_snapshot_document()
    invalid_issue["issues"][0]["sequence"] = 1

    extra_key = _empty_snapshot_document()
    extra_key["stream_id"] = STREAM_ID

    status_mismatch = _empty_snapshot_document()
    status_mismatch["status"] = "DEGRADED"

    vectors = (
        (overflow, "outside PostgreSQL bigint bounds"),
        (wrong_event_count, "event_count does not match applied events"),
        (wrong_type_total, "type counts do not match event_count"),
        (wrong_stream_total, "stream counts do not match event_count"),
        (invalid_issue, "replay issue is structurally inconsistent"),
        (extra_key, "wrapper keys are invalid"),
        (status_mismatch, "status does not match replay issues"),
    )

    with psycopg.connect(snapshot_database.conninfo(), autocommit=True) as connection:
        for document, message in vectors:
            forged = deepcopy(document)
            assert _canonical(forged) == _canonical(json.loads(_canonical(forged)))
            _assert_rejected(connection, forged, message)


def test_runtime_postgres_retry_survives_publish_retention_and_inbox_claim_is_permanent(
    snapshot_database,
) -> None:
    with psycopg.connect(snapshot_database.conninfo(), autocommit=True) as connection:
        assert _append_event(connection) is True
        event_text = _canonical(
            {
                "event_id": EVENT_ID,
                "event_type": "SignalProposal",
                "sequence": 1,
                "stream_id": STREAM_ID,
            }
        )
        assert connection.execute(
            "SELECT request_digest FROM public.event_append_idempotency WHERE event_id = %s",
            (EVENT_ID,),
        ).fetchone()[0] == _append_request_digest(event_text)
        with pytest.raises(psycopg.Error, match="durable publication receipt"):
            connection.execute(
                "DELETE FROM public.event_outbox WHERE event_id = %s",
                (EVENT_ID,),
            )
        with pytest.raises(psycopg.Error, match="immutable pending work"):
            connection.execute(
                "UPDATE public.event_outbox SET topic = 'changed' WHERE event_id = %s",
                (EVENT_ID,),
            )
        assert connection.execute(
            "SELECT public.acknowledge_domain_publication(%s)",
            (EVENT_ID,),
        ).fetchone()[0] is True
        assert connection.execute(
            "SELECT public.acknowledge_domain_publication(%s)",
            (EVENT_ID,),
        ).fetchone()[0] is False
        with pytest.raises(psycopg.Error, match="pending outbox"):
            connection.execute(
                "SELECT public.acknowledge_domain_publication(%s)",
                (UNKNOWN_EVENT_ID,),
            )
        assert connection.execute(
            "SELECT count(*) FROM public.event_outbox WHERE event_id = %s",
            (EVENT_ID,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM public.event_publications WHERE event_id = %s",
            (EVENT_ID,),
        ).fetchone()[0] == 1

        assert _append_event(connection) is False
        for changed_arguments in (
            {"payload_text": '{"attempt":2}'},
            {"topic": "domain.changed"},
            {"source": "changed"},
        ):
            with pytest.raises(psycopg.Error) as conflict:
                _append_event(connection, **changed_arguments)
            assert conflict.value.sqlstate == "23505"
        assert connection.execute(
            "SELECT count(*) FROM public.event_outbox WHERE event_id = %s",
            (EVENT_ID,),
        ).fetchone()[0] == 0

        claim_sql = """WITH inserted AS (
            INSERT INTO public.consumer_inbox (consumer, event_id)
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING
            RETURNING 1
        ) SELECT EXISTS (SELECT 1 FROM inserted)"""
        assert connection.execute(claim_sql, ("consumer", EVENT_ID)).fetchone()[0] is True
        assert connection.execute(claim_sql, ("consumer", EVENT_ID)).fetchone()[0] is False

        for statement in (
            "DELETE FROM public.consumer_inbox WHERE consumer = 'consumer'",
            "UPDATE public.consumer_inbox SET consumer = 'other' WHERE consumer = 'consumer'",
            "DELETE FROM public.event_publications WHERE event_id = '00000000-0000-0000-0000-000000000001'",
            "UPDATE public.event_append_idempotency SET request_digest = repeat('b', 64) WHERE event_id = '00000000-0000-0000-0000-000000000001'",
            "TRUNCATE public.consumer_inbox",
            "TRUNCATE public.event_publications",
            "TRUNCATE public.event_append_idempotency",
            "TRUNCATE public.domain_events CASCADE",
        ):
            with pytest.raises(psycopg.Error, match="append-only"):
                connection.execute(statement)
        with pytest.raises(psycopg.Error, match="immutable"):
            connection.execute("TRUNCATE public.aggregate_snapshots")
        with pytest.raises(psycopg.Error, match="immutable pending work"):
            connection.execute("TRUNCATE public.event_outbox")

        assert connection.execute(claim_sql, ("consumer", EVENT_ID)).fetchone()[0] is False


def test_runtime_postgres_keeps_existing_non_owner_roles_dormant(
    snapshot_database,
) -> None:
    probes = (
        ("SELECT count(*) FROM public.event_outbox", ()),
        (
            "SELECT public.acknowledge_domain_publication(%s)",
            (UNKNOWN_EVENT_ID,),
        ),
        (
            "INSERT INTO public.consumer_inbox (consumer, event_id) VALUES (%s, %s)",
            ("unauthorized", EVENT_ID),
        ),
    )
    for role in NON_OWNER_ROLES:
        settings = disposable_role_settings(snapshot_database, role)
        with psycopg.connect(settings.conninfo(), autocommit=True) as connection:
            connection.execute("SET default_transaction_read_only = off")
            for statement, parameters in probes:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    connection.execute(statement, parameters)


def test_runtime_postgres_event_chain_is_atomic_and_replay_order_is_deterministic(
    snapshot_database,
) -> None:
    with psycopg.connect(snapshot_database.conninfo(), autocommit=True) as connection:
        assert _append_event(
            connection,
            event_id=CHAIN_EVENT_ID_1,
            stream_id=CHAIN_STREAM_ID,
            sequence=1,
        ) is True
        assert _append_event(
            connection,
            event_id=CHAIN_EVENT_ID_2,
            stream_id=CHAIN_STREAM_ID,
            sequence=2,
        ) is True

        replay_sql = """
            SELECT event_id::text,sequence,canonical_event_text,digest
            FROM public.domain_events
            WHERE stream_id=%s ORDER BY sequence,event_id
        """
        first = connection.execute(replay_sql, (CHAIN_STREAM_ID,)).fetchall()
        second = connection.execute(replay_sql, (CHAIN_STREAM_ID,)).fetchall()
        assert first == second
        assert [row[0] for row in first] == [CHAIN_EVENT_ID_1, CHAIN_EVENT_ID_2]
        assert [row[1] for row in first] == [1, 2]
        assert all(sha256(row[2].encode("utf-8")).hexdigest() == row[3] for row in first)

        with pytest.raises(psycopg.Error) as gap:
            _append_event(
                connection,
                event_id=CHAIN_GAP_EVENT_ID,
                stream_id=CHAIN_STREAM_ID,
                sequence=4,
            )
        assert gap.value.sqlstate == "23514"
        assert connection.execute(
            "SELECT count(*) FROM public.domain_events WHERE event_id=%s",
            (CHAIN_GAP_EVENT_ID,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM public.event_append_idempotency WHERE event_id=%s",
            (CHAIN_GAP_EVENT_ID,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM public.event_outbox WHERE event_id=%s",
            (CHAIN_GAP_EVENT_ID,),
        ).fetchone()[0] == 0

        with connection.transaction(force_rollback=True):
            assert _append_event(
                connection,
                event_id=ROLLBACK_EVENT_ID,
                stream_id=ROLLBACK_STREAM_ID,
                sequence=1,
            ) is True
        for table in (
            "domain_events",
            "event_append_idempotency",
            "event_outbox",
        ):
            assert connection.execute(
                f"SELECT count(*) FROM public.{table} WHERE event_id=%s",
                (ROLLBACK_EVENT_ID,),
            ).fetchone()[0] == 0
