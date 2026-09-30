"""Offline isolation checks: synthetic credentials, no connection opened."""
from importlib import import_module

import pytest

from tests.jobs._postgres import disposable_role_settings
from tests.jobs import _postgres as harness
from scripts import check_test_governance as governance
from trading_control.db import DatabaseSettings


@pytest.mark.parametrize("module_name", [
    "tests.control_api.test_fixture_importer",
    "tests.control_api.test_phase3b_transactions",
    "tests.control_api.test_real_data_apply",
])
def test_control_api_roles_inherit_the_whole_disposable_owner(
    module_name, tmp_path, monkeypatch,
) -> None:
    home = tmp_path / "synthetic-home"
    config = home / ".config/trading-agent"
    config.mkdir(parents=True)
    # Deliberately hostile endpoint; these bytes never go to a driver or socket.
    (config / "postgres-migrator.env").write_text(
        "TRADING_DATABASE_HOST=127.0.0.1\nTRADING_DATABASE_PORT=55432\n"
        "TRADING_DATABASE_NAME=operator_fake\nTRADING_DATABASE_USER=trading_migrator\n"
        "TRADING_DATABASE_PASSWORD=synthetic-credential-only\n"
    )
    monkeypatch.setenv("HOME", str(home))
    owner = DatabaseSettings(host="127.0.0.1", port=55501,
        database="trading_agent_disposable_test", user="trading_owner",
        password="synthetic-owner-only")
    module = import_module(module_name)
    monkeypatch.setattr(module, "_TEST_DATABASE_NAME", owner.database, raising=False)
    monkeypatch.setattr(module, "_TEST_DATABASE", owner, raising=False)
    assert module.role_settings("postgres-migrator.env") == disposable_role_settings(
        owner, "trading_migrator",
    )


def test_lane_pytest_has_an_empty_private_home_and_explicit_uv_cache(
    tmp_path, monkeypatch,
) -> None:
    monkeypatch.setenv("HOME", "/synthetic/operator-home")
    monkeypatch.setenv("XDG_CONFIG_HOME", "/synthetic/operator-config")
    monkeypatch.setenv("TRADING_DATABASE_PASSWORD", "synthetic-only")
    monkeypatch.setenv("PGSERVICE", "synthetic-operator-service")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", "/synthetic/operator-credentials")
    monkeypatch.setattr(governance, "_candidate_source", lambda: {})
    monkeypatch.setattr(governance, "_disposable_cluster_roots", lambda: set())
    monkeypatch.setattr(governance, "_approval_binding", lambda _: ("DISPOSABLE_PG_GREEN", None))
    monkeypatch.setattr(governance, "_read_json", lambda _: {})
    monkeypatch.setattr(governance, "_postgres_lane_problems", lambda *args: ([], []))
    homes = []

    def invocation(command, *, cwd, env, log_path):
        from pathlib import Path
        import os
        home = Path(env["HOME"])
        homes.append(home)
        assert home != Path("/synthetic/operator-home")
        assert home.is_dir() and not list(home.iterdir())
        assert home.stat().st_mode & 0o777 == 0o700
        assert home.stat().st_uid == os.getuid()
        assert Path(env["UV_CACHE_DIR"]).is_dir()
        assert env.get("XDG_CONFIG_HOME") is None
        assert env.get("CREDENTIALS_DIRECTORY") is None
        assert not any(name.startswith(("TRADING_DATABASE_", "PG")) for name in env)
        return 0

    monkeypatch.setattr(governance, "_run", invocation)
    governance.run_postgres_lanes(tmp_path / "reports",
        {"lanes": [{"lane_id": "green", "scope": "DISPOSABLE_PG_GREEN"}], "nodes": []},
        {"green": {"TRADING_TEST_DISPOSABLE_APPROVAL_RECORD": "/synthetic/record"}}, source={})
    assert homes and all(not home.exists() for home in homes)


@pytest.mark.parametrize("host,port", [
    ("127.0.0.1", "5432"), ("127.0.0.1", "55432"),
    ("localhost", "55501"), ("203.0.113.1", "55501"),
    ("127.0.0.1", "55502"), ("127.0.0.1,203.0.113.1", "55501"),
])
def test_driver_fence_rejects_unapproved_endpoint_before_driver(
    host, port, monkeypatch,
) -> None:
    import psycopg
    from psycopg.conninfo import make_conninfo
    calls = []
    monkeypatch.setattr(psycopg.Connection, "connect",
        classmethod(lambda cls, *args, **kwargs: calls.append(True)))
    # The installer and active endpoint registry are the actual lane seam.
    harness.install_disposable_connection_fence(monkeypatch)
    monkeypatch.setattr(harness, "_ACTIVE_DISPOSABLE_ENDPOINTS", {("127.0.0.1", 55501)})
    with pytest.raises(harness.DisposablePostgresApprovalRejected, match="active approved slot"):
        psycopg.connect(make_conninfo(host=host, port=port, password="synthetic-only"))
    assert not calls


def test_driver_fence_preserves_the_valid_endpoint_and_leader_credentials(monkeypatch) -> None:
    import psycopg
    from psycopg.conninfo import make_conninfo
    sentinel = object()
    calls = []

    def driver(cls, conninfo="", **kwargs):
        calls.append((conninfo, kwargs))
        return sentinel

    monkeypatch.setattr(psycopg.Connection, "connect", classmethod(driver))
    harness.install_disposable_connection_fence(monkeypatch)
    monkeypatch.setattr(harness, "_ACTIVE_DISPOSABLE_ENDPOINTS", {("127.0.0.1", 55501)})
    dsn = make_conninfo(host="127.0.0.1", port=55501, password="synthetic-only")
    assert psycopg.connect(dsn, autocommit=True) is sentinel
    assert calls == [(dsn, {"autocommit": True})]


def test_real_import_fixture_requires_disposable_owner_and_explicit_apply(monkeypatch):
    from trading_control import real_import
    from trading_control.writer import ApplyRejected
    calls = []
    owner = DatabaseSettings(host="127.0.0.1", port=55501,
        database="trading_agent_disposable_test", user="trading_owner", password="synthetic-only")
    monkeypatch.setattr(real_import, "_apply_real_plan", lambda *args, **kwargs: calls.append(True), raising=False)
    with pytest.raises(ApplyRejected, match="explicit apply"):
        harness.apply_disposable_real_plan(object(), owner, apply=False, code_commit="test")
    with pytest.raises(harness.DisposablePostgresApprovalRejected, match="active approved slot"):
        harness.apply_disposable_real_plan(object(), owner, apply=True, code_commit="test")
    assert not calls


def test_production_real_import_guard_still_rejects_disposable_port(monkeypatch):
    from trading_control.real_import import apply_real_plan
    from trading_control.writer import ApplyRejected
    import psycopg
    calls = []
    monkeypatch.setattr(psycopg, "connect", lambda *args, **kwargs: calls.append(True))
    role = DatabaseSettings(host="127.0.0.1", port=55501,
        database="trading_agent_disposable_test", user="trading_migrator", password="synthetic-only")
    with pytest.raises(ApplyRejected, match="localhost migrator role"):
        apply_real_plan(object(), role, apply=True, code_commit="test")
    assert not calls
