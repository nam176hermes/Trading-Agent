"""Step 2: every head consumer moves from 0032 to the 0033 repair, pinned to the catalog SV01 GREEN observed."""

from __future__ import annotations

import pytest

REPAIR_REVISION = "0033_p3_core_closure_repair"
# Observed at 0033 in SV01 GREEN issue 5 (ops 1-6, each on a fresh database); accepted by Codex.
REPAIR_SESSION_CATALOG_SHA256 = "894218b307c2c7748db0bd4183fa9528e04a735f1fad98e58066b26ab2432969"
LEGACY_REVISION = "0030_p3_session_terminal_fences"
LEGACY_CATALOG_SHA256 = "506071c06cdd91fe7e9506a03c15df10949e41f97f80dbbdd27c305aef3a82d4"


def test_session_admission_pins_only_the_repair_head_and_the_legacy_revision() -> None:
    from services.job_store import p3_catalog

    assert (p3_catalog.SESSION_REVISION, p3_catalog.SESSION_CATALOG_SHA256) == (
        REPAIR_REVISION, REPAIR_SESSION_CATALOG_SHA256)
    assert p3_catalog.SESSION_CATALOGS == {
        LEGACY_REVISION: LEGACY_CATALOG_SHA256, REPAIR_REVISION: REPAIR_SESSION_CATALOG_SHA256,
    }                                                         # 0032 lacks the recovery repair and the C_new pin


class _Result:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _Connection:
    def __init__(self, revision: str) -> None:
        self.revision, self.reader_calls = revision, 0

    def execute(self, sql, parameters=None):
        if "version_num" in sql:
            return _Result({"version_num": self.revision})
        self.reader_calls += 1
        return _Result(None)


@pytest.mark.parametrize("revision", ["0031_p3_local_identity", "0032_p3_enqueue_freshness"])
def test_enqueue_refuses_a_pre_repair_catalog_before_reading_authority(revision: str) -> None:
    from services.job_store.errors import JobStoreError
    from services.job_store.p3_enqueue import read_enqueue_authority

    connection = _Connection(revision)
    with pytest.raises(JobStoreError, match="catalog is unavailable"):
        read_enqueue_authority(connection, object())
    assert connection.reader_calls == 0


def test_enqueue_reads_authority_only_at_the_repair_head() -> None:
    from services.job_store.errors import JobStoreError
    from services.job_store.p3_enqueue import read_enqueue_authority

    connection = _Connection(REPAIR_REVISION)
    with pytest.raises(JobStoreError, match="LOCAL enqueue authority is unavailable"):   # reader row absent
        read_enqueue_authority(connection, {"logical_trial_id": "x"})
    assert connection.reader_calls == 1


def test_local_fixture_plan_and_sql_fixture_target_the_repair_head() -> None:
    from services.job_worker import p3_local_sql_fixture
    from services.job_worker.p3_integration import LocalFixturePlan

    assert p3_local_sql_fixture.REVISION == REPAIR_REVISION
    annotation = LocalFixturePlan.model_fields["sql_revision"].annotation
    assert annotation.__args__ == (REPAIR_REVISION,)
