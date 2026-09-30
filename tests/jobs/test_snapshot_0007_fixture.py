from pathlib import Path
import hashlib

import pytest


def test_catalog_fixture_retains_both_accepted_pins_and_rejects_mutation(monkeypatch):
    from tests.jobs._snapshot0007 import reviewed_disposable_catalog_snapshot

    for revision, digest in (
        ("0006", "69b6886986b9a6e9d6ab824663b4acc2259a41f0e76b1217f15364e13302911f"),
        ("0007", "606963c54fbe6cedd25fc166c7b13bcd3a97effa9c83fde2de7c2a567b9359f6"),
    ):
        assert hashlib.sha256(reviewed_disposable_catalog_snapshot(revision)).hexdigest() == digest
    original = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda path: original(path) + b" ")
    with pytest.raises(ValueError, match="reviewed source catalog pin"):
        reviewed_disposable_catalog_snapshot("0007")


def test_snapshot_0007_adapter_only_dispatches_its_historical_capabilities():
    from tests.jobs._snapshot0007 import Snapshot0007WorkerRepository

    statement = "SELECT job_plane.worker_start_paper(%s) AS started"
    assert Snapshot0007WorkerRepository._session_statement(statement, ()) == (
        "SELECT job_plane.worker_start_snapshot(%s) AS started"
    )
    for sql, parameters in ((statement, (1, 2)), ("SELECT job_plane.unreviewed(%s)", ())):
        with pytest.raises(ValueError, match="0007 snapshot"):
            Snapshot0007WorkerRepository._session_statement(sql, parameters)


def test_restore_drop_targets_only_the_disposable_database(monkeypatch):
    from tests.jobs import test_job_transition_restore as restore
    from tests.jobs._postgres import DISPOSABLE_DATABASE
    from trading_control.db import DatabaseSettings

    statements = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, statement, parameters=None):
            from types import SimpleNamespace
            statements.append((statement, parameters))
            result = ("postgres",) if statement == "SELECT current_database()" else (True,)
            return SimpleNamespace(fetchone=lambda: result)

    monkeypatch.setattr(restore.psycopg, "connect", lambda *args, **kwargs: Connection())
    owner = DatabaseSettings(host="127.0.0.1", port=55501, database="postgres",
        user="trading_owner", password="synthetic-only")
    restore._drop_only_target_database(owner)
    assert (f"DROP DATABASE {DISPOSABLE_DATABASE}", None) in statements
    assert not any(statement == "DROP DATABASE trading_agent" for statement, _ in statements)


def test_restore_acl_scope_keeps_every_selected_object_and_rejects_unknown_database():
    from tests.jobs import test_job_transition_restore as restore
    from tests.jobs._postgres import DISPOSABLE_DATABASE

    rows = [(kind, DISPOSABLE_DATABASE if kind == "DATABASE" else name,
             "trading_owner", "trading_owner", "CONNECT", False)
            for kind, name in restore.SELECTED_ACL_OBJECTS]
    assert len(restore._canonical_effective_acl_rows(rows, database_name=DISPOSABLE_DATABASE)) == len(rows)
    with pytest.raises(AssertionError, match=restore.RESTORE_PROOF_EFFECTIVE_DATABASE_ACL_SELECTED_OBJECTS_MISMATCH):
        restore._canonical_effective_acl_rows(rows, database_name="unknown_database")
    with pytest.raises(AssertionError, match=restore.RESTORE_PROOF_EFFECTIVE_DATABASE_ACL_SELECTED_OBJECTS_MISMATCH):
        restore._canonical_effective_acl_rows(rows[:-1], database_name=DISPOSABLE_DATABASE)
