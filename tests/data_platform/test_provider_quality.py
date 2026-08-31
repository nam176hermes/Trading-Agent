from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest

from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.data_providers import ingest_market_data
from packages.data_quality import (
    DataConflictError,
    DataQualityError,
    ProviderCandidateV1,
    resolve_provider_conflict,
    validate_bar_rows,
)
from packages.job_contracts import MarketDataSnapshotRequest
from services.market_data.fixture import (
    DeterministicProviderFreeFixture,
    P10_INSTRUMENT,
    P10_INTERVAL_SECONDS,
    P10_PROVIDER,
    P10_TIMEFRAME,
)


T0 = datetime(2026, 1, 1, tzinfo=UTC)


def request() -> MarketDataSnapshotRequest:
    return MarketDataSnapshotRequest(
        provider=P10_PROVIDER,
        instrument=P10_INSTRUMENT,
        timeframe=P10_TIMEFRAME,
        interval_seconds=P10_INTERVAL_SECONDS,
        requested_at="2026-01-01T00:00:00Z",
        provider_retry_limit=1,
    )


def test_provider_ingestion_preserves_raw_evidence_and_emits_receipt(tmp_path: Path) -> None:
    result = ingest_market_data(
        DeterministicProviderFreeFixture(),
        request(),
        store=LocalArtifactStore(tmp_path),
        evidence_id=UUID("50000000-0000-4000-8000-000000000001"),
    )

    assert result.receipt.provider == P10_PROVIDER
    assert result.receipt.evidence == (result.evidence,)
    assert result.evidence.content_sha256 == result.artifact.content_sha256
    assert result.artifact.size_bytes == result.evidence.byte_length
    assert result.snapshot.provenance.raw_evidence_sha256 == result.evidence.content_sha256


def test_provider_ingestion_allows_knowledge_after_fetch_without_relabeling_source_time(
    tmp_path: Path,
) -> None:
    observation = DeterministicProviderFreeFixture().fetch(request())

    class LaterKnowledgeProvider:
        def fetch(self, requested: MarketDataSnapshotRequest):
            assert requested == request()
            return type(observation)(
                snapshot=observation.snapshot.model_copy(
                    update={"known_at": T0 + timedelta(minutes=1)}
                ),
                raw_evidence=observation.raw_evidence,
            )

    result = ingest_market_data(
        LaterKnowledgeProvider(),
        request(),
        store=LocalArtifactStore(tmp_path),
        evidence_id=UUID("50000000-0000-4000-8000-000000000002"),
    )

    assert result.evidence.source_available_at == T0
    assert result.evidence.system_observed_at == T0


def test_bar_quality_receipt_is_deterministic_and_rejects_unsafe_ohlc() -> None:
    rows = (
        {"ts_event": T0, "open": "100", "high": "102", "low": "99", "close": "101", "volume": "2"},
    )

    first = validate_bar_rows(rows, dataset="bars")
    second = validate_bar_rows(rows, dataset="bars")

    assert first == second
    assert first.row_count == 1
    assert first.issue_codes == ()
    with pytest.raises(DataQualityError, match="OHLC"):
        validate_bar_rows(
            ({**rows[0], "high": "100", "close": "101"},),
            dataset="bars",
        )


def test_bar_quality_rejects_duplicate_or_out_of_order_timestamps() -> None:
    row = {"ts_event": T0, "open": "1", "high": "1", "low": "1", "close": "1", "volume": "0"}
    with pytest.raises(DataQualityError, match="strictly increasing"):
        validate_bar_rows((row, row), dataset="bars")
    non_utc = {**row, "ts_event": T0.astimezone(timezone(timedelta(hours=1)))}
    with pytest.raises(DataQualityError, match="UTC"):
        validate_bar_rows((non_utc,), dataset="bars")


def test_provider_conflicts_use_explicit_priority_and_fail_on_same_rank_ambiguity() -> None:
    preferred = ProviderCandidateV1("primary", "a" * 64, T0)
    backup = ProviderCandidateV1("backup", "b" * 64, T0)

    receipt = resolve_provider_conflict(
        (backup, preferred), provider_priority=("primary", "backup")
    )

    assert receipt.selected == preferred
    assert receipt.rejected == (backup,)
    with pytest.raises(DataConflictError, match="ambiguous"):
        resolve_provider_conflict(
            (preferred, ProviderCandidateV1("primary", "c" * 64, T0)),
            provider_priority=("primary", "backup"),
        )
