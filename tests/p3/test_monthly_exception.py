"""The approved February 8 exception cannot become a monthly fallback."""
import hashlib
import io
import json
from datetime import UTC, date, datetime
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from packages.alpha_lifecycle import acquisition
from packages.alpha_lifecycle.pit_evidence import materialize_daily_revision
from packages.engine_contracts.serialization import canonical_json_bytes
from tests.p3.test_acquisition import FixtureTransport, _store


URL = 'https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1d/BTCUSDT-1d-2018-02.zip'
DAY = date(2018, 2, 8)


def monthly_bytes(fault=None):
    rows = []
    for offset in range(28):
        opened = 1517443200000 + offset * 86400000
        rows.append(f'{opened},100,110,90,105,1,{opened + 86399999},100,1,0,0,0'.split(','))
    if fault == 'missing':
        rows.pop()
    elif fault == 'duplicate':
        rows[2] = rows[1]
    elif fault == 'order':
        rows[0], rows[1] = rows[1], rows[0]
    elif fault == 'schema':
        rows[7].pop()
    elif fault == 'truncated':
        rows[7][6] = '1518049694788'
    elif fault == 'unit':
        rows[7][0] += '000'
    elif fault == 'quality':
        rows[7][2] = '99'
    elif fault == 'unselected_quality':
        rows[0][2] = '99'
    elif fault == 'numeric':
        rows[7][7] = 'NaN'
    elif fault == 'ignored':
        rows[7][11] = '1'
    raw = ''.join(','.join(row) + '\n' for row in rows).encode()
    output = io.BytesIO()
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        archive.writestr('../escape.csv' if fault == 'member' else 'BTCUSDT-1d-2018-02.csv', raw)
    zipped = output.getvalue()
    digest = '0' * 64 if fault == 'checksum' else hashlib.sha256(zipped).hexdigest()
    name = 'BTCUSDT-1d-2018-02-08.zip' if fault == 'filename' else 'BTCUSDT-1d-2018-02.zip'
    return zipped, f'{digest}  {name}\n'.encode(), raw


def retain(store, fault=None, day=DAY):
    zipped, checksum, _ = monthly_bytes(fault)
    return acquisition.retain_monthly_exception(day, checksum, zipped, store,
        system_observed_at=datetime(2026, 9, 10, tzinfo=UTC),
        fetched_at=datetime(2026, 9, 10, tzinfo=UTC))


def test_monthly_exception_survives_read_only_p2_reconstruction(tmp_path, monkeypatch):
    store = _store(tmp_path / 'cas')
    receipt = retain(store)
    _, _, raw = monthly_bytes()
    assert receipt.schema_version == 'p3-monthly-row-acquisition-v1'
    assert receipt.member_sha256 == hashlib.sha256(raw).hexdigest()
    assert receipt.row_sha256 == hashlib.sha256(raw.splitlines(keepends=True)[7]).hexdigest()
    assert receipt.row_ordinal == 7
    assert receipt.archive_url == URL
    assert receipt.provider_published_at is None
    row = acquisition.normalize_daily_acquisition(receipt.artifact_ref, store)['row']
    assert row['date'] == '2018-02-08'
    assert row['raw_close_time'] == 1518134399999
    assert row['closed_at_exclusive'] == '2018-02-09T00:00:00Z'
    entry = materialize_daily_revision(receipt.artifact_ref, store,
        ingested_at=datetime(2026, 9, 11, tzinfo=UTC))
    provider = json.loads(store.read_bytes(entry.provider_ref))
    assert provider['normalization_version'] == 'p3.binance.12-column.monthly-row.v1'
    assert set(entry.partition.raw_evidence_sha256s) == {
        receipt.archive_ref.content_sha256, receipt.checksum_ref.content_sha256}
    from packages.alpha_lifecycle.replica_store import ReadbackStore
    replay = ReadbackStore(store, store)
    monkeypatch.setattr(store, 'put_bytes', lambda *a, **k: pytest.fail('readback wrote'))
    assert materialize_daily_revision(receipt.artifact_ref, replay,
        ingested_at=entry.partition.ingested_at) == entry
    with pytest.raises(ValueError):
        acquisition.parse_daily_archive(DAY, store.read_bytes(receipt.archive_ref))


@pytest.mark.parametrize('fault', ['missing', 'duplicate', 'order', 'schema', 'truncated',
    'unit', 'quality', 'unselected_quality', 'numeric', 'ignored', 'member', 'checksum', 'filename'])
def test_monthly_corruption_is_rejected_before_retention(tmp_path, fault):
    store = _store(tmp_path / 'cas')
    with pytest.raises(ValueError):
        retain(store, fault)
    assert list(store._root.iterdir()) == []


@pytest.mark.parametrize('day', [date(2018, 2, 7), date(2018, 3, 8), date(2025, 9, 1)])
def test_exception_cannot_acquire_or_retain_another_day(tmp_path, day):
    store = _store(tmp_path / 'cas')
    transport = FixtureTransport({})
    with pytest.raises(ValueError):
        acquisition.acquire_monthly_exception(day, transport, store)
    assert transport.urls == []
    with pytest.raises(ValueError):
        retain(store, day=day)
    assert list(store._root.iterdir()) == []


@pytest.mark.parametrize('field,value', [
    ('schema_version', 'unknown'), ('day', '2018-02-09'), ('row_ordinal', 8),
    ('member_name', 'other.csv'), ('member_sha256', '0' * 64), ('row_sha256', '0' * 64),
    ('archive_url', URL.replace('2018-02.zip', '2018-03.zip')),
    ('parser_version', 'unknown'), ('instrument', 'ETHUSDT.BINANCE'),
    ('exception_id', 'unapproved'),
])
def test_forged_monthly_provenance_is_rejected(tmp_path, field, value):
    store = _store(tmp_path / 'cas')
    receipt = retain(store)
    body = receipt.model_dump(mode='json', exclude={'digest'})
    body[field] = value
    body['digest'] = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
    ref = store.put_bytes(canonical_json_bytes(body), media_type='application/json')
    with pytest.raises(ValueError):
        acquisition.validate_acquisition_receipt(ref, store)


def test_explicit_monthly_acquisition_uses_only_exact_object_pair(tmp_path):
    zipped, checksum, _ = monthly_bytes()
    transport = FixtureTransport({URL: zipped, URL + '.CHECKSUM': checksum})
    acquired = acquisition.acquire_monthly_exception(DAY, transport, _store(tmp_path / 'cas'))
    assert transport.urls == [URL + '.CHECKSUM', URL]
    assert acquired.day == DAY


def test_transport_and_cli_require_exact_explicit_exception(tmp_path):
    from scripts.acquire_p3_binance_daily import PublicArchiveTransport, main
    for enabled, url in [(False, URL), (True, URL.replace('2018-02', '2018-03')),
        (True, URL + '?x=1'), (True, URL.replace('https:', 'http:'))]:
        transport = PublicArchiveTransport(monthly_exception=enabled)
        with pytest.raises(ValueError):
            transport.get(url, timeout_seconds=20, max_bytes=4096)
    with pytest.raises(ValueError):
        main(DAY, date(2018, 2, 9), tmp_path / 'absent', monthly_exception=True)
    assert not (tmp_path / 'absent').exists()


def test_explicit_cli_retains_monthly_receipt_and_transport_allows_exact_pair(tmp_path, monkeypatch, capsys):
    from scripts import acquire_p3_binance_daily as cli
    zipped, checksum, _ = monthly_bytes()
    class Opener:
        def open(self, request, *, timeout):
            assert timeout == 20
            return io.BytesIO({URL: zipped, URL + '.CHECKSUM': checksum}[request.full_url])
    transport = cli.PublicArchiveTransport(monthly_exception=True)
    monkeypatch.setattr(transport, '_opener', Opener())
    monkeypatch.setattr(cli, 'PublicArchiveTransport', lambda **kw: transport)
    store = _store(tmp_path / 'cas')
    cli.main(DAY, DAY, store._root, receipt_jsonl=True, monthly_exception=True)
    from packages.data_contracts import ArtifactRefV1
    ref = ArtifactRefV1.model_validate_json(capsys.readouterr().out)
    receipt = acquisition.validate_acquisition_receipt(ref, store)
    assert receipt.schema_version == 'p3-monthly-row-acquisition-v1'
    assert store.read_bytes(receipt.archive_ref) == zipped


@pytest.mark.parametrize('observed,fetched', [
    (datetime(2018, 2, 9, tzinfo=UTC), datetime(2018, 2, 9, tzinfo=UTC)),
    (datetime(2026, 9, 10), datetime(2026, 9, 10, tzinfo=UTC)),
    (datetime(2026, 9, 11, tzinfo=UTC), datetime(2026, 9, 10, tzinfo=UTC)),
    (datetime(2099, 1, 1, tzinfo=UTC), datetime(2099, 1, 1, tzinfo=UTC)),
])
def test_invalid_monthly_observation_cannot_be_retained(tmp_path, observed, fetched):
    store = _store(tmp_path / 'cas')
    zipped, checksum, _ = monthly_bytes()
    with pytest.raises(ValueError):
        acquisition.retain_monthly_exception(DAY, checksum, zipped, store,
            system_observed_at=observed, fetched_at=fetched)
    assert list(store._root.iterdir()) == []
