from datetime import UTC, date, datetime, timedelta
import hashlib
from uuid import NAMESPACE_URL, uuid5

import pytest

from packages.alpha_lifecycle.acquisition import acquire_day_receipt
from packages.alpha_lifecycle.pit_evidence import materialize_daily_revision
from packages.engine_contracts.serialization import canonical_json_bytes
from tests.p3.test_acquisition import FixtureTransport, _archive, _store


def test_daily_materialization_binds_p2_revision_and_truthful_observation(tmp_path):
    store = _store(tmp_path / "artifacts")
    day = date(2024, 1, 2)

    def acquire(close):
        filename, raw = _archive(day, column=(4, close))
        base = "https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1d/"
        transport = FixtureTransport({
            base + filename: raw,
            base + filename + ".CHECKSUM": f"{hashlib.sha256(raw).hexdigest()}  {filename}\n".encode(),
        })
        return acquire_day_receipt(day, transport, store)

    acquired = acquire("105")
    now = datetime.now(UTC)
    entry = materialize_daily_revision(acquired.artifact_ref, store, ingested_at=now)
    series = uuid5(NAMESPACE_URL, f"p3.research.daily/BTCUSDT.BINANCE/{day.isoformat()}")
    identity = canonical_json_bytes(dict(archive_sha256=acquired.archive_ref.content_sha256,
        checksum_sha256=acquired.checksum_ref.content_sha256, revision_ordinal=1)).decode()
    assert entry.partition.partition_id == uuid5(series, identity)
    assert entry.partition.revision_series_id == series
    assert entry.partition.system_observed_at == entry.partition.source_available_at == acquired.system_observed_at
    assert entry.partition.ingested_at == now
    assert acquired.vintage_class == "RETROSPECTIVE_CURRENT_ARCHIVE"
    assert entry == materialize_daily_revision(acquired.artifact_ref, store, ingested_at=now)
    with pytest.raises(ValueError, match="earlier"):
        materialize_daily_revision(acquired.artifact_ref, store, ingested_at=acquired.fetched_at - timedelta(seconds=1))
    with pytest.raises(ValueError, match="different raw"):
        materialize_daily_revision(acquired.artifact_ref, store, ingested_at=now, previous=entry)
    changed = acquire("106")
    revised = materialize_daily_revision(changed.artifact_ref, store, ingested_at=datetime.now(UTC), previous=entry)
    assert revised.partition.revision_ordinal == 2
    assert revised.partition.supersedes_partition_id == entry.partition.partition_id
    assert revised.partition.supersedes_manifest_sha256 == entry.partition.digest
