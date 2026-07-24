import hashlib

from trading_control.identity import chunk_ranges, record_key


def test_record_key_matches_adr_and_changes_with_provenance() -> None:
    expected = hashlib.sha256(
        b"decisions\0" + (b"a" * 64) + b"\0" + b"7" + b"\0phase3-v1"
    ).hexdigest()
    assert record_key("decisions", "a" * 64, 7, "phase3-v1") == expected
    assert record_key("decisions", "b" * 64, 7, "phase3-v1") != expected
    assert record_key("decisions", "a" * 64, 8, "phase3-v1") != expected
    assert record_key("decisions", "a" * 64, 7, "phase3-v2") != expected


def test_chunk_ranges_are_one_based_fixed_and_deterministic() -> None:
    assert list(chunk_ranges(0)) == []
    assert list(chunk_ranges(1001)) == [(1, 500), (501, 1000), (1001, 1001)]
