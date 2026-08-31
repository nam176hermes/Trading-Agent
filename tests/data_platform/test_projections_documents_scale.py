from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.data_documents import (
    DocumentRegistry,
    LocalPageIndex,
    RetrievalCaseV1,
    benchmark_retrieval,
)
from packages.data_projections import ProjectionError, project_qlib_csv
from packages.data_scale import IcebergScaleProfileV1, evaluate_iceberg_gate


T0 = datetime(2026, 1, 1, tzinfo=UTC)


def test_qlib_projection_is_deterministic_and_uses_canonical_names() -> None:
    rows = (
        {
            "instrument": "BTC-USD",
            "ts_event": T0,
            "open": "100.0",
            "high": "102.0",
            "low": "99.0",
            "close": "101.0",
            "volume": "2.0",
        },
        {
            "instrument": "BTC-USD",
            "ts_event": T0 + timedelta(days=1),
            "open": "101.0",
            "high": "103.0",
            "low": "100.0",
            "close": "102.0",
            "volume": "3.0",
        },
    )

    first = project_qlib_csv(rows)
    second = project_qlib_csv(tuple(reversed(rows)))

    assert first == second
    assert first.calendar == b"2026-01-01\n2026-01-02\n"
    assert first.instruments == b"BTC-USD\t2026-01-01\t2026-01-02\n"
    assert first.features.startswith(b"instrument,date,$open,$high,$low,$close,$volume\n")


def test_qlib_projection_rejects_non_numeric_features() -> None:
    row = {
        "instrument": "BTC-USD",
        "ts_event": T0,
        "open": "=1+1",
        "high": "102.0",
        "low": "99.0",
        "close": "101.0",
        "volume": "2.0",
    }

    with pytest.raises(ProjectionError, match="finite decimal"):
        project_qlib_csv((row,))


def test_document_registry_is_pit_safe_and_local_page_index_is_deterministic(
    tmp_path: Path,
) -> None:
    registry = DocumentRegistry(LocalArtifactStore(tmp_path))
    registry.register(
        document_id=UUID("60000000-0000-4000-8000-000000000001"),
        title="BTC protocol update",
        content=b"Bitcoin protocol upgrade activates after miner signaling.",
        source_available_at=T0,
        system_observed_at=T0 + timedelta(minutes=1),
        ingested_at=T0 + timedelta(minutes=2),
    )
    future = registry.register(
        document_id=UUID("60000000-0000-4000-8000-000000000002"),
        title="Future filing",
        content=b"Confidential future revenue guidance.",
        source_available_at=T0 + timedelta(days=2),
        system_observed_at=T0 + timedelta(days=2, minutes=1),
        ingested_at=T0 + timedelta(days=2, minutes=2),
    )
    index = LocalPageIndex(registry)

    hits = index.search("protocol miner", cutoff=T0 + timedelta(hours=1), limit=5)

    assert [hit.title for hit in hits] == ["BTC protocol update"]
    assert future not in registry.visible_at(T0 + timedelta(hours=1))


def test_retrieval_benchmark_computes_recall_and_mrr(tmp_path: Path) -> None:
    registry = DocumentRegistry(LocalArtifactStore(tmp_path))
    document = registry.register(
        document_id=UUID("60000000-0000-4000-8000-000000000003"),
        title="Corporate action",
        content=b"Acme declared a two for one stock split.",
        source_available_at=T0,
        system_observed_at=T0,
        ingested_at=T0,
    )

    result = benchmark_retrieval(
        LocalPageIndex(registry),
        (
            RetrievalCaseV1(
                query="stock split",
                expected_document_id=document.document_id,
                cutoff=T0,
            ),
        ),
        k=3,
    )

    assert result.case_count == 1
    assert result.recall_at_k == 1
    assert result.mean_reciprocal_rank == 1


def test_document_registry_rejects_naive_time_and_non_utf8_content(tmp_path: Path) -> None:
    registry = DocumentRegistry(LocalArtifactStore(tmp_path))
    values = {
        "document_id": UUID("60000000-0000-4000-8000-000000000004"),
        "title": "Invalid document",
        "content": b"valid",
        "source_available_at": T0,
        "system_observed_at": T0,
        "ingested_at": T0,
    }
    with pytest.raises(ValueError, match="UTC"):
        registry.register(**{**values, "ingested_at": T0.replace(tzinfo=None)})
    with pytest.raises(ValueError, match="UTF-8"):
        registry.register(**{**values, "content": b"\xff"})


def test_iceberg_stays_disabled_until_a_measured_scale_trigger() -> None:
    local = evaluate_iceberg_gate(
        IcebergScaleProfileV1(
            dataset_bytes=100 * 1024**3,
            concurrent_writers=1,
            object_store_primary=False,
            partition_evolutions_per_quarter=1,
            query_engines=2,
        )
    )
    scaled = evaluate_iceberg_gate(
        IcebergScaleProfileV1(
            dataset_bytes=100 * 1024**3 + 1,
            concurrent_writers=1,
            object_store_primary=False,
            partition_evolutions_per_quarter=1,
            query_engines=2,
        )
    )

    assert local.enabled is False
    assert local.reasons == ()
    assert scaled.enabled is True
    assert scaled.reasons == ("dataset-over-100-gib",)
