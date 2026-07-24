from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from services.semantic_input_refresher import main
from packages.runtime_release.semantic import SEMANTIC_INPUT_ROOT


NOW = datetime(2026, 7, 12, 18, 30, 1, tzinfo=UTC)


def _fixture_sources(tmp_path: Path) -> tuple[Path, Path]:
    reports = tmp_path / "reports"
    macro = tmp_path / "memory" / "macro"
    reports.mkdir(parents=True)
    macro.mkdir(parents=True)
    for prefix in ("macro", "sentiment", "onchain"):
        payload = (
            {"regime": "neutral", "regime_confidence": 0.5}
            if prefix == "macro" else {"assets": {"BTC": {}}, "source": "fixture"}
        )
        (reports / f"{prefix}_report_20260712_180000.json").write_text(json.dumps(payload))
        (reports / f"{prefix}_report_20260712_183000.json").write_text(json.dumps(payload))
    for name in ("fred_cache.json", "yf_macro_cache.json", "coingecko_global_cache.json"):
        path = macro / name
        path.write_text(json.dumps({"name": name}))
        os.utime(path, (NOW.timestamp(), NOW.timestamp()))
    (reports / ".env").write_text("SECRET=forbidden")
    (macro / "trading.db").write_text("forbidden")
    return reports, macro


def test_refresher_selects_only_latest_structured_reports_and_three_named_caches(
    tmp_path: Path, monkeypatch,
) -> None:
    reports, macro = _fixture_sources(tmp_path)
    calls: list[dict[str, object]] = []

    def fake_builder(**kwargs):
        calls.append(kwargs)
        return type("Result", (), {"plan_digest": "d" * 64})()

    monkeypatch.setattr(main, "REPORTS_SOURCE_ROOT", reports)
    monkeypatch.setattr(main, "MACRO_SOURCE_ROOT", macro)
    monkeypatch.setattr(main, "build_semantic_manifest", fake_builder)

    result = main.refresh(clock=lambda: NOW, apply=False)

    assert result.plan_digest == "d" * 64
    assert len(calls) == 1
    sources = calls[0]["sources"]
    assert {path.name for path in sources.values()} == {
        "macro_report_20260712_183000.json",
        "sentiment_report_20260712_183000.json",
        "onchain_report_20260712_183000.json",
        "fred_cache.json", "yf_macro_cache.json", "coingecko_global_cache.json",
    }
    assert not calls[0]["apply"]
    assert calls[0]["generated_at"] == NOW
    assert calls[0]["validity_minutes"] == 30
    assert calls[0]["destination_root"] == SEMANTIC_INPUT_ROOT
    assert set(calls[0]["expected_source_attestations"]) == set(sources)


def test_apply_reuses_exact_timestamp_and_approved_plan_digest(tmp_path: Path, monkeypatch) -> None:
    reports, macro = _fixture_sources(tmp_path)
    calls: list[dict[str, object]] = []

    def fake_builder(**kwargs):
        calls.append(kwargs)
        return type("Result", (), {"plan_digest": "a" * 64})()

    monkeypatch.setattr(main, "REPORTS_SOURCE_ROOT", reports)
    monkeypatch.setattr(main, "MACRO_SOURCE_ROOT", macro)
    monkeypatch.setattr(main, "build_semantic_manifest", fake_builder)
    monkeypatch.setattr(main.os, "geteuid", lambda: 0)

    main.refresh(clock=lambda: NOW, apply=True)

    assert len(calls) == 2
    assert calls[0]["generated_at"] == calls[1]["generated_at"] == NOW
    assert calls[0]["apply"] is False
    assert calls[1]["apply"] is True
    assert calls[1]["approved_plan_digest"] == "a" * 64


def test_missing_or_ambiguous_structured_report_fails_before_builder(tmp_path: Path, monkeypatch) -> None:
    reports, macro = _fixture_sources(tmp_path)
    (reports / "macro_report_20260712_183000.json").unlink()
    (reports / "macro_report_bad.json").write_text("{}")
    called = False

    def fake_builder(**_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(main, "REPORTS_SOURCE_ROOT", reports)
    monkeypatch.setattr(main, "MACRO_SOURCE_ROOT", macro)
    monkeypatch.setattr(main, "build_semantic_manifest", fake_builder)
    (reports / "macro_report_20260712_180000.json").unlink()

    with pytest.raises(main.SemanticRefreshError):
        main.refresh(clock=lambda: NOW)
    assert called is False


def test_non_root_apply_fails_before_reading_source_directories(monkeypatch) -> None:
    reads: list[object] = []
    monkeypatch.setattr(main.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(
        main.os, "scandir",
        lambda *args, **_kwargs: reads.append(args) or (_ for _ in ()).throw(AssertionError("source read")),
    )

    with pytest.raises(main.SemanticRefreshError):
        main.refresh(clock=lambda: NOW, apply=True)
    assert reads == []


@pytest.mark.parametrize("symlink_position", ("leaf", "ancestor"))
def test_symlinked_reports_source_root_fails_before_builder(
    tmp_path: Path, monkeypatch, symlink_position: str,
) -> None:
    actual, macro = _fixture_sources(tmp_path)
    if symlink_position == "leaf":
        reports_root = tmp_path / "reports-link"
        reports_root.symlink_to(actual, target_is_directory=True)
    else:
        ancestor = tmp_path / "ancestor-link"
        ancestor.symlink_to(actual.parent, target_is_directory=True)
        reports_root = ancestor / actual.name
    called = False

    def fake_builder(**_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(main, "REPORTS_SOURCE_ROOT", reports_root)
    monkeypatch.setattr(main, "MACRO_SOURCE_ROOT", macro)
    monkeypatch.setattr(main, "build_semantic_manifest", fake_builder)

    with pytest.raises(main.SemanticRefreshError):
        main.refresh(clock=lambda: NOW)
    assert called is False


@pytest.mark.parametrize("condition", ("future", "stale", "invalid_schema"))
def test_future_stale_or_invalid_structured_report_never_mints_authority(
    tmp_path: Path, monkeypatch, condition: str,
) -> None:
    reports, macro = _fixture_sources(tmp_path)
    if condition == "future":
        (reports / "macro_report_20260712_190000.json").write_text(
            json.dumps({"regime": "neutral", "regime_confidence": 0.5})
        )
    elif condition == "stale":
        for path in reports.glob("*_report_*.json"):
            path.unlink()
        for prefix in ("macro", "sentiment", "onchain"):
            payload = {"regime": "neutral", "regime_confidence": 0.5} if prefix == "macro" else {"assets": {}}
            (reports / f"{prefix}_report_20260712_120000.json").write_text(json.dumps(payload))
    else:
        (reports / "sentiment_report_20260712_183000.json").write_text('{"assets":[]}')
    called = False

    def fake_builder(**_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(main, "REPORTS_SOURCE_ROOT", reports)
    monkeypatch.setattr(main, "MACRO_SOURCE_ROOT", macro)
    monkeypatch.setattr(main, "build_semantic_manifest", fake_builder)

    with pytest.raises(main.SemanticRefreshError):
        main.refresh(clock=lambda: NOW)
    assert called is False


def test_stale_invalid_historical_report_is_ignored_when_latest_is_valid(
    tmp_path: Path, monkeypatch,
) -> None:
    reports, macro = _fixture_sources(tmp_path)
    (reports / "macro_report_20260712_120000.json").write_text("not-json")
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(main, "REPORTS_SOURCE_ROOT", reports)
    monkeypatch.setattr(main, "MACRO_SOURCE_ROOT", macro)
    monkeypatch.setattr(
        main, "build_semantic_manifest",
        lambda **kwargs: calls.append(kwargs) or type("Result", (), {"plan_digest": "d" * 64})(),
    )

    main.refresh(clock=lambda: NOW)

    assert len(calls) == 1
    assert calls[0]["sources"]["macro_report"].name == "macro_report_20260712_183000.json"
