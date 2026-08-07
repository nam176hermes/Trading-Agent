from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
import pytest

from packages.engine_contracts import canonical_json_bytes
from services.job_store.engine_event_repository import (
    PostgresEngineEventLedger,
    _validated_records,
)
from tests.control_api._disposable_runtime import require_disposable_green
from tests.jobs._postgres import disposable_database, upgrade_to_head
from tests.jobs.test_engine_event_ledger import _batch, _event


OPERATION_ID = "engine-event-ingestion-concurrency-runtime-green-v1"
RECEIPT_FIELDS = (
    "batch_sha256",
    "ingestion_digest",
    "job_id",
    "attempt_id",
    "engine_run_id",
    "event_count",
    "first_sequence",
    "last_sequence",
    "last_digest",
)
pytestmark = pytest.mark.runtime_postgres


@pytest.fixture(scope="module")
def engine_event_database():
    require_disposable_green()
    with disposable_database(operation_id=OPERATION_ID, planned=True) as owner:
        upgrade_to_head(owner)
        yield owner


def _semantic_receipt_bytes(receipt: dict[str, object]) -> bytes:
    return canonical_json_bytes(
        {
            field: str(receipt[field])
            if isinstance(receipt[field], UUID)
            else receipt[field]
            for field in RECEIPT_FIELDS
        }
    )


def test_concurrent_identical_engine_batch_is_idempotent_and_serialized(
    engine_event_database,
) -> None:
    batch = _batch(
        _event(2, "BacktestStarted"),
        _event(3, "BacktestCompleted"),
    )
    records, ingestion_digest = _validated_records(batch)
    batch_document = PostgresEngineEventLedger._batch_document(
        batch,
        records,
        ingestion_digest,
    )
    barrier = Barrier(2)

    def ingest() -> dict[str, object]:
        with psycopg.connect(
            engine_event_database.conninfo(),
            autocommit=True,
            row_factory=dict_row,
        ) as connection:
            barrier.wait(timeout=10)
            receipt = connection.execute(
                "SELECT * FROM public.ingest_engine_event_batch(%s)",
                (batch_document,),
            ).fetchone()
        assert receipt is not None
        return receipt

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = tuple(executor.submit(ingest) for _ in range(2))
        receipts = tuple(future.result() for future in futures)

    assert _semantic_receipt_bytes(receipts[0]) == _semantic_receipt_bytes(
        receipts[1]
    )
    with psycopg.connect(engine_event_database.conninfo()) as connection:
        assert connection.execute(
            "SELECT count(*) FROM public.engine_event_batch_receipts",
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT count(*) FROM public.engine_events",
        ).fetchone() == (len(batch.events),)
        assert connection.execute(
            "SELECT count(*) FROM public.engine_run_projections",
        ).fetchone() == (1,)
