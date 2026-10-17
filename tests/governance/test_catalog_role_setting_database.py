"""Catalog adversarial statements use the owner database, including quoted names."""
from contextlib import contextmanager
from types import SimpleNamespace

from psycopg import sql
import pytest

from tests.jobs import test_job_authority_catalog as catalog


@pytest.mark.parametrize("database", ["trading_agent_disposable_test", 'owned";SELECT 1;--'])
@pytest.mark.parametrize("case", ["safe", "unknown"])
def test_role_setting_mutations_use_the_owner_database(monkeypatch, database, case):
    statements = []
    @contextmanager
    def transaction(*, force_rollback):
        assert force_rollback is True
        yield
    connection = SimpleNamespace(
        execute=lambda statement: statements.append(
            statement.as_string() if isinstance(statement, sql.Composable) else statement
        ),
        transaction=transaction,
    )
    @contextmanager
    def connect(conninfo, *, autocommit):
        assert conninfo == "synthetic-owner" and autocommit is True
        yield connection
    def capture(*args):
        if case == "unknown":
            raise catalog.UnsafeCatalogSettingError("unknown role setting key: application_name")
        return SimpleNamespace(sha256=str(len(statements)), canonical_bytes=bytes(len(statements)))
    monkeypatch.setattr(catalog.psycopg, "connect", connect)
    monkeypatch.setattr(catalog, "capture_catalog", capture)
    owner = SimpleNamespace(database=database, conninfo=lambda: "synthetic-owner")
    if case == "safe":
        catalog.test_catalog_digest_changes_for_every_adversarial_catalog_drift(
            owner, "safe-role-setting-drift",
            ("ALTER ROLE trading_owner IN DATABASE trading_agent SET timezone = 'Etc/GMT+1'",),
        )
    else:
        catalog.test_unknown_role_setting_returns_only_key_and_never_secret_value(owner)
    assert len(statements) == 1
    assert "IN DATABASE " + sql.Identifier(database).as_string() + " SET " in statements[0]
