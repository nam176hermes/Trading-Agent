from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime

import pytest

from packages.job_contracts import ActorIdentity, JobState
from services.job_store import JobRepository


_NOW = datetime(2026, 7, 16, tzinfo=UTC)


class _Result:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _Transaction:
    def __init__(self):
        self.exit_error = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.exit_error = exc
        return False


class _CancellationConnection:
    def __init__(self, *, fail_event: bool = False):
        self.fail_event = fail_event
        self.statements: list[tuple[str, object]] = []
        self.transaction_context = _Transaction()
        self.row = {
            "job_id": "job_cancel_acl",
            "job_type": "SNAPSHOT",
            "state": "RUNNING",
            "payload": {"scope": "default", "requested_as_of": None},
            "payload_fingerprint": "f" * 64,
            "idempotency_key": "manual:snapshot:cancel-acl",
            "actor_type": "OPERATOR",
            "actor_id": "operator-origin",
            "priority": 0,
            "requested_at": _NOW,
            "updated_at": _NOW,
            "attempt_count": 1,
            "max_attempts": 2,
            "reason_code": "STARTED",
            "result_hash": None,
            "cancel_requested_at": None,
            "cancel_actor_type": None,
            "cancel_actor_id": None,
        }

    def transaction(self):
        return self.transaction_context

    def execute(self, statement, parameters=None):
        sql = str(statement)
        self.statements.append((sql, parameters))
        normalized = " ".join(sql.split())
        if "job_plane.api_cancel_snapshot" in normalized:
            if self.fail_event:
                raise RuntimeError("synthetic append-only event failure")
            self.row.update(
                state="CANCEL_REQUESTED",
                reason_code="CANCEL_REQUESTED",
                cancel_requested_at=_NOW,
                cancel_actor_type="OPERATOR",
                cancel_actor_id="operator-cancel",
            )
            return _Result(
                {
                    "job_id": self.row["job_id"],
                    "state": self.row["state"],
                    "changed": True,
                }
            )
        if normalized.startswith("SELECT") and "FROM jobs" in normalized:
            return _Result(dict(self.row))
        raise AssertionError(f"unexpected SQL shape: {normalized}")


class _Pool:
    def __init__(self, connection):
        self._connection = connection

    @contextmanager
    def connection(self):
        yield self._connection


def _repository(connection: _CancellationConnection) -> JobRepository:
    repository = object.__new__(JobRepository)
    repository._pool = _Pool(connection)
    return repository


def _cancel_actor() -> ActorIdentity:
    return ActorIdentity(actor_type="OPERATOR", actor_id="operator-cancel")


def test_cancel_uses_only_fixed_api_transition_capability() -> None:
    connection = _CancellationConnection()

    cancelled = _repository(connection).request_cancel(
        "job_cancel_acl", _cancel_actor(), "trace-cancel-acl"
    )

    capability_calls = [
        (sql, parameters)
        for sql, parameters in connection.statements
        if "job_plane.api_cancel_snapshot" in sql
    ]
    assert len(capability_calls) == 1
    _, parameters = capability_calls[0]
    assert parameters[:3] == (
        "job_cancel_acl",
        "operator-cancel",
        "trace-cancel-acl",
    )
    assert parameters[3].startswith("event_")
    assert all("UPDATE jobs" not in sql for sql, _ in connection.statements)
    assert all("INSERT INTO job_events" not in sql for sql, _ in connection.statements)
    assert cancelled.state is JobState.CANCEL_REQUESTED
    assert cancelled.cancel_actor == _cancel_actor()
    assert connection.transaction_context.exit_error is None


def test_cancel_event_failure_escapes_the_same_transaction_for_rollback() -> None:
    connection = _CancellationConnection(fail_event=True)

    with pytest.raises(RuntimeError, match="append-only event failure"):
        _repository(connection).request_cancel(
            "job_cancel_acl", _cancel_actor(), "trace-cancel-rollback"
        )

    assert isinstance(connection.transaction_context.exit_error, RuntimeError)
