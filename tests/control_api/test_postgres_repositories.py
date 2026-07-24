from __future__ import annotations

from collections.abc import Iterator

import pytest

from control_api.contracts import CapabilityStatus, FreshnessStatus
from control_api.repositories.capabilities import PostgresCapabilityRepository
from control_api.repositories.costs import PostgresCostRepository
from control_api.repositories.decisions import PostgresDecisionRepository
from control_api.repositories.market import PostgresMarketReportRepository
from tests.control_api._disposable_runtime import (
    FIXTURE_NOW,
    DisposableRuntimeFixture,
    build_disposable_runtime_fixture,
    require_disposable_green,
)
from tests.jobs._postgres import disposable_database


OPERATION_ID = "control-api-postgres-repositories-green-v1"
pytestmark = pytest.mark.runtime_postgres


@pytest.fixture(scope="module")
def runtime_fixture(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[DisposableRuntimeFixture]:
    require_disposable_green()
    data_root = tmp_path_factory.mktemp("control-api-postgres-repositories")
    with disposable_database(operation_id=OPERATION_ID, planned=True) as owner:
        yield build_disposable_runtime_fixture(owner, data_root)


def test_postgres_repositories_read_disposable_canonical_store(
    runtime_fixture: DisposableRuntimeFixture,
) -> None:
    settings = runtime_fixture.reader
    market = PostgresMarketReportRepository(
        settings,
        stale_after_seconds=1800,
        clock=lambda: FIXTURE_NOW,
    ).latest()
    decisions = PostgresDecisionRepository(settings)
    capabilities = PostgresCapabilityRepository(settings).list()
    costs = PostgresCostRepository(settings).get()

    assert market.report is not None
    assert market.report.as_of.isoformat() == "2026-07-01T00:00:00+00:00"
    assert len(market.report.assets) == 1
    assert market.report.invalid_source_count == 0
    assert market.freshness.status is FreshnessStatus.STALE
    first = decisions.list(page=1, page_size=2)
    last = decisions.list(page=2, page_size=2)
    assert first.total == runtime_fixture.decision_count == 3
    assert len(first.items) == 2
    assert len(last.items) == 1
    assert decisions.get(first.items[0].decision_id) == first.items[0]
    assert len(capabilities) == runtime_fixture.capability_count == 9
    assert all(item.status is CapabilityStatus.UNKNOWN for item in capabilities)
    assert costs.total_sessions == runtime_fixture.cost_session_count == 1
    assert all(item.symbols == sorted(set(item.symbols)) for item in costs.sessions)
    assert all(item.symbols == [] for item in costs.sessions)
