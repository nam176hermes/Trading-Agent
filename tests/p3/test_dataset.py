from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta

import pytest

from packages.alpha_lifecycle.contracts.data import DailyBar
from packages.alpha_lifecycle.data_view import DatasetSealError, to_daily_close, validate_dates


def _ref(digest: str) -> dict[str, object]:
    return {
        "content_sha256": digest,
        "size_bytes": 1,
        "media_type": "application/json",
        "locator": f"{digest}.blob",
    }


def _bar(day: date) -> DailyBar:
    opened = datetime(day.year, day.month, day.day, tzinfo=UTC)
    payload: dict[str, object] = {
        "schema_version": "p3-daily-bar-v1",
        "date": day.isoformat(),
        "instrument": "BTCUSDT.BINANCE",
        "opened_at": opened.isoformat().replace("+00:00", "Z"),
        "closed_at_exclusive": (opened + timedelta(days=1)).isoformat().replace("+00:00", "Z"),
        "raw_close_time": int((opened + timedelta(days=1)).timestamp() * 1_000_000) - 1,
        "raw_timestamp_unit": "MICROSECONDS",
        "open": "100", "high": "110", "low": "90", "close": "105",
        "base_volume": "1", "quote_volume": "105", "trade_count": 1,
        "provider_published_at": None,
        "system_observed_at": "2026-09-05T12:00:00Z",
        "ingested_at": "2026-09-05T12:00:01Z",
        "partition_ref": _ref("a" * 64),
        "row_ordinal": 0,
    }
    payload["digest"] = hashlib.sha256(
        __import__("json").dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return DailyBar.model_validate(payload)


def test_generic_dataset_minimum_and_actual_campaign_coverage() -> None:
    start = date(2018, 1, 1)
    assert len(validate_dates("GENERIC_RESEARCH", tuple(start + timedelta(days=i) for i in range(750)))) == 750
    with pytest.raises(DatasetSealError, match="750"):
        validate_dates("GENERIC_RESEARCH", tuple(start + timedelta(days=i) for i in range(749)))
    with pytest.raises(DatasetSealError, match="coverage"):
        validate_dates("RESEARCH", tuple(start + timedelta(days=i) for i in range(750)))


def test_daily_close_uses_end_exclusive_minus_one_microsecond() -> None:
    bar = _bar(date(2025, 1, 6))
    close = to_daily_close(bar)
    assert close.closed_at == bar.closed_at_exclusive - timedelta(microseconds=1)
    assert close.closed_at.date() == bar.date


def _materialized_daily(store, day):
    import pyarrow as pa
    from uuid import UUID
    from packages.data_catalog.v3 import materialize_arrow_partition_v3
    from packages.data_contracts import ArrowSchemaV1, ArrowFieldV1
    expected=_bar(day)
    row=expected.model_dump(mode='json',exclude={'schema_version','digest','partition_ref',
        'row_ordinal','system_observed_at','ingested_at'})
    row['opened_at']=expected.opened_at
    row['closed_at_exclusive']=expected.closed_at_exclusive
    row['ts_event']=expected.closed_at_exclusive
    fields=tuple(ArrowFieldV1(field_id=i,name=name,data_type=(
        'timestamp[ns,UTC]' if name in {'opened_at','closed_at_exclusive','provider_published_at','ts_event'}
        else 'int64' if name in {'raw_close_time','trade_count'} else 'string'),
        nullable=name=='provider_published_at') for i,name in enumerate(row,1))
    schema=ArrowSchemaV1(schema_id='fixture.daily.v1',data_api_epoch=2,fields=fields)
    from packages.data_catalog.v2 import _expected_schema
    partition=materialize_arrow_partition_v3(pa.Table.from_pylist([row],schema=_expected_schema(schema)),
        schema=schema,store=store,partition_id=UUID(int=day.toordinal()),dataset='p3.research.daily',
        partition_key=('BTCUSDT.BINANCE',day.isoformat()),partition_spec_version='fixture.v1',
        source_available_at=expected.system_observed_at,system_observed_at=expected.system_observed_at,
        ingested_at=expected.ingested_at,raw_evidence_sha256s=('a'*64,),
        transform_receipt_sha256='b'*64,quality_receipt_sha256='c'*64,
        revision_series_id=UUID(int=day.toordinal()+1000000),revision_ordinal=1)
    return schema, partition


def test_daily_view_reads_real_arrow_timestamps_without_changing_digest(tmp_path):
    from packages.alpha_lifecycle.data_view import _daily_bar
    from packages.data_catalog.artifact_store import LocalArtifactStore
    from packages.engine_contracts.serialization import canonical_json_bytes
    root=tmp_path/'store'
    root.mkdir(mode=0o700)
    store=LocalArtifactStore(root)
    expected=_bar(date(2025,1,6))
    _,partition=_materialized_daily(store,expected.date)
    actual=_daily_bar(partition.manifest,store)
    payload=expected.model_dump(mode='json',exclude={'digest'})
    payload['partition_ref']=partition.artifact.model_dump(mode='json')
    payload['digest']=hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    assert actual==DailyBar.model_validate(payload)


def test_research_sealer_reconstructs_complete_synthetic_v3_dataset(tmp_path):
    from packages.alpha_lifecycle.data_view import seal_research_dataset
    from packages.alpha_lifecycle.baseline_campaign import ReadbackStore
    from packages.data_catalog.artifact_store import LocalArtifactStore
    from packages.data_contracts import PITQueryV1
    root=tmp_path/'store'
    root.mkdir(mode=0o700)
    store=LocalArtifactStore(root)
    start,end=date(2018,1,1),date(2025,8,31)
    days=tuple(start+timedelta(days=i) for i in range((end-start).days+1))
    assert len(days)==2800
    built=tuple(_materialized_daily(store,day) for day in days)
    schema=built[0][0]
    partitions=tuple(partition.manifest for _,partition in built)
    query=PITQueryV1.model_validate_json('{"mode":"SYSTEM_OBSERVED","valid_at":"2025-09-01T00:00:00Z","cutoff":"2026-09-05T12:00:02Z"}')
    evidence=seal_research_dataset(partitions,query,schema,store)
    assert evidence.usable_rows==len(days)
    assert evidence.date_range.start==start and evidence.date_range.end==end
    assert evidence==seal_research_dataset(partitions,query,schema,ReadbackStore(store,store))
