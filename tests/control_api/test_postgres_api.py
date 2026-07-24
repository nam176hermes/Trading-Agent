from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from control_api.app import create_app
from control_api.config import Settings
from services.job_worker.errors import SafetyBlockedError
from services.job_worker.safety import KillSwitchState, SafetyMode, SafetySnapshot
from trading_control.db import DatabaseUnavailable, connect
from tests.control_api._disposable_runtime import (
    DisposableRuntimeFixture,
    build_disposable_runtime_fixture,
    require_disposable_green,
)
from tests.jobs._postgres import disposable_database


OPERATION_ID = "control-api-postgres-api-green-v1"
pytestmark = pytest.mark.runtime_postgres


@pytest.fixture(scope="module")
def runtime_fixture(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[DisposableRuntimeFixture]:
    require_disposable_green()
    data_root = tmp_path_factory.mktemp("control-api-postgres-api")
    with disposable_database(operation_id=OPERATION_ID, planned=True) as owner:
        yield build_disposable_runtime_fixture(owner, data_root)


def current_safety() -> SafetySnapshot:
    return SafetySnapshot(
        requested_mode=SafetyMode.PAPER,
        effective_mode=SafetyMode.PAPER,
        live_execution_enabled=False,
        live_trading_approved=False,
        kill_switch_state=KillSwitchState.INACTIVE,
    )


def test_postgres_backed_api_smoke_is_read_only_and_contract_compatible(
    runtime_fixture: DisposableRuntimeFixture,
) -> None:
    env = runtime_fixture.env
    db = runtime_fixture.reader
    settings = Settings.from_env(env)
    with connect(db, read_only=True) as connection:
        before = connection.execute(
            "SELECT (SELECT count(*) FROM migration_runs),"
            "(SELECT count(*) FROM audit_events),"
            "(SELECT count(*) FROM decisions)"
        ).fetchone()
    with TestClient(create_app(settings, env=env, safety_provider=current_safety)) as client:
        for route in (
            "/health/live",
            "/health/ready",
            "/v1/meta",
            "/v1/system/status",
            "/v1/market/latest",
            "/v1/signals",
            "/v1/decisions",
            "/v1/capabilities",
            "/v1/costs",
        ):
            response = client.get(route)
            assert response.status_code == 200, (route, response.text)
        decisions = client.get("/v1/decisions").json()["data"]
        assert decisions["total"] == runtime_fixture.decision_count
        detail = client.get(f"/v1/decisions/{decisions['items'][0]['decision_id']}")
        assert detail.status_code == 200
        market = client.get("/v1/market/latest").json()
        assert market["freshness"]["status"] == "STALE"
        assert len(market["data"]["report"]["assets"]) == 1
        status = client.get("/v1/system/status").json()["data"]
        assert status["database_status"] == "AVAILABLE"
        assert status["requested_mode"] == "PAPER"
        assert status["effective_mode"] == "PAPER"
        assert status["execution_capability"] == "NON_LIVE"
        assert status["kill_switch_state"] == "INACTIVE"
        assert status["orders_count"] is None
        assert status["trades_count"] is None
        capabilities = client.get("/v1/capabilities").json()["data"]
        assert capabilities["total"] == runtime_fixture.capability_count
        assert capabilities["verified"] == 0
        costs = client.get("/v1/costs").json()["data"]
        assert costs["total_sessions"] == runtime_fixture.cost_session_count
        assert client.post("/v1/decisions").status_code == 405
        assert client.put("/v1/decisions").status_code == 405
    with connect(db, read_only=True) as connection:
        after = connection.execute(
            "SELECT (SELECT count(*) FROM migration_runs),"
            "(SELECT count(*) FROM audit_events),"
            "(SELECT count(*) FROM decisions)"
        ).fetchone()
    assert after == before


def test_postgres_mode_unavailable_fails_readiness_without_legacy_fallback(
    runtime_fixture: DisposableRuntimeFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(*_args: object, **_kwargs: object) -> None:
        raise DatabaseUnavailable("synthetic disposable database outage")

    monkeypatch.setattr("control_api.repositories.status.connect", unavailable)
    monkeypatch.setattr("control_api.repositories.decisions.connect", unavailable)
    env = runtime_fixture.env
    with TestClient(
        create_app(Settings.from_env(env), env=env, safety_provider=current_safety),
        raise_server_exceptions=False,
    ) as client:
        assert client.get("/health/live").status_code == 200
        ready = client.get("/health/ready")
        assert ready.status_code == 503
        assert ready.json()["data"]["status"] == "NOT_READY"
        assert client.get("/v1/decisions").status_code == 500


def test_postgres_status_fails_closed_when_current_safety_evidence_is_unavailable(
    runtime_fixture: DisposableRuntimeFixture,
) -> None:
    env = runtime_fixture.env

    def unavailable_safety() -> SafetySnapshot:
        raise SafetyBlockedError("SAFETY_STATE_STALE", "current safety state unavailable")

    try:
        with TestClient(
            create_app(
                Settings.from_env(env),
                env=env,
                safety_provider=unavailable_safety,
            )
        ) as client:
            status = client.get("/v1/system/status")
    except DatabaseUnavailable as error:
        pytest.fail(str(error), pytrace=False)

    assert status.status_code == 200
    assert status.json()["data"]["requested_mode"] == "PAPER"
    assert status.json()["data"]["effective_mode"] == "PAPER"
    assert status.json()["data"]["execution_capability"] == "NON_LIVE"
    assert status.json()["data"]["kill_switch_state"] == "UNKNOWN"
