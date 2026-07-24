import json

import pytest

from control_api.contracts import DecisionAction
from control_api.repositories.decisions import LegacyDecisionRepository


def decision(asset: str, action: str, timestamp: str, confidence: float = 0.5) -> dict[str, object]:
    return {
        "ticker": asset,
        "suggestion": action,
        "confidence": confidence,
        "stored_at": timestamp,
        "date": timestamp[:10],
        "price_at_decision": 10.0,
        "signals": {"rsi_14": 50.0},
        "report_snippet": "fixture",
        "reflected": False,
    }


def test_decision_repository_paginates_filters_and_skips_invalid_lines(tmp_path) -> None:
    memory = tmp_path / "memory"
    memory.mkdir()
    records = [
        decision("BTC", "BUY", "2026-06-01T00:00:00Z"),
        decision("ETH", "STRONG SELL", "2026-06-02T00:00:00Z"),
        decision("BTC", "HOLD", "2026-06-03T00:00:00Z"),
    ]
    (memory / "decisions.jsonl").write_text(
        "\n".join([json.dumps(records[0]), "{bad", json.dumps(records[1]), json.dumps(records[2])]) + "\n",
        encoding="utf-8",
    )
    repository = LegacyDecisionRepository(tmp_path)

    page = repository.list(page=1, page_size=1, asset="BTC")

    assert page.total == 2
    assert page.has_next is True
    assert page.items[0].action is DecisionAction.HOLD
    assert page.items[0].confidence == 0.5
    assert repository.invalid_line_count == 1


def test_decision_repository_detail_uses_stable_generated_id(tmp_path) -> None:
    memory = tmp_path / "memory"
    memory.mkdir()
    (memory / "decisions.jsonl").write_text(
        json.dumps(decision("ADA", "SELL", "2026-06-25T04:54:37Z")) + "\n",
        encoding="utf-8",
    )
    repository = LegacyDecisionRepository(tmp_path)

    first = repository.list(page=1, page_size=10).items[0]

    assert first.decision_id.startswith("decision_")
    assert repository.get(first.decision_id) == first
    assert LegacyDecisionRepository(tmp_path).list(page=1, page_size=10).items[0].decision_id == first.decision_id


def test_decision_repository_rejects_oversized_direct_page_window(tmp_path) -> None:
    (tmp_path / "memory").mkdir()
    repository = LegacyDecisionRepository(tmp_path)

    with pytest.raises(ValueError, match="page window"):
        repository.list(page=101, page_size=200)


def test_decision_repository_skips_an_unsafe_or_oversized_jsonl_source(tmp_path, monkeypatch) -> None:
    from control_api.repositories import decisions

    memory = tmp_path / "memory"
    memory.mkdir()
    source = memory / "decisions.jsonl"
    target = tmp_path / "outside.jsonl"
    target.write_text(json.dumps(decision("BTC", "BUY", "2026-06-01T00:00:00Z")) + "\n", encoding="utf-8")
    source.symlink_to(target)

    repository = LegacyDecisionRepository(tmp_path)
    assert repository.list(page=1, page_size=10).items == []

    source.unlink()
    source.write_text(json.dumps(decision("BTC", "BUY", "2026-06-01T00:00:00Z")) + "\n", encoding="utf-8")
    monkeypatch.setattr(decisions, "MAX_DECISION_JSONL_BYTES", 16)

    assert repository.list(page=1, page_size=10).items == []


def test_decision_repository_rejects_an_oversized_jsonl_line(tmp_path, monkeypatch) -> None:
    from control_api.repositories import decisions

    memory = tmp_path / "memory"
    memory.mkdir()
    source = memory / "decisions.jsonl"
    source.write_text(
        json.dumps(decision("BTC", "BUY", "2026-06-01T00:00:00Z")) + "\n" + "x" * 1024 + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(decisions, "MAX_DECISION_JSONL_LINE_BYTES", 512)

    assert LegacyDecisionRepository(tmp_path).list(page=1, page_size=10).items == []
