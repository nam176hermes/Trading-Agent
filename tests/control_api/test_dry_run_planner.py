from __future__ import annotations

import json
import sqlite3

from trading_control.planner import plan_migration


def make_source(tmp_path):
    (tmp_path / "reports").mkdir()
    (tmp_path / "memory").mkdir()
    (tmp_path / ".dexter" / "scratchpad").mkdir(parents=True)
    report = {
        "timestamp": "2026-06-25T04:54:37Z",
        "assets": [{"symbol": "BTC", "current_price": 100.0}],
    }
    (tmp_path / "reports" / "report_valid.json").write_text(json.dumps(report))
    (tmp_path / "reports" / "report_invalid.json").write_text("{bad")
    decision = {
        "ticker": "BTC", "suggestion": "STRONG SELL", "confidence": 0.5,
        "stored_at": "2026-06-25T04:54:37Z", "price_at_decision": 100.0,
        "signals": {},
    }
    (tmp_path / "memory" / "decisions.jsonl").write_text(json.dumps(decision) + "\n")
    database = sqlite3.connect(tmp_path / "memory" / "trading.db")
    database.execute("CREATE TABLE signals (id INTEGER, symbol TEXT)")
    database.execute("INSERT INTO signals VALUES (1, 'BTC')")
    database.commit()
    database.close()
    return tmp_path


def test_fixture_dry_run_is_deterministic_and_explains_invalids(tmp_path) -> None:
    source = make_source(tmp_path)
    first = plan_migration(source)
    second = plan_migration(source)
    assert first.inventory_hash == second.inventory_hash
    assert first.counts["report_files_discovered"] == 2
    assert first.counts["valid_reports"] == 1
    assert first.counts["invalid_reports"] == 1
    assert first.counts["decisions_seen"] == 1
    assert first.counts["sqlite_signals"] == 1
    assert first.counts["capabilities"] == 9
    assert first.records_updated == 0
    assert first.errors[0].code == "INVALID_JSON"
    assert "{bad" not in first.errors[0].message
