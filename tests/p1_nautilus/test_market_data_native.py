from __future__ import annotations

import json
from pathlib import Path
import subprocess

from test_instrument_factory_native import exact_g1_command, exact_g1_runtime


ROOT = Path(__file__).parents[2]


def test_exact_quote_before_bar_conversion_on_g1_host() -> None:
    with exact_g1_runtime() as runtime:
        if runtime is None:
            assert "nautilus_trader" not in subprocess.run(
                ["uv", "run", "python", "-c", "import sys;print(' '.join(sys.path))"],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            return
        root, site_packages = runtime
        script = r'''
import hashlib
import json
from pathlib import Path
import sys
sys.path[:0] = [sys.argv[1], sys.argv[2]]
import nautilus_trader
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Money
from runtime_v1.input_loader import ArtifactReference, RunBacktestRequest, RuntimeInputs
from runtime_v1.instrument_factory import build_instrument
from runtime_v1.market_data_loader import MarketDataError, load_market_data
assert nautilus_trader.__version__ == "1.231.0"
assert Path(nautilus_trader.__file__).resolve().is_relative_to(Path(sys.argv[2]).resolve())

def canonical(value):
    return json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()

catalog_raw = open(sys.argv[3], "rb").read()
catalog = tuple(sorted(json.loads(catalog_raw).items()))
configuration = tuple(sorted(json.loads(open(sys.argv[4], "rb").read()).items()))
rows = [
    {"ask":"100","bid":"99","close":"100","event_time":"2026-08-05T12:00:00Z","high":"101","low":"98","open":"99","quote_time":"2026-08-05T12:00:00Z","sequence":1,"volume":"2"},
    {"ask":"102","bid":"101","close":"102","event_time":"2026-08-05T12:01:00Z","high":"103","low":"100","open":"101","quote_time":"2026-08-05T12:01:00Z","sequence":2,"volume":"3"},
]

def raw_for(values):
    return b"".join(canonical(row) + b"\n" for row in values)

def inputs(raw, *, data_digest=None, catalog_digest=None, end_time="2026-08-05T12:01:00Z", catalog_value=catalog, catalog_bytes=catalog_raw, start_time="2026-08-05T12:00:00Z"):
    market_digest = data_digest or hashlib.sha256(raw).hexdigest()
    request = RunBacktestRequest(
        message_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        correlation_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        causation_id="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        engine_run_id="dddddddd-dddd-4ddd-8ddd-dddddddddddd",
        stream_sequence=1,
        event_time="2026-08-05T12:00:00Z",
        initialization_time="2026-08-05T12:00:00Z",
        schema_version="1.0.0",
        producer_identity="worker-authority-1",
        source_commit="0123456789abcdef0123456789abcdef01234567",
        config_digest="1" * 64,
        payload_digest="2" * 64,
        command_type="RunBacktest",
        engine_configuration=ArtifactReference("11111111-1111-4111-8111-111111111111", "3" * 64, "application/json"),
        instrument_catalog=ArtifactReference("22222222-2222-4222-8222-222222222222", catalog_digest or hashlib.sha256(catalog_bytes).hexdigest(), "application/json"),
        strategy_configuration=ArtifactReference("33333333-3333-4333-8333-333333333333", "4" * 64, "application/json"),
        market_data=ArtifactReference("44444444-4444-4444-8444-444444444444", market_digest, "application/jsonl"),
        start_time=start_time,
        end_time=end_time,
    )
    return RuntimeInputs(request, configuration, catalog_value, (), raw)

raw = raw_for(rows)
instrument = build_instrument(catalog)
batch = load_market_data(inputs(raw), instrument)
objects = []
for item in batch.data:
    if type(item).__name__ == "QuoteTick":
        objects.append({
            "ask": str(item.ask_price),
            "ask_size": str(item.ask_size),
            "bid": str(item.bid_price),
            "bid_size": str(item.bid_size),
            "instrument_id": str(item.instrument_id),
            "ts_event": item.ts_event,
            "ts_init": item.ts_init,
            "type": "QuoteTick",
        })
    else:
        objects.append({
            "bar_type": str(item.bar_type),
            "close": str(item.close),
            "high": str(item.high),
            "low": str(item.low),
            "open": str(item.open),
            "ts_event": item.ts_event,
            "ts_init": item.ts_init,
            "type": "Bar",
            "volume": str(item.volume),
        })

rejections = []
def reject(label, candidate, **kwargs):
    try:
        load_market_data(inputs(candidate, **kwargs), instrument)
    except MarketDataError:
        rejections.append(label)
    else:
        raise AssertionError(f"accepted {label}")

reject("raw_digest", raw, data_digest="0" * 64)
reject("catalog_digest", raw, catalog_digest="0" * 64)
reject("unaligned_window", raw, end_time="2026-08-05T12:01:30Z")
for label, mutation in (
    ("sequence_gap", {"sequence":3}),
    ("quote_lookahead", {"quote_time":"2026-08-05T12:01:00Z"}),
    ("crossed_quote", {"bid":"101"}),
    ("nonpositive_price", {"low":"0"}),
    ("invalid_ohlc", {"high":"99"}),
    ("negative_volume", {"volume":"-1"}),
    ("timestamp_gap", {"event_time":"2026-08-05T12:02:00Z","quote_time":"2026-08-05T12:02:00Z"}),
    ("over_precision", {"ask":"100.001"}),
):
    changed = [dict(row) for row in rows]
    changed[1 if label in {"sequence_gap", "timestamp_gap"} else 0].update(mutation)
    reject(label, raw_for(changed))
reject("duplicate_key", raw.replace(b'"ask":"100"', b'"ask":"100","ask":"100"', 1))
reject("noncanonical", raw.replace(b'"ask":"100"', b'"ask": "100"', 1))
reject("float", raw.replace(b'"sequence":1', b'"sequence":1.0', 1))

alternate_values = dict(catalog)
alternate_values.update({"tick_size":"0.02", "step_size":"0.000002"})
alternate_catalog = tuple(sorted(alternate_values.items()))
alternate_catalog_raw = canonical(alternate_values) + b"\n"
alternate_instrument = build_instrument(alternate_catalog)
for label, mutation in (
    ("off_tick", {"bid":"99.01"}),
    ("off_step", {"volume":"2.000001"}),
):
    changed = [dict(row) for row in rows]
    changed[0].update(mutation)
    try:
        load_market_data(
            inputs(
                raw_for(changed),
                catalog_value=alternate_catalog,
                catalog_bytes=alternate_catalog_raw,
            ),
            alternate_instrument,
        )
    except MarketDataError:
        rejections.append(label)
    else:
        raise AssertionError(f"accepted {label}")
try:
    load_market_data(
        inputs(raw, catalog_value=alternate_catalog, catalog_bytes=alternate_catalog_raw),
        instrument,
    )
except MarketDataError:
    rejections.append("catalog_instrument_mismatch")
else:
    raise AssertionError("accepted catalog_instrument_mismatch")

def changed_instrument(*, min_quantity=instrument.min_quantity, min_notional=instrument.min_notional):
    return CurrencyPair(
        instrument_id=instrument.id,
        raw_symbol=instrument.raw_symbol,
        base_currency=instrument.base_currency,
        quote_currency=instrument.quote_currency,
        price_precision=instrument.price_precision,
        size_precision=instrument.size_precision,
        price_increment=instrument.price_increment,
        size_increment=instrument.size_increment,
        ts_event=0,
        ts_init=0,
        min_quantity=min_quantity,
        min_notional=min_notional,
        maker_fee=instrument.maker_fee,
        taker_fee=instrument.taker_fee,
    )

for label, altered in (
    ("missing_min_quantity", changed_instrument(min_quantity=None)),
    ("missing_min_notional", changed_instrument(min_notional=None)),
    (
        "wrong_min_notional_currency",
        changed_instrument(min_notional=Money.from_str("10 BTC")),
    ),
):
    try:
        load_market_data(inputs(raw), altered)
    except MarketDataError:
        rejections.append(label)
    else:
        raise AssertionError(f"accepted {label}")

for label, first, second in (
    ("pre_epoch", "1960-01-01T00:00:00Z", "1960-01-01T00:01:00Z"),
    ("timestamp_overflow", "9998-01-01T00:00:00Z", "9998-01-01T00:01:00Z"),
):
    changed = [dict(row) for row in rows]
    changed[0].update({"event_time":first, "quote_time":first})
    changed[1].update({"event_time":second, "quote_time":second})
    try:
        load_market_data(
            inputs(raw_for(changed), start_time=first, end_time=second),
            instrument,
        )
    except MarketDataError:
        rejections.append(label)
    else:
        raise AssertionError(f"accepted {label}")

changed = [dict(row) for row in rows]
changed[0]["close"] = "100.01"
changed_raw = raw_for(changed)
changed_batch = load_market_data(inputs(changed_raw), instrument)
print(json.dumps({
    "objects": objects,
    "raw_sha256": batch.raw_sha256,
    "raw_changed": changed_batch.raw_sha256 != batch.raw_sha256,
    "rejections": rejections,
    "row_count": batch.row_count,
    "semantic_changed": changed_batch.semantic_sha256 != batch.semantic_sha256,
    "semantic_sha256": batch.semantic_sha256,
}, separators=(",", ":"), sort_keys=True))
'''
        completed = subprocess.run(
            exact_g1_command(
                root,
                site_packages,
                script,
                (
                    ROOT / "tests/fixtures/p1_nautilus/contracts/instrument-catalog.json",
                    "/inputs/instrument-catalog.json",
                ),
                (
                    ROOT / "tests/fixtures/p1_nautilus/contracts/engine-configuration.json",
                    "/inputs/engine-configuration.json",
                ),
            ),
            cwd="/",
            env={},
            check=False,
            capture_output=True,
            text=True,
        )

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    observed = json.loads(completed.stdout)
    assert observed["row_count"] == 2
    assert observed["raw_changed"] is True
    assert observed["semantic_changed"] is True
    assert observed["rejections"] == [
        "raw_digest",
        "catalog_digest",
        "unaligned_window",
        "sequence_gap",
        "quote_lookahead",
        "crossed_quote",
        "nonpositive_price",
        "invalid_ohlc",
        "negative_volume",
        "timestamp_gap",
        "over_precision",
        "duplicate_key",
        "noncanonical",
        "float",
        "off_tick",
        "off_step",
        "catalog_instrument_mismatch",
        "missing_min_quantity",
        "missing_min_notional",
        "wrong_min_notional_currency",
        "pre_epoch",
        "timestamp_overflow",
    ]
    assert observed["objects"] == [
        {
            "ask": "100.00",
            "ask_size": "2.000000",
            "bid": "99.00",
            "bid_size": "2.000000",
            "instrument_id": "BTCUSDT.BINANCE",
            "ts_event": 1785931200000000000,
            "ts_init": 1785931200000000000,
            "type": "QuoteTick",
        },
        {
            "bar_type": "BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL",
            "close": "100.00",
            "high": "101.00",
            "low": "98.00",
            "open": "99.00",
            "ts_event": 1785931200000000000,
            "ts_init": 1785931200000000000,
            "type": "Bar",
            "volume": "2.000000",
        },
        {
            "ask": "102.00",
            "ask_size": "3.000000",
            "bid": "101.00",
            "bid_size": "3.000000",
            "instrument_id": "BTCUSDT.BINANCE",
            "ts_event": 1785931260000000000,
            "ts_init": 1785931260000000000,
            "type": "QuoteTick",
        },
        {
            "bar_type": "BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL",
            "close": "102.00",
            "high": "103.00",
            "low": "100.00",
            "open": "101.00",
            "ts_event": 1785931260000000000,
            "ts_init": 1785931260000000000,
            "type": "Bar",
            "volume": "3.000000",
        },
    ]
    assert len(observed["raw_sha256"]) == 64
    assert len(observed["semantic_sha256"]) == 64
