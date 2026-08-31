"""Provider-free end-to-end P2 data-platform certification."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
import hashlib
from uuid import UUID

import pyarrow as pa

from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.data_catalog.v2 import build_snapshot, materialize_arrow_partition
from packages.data_contracts import ArrowFieldV1, ArrowSchemaV1, PITQueryMode, PITQueryV1
from packages.data_documents import DocumentRegistry, LocalPageIndex, RetrievalCaseV1, benchmark_retrieval
from packages.data_projections import project_qlib_csv
from packages.data_providers import ingest_market_data
from packages.data_quality import validate_bar_rows
from packages.data_query import query_snapshot_parity
from packages.data_scale import IcebergScaleProfileV1, evaluate_iceberg_gate
from packages.engine_contracts.serialization import canonical_json_bytes
from packages.job_contracts import MarketDataSnapshotRequest
from services.market_data.fixture import DeterministicProviderFreeFixture, P10_PROVIDER


_AT = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class P2CertificationReceiptV1:
    provider_receipt_sha256: str
    quality_receipt_sha256: str
    snapshot_sha256: str
    snapshot_row_count: int
    query_rows_sha256: str
    query_parity: bool
    qlib_manifest_sha256: str
    retrieval_recall_at_3: str
    iceberg_enabled: bool

    def payload(self) -> dict[str, object]:
        return {
            "iceberg_enabled": self.iceberg_enabled,
            "provider_receipt_sha256": self.provider_receipt_sha256,
            "qlib_manifest_sha256": self.qlib_manifest_sha256,
            "quality_receipt_sha256": self.quality_receipt_sha256,
            "query_parity": self.query_parity,
            "query_rows_sha256": self.query_rows_sha256,
            "retrieval_recall_at_3": self.retrieval_recall_at_3,
            "schema_version": "p2-data-platform-certification-v1",
            "snapshot_row_count": self.snapshot_row_count,
            "snapshot_sha256": self.snapshot_sha256,
        }

    @property
    def receipt_sha256(self) -> str:
        return hashlib.sha256(canonical_json_bytes(self.payload())).hexdigest()


def _schema() -> ArrowSchemaV1:
    decimal = "decimal128(18,8)"
    return ArrowSchemaV1(
        schema_id="canonical-bars-v1",
        data_api_epoch=1,
        fields=(
            ArrowFieldV1(field_id=1, name="ts_event", data_type="timestamp[ns,UTC]", nullable=False),
            ArrowFieldV1(field_id=2, name="open", data_type=decimal, nullable=False),
            ArrowFieldV1(field_id=3, name="high", data_type=decimal, nullable=False),
            ArrowFieldV1(field_id=4, name="low", data_type=decimal, nullable=False),
            ArrowFieldV1(field_id=5, name="close", data_type=decimal, nullable=False),
            ArrowFieldV1(field_id=6, name="volume", data_type=decimal, nullable=False),
        ),
    )


def certify_p2_data_platform(store: LocalArtifactStore) -> P2CertificationReceiptV1:
    request = MarketDataSnapshotRequest(
        provider=P10_PROVIDER,
        instrument="crypto_spot:FIXTURE:BTC",
        timeframe="1m",
        interval_seconds=60,
        requested_at="2026-01-01T00:00:00Z",
        provider_retry_limit=1,
    )
    ingestion = ingest_market_data(
        DeterministicProviderFreeFixture(),
        request,
        store=store,
        evidence_id=UUID("70000000-0000-4000-8000-000000000001"),
    )
    rows = tuple(
        {
            "ts_event": candle.open_time,
            "open": candle.open,
            "high": candle.high,
            "low": candle.low,
            "close": candle.close,
            "volume": candle.volume,
        }
        for candle in ingestion.snapshot.candles
    )
    quality = validate_bar_rows(rows, dataset="bars")
    decimal_type = pa.decimal128(18, 8)
    table = pa.table(
        {
            "ts_event": pa.array(tuple(row["ts_event"] for row in rows), type=pa.timestamp("ns", tz="UTC")),
            **{
                name: pa.array(tuple(Decimal(row[name]) for row in rows), type=decimal_type)
                for name in ("open", "high", "low", "close", "volume")
            },
        }
    )
    partition = materialize_arrow_partition(
        table,
        schema=_schema(),
        store=store,
        partition_id=UUID("70000000-0000-4000-8000-000000000002"),
        dataset="bars",
        partition_key=("BTC-FIXTURE", "2025-12-31"),
        partition_spec_version="day-v1",
        source_available_at=_AT,
        system_observed_at=_AT,
        ingested_at=_AT,
        raw_evidence_sha256s=(ingestion.evidence.content_sha256,),
        transform_receipt_sha256=ingestion.receipt.digest,
        quality_receipt_sha256=quality.digest,
    )
    snapshot = build_snapshot(
        dataset="bars",
        query=PITQueryV1(mode=PITQueryMode.AS_INGESTED, valid_at=_AT, cutoff=_AT),
        schema=_schema(),
        partitions=(partition.manifest,),
    )
    parity = query_snapshot_parity(snapshot, store=store, order_by=("ts_event",))
    qlib = project_qlib_csv(
        tuple({"instrument": "BTC-FIXTURE", **row} for row in rows)
    )

    registry = DocumentRegistry(store)
    document = registry.register(
        document_id=UUID("70000000-0000-4000-8000-000000000003"),
        title="Provider-free certification",
        content=b"P2 immutable snapshot provider evidence and point in time query.",
        source_available_at=_AT,
        system_observed_at=_AT,
        ingested_at=_AT,
    )
    retrieval = benchmark_retrieval(
        LocalPageIndex(registry),
        (RetrievalCaseV1("immutable snapshot", document.document_id, _AT),),
        k=3,
    )
    iceberg = evaluate_iceberg_gate(
        IcebergScaleProfileV1(0, 1, False, 0, 2)
    )
    return P2CertificationReceiptV1(
        provider_receipt_sha256=ingestion.receipt.digest,
        quality_receipt_sha256=quality.digest,
        snapshot_sha256=snapshot.snapshot_digest,
        snapshot_row_count=snapshot.row_count,
        query_rows_sha256=parity.pyarrow_sha256,
        query_parity=(
            parity.pyarrow_sha256 == parity.polars_sha256 == parity.duckdb_sha256
        ),
        qlib_manifest_sha256=qlib.manifest_sha256,
        retrieval_recall_at_3=str(retrieval.recall_at_k),
        iceberg_enabled=iceberg.enabled,
    )


__all__ = ["P2CertificationReceiptV1", "certify_p2_data_platform"]
