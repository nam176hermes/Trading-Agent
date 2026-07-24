from __future__ import annotations

from collections.abc import Iterator

import pytest

from control_api.contracts import DecisionAction
from control_api.repositories.capabilities import (
    LegacyCapabilityRepository,
    PostgresCapabilityRepository,
)
from control_api.repositories.costs import LegacyCostRepository, PostgresCostRepository
from control_api.repositories.decisions import (
    LegacyDecisionRepository,
    PostgresDecisionRepository,
)
from control_api.repositories.market import (
    LegacyMarketReportRepository,
    PostgresMarketReportRepository,
)
from tests.control_api._disposable_runtime import (
    FIXTURE_NOW,
    DisposableRuntimeFixture,
    build_disposable_runtime_fixture,
    require_disposable_green,
)
from tests.jobs._postgres import disposable_database


OPERATION_ID = "control-api-dual-read-green-v1"
pytestmark = pytest.mark.runtime_postgres


@pytest.fixture(scope="module")
def runtime_fixture(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[DisposableRuntimeFixture]:
    require_disposable_green()
    data_root = tmp_path_factory.mktemp("control-api-dual-read")
    with disposable_database(operation_id=OPERATION_ID, planned=True) as owner:
        yield build_disposable_runtime_fixture(owner, data_root)


def test_legacy_postgres_dual_read_preserves_canonical_query_semantics(
    runtime_fixture: DisposableRuntimeFixture,
) -> None:
    root = runtime_fixture.data_root
    settings = runtime_fixture.reader
    legacy_market = LegacyMarketReportRepository(
        root,
        stale_after_seconds=1800,
        clock=lambda: FIXTURE_NOW,
    )
    postgres_market = PostgresMarketReportRepository(
        settings,
        stale_after_seconds=1800,
        clock=lambda: FIXTURE_NOW,
    )
    legacy_decisions = LegacyDecisionRepository(root)
    postgres_decisions = PostgresDecisionRepository(settings)

    assert legacy_market.latest() == postgres_market.latest()
    legacy_first = legacy_decisions.list(page=1, page_size=2)
    postgres_first = postgres_decisions.list(page=1, page_size=2)
    assert legacy_first == postgres_first
    assert legacy_first.total == runtime_fixture.decision_count
    for action in DecisionAction:
        legacy_page = legacy_decisions.list(page=1, page_size=1, action=action)
        postgres_page = postgres_decisions.list(page=1, page_size=1, action=action)
        assert legacy_page == postgres_page

    assert LegacyCapabilityRepository(root).list() == PostgresCapabilityRepository(
        settings
    ).list()
    assert LegacyCostRepository(root).get() == PostgresCostRepository(settings).get()
