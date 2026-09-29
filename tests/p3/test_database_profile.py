import pytest

from services.job_store.config import (
    CANONICAL_DATABASE_REVISION,
    P1_DISPOSABLE_DATABASE_REVISION,
    P3_DISPOSABLE_DATABASE_REVISION,
)
from services.job_store.worker_repository import WorkerRepository


def test_database_revisions_remain_profile_specific() -> None:
    assert CANONICAL_DATABASE_REVISION == "0011_engine_backtest_worker_authority"
    assert P1_DISPOSABLE_DATABASE_REVISION == "0033_p3_core_closure_repair"
    assert P3_DISPOSABLE_DATABASE_REVISION == "0026_p3_holdout_disclosure"


def test_p3_runtime_identity_is_an_explicit_worker_capability() -> None:
    repository = object.__new__(WorkerRepository)
    observed = []
    repository._assert_database_identity = lambda user, revision: observed.append(  # type: ignore[method-assign]
        (user, revision)
    )
    repository.assert_p3_runtime_identity()
    assert observed == [("trading_job_worker", P3_DISPOSABLE_DATABASE_REVISION)]


class _Row:
    def __init__(self, revision: str) -> None:
        self.revision = revision

    def fetchone(self) -> dict[str, str]:
        return {"current_user": "trading_job_worker", "version_num": self.revision}


class _Pool:
    def __init__(self, revision: str) -> None:
        self.revision = revision

    def connection(self):
        pool = self

        class _Connection:
            def __enter__(self):
                return self

            def __exit__(self, *_exc) -> None:
                return None

            def execute(self, _sql):
                return _Row(pool.revision)

        return _Connection()


@pytest.mark.parametrize(("revision", "admitted"), [
    ("0033_p3_core_closure_repair", True),       # the repair head: recovery fixed, C_new pinned in SQL
    ("0018_p1_paper_closure_rotation", False),   # the old P1 head pins the prior closure
    ("0032_p3_enqueue_freshness", False),
    ("0019_p2_security_master", False),
])
def test_p1_worker_identity_admits_only_the_repair_head(revision: str, admitted: bool) -> None:
    repository = object.__new__(WorkerRepository)
    repository._pool = _Pool(revision)
    if admitted:
        repository.assert_p1_disposable_runtime_identity()
    else:
        with pytest.raises(RuntimeError, match="worker database authority is unavailable"):
            repository.assert_p1_disposable_runtime_identity()
