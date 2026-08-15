from __future__ import annotations

import os
from pathlib import Path
import re

import psycopg
from psycopg.types.json import Jsonb
import pytest

from packages.job_authority import find_event_chain_violations, load_frozen_contract
from packages.job_contracts import ORDINARY_TRANSITIONS, RETRY_TRANSITIONS
from tests.jobs._postgres import (
    _upgrade_to_revision,
    disposable_database,
)


ROOT = Path(__file__).parents[2]
CONTRACT_PATH = (
    ROOT / "ops/postgres/job-plane-authority/query-contract-v1.json"
)
EVENT_CHAIN_RED_OPERATION_ID = "jobs-event-chain-authority-red-v1"
EVENT_CHAIN_GREEN_OPERATION_ID = "jobs-event-chain-authority-green-v1"
EXACT_0006_HEAD = "0006_job_transition_database_authority"
EXACT_0007_HEAD = "0007_job_event_chain_authority"
CONTRACT = load_frozen_contract(CONTRACT_PATH)

ORDINARY_EDGES = {
    (source.value, target.value) for source, target in ORDINARY_TRANSITIONS
}
RETRY_EDGES = {
    (source.value, target.value) for source, target in RETRY_TRANSITIONS
}
VIOLATION_CODES = {
    "NO_HISTORY",
    "SEQUENCE_START",
    "SEQUENCE_GAP",
    "SEQUENCE_DUPLICATE",
    "BOOTSTRAP_EDGE",
    "BOOTSTRAP_ATTEMPT",
    "LATER_NULL_FROM_STATE",
    "DISCONNECTED_FROM_STATE",
    "UNAPPROVED_EDGE",
    "FINAL_STATE_MISMATCH",
    "CROSS_JOB_ATTEMPT",
    "RETRY_WRONG_ACTOR",
    "RETRY_WRONG_REASON",
    "RETRY_METADATA",
    "RETRY_ATTEMPT_CHANGED",
    "RETRY_FORGED_ATTEMPT",
    "RETRY_OVER_BUDGET",
    "RETRY_NOT_ADJACENT",
    "EVENT_AFTER_TERMINAL",
    "DUPLICATE_TERMINAL_IN_EPOCH",
}


def _event_vocabularies(
    sql: str,
) -> tuple[list[tuple[str, str]], list[str]]:
    edge_match = re.search(
        r"SELECT 'UNAPPROVED_EDGE'.*?NOT IN \((?P<body>.*?)\n    \)\n\n"
        r"  UNION ALL\n  SELECT 'FINAL_STATE_MISMATCH'",
        sql,
        flags=re.DOTALL,
    )
    assert edge_match is not None
    edges = re.findall(
        r"\('([A-Z_]+)', '([A-Z_]+)'\)",
        edge_match.group("body"),
    )
    codes = re.findall(r"\bSELECT '([A-Z_]+)'", sql)
    return edges, codes


def _assert_event_vocabularies_closed(sql: str) -> None:
    edges, codes = _event_vocabularies(sql)
    assert len(edges) == 14
    assert set(edges) == ORDINARY_EDGES | RETRY_EDGES
    assert len(codes) == 20
    assert set(codes) == VIOLATION_CODES


@pytest.fixture(
    scope="module",
    params=("DISPOSABLE_PG_RED", "DISPOSABLE_PG_GREEN"),
    ids=("DISPOSABLE_PG_RED", "DISPOSABLE_PG_GREEN"),
)
def authority_database(request: pytest.FixtureRequest):
    scope = str(request.param)
    if (
        os.environ.get("TRADING_TEST_ALLOW_DISPOSABLE_POSTGRES") != "YES"
        or os.environ.get("TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE") != scope
    ):
        pytest.skip("exact disposable PostgreSQL authority is not present")
    with disposable_database(
        operation_id=(
            EVENT_CHAIN_RED_OPERATION_ID
            if scope == "DISPOSABLE_PG_RED"
            else EVENT_CHAIN_GREEN_OPERATION_ID
        ),
    ) as owner:
        if scope == "DISPOSABLE_PG_RED":
            _upgrade_to_revision(owner, EXACT_0006_HEAD)
        else:
            _upgrade_to_revision(owner, EXACT_0007_HEAD)
        yield owner


def test_event_query_freezes_exact_transition_and_violation_vocabularies() -> None:
    assert len(ORDINARY_EDGES) == 12
    assert len(RETRY_EDGES) == 2
    assert len(VIOLATION_CODES) == 20
    _assert_event_vocabularies_closed(CONTRACT.event_chain_sql)
    assert CONTRACT.event_chain_query_id == "job-plane-event-chain-v1"
    assert 'COLLATE "C"' in CONTRACT.event_chain_sql


@pytest.mark.parametrize(
    "drifted_sql",
    (
        CONTRACT.event_chain_sql.replace(
            "      ('TIMED_OUT', 'QUEUED')\n    )",
            "      ('TIMED_OUT', 'QUEUED'),\n"
            "      ('BLOCKED', 'QUEUED')\n    )",
            1,
        ),
        CONTRACT.event_chain_sql.replace(
            "  SELECT 'FINAL_STATE_MISMATCH'",
            "  SELECT 'UNREVIEWED_CODE', job_row.job_id, NULL::text, "
            "NULL::bigint\n  FROM public.jobs job_row\n\n  UNION ALL\n"
            "  SELECT 'FINAL_STATE_MISMATCH'",
            1,
        ),
    ),
    ids=("extra-edge", "extra-code"),
)
def test_event_vocabulary_closure_rejects_explicit_extras(
    drifted_sql: str,
) -> None:
    with pytest.raises(AssertionError):
        _assert_event_vocabularies_closed(drifted_sql)


def test_retry_validity_is_classified_before_epoch_assignment() -> None:
    sql = CONTRACT.event_chain_sql

    assert sql.index("retry_classification AS") < sql.index("epoch_events AS")
    assert "CASE WHEN retry_classification.is_valid_retry THEN 1 ELSE 0 END" in sql
    assert "event_context.attempt_number < event_context.max_attempts" in sql
    assert (
        "event_context.sequence::numeric -\n"
        "                 event_context.previous_sequence::numeric) = 1"
    ) in sql
    assert "event_context.attempt_id = event_context.previous_attempt_id" in sql
    assert "event_context.metadata = '{}'::jsonb" in sql
    assert not re.search(
        r"event_context\.from_state = 'FAILED'\s+AND\s+"
        r"event_context\.reason_code =\s+"
        r"'LEASE_EXPIRED_RETRY_SCHEDULED'",
        sql,
    )


def test_sequence_checks_are_overflow_safe_at_bigint_boundary() -> None:
    sql = CONTRACT.event_chain_sql

    assert "previous_sequence + 1" not in sql
    assert "sequence > event_row.previous_sequence + 1" not in sql
    assert (
        "event_row.sequence::numeric -\n"
        "         event_row.previous_sequence::numeric) > 1"
    ) in sql
    assert (
        "event_row.sequence::numeric -\n"
        "           event_row.previous_sequence::numeric) IS DISTINCT FROM 1"
    ) in sql

    maximum = 2**63 - 1
    conceptual_cases = (
        (maximum, maximum, "duplicate"),
        (maximum, maximum - 1, "adjacent"),
        (maximum, maximum - 2, "gap"),
    )
    assert [
        "duplicate"
        if current == previous
        else "adjacent"
        if current - previous == 1
        else "gap"
        for current, previous, _expected in conceptual_cases
    ] == [expected for _current, _previous, expected in conceptual_cases]


def _insert_job(
    connection,
    job_id: str,
    *,
    state: str,
    max_attempts: int = 3,
    attempt_count: int = 0,
) -> None:
    connection.execute(
        """
        INSERT INTO public.jobs (
          job_id, job_type, state, payload, payload_fingerprint,
          idempotency_key, actor_type, actor_id, max_attempts, attempt_count
        ) VALUES (
          %s, 'SNAPSHOT', %s, '{}'::jsonb, %s, %s,
          'OPERATOR', 'event-chain-test', %s, %s
        )
        """,
        (
            job_id,
            state,
            "0" * 64,
            f"event-chain:{job_id}",
            max_attempts,
            attempt_count,
        ),
    )


def _insert_attempt(
    connection,
    job_id: str,
    attempt_id: str,
    attempt_number: int,
) -> None:
    connection.execute(
        """
        INSERT INTO public.job_attempts (
          attempt_id, job_id, attempt_number, worker_id, outcome,
          lease_token, lease_expires_at, claimed_at
        ) VALUES (
          %s, %s, %s, 'event-chain-worker', 'RUNNING',
          'test-lease-token-0001', now() + interval '1 hour', now()
        )
        """,
        (attempt_id, job_id, attempt_number),
    )


def _insert_event(
    connection,
    job_id: str,
    event_id: str,
    sequence: int,
    from_state: str | None,
    to_state: str,
    *,
    attempt_id: str | None = None,
    reason: str = "TEST_TRANSITION",
    actor: str = "WORKER",
    metadata: dict[str, object] | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO public.job_events (
          event_id, job_id, attempt_id, sequence, from_state, to_state,
          reason_code, actor_type, actor_id, trace_id, metadata
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s,
                  'event-chain-actor', %s, %s)
        """,
        (
            event_id,
            job_id,
            attempt_id,
            sequence,
            from_state,
            to_state,
            reason,
            actor,
            f"trace-{event_id}",
            Jsonb({} if metadata is None else metadata),
        ),
    )


def _codes(connection) -> set[str]:
    return {
        violation.code
        for violation in find_event_chain_violations(connection, CONTRACT)
    }


def _running_history(
    connection,
    job_id: str,
    *,
    final_state: str,
    max_attempts: int = 3,
) -> str:
    attempt_id = f"attempt-{job_id}-1"
    _insert_job(
        connection,
        job_id,
        state=final_state,
        max_attempts=max_attempts,
        attempt_count=1,
    )
    _insert_attempt(connection, job_id, attempt_id, 1)
    _insert_event(connection, job_id, f"event-{job_id}-1", 1, None, "QUEUED")
    _insert_event(
        connection,
        job_id,
        f"event-{job_id}-2",
        2,
        "QUEUED",
        "CLAIMED",
        attempt_id=attempt_id,
    )
    _insert_event(
        connection,
        job_id,
        f"event-{job_id}-3",
        3,
        "CLAIMED",
        "RUNNING",
        attempt_id=attempt_id,
    )
    return attempt_id


def test_reports_job_with_no_history(authority_database) -> None:
    with psycopg.connect(
        authority_database.conninfo(), autocommit=True
    ) as connection, connection.transaction(force_rollback=True):
        _insert_job(connection, "job-no-history", state="QUEUED")
        assert "NO_HISTORY" in _codes(connection)


@pytest.mark.parametrize(
    "case_id,expected_code",
    (
        ("sequence-start", "SEQUENCE_START"),
        ("sequence-gap", "SEQUENCE_GAP"),
        ("sequence-duplicate", "SEQUENCE_DUPLICATE"),
        ("bootstrap-edge", "BOOTSTRAP_EDGE"),
        ("bootstrap-attempt", "BOOTSTRAP_ATTEMPT"),
        ("later-null", "LATER_NULL_FROM_STATE"),
        ("disconnected", "DISCONNECTED_FROM_STATE"),
        ("unapproved-edge", "UNAPPROVED_EDGE"),
        ("final-mismatch", "FINAL_STATE_MISMATCH"),
        ("cross-job-attempt", "CROSS_JOB_ATTEMPT"),
    ),
)
def test_reports_every_non_retry_history_violation(
    authority_database,
    case_id: str,
    expected_code: str,
) -> None:
    job_id = f"job-{case_id}"
    with psycopg.connect(
        authority_database.conninfo(), autocommit=True
    ) as connection, connection.transaction(force_rollback=True):
        if case_id == "sequence-start":
            _insert_job(connection, job_id, state="QUEUED")
            _insert_event(connection, job_id, "event-start", 2, None, "QUEUED")
        elif case_id == "sequence-gap":
            _insert_job(connection, job_id, state="CANCELLED")
            _insert_event(connection, job_id, "event-gap-1", 1, None, "QUEUED")
            _insert_event(
                connection,
                job_id,
                "event-gap-3",
                3,
                "QUEUED",
                "CANCELLED",
            )
        elif case_id == "sequence-duplicate":
            connection.execute(
                "ALTER TABLE public.job_events DROP CONSTRAINT "
                "uq_job_events_job_sequence"
            )
            _insert_job(connection, job_id, state="CANCELLED")
            _insert_event(connection, job_id, "event-duplicate-1", 1, None, "QUEUED")
            _insert_event(
                connection,
                job_id,
                "event-duplicate-a",
                2,
                "QUEUED",
                "CLAIMED",
            )
            _insert_event(
                connection,
                job_id,
                "event-duplicate-b",
                2,
                "QUEUED",
                "CANCELLED",
            )
        elif case_id == "bootstrap-edge":
            _insert_job(connection, job_id, state="QUEUED")
            _insert_event(
                connection,
                job_id,
                "event-bootstrap-edge",
                1,
                "RUNNING",
                "QUEUED",
            )
        elif case_id == "bootstrap-attempt":
            _insert_job(connection, job_id, state="QUEUED", attempt_count=1)
            _insert_attempt(connection, job_id, "attempt-bootstrap", 1)
            _insert_event(
                connection,
                job_id,
                "event-bootstrap-attempt",
                1,
                None,
                "QUEUED",
                attempt_id="attempt-bootstrap",
            )
        elif case_id == "later-null":
            _insert_job(connection, job_id, state="CLAIMED")
            _insert_event(connection, job_id, "event-null-1", 1, None, "QUEUED")
            _insert_event(connection, job_id, "event-null-2", 2, None, "CLAIMED")
        elif case_id == "disconnected":
            _insert_job(connection, job_id, state="BLOCKED")
            _insert_event(
                connection, job_id, "event-disconnected-1", 1, None, "QUEUED"
            )
            _insert_event(
                connection,
                job_id,
                "event-disconnected-2",
                2,
                "RUNNING",
                "BLOCKED",
            )
        elif case_id == "unapproved-edge":
            _insert_job(connection, job_id, state="SUCCEEDED")
            _insert_event(
                connection, job_id, "event-unapproved-1", 1, None, "QUEUED"
            )
            _insert_event(
                connection,
                job_id,
                "event-unapproved-2",
                2,
                "QUEUED",
                "SUCCEEDED",
            )
        elif case_id == "final-mismatch":
            _insert_job(connection, job_id, state="QUEUED")
            _insert_event(
                connection, job_id, "event-mismatch-1", 1, None, "QUEUED"
            )
            _insert_event(
                connection,
                job_id,
                "event-mismatch-2",
                2,
                "QUEUED",
                "CANCELLED",
            )
        else:
            other_job = "job-cross-attempt-owner"
            _insert_job(connection, job_id, state="CLAIMED")
            _insert_job(connection, other_job, state="RUNNING", attempt_count=1)
            _insert_attempt(connection, other_job, "attempt-cross-job", 1)
            connection.execute(
                "ALTER TABLE public.job_events DROP CONSTRAINT "
                "fk_job_events_job_attempt"
            )
            _insert_event(connection, job_id, "event-cross-1", 1, None, "QUEUED")
            _insert_event(
                connection,
                job_id,
                "event-cross-2",
                2,
                "QUEUED",
                "CLAIMED",
                attempt_id="attempt-cross-job",
            )

        assert expected_code in _codes(connection)


@pytest.mark.parametrize(
    "case_id,expected_code",
    (
        ("wrong-actor", "RETRY_WRONG_ACTOR"),
        ("wrong-reason", "RETRY_WRONG_REASON"),
        ("different-attempt", "RETRY_ATTEMPT_CHANGED"),
        ("null-attempt", "RETRY_ATTEMPT_CHANGED"),
        ("over-budget", "RETRY_OVER_BUDGET"),
        ("not-adjacent", "RETRY_NOT_ADJACENT"),
        ("forged-attempt", "RETRY_FORGED_ATTEMPT"),
        ("changed-metadata", "RETRY_METADATA"),
    ),
)
def test_retry_rejects_actor_reason_attempt_budget_adjacency_and_metadata(
    authority_database,
    case_id: str,
    expected_code: str,
) -> None:
    job_id = f"job-retry-{case_id}"
    max_attempts = 1 if case_id == "over-budget" else 3
    with psycopg.connect(
        authority_database.conninfo(), autocommit=True
    ) as connection, connection.transaction(force_rollback=True):
        attempt_id = _running_history(
            connection,
            job_id,
            final_state="QUEUED",
            max_attempts=max_attempts,
        )
        _insert_event(
            connection,
            job_id,
            f"event-{job_id}-4",
            4,
            "RUNNING",
            "FAILED",
            attempt_id=attempt_id,
        )
        retry_attempt_id: str | None = attempt_id
        retry_reason = "PROCESS_RETRY_SCHEDULED"
        retry_actor = "WORKER"
        retry_sequence = 5
        retry_metadata: dict[str, object] = {}
        if case_id == "wrong-actor":
            retry_actor = "RECOVERY"
        elif case_id == "wrong-reason":
            retry_reason = "LEASE_EXPIRED_RETRY_SCHEDULED"
        elif case_id == "different-attempt":
            retry_attempt_id = f"attempt-{job_id}-2"
            _insert_attempt(connection, job_id, retry_attempt_id, 2)
        elif case_id == "null-attempt":
            retry_attempt_id = None
        elif case_id == "not-adjacent":
            retry_sequence = 6
        elif case_id == "forged-attempt":
            connection.execute(
                "ALTER TABLE public.job_events DROP CONSTRAINT "
                "fk_job_events_job_attempt"
            )
            retry_attempt_id = "attempt-forged"
        elif case_id == "changed-metadata":
            retry_metadata = {"forged": True}
        _insert_event(
            connection,
            job_id,
            f"event-{job_id}-retry",
            retry_sequence,
            "FAILED",
            "QUEUED",
            attempt_id=retry_attempt_id,
            reason=retry_reason,
            actor=retry_actor,
            metadata=retry_metadata,
        )

        assert expected_code in _codes(connection)


def test_event_after_terminal_without_retry_is_rejected(
    authority_database,
) -> None:
    job_id = "job-event-after-terminal"
    with psycopg.connect(
        authority_database.conninfo(), autocommit=True
    ) as connection, connection.transaction(force_rollback=True):
        attempt_id = _running_history(
            connection, job_id, final_state="BLOCKED"
        )
        _insert_event(
            connection,
            job_id,
            "event-terminal-failed",
            4,
            "RUNNING",
            "FAILED",
            attempt_id=attempt_id,
        )
        _insert_event(
            connection,
            job_id,
            "event-after-terminal",
            5,
            "FAILED",
            "BLOCKED",
            attempt_id=attempt_id,
        )

        assert "EVENT_AFTER_TERMINAL" in _codes(connection)


def test_duplicate_terminal_transition_within_epoch_is_rejected(
    authority_database,
) -> None:
    job_id = "job-duplicate-terminal"
    with psycopg.connect(
        authority_database.conninfo(), autocommit=True
    ) as connection, connection.transaction(force_rollback=True):
        attempt_id = _running_history(
            connection, job_id, final_state="BLOCKED"
        )
        _insert_event(
            connection,
            job_id,
            "event-terminal-first",
            4,
            "RUNNING",
            "FAILED",
            attempt_id=attempt_id,
        )
        _insert_event(
            connection,
            job_id,
            "event-terminal-second",
            5,
            "FAILED",
            "BLOCKED",
            attempt_id=attempt_id,
        )

        assert "DUPLICATE_TERMINAL_IN_EPOCH" in _codes(connection)


@pytest.mark.parametrize(
    "retry_from_state,authority_reason,authority_actor",
    (
        ("FAILED", "PROCESS_RETRY_SCHEDULED", "WORKER"),
        ("TIMED_OUT", "LEASE_EXPIRED_RETRY_SCHEDULED", "RECOVERY"),
    ),
    ids=("worker", "recovery"),
)
def test_valid_retry_epoch_allows_a_second_terminal_result(
    authority_database,
    retry_from_state: str,
    authority_reason: str,
    authority_actor: str,
) -> None:
    job_id = f"job-positive-{authority_actor.lower()}"
    attempt_1 = f"attempt-{job_id}-1"
    attempt_2 = f"attempt-{job_id}-2"
    with psycopg.connect(
        authority_database.conninfo(), autocommit=True
    ) as connection, connection.transaction(force_rollback=True):
        _insert_job(
            connection,
            job_id,
            state="SUCCEEDED",
            max_attempts=3,
            attempt_count=2,
        )
        _insert_attempt(connection, job_id, attempt_1, 1)
        _insert_attempt(connection, job_id, attempt_2, 2)
        events = (
            (1, None, "QUEUED", None, "ENQUEUED", "OPERATOR"),
            (2, "QUEUED", "CLAIMED", attempt_1, "CLAIMED", "WORKER"),
            (3, "CLAIMED", "RUNNING", attempt_1, "STARTED", "WORKER"),
            (
                4,
                "RUNNING",
                retry_from_state,
                attempt_1,
                "PROCESS_FAILED",
                authority_actor,
            ),
            (
                5,
                retry_from_state,
                "QUEUED",
                attempt_1,
                authority_reason,
                authority_actor,
            ),
            (6, "QUEUED", "CLAIMED", attempt_2, "CLAIMED", "WORKER"),
            (7, "CLAIMED", "RUNNING", attempt_2, "STARTED", "WORKER"),
            (8, "RUNNING", "SUCCEEDED", attempt_2, "PROCESS_SUCCEEDED", "WORKER"),
        )
        for sequence, source, target, attempt_id, reason, actor in events:
            _insert_event(
                connection,
                job_id,
                f"event-{job_id}-{sequence}",
                sequence,
                source,
                target,
                attempt_id=attempt_id,
                reason=reason,
                actor=actor,
            )

        assert find_event_chain_violations(connection, CONTRACT) == ()
