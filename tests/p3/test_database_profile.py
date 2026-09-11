from services.job_store.config import (
    CANONICAL_DATABASE_REVISION,
    P1_DISPOSABLE_DATABASE_REVISION,
    P3_DISPOSABLE_DATABASE_REVISION,
)
from services.job_store.worker_repository import WorkerRepository


def test_database_revisions_remain_profile_specific() -> None:
    assert CANONICAL_DATABASE_REVISION == "0011_engine_backtest_worker_authority"
    assert P1_DISPOSABLE_DATABASE_REVISION == "0018_p1_paper_closure_rotation"
    assert P3_DISPOSABLE_DATABASE_REVISION == "0023_p3_output_custody"


def test_p3_runtime_identity_is_an_explicit_worker_capability() -> None:
    repository = object.__new__(WorkerRepository)
    observed = []
    repository._assert_database_identity = lambda user, revision: observed.append(  # type: ignore[method-assign]
        (user, revision)
    )
    repository.assert_p3_runtime_identity()
    assert observed == [("trading_job_worker", P3_DISPOSABLE_DATABASE_REVISION)]
