from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from packages.domain import (
    InstrumentId,
    MarketCandle,
    MarketContinuity,
    MarketDataProvenance,
    MarketSnapshot,
    MarketTimeframe,
    ProductType,
    normalize_market_symbol,
)


INSTRUMENT = InstrumentId("BTCUSDT", ProductType.CRYPTO_SPOT, "BINANCE")
OPEN = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
RAW_EVIDENCE_SHA256 = "a" * 64
SYMBOL_ALIASES = {
    "BTCUSDT": "BTCUSDT",
    "BTC-USDT": "BTCUSDT",
    "BTC/USDT": "BTCUSDT",
    "ETH/USDT": "ETHUSDT",
    "AAPL": "AAPL",
}


def wire_schema_accepts_string(schema: dict[str, object], value: str) -> bool:
    def branch_accepts(branch: dict[str, object]) -> bool:
        if branch.get("type", "string") != "string":
            return False
        if "const" in branch and branch["const"] != value:
            return False
        min_length = branch.get("minLength")
        if isinstance(min_length, int) and len(value) < min_length:
            return False
        max_length = branch.get("maxLength")
        if isinstance(max_length, int) and len(value) > max_length:
            return False
        pattern = branch.get("pattern")
        return not isinstance(pattern, str) or re.search(pattern, value) is not None

    branches = schema.get("anyOf")
    if isinstance(branches, list):
        return any(
            branch_accepts(branch)
            for branch in branches
            if isinstance(branch, dict)
        )
    return branch_accepts(schema)


def provenance(**changes: object) -> MarketDataProvenance:
    values: dict[str, object] = {
        "provider": "public-market-data",
        "observed_at": OPEN + timedelta(minutes=2),
        "fetched_at": OPEN + timedelta(minutes=3),
        "raw_evidence_sha256": RAW_EVIDENCE_SHA256,
        "schema_version": "market-data-v1",
        "normalization_version": "market-normalization-v1",
    }
    values.update(changes)
    return MarketDataProvenance(**values)


def candle(*, open_time: datetime = OPEN, **changes: object) -> MarketCandle:
    values: dict[str, object] = {
        "instrument": INSTRUMENT,
        "timeframe": MarketTimeframe.ONE_MINUTE,
        "open_time": open_time,
        "open": Decimal("100"),
        "high": Decimal("102"),
        "low": Decimal("99"),
        "close": Decimal("101"),
        "volume": Decimal("12.5"),
    }
    values.update(changes)
    return MarketCandle(**values)


def snapshot(*candles: MarketCandle, **changes: object) -> MarketSnapshot:
    values: dict[str, object] = {
        "instrument": INSTRUMENT,
        "timeframe": MarketTimeframe.ONE_MINUTE,
        "candles": candles or (candle(),),
        "provenance": provenance(),
        "known_at": OPEN + timedelta(minutes=4),
        "schema_version": "market-snapshot-v1",
        "normalization_version": "market-normalization-v1",
    }
    values.update(changes)
    return MarketSnapshot(**values)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("btc/usdt", "BTCUSDT"),
        ("BTC-USDT", "BTCUSDT"),
        ("BTCUSDT", "BTCUSDT"),
        ("eth/usdt", "ETHUSDT"),
        ("aapl", "AAPL"),
    ],
)
def test_market_symbol_normalization_is_explicit_and_bounded(raw: str, expected: str) -> None:
    assert normalize_market_symbol(raw, aliases=SYMBOL_ALIASES) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "ＢＴＣＵＳＤＴ", "BTC\x00USDT", "../BTCUSDT", "account", "order", "BTC_USDT"],
)
def test_market_symbol_normalization_rejects_unsafe_or_ambiguous_inputs(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_market_symbol(raw, aliases=SYMBOL_ALIASES)


def test_market_symbol_normalization_rejects_unregistered_safe_symbols() -> None:
    with pytest.raises(ValueError, match="explicitly supported alias"):
        normalize_market_symbol("SOL/USDT", aliases=SYMBOL_ALIASES)


@pytest.mark.parametrize(
    "aliases",
    [
        {},
        {"BTC/USDT": "BTC/USDT"},
        {"BTC/USDT": " account "},
        {"BTC/USDT": 123},
    ],
)
def test_market_symbol_normalization_rejects_invalid_alias_maps(
    aliases: object,
) -> None:
    with pytest.raises(ValueError):
        normalize_market_symbol("BTC/USDT", aliases=aliases)  # type: ignore[arg-type]


@pytest.mark.parametrize("target", ["ac_count", "or_der", "exe-cution"])
def test_market_symbol_normalization_rejects_split_prohibited_canonical_targets(
    target: str,
) -> None:
    with pytest.raises(ValueError):
        normalize_market_symbol("BTC/USDT", aliases={"BTC/USDT": target})


@pytest.mark.parametrize(
    "raw",
    [
        "../BTCUSDT",
        "BTC/../USDT",
        "BTC//USDT",
        "account",
        "ac/count",
        "order",
        "or_der",
        "execution",
        "exe-cution",
    ],
)
def test_market_symbol_normalization_rejects_malicious_authorized_aliases(
    raw: str,
) -> None:
    with pytest.raises(ValueError):
        normalize_market_symbol(raw, aliases={raw: "BTCUSDT"})


def test_market_symbol_normalization_rejects_oversized_alias_maps() -> None:
    aliases = {f"ALIAS{index}": "AAPL" for index in range(257)}

    with pytest.raises(ValueError, match="supported range"):
        normalize_market_symbol("ALIAS0", aliases=aliases)


def test_market_symbol_normalization_rejects_conflicting_normalized_aliases() -> None:
    with pytest.raises(ValueError, match="conflicting normalized keys"):
        normalize_market_symbol(
            "BTC",
            aliases={"btc": "BTCUSDT", "BTC": "ETHUSDT"},
        )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1m", MarketTimeframe.ONE_MINUTE), ("60s", MarketTimeframe.ONE_MINUTE), ("1h", MarketTimeframe.ONE_HOUR)],
)
def test_market_timeframe_uses_closed_explicit_aliases(raw: str, expected: MarketTimeframe) -> None:
    assert MarketTimeframe.normalize(raw) is expected


@pytest.mark.parametrize("raw", ["2m", "1M", "3600", "hour", "1 minute"])
def test_market_timeframe_rejects_unsupported_aliases(raw: str) -> None:
    with pytest.raises(ValueError):
        MarketTimeframe.normalize(raw)


def test_market_timeframe_rejects_non_string_alias() -> None:
    with pytest.raises(ValueError, match="string alias"):
        MarketTimeframe.normalize(60)


def test_market_timeframe_has_exact_interval_seconds() -> None:
    assert MarketTimeframe.ONE_MINUTE.interval_seconds == 60
    assert MarketTimeframe.ONE_HOUR.interval_seconds == 3600


def market_models_for_timeframe(
    timeframe: MarketTimeframe,
) -> tuple[MarketCandle, MarketContinuity, MarketSnapshot]:
    open_time = datetime(2026, 7, 20, tzinfo=UTC)
    item = candle(open_time=open_time, timeframe=timeframe)
    interval = timedelta(seconds=timeframe.interval_seconds)
    observed_at = open_time + interval
    item_provenance = provenance(
        observed_at=observed_at,
        fetched_at=observed_at + timedelta(seconds=1),
    )
    item_snapshot = snapshot(
        item,
        timeframe=timeframe,
        provenance=item_provenance,
        known_at=observed_at + timedelta(seconds=2),
    )
    return item, MarketContinuity(timeframe=timeframe), item_snapshot


@pytest.mark.parametrize("timeframe", tuple(MarketTimeframe))
def test_market_models_accept_every_canonical_timeframe_wire_value(
    timeframe: MarketTimeframe,
) -> None:
    item, continuity, item_snapshot = market_models_for_timeframe(timeframe)

    assert MarketCandle.model_validate_json(item.model_dump_json()) == item
    assert MarketContinuity.model_validate_json(continuity.model_dump_json()) == continuity
    assert MarketSnapshot.model_validate_json(item_snapshot.model_dump_json()) == item_snapshot


@pytest.mark.parametrize(
    "raw",
    ["60s", "300s", "900s", "3600s", "14400s", "86400s", " 1m "],
)
def test_market_models_reject_noncanonical_timeframe_wire_aliases(raw: str) -> None:
    item, continuity, item_snapshot = market_models_for_timeframe(
        MarketTimeframe.ONE_MINUTE
    )
    for model, instance in (
        (MarketCandle, item),
        (MarketContinuity, continuity),
        (MarketSnapshot, item_snapshot),
    ):
        payload = instance.model_dump(mode="json")
        payload["timeframe"] = raw
        with pytest.raises(ValidationError):
            model.model_validate_json(json.dumps(payload))


def test_market_timeframe_wire_schemas_match_the_canonical_runtime_values() -> None:
    generated_root = Path(__file__).parents[2] / "generated/domain/json-schema"
    schemas = (
        MarketCandle.model_json_schema(),
        MarketContinuity.model_json_schema(),
        MarketSnapshot.model_json_schema(),
        json.loads((generated_root / "MarketCandle.json").read_text()),
        json.loads((generated_root / "MarketContinuity.json").read_text()),
        json.loads((generated_root / "MarketSnapshot.json").read_text()),
    )
    expected = [timeframe.value for timeframe in MarketTimeframe]

    for schema in schemas:
        assert schema["$defs"]["MarketTimeframe"]["enum"] == expected


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("open", True),
        ("open", 100.0),
        ("high", Decimal("NaN")),
        ("low", Decimal("Infinity")),
        ("close", Decimal("1e-999999")),
        ("open", Decimal("-1")),
        ("volume", Decimal("-0.01")),
    ],
)
def test_candle_rejects_noncanonical_numeric_domains(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        candle(**{field: value})


def test_candle_allows_zero_volume_but_requires_positive_normal_prices() -> None:
    assert candle(volume=Decimal("0")).volume == Decimal("0")
    with pytest.raises(ValidationError, match="positive"):
        candle(open=Decimal("0"))


def test_candle_rejects_oversized_decimal_coefficients() -> None:
    oversized = Decimal((0, (9,) * 129, -128))

    with pytest.raises(ValidationError, match="128 coefficient digits"):
        candle(open=oversized, high=oversized, low=oversized, close=oversized)


def test_candle_enforces_adjusted_exponent_wire_bounds() -> None:
    for boundary in (Decimal("1e127"), Decimal("1e-128")):
        item = candle(
            open=boundary,
            high=boundary,
            low=boundary,
            close=boundary,
            volume=boundary,
        )
        assert item.open == boundary
        assert item.volume == boundary

    for outside in (Decimal("1e128"), Decimal("1e-129")):
        with pytest.raises(ValidationError, match="normal Decimal"):
            candle(open=outside, high=outside, low=outside, close=outside)
        with pytest.raises(ValidationError, match="normal Decimal"):
            candle(volume=outside)


@pytest.mark.parametrize(
    ("open_time", "match"),
    [
        (OPEN.replace(tzinfo=None), "UTC"),
        (OPEN.astimezone(timezone(timedelta(hours=-4))), "UTC"),
        (OPEN + timedelta(seconds=1), "aligned"),
    ],
)
def test_candle_requires_utc_interval_aligned_open_time(open_time: datetime, match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        candle(open_time=open_time)


@pytest.mark.parametrize(
    "changes",
    [
        {"high": Decimal("100")},
        {"low": Decimal("101")},
    ],
)
def test_candle_requires_valid_ohlc_relationships(changes: dict[str, Decimal]) -> None:
    with pytest.raises(ValidationError):
        candle(**changes)


def test_provenance_is_bounded_temporal_and_evidence_bound() -> None:
    item = provenance()

    assert item.raw_evidence_sha256 == RAW_EVIDENCE_SHA256
    with pytest.raises(ValidationError):
        provenance(provider="broker-routing")
    with pytest.raises(ValidationError):
        provenance(raw_evidence_sha256="A" * 64)
    with pytest.raises(ValidationError):
        provenance(fetched_at=OPEN + timedelta(minutes=1))
    with pytest.raises(ValidationError):
        provenance(schema_version="")


def test_snapshot_normalizes_candles_in_chronological_order() -> None:
    later = candle(open_time=OPEN + timedelta(minutes=1), close=Decimal("102"), high=Decimal("103"))
    ordered = snapshot(later, candle())

    assert tuple(item.open_time for item in ordered.candles) == (OPEN, OPEN + timedelta(minutes=1))


def test_snapshot_rejects_duplicate_candle_identities_without_overwriting() -> None:
    with pytest.raises(ValidationError, match="duplicate"):
        snapshot(candle(), candle())


def test_continuity_reports_exact_missing_intervals_without_fabrication() -> None:
    first = candle()
    third = candle(open_time=OPEN + timedelta(minutes=2))

    continuity = MarketContinuity.analyze((third, first), MarketTimeframe.ONE_MINUTE)

    assert continuity.duplicate_open_times == ()
    assert continuity.missing_open_times == (OPEN + timedelta(minutes=1),)
    assert continuity.is_continuous is False


def test_continuity_explicitly_reports_duplicate_identities() -> None:
    continuity = MarketContinuity.analyze((candle(), candle()), MarketTimeframe.ONE_MINUTE)

    assert continuity.duplicate_open_times == (OPEN,)


def test_continuity_reports_each_duplicate_timestamp_once() -> None:
    continuity = MarketContinuity.analyze(
        (candle(), candle(), candle()),
        MarketTimeframe.ONE_MINUTE,
    )

    assert continuity.duplicate_open_times == (OPEN,)


@pytest.mark.parametrize("field", ["duplicate_open_times", "missing_open_times"])
def test_continuity_rejects_unaligned_issue_times_from_python_and_json(
    field: str,
) -> None:
    unaligned = OPEN + timedelta(seconds=1)
    with pytest.raises(ValidationError, match="aligned"):
        MarketContinuity(timeframe=MarketTimeframe.ONE_MINUTE, **{field: (unaligned,)})

    payload = {
        "timeframe": MarketTimeframe.ONE_MINUTE.value,
        "duplicate_open_times": [],
        "missing_open_times": [],
    }
    payload[field] = [unaligned.isoformat()]
    with pytest.raises(ValidationError, match="aligned"):
        MarketContinuity.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize(
    "issue_times",
    [
        (OPEN + timedelta(minutes=1), OPEN),
        (OPEN, OPEN),
    ],
)
def test_continuity_rejects_unsorted_or_duplicate_issue_times(
    issue_times: tuple[datetime, ...],
) -> None:
    with pytest.raises(ValidationError, match="sorted and unique"):
        MarketContinuity(
            timeframe=MarketTimeframe.ONE_MINUTE,
            missing_open_times=issue_times,
        )


def test_continuity_wire_schema_is_bounded_unique_and_documents_alignment() -> None:
    generated = json.loads(
        (
            Path(__file__).parents[2]
            / "generated/domain/json-schema/MarketContinuity.json"
        ).read_text()
    )
    for schema in (MarketContinuity.model_json_schema(), generated):
        for field in ("duplicate_open_times", "missing_open_times"):
            assert schema["properties"][field]["maxItems"] == 4096
            assert schema["properties"][field]["uniqueItems"] is True
        assert schema["x-temporal-invariants"] == [
            "duplicate_open_times and missing_open_times are sorted and unique",
            "all issue timestamps are UTC and aligned to timeframe",
        ]


def test_continuity_rejects_mixed_instrument_series() -> None:
    other = InstrumentId("ETHUSDT", ProductType.CRYPTO_SPOT, "BINANCE")

    with pytest.raises(ValueError, match="instrument"):
        MarketContinuity.analyze(
            (candle(), candle(instrument=other, open_time=OPEN + timedelta(minutes=1))),
            MarketTimeframe.ONE_MINUTE,
        )


def test_continuity_rejects_gap_beyond_bounded_issue_budget() -> None:
    beyond_budget = candle(open_time=OPEN + timedelta(minutes=4_098))

    with pytest.raises(ValueError, match="supported range"):
        MarketContinuity.analyze(
            (candle(), beyond_budget),
            MarketTimeframe.ONE_MINUTE,
        )


def test_continuity_rejects_non_candle_input() -> None:
    with pytest.raises(ValueError, match="MarketCandle"):
        MarketContinuity.analyze((object(),), MarketTimeframe.ONE_MINUTE)  # type: ignore[arg-type]


def test_continuity_rejects_candle_with_different_timeframe() -> None:
    hourly = candle(timeframe=MarketTimeframe.ONE_HOUR)

    with pytest.raises(ValueError, match="requested timeframe"):
        MarketContinuity.analyze((hourly,), MarketTimeframe.ONE_MINUTE)


def test_continuity_rejects_candle_series_beyond_budget() -> None:
    repeated = (candle(),) * 4_097

    with pytest.raises(ValueError, match="candle series"):
        MarketContinuity.analyze(repeated, MarketTimeframe.ONE_MINUTE)


def test_snapshot_rejects_point_in_time_leakage() -> None:
    not_yet_observed = candle(open_time=OPEN + timedelta(minutes=2))

    with pytest.raises(ValidationError, match="observed_at"):
        snapshot(not_yet_observed)


def test_snapshot_rejects_unclosed_max_datetime_without_overflow() -> None:
    maximum_open = datetime(9999, 12, 31, tzinfo=UTC)
    final_candle = candle(
        open_time=maximum_open,
        timeframe=MarketTimeframe.ONE_DAY,
    )
    final_provenance = provenance(
        observed_at=datetime.max.replace(tzinfo=UTC),
        fetched_at=datetime.max.replace(tzinfo=UTC),
    )

    with pytest.raises(ValidationError, match="observed_at"):
        snapshot(
            final_candle,
            timeframe=MarketTimeframe.ONE_DAY,
            provenance=final_provenance,
            known_at=datetime.max.replace(tzinfo=UTC),
        )


def test_snapshot_requires_matching_normalization_versions() -> None:
    with pytest.raises(ValidationError, match="normalization_version"):
        snapshot(normalization_version="market-normalization-v2")


def test_snapshot_rejects_candle_with_different_instrument() -> None:
    other = InstrumentId("ETHUSDT", ProductType.CRYPTO_SPOT, "BINANCE")

    with pytest.raises(ValidationError, match="instrument and timeframe"):
        snapshot(candle(instrument=other))


def test_snapshot_rejects_candle_with_different_timeframe() -> None:
    with pytest.raises(ValidationError, match="instrument and timeframe"):
        snapshot(candle(timeframe=MarketTimeframe.ONE_HOUR))


def test_snapshot_rejects_known_at_before_fetch_time() -> None:
    with pytest.raises(ValidationError, match="known_at"):
        snapshot(known_at=OPEN + timedelta(minutes=2))


def test_snapshot_exposes_derived_continuity() -> None:
    item = snapshot(
        candle(),
        candle(
            open_time=OPEN + timedelta(minutes=1),
            close=Decimal("102"),
            high=Decimal("103"),
        ),
    )

    assert item.continuity.is_continuous is True


def test_snapshot_schema_declares_point_in_time_invariants() -> None:
    assert MarketSnapshot.model_json_schema()["x-temporal-invariants"] == [
        "candles[*].instrument == instrument",
        "candles[*].timeframe == timeframe",
        "candles[*].open_time + timeframe.interval <= provenance.observed_at",
        "provenance.observed_at <= provenance.fetched_at <= known_at",
        "normalization_version == provenance.normalization_version",
    ]


def test_market_instrument_wire_schema_is_strict_and_bounded() -> None:
    instrument_schema = MarketCandle.model_json_schema()["properties"]["instrument"]

    assert instrument_schema["additionalProperties"] is False
    assert instrument_schema["required"] == ["symbol", "product_type", "venue"]
    for field in ("symbol", "venue"):
        assert instrument_schema["properties"][field]["maxLength"] == 32
        assert instrument_schema["properties"][field]["pattern"]


@pytest.mark.parametrize(
    "instrument",
    [
        "BTCUSDT",
        {"symbol": "btcusdt", "product_type": "crypto_spot", "venue": "BINANCE"},
        {"symbol": "BTCUSDT", "product_type": "crypto_spot", "venue": "binance"},
        {"symbol": " BTCUSDT ", "product_type": "crypto_spot", "venue": "BINANCE"},
        {"symbol": "BTCUSDT", "product_type": "CRYPTO_SPOT", "venue": "BINANCE"},
        {"symbol": "BTCUSDT", "product_type": "crypto_spot"},
        {
            "symbol": "BTCUSDT",
            "product_type": "crypto_spot",
            "venue": "BINANCE",
            "extra": "rejected",
        },
    ],
)
def test_market_json_rejects_noncanonical_instrument_wire_inputs(
    instrument: object,
) -> None:
    candle_payload = candle().model_dump(mode="json")
    candle_payload["instrument"] = instrument
    with pytest.raises(ValidationError):
        MarketCandle.model_validate_json(json.dumps(candle_payload))

    snapshot_payload = snapshot().model_dump(mode="json")
    snapshot_payload["candles"][0]["instrument"] = instrument
    with pytest.raises(ValidationError):
        MarketSnapshot.model_validate_json(json.dumps(snapshot_payload))


def test_market_json_accepts_canonical_instrument_wire_input() -> None:
    expected_candle = candle()
    assert MarketCandle.model_validate_json(expected_candle.model_dump_json()) == expected_candle

    expected_snapshot = snapshot()
    assert (
        MarketSnapshot.model_validate_json(expected_snapshot.model_dump_json())
        == expected_snapshot
    )


def test_market_decimal_wire_schemas_match_runtime_bounds() -> None:
    generated_root = Path(__file__).parents[2] / "generated/domain/json-schema"
    generated_candle = json.loads((generated_root / "MarketCandle.json").read_text())
    generated_snapshot = json.loads((generated_root / "MarketSnapshot.json").read_text())
    property_sets = (
        MarketCandle.model_json_schema()["properties"],
        MarketSnapshot.model_json_schema()["$defs"]["MarketCandle"]["properties"],
        generated_candle["properties"],
        generated_snapshot["$defs"]["MarketCandle"]["properties"],
    )
    valid_positive = [
        "1",
        "9" * 128,
        "1" + "0" * 127,
        "1.25",
        "0." + "0" * 127 + "1",
        "0." + "1" * 128,
    ]
    invalid_positive = [
        "0",
        "-1",
        "9" * 129,
        "1" + "0" * 128,
        "1." + "1" * 128,
        "0." + "0" * 128 + "1",
        "0." + "1" * 129,
        "1e127",
    ]

    for properties in property_sets:
        for field in ("open", "high", "low", "close"):
            field_schema = properties[field]
            assert all(
                wire_schema_accepts_string(field_schema, value)
                for value in valid_positive
            )
            assert all(
                not wire_schema_accepts_string(field_schema, value)
                for value in invalid_positive
            )

        volume_schema = properties["volume"]
        assert wire_schema_accepts_string(volume_schema, "0")
        assert all(
            wire_schema_accepts_string(volume_schema, value)
            for value in valid_positive
        )
        assert all(
            not wire_schema_accepts_string(volume_schema, value)
            for value in [
                "-1",
                "9" * 129,
                "0." + "0" * 128 + "1",
                "1e127",
            ]
        )


def test_snapshot_canonical_bytes_and_digest_are_permutation_invariant() -> None:
    first = candle()
    second = candle(open_time=OPEN + timedelta(minutes=1), close=Decimal("102"), high=Decimal("103"))

    first_snapshot = snapshot(first, second)
    reordered_snapshot = snapshot(second, first)

    assert first_snapshot.canonical_payload_bytes == reordered_snapshot.canonical_payload_bytes
    assert first_snapshot.digest == reordered_snapshot.digest
    assert first_snapshot.digest == first_snapshot.snapshot_digest


def test_snapshot_digest_changes_for_candle_or_provenance_mutation() -> None:
    original = snapshot()
    changed_candle = snapshot(candle(close=Decimal("100"), high=Decimal("102")))
    changed_provenance = snapshot(provenance=provenance(provider="public-feed-b"))

    assert original.digest != changed_candle.digest
    assert original.digest != changed_provenance.digest
