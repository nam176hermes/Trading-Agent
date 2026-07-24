from __future__ import annotations

import hashlib
import json
from pathlib import Path

from trading_control.phase3b_sources import (
    ProvenanceQuality,
    ReasonCode,
    analyze_phase3b_sources,
    extract_cost_session_symbols,
    extract_decision_field_evidence,
    make_asset_lineage_evidence,
)


REAL_ROOT = Path("/home/thenam176/.hermes/crypto-research")


def write_jsonl(path: Path, values: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(value, separators=(",", ":")) + "\n" for value in values),
        encoding="utf-8",
    )


def test_direct_decision_price_and_snippet_are_exact(tmp_path: Path) -> None:
    source = tmp_path / "memory" / "decisions.jsonl"
    write_jsonl(source, [{
        "ticker": "BTC", "date": "2026-07-11", "suggestion": "BUY",
        "confidence": 0.8, "price_at_decision": 123.45,
        "signals": {}, "report_snippet": "stored legacy text",
        "stored_at": "2026-07-11T00:00:01Z", "reflected": False,
    }])

    evidence = extract_decision_field_evidence(source)

    assert len(evidence) == 2
    price, snippet = evidence
    assert price.field_name == "price_at_decision"
    assert price.value == "123.45"
    assert price.quality is ProvenanceQuality.EXACT
    assert price.source_field == "price_at_decision"
    assert price.source_record_index == 1
    assert snippet.field_name == "report_snippet"
    assert snippet.value == "stored legacy text"
    assert snippet.quality is ProvenanceQuality.EXACT
    assert snippet.source_field == "report_snippet"


def test_missing_decision_fields_are_unknown_without_derivation(tmp_path: Path) -> None:
    source = tmp_path / "memory" / "decisions.jsonl"
    write_jsonl(source, [{
        "ticker": "ETH", "date": "2026-07-11", "suggestion": "HOLD",
        "confidence": 0.4, "signals": {"close": 999.0},
        "report_snippet": "", "stored_at": "2026-07-11T00:00:01Z",
        "reflected": False,
    }])

    price, snippet = extract_decision_field_evidence(source)

    assert price.value is None
    assert price.quality is ProvenanceQuality.UNKNOWN
    assert price.reason_code is ReasonCode.SOURCE_FIELD_MISSING
    assert snippet.value is None
    assert snippet.quality is ProvenanceQuality.UNKNOWN
    assert snippet.reason_code is ReasonCode.SNIPPET_SOURCE_MISSING


def test_cost_symbols_use_top_level_structured_evidence_and_stable_sort(
    tmp_path: Path,
) -> None:
    source = tmp_path / "session.jsonl"
    write_jsonl(source, [
        {"type": "session", "symbols": ["ETH", "btc", "ETH"]},
        {"type": "tool", "args": {"symbol": "SOL"}},
    ])

    evidence = extract_cost_session_symbols(source, {"BTC", "ETH", "SOL"})

    assert evidence.symbols == ("BTC", "ETH")
    assert evidence.quality is ProvenanceQuality.EXACT
    assert evidence.source_record_index == 1
    assert evidence.source_field == "symbols"
    assert evidence.unknown_symbols == ()


def test_cost_symbols_do_not_infer_from_nested_or_free_text(tmp_path: Path) -> None:
    source = tmp_path / "session.jsonl"
    write_jsonl(source, [
        {"type": "note", "text": "BTC ETH", "args": {"symbols": ["SOL"]}},
    ])

    evidence = extract_cost_session_symbols(source, {"BTC", "ETH", "SOL"})

    assert evidence.symbols == ()
    assert evidence.quality is ProvenanceQuality.UNKNOWN
    assert evidence.reason_code is ReasonCode.SYMBOL_EVIDENCE_MISSING


def test_asset_lineage_preserves_multiple_sources_and_changed_hash() -> None:
    first = make_asset_lineage_evidence(
        asset_id="crypto:spot:BTC/USDT", symbol="BTC", source_type="DECISION_JSONL",
        source_path="memory/decisions.jsonl", source_hash="a" * 64,
        source_record_index=1, source_field="ticker",
    )
    second = make_asset_lineage_evidence(
        asset_id="crypto:spot:BTC/USDT", symbol="BTC", source_type="MARKET_REPORT",
        source_path="reports/report.json", source_hash="b" * 64,
        source_record_index=1, source_field="assets[0].symbol",
    )
    changed = make_asset_lineage_evidence(
        asset_id="crypto:spot:BTC/USDT", symbol="BTC", source_type="DECISION_JSONL",
        source_path="memory/decisions.jsonl", source_hash="c" * 64,
        source_record_index=1, source_field="ticker",
    )

    assert len({first.identity, second.identity, changed.identity}) == 3
    assert first.asset_id == second.asset_id == changed.asset_id
    assert first.canonical_fingerprint == hashlib.sha256(b"BTC").hexdigest()


def test_real_phase3b_source_analysis_has_reviewed_exact_counts() -> None:
    analysis = analyze_phase3b_sources(REAL_ROOT)

    assert analysis.inventory_hash == (
        "dbc94142b6773bb5a79c7bc889e7323ca92c03e5375d0a596b679c3f01c7b4ce"
    )
    assert analysis.decision_total == 16517
    assert analysis.price_counts == {
        "EXACT": 16517, "DERIVED": 0, "LEGACY_ESTIMATED": 0, "UNKNOWN": 0,
    }
    assert analysis.snippet_counts == {
        "EXACT": 16516, "DERIVED": 0, "LEGACY_ESTIMATED": 0, "UNKNOWN": 1,
    }
    assert analysis.cost_sessions == 20
    assert analysis.cost_sessions_with_evidence == 20
    assert analysis.cost_sessions_without_evidence == 0
    assert analysis.cost_unknown_assets == ()
    assert analysis.asset_count == 17
    assert analysis.asset_lineage_rows_planned == 41039
    assert analysis.asset_source_files == 2209
