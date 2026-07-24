import json
from datetime import UTC, datetime

from control_api.contracts import DecisionAction, FreshnessStatus
from control_api.repositories.market import LegacyMarketReportRepository


def asset(symbol: str = "BTC", suggestion: str = "STRONG SELL") -> dict[str, object]:
    return {
        "symbol": symbol,
        "current_price": 100.0,
        "price_change_24h_pct": None,
        "price_change_7d_pct": 2.0,
        "rsi_14": 50.0,
        "rsi_signal": "neutral",
        "macd_signal": "neutral",
        "price_vs_sma200": "above",
        "volume_trend": "1.0x",
        "suggestion": suggestion,
        "confidence": "high",
        "signal_conflict": False,
        "reasoning": "fixture",
        "atr_14": 1.0,
        "atr_pct": 1.0,
        "stop_method": "atr",
        "stop_note": "fixture",
        "alerts": [],
        "risk_assessment": {
            "position_size_pct": 0.0,
            "stop_loss_pct": 0.0,
            "risk_level": "LOW",
            "rationale": "fixture",
        },
    }


def write_report(directory, name: str, timestamp: str, assets: list[dict[str, object]]) -> None:
    (directory / name).write_text(
        json.dumps({"timestamp": timestamp, "assets": assets}), encoding="utf-8"
    )


def test_market_repository_selects_semantic_latest_and_normalizes_boundary(tmp_path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    write_report(reports, "report_99999999.json", "2026-01-01T00:00:00Z", [asset("ETH")])
    write_report(reports, "report_older_name.json", "2026-07-01T00:00:00Z", [asset("BTC")])
    (reports / "report_invalid.json").write_text("{bad", encoding="utf-8")
    (reports / "ta_validation_999.json").write_text('{"validations": []}', encoding="utf-8")

    result = LegacyMarketReportRepository(
        tmp_path,
        stale_after_seconds=1800,
        clock=lambda: datetime(2026, 7, 1, 1, tzinfo=UTC),
    ).latest()

    assert result.report is not None
    assert result.report.source_file == "report_older_name.json"
    assert result.report.assets[0].suggestion is DecisionAction.STRONG_SELL
    assert result.report.assets[0].price_change_24h_pct == 0.0
    assert result.freshness.status is FreshnessStatus.STALE
    assert result.invalid_source_count == 1


def test_market_repository_returns_no_data_without_valid_report(tmp_path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "report_invalid.json").write_text("{}", encoding="utf-8")

    result = LegacyMarketReportRepository(tmp_path, stale_after_seconds=1800).latest()

    assert result.report is None
    assert result.freshness.status is FreshnessStatus.NO_DATA
    assert result.invalid_source_count == 1


def test_market_repository_skips_symlinked_and_oversized_reports(tmp_path, monkeypatch) -> None:
    from control_api.repositories import market

    reports = tmp_path / "reports"
    reports.mkdir()
    target = tmp_path / "outside.json"
    write_report(target.parent, target.name, "2026-07-01T00:00:00Z", [asset()])
    (reports / "report_link.json").symlink_to(target)

    result = LegacyMarketReportRepository(tmp_path, stale_after_seconds=1800).latest()
    assert result.report is None
    assert result.invalid_source_count == 1

    (reports / "report_link.json").unlink()
    write_report(reports, "report_large.json", "2026-07-01T00:00:00Z", [asset()])
    monkeypatch.setattr(market, "MAX_MARKET_REPORT_BYTES", 16)

    result = LegacyMarketReportRepository(tmp_path, stale_after_seconds=1800).latest()
    assert result.report is None
    assert result.invalid_source_count == 1


def test_market_repository_reports_unknown_when_candidate_scan_is_truncated(tmp_path, monkeypatch) -> None:
    from control_api.repositories import market

    reports = tmp_path / "reports"
    reports.mkdir()
    write_report(reports, "report_2026-07-01.json", "2026-07-01T00:00:00Z", [asset("BTC")])
    write_report(reports, "report_2026-07-02.json", "2026-07-02T00:00:00Z", [asset("ETH")])
    monkeypatch.setattr(market, "MAX_MARKET_REPORT_CANDIDATES", 1)

    result = LegacyMarketReportRepository(tmp_path, stale_after_seconds=1800).latest()

    assert result.report is None
    assert result.freshness.status is FreshnessStatus.UNKNOWN


def test_market_repository_junk_names_cannot_misreport_a_partial_scan(tmp_path, monkeypatch) -> None:
    from control_api.repositories import market

    reports = tmp_path / "reports"
    reports.mkdir()
    write_report(reports, "report_valid.json", "2026-07-01T00:00:00Z", [asset("BTC")])
    for index in range(64):
        write_report(
            reports,
            f"report_zzzz_{index:03d}.json",
            "2026-01-01T00:00:00Z",
            [asset("ETH")],
        )
    monkeypatch.setattr(market, "MAX_MARKET_REPORT_CANDIDATES", 1)

    result = LegacyMarketReportRepository(tmp_path, stale_after_seconds=1800).latest()

    assert result.report is None
    assert result.freshness.status is FreshnessStatus.UNKNOWN


def test_market_repository_bounds_directory_entries_before_reading_candidates(tmp_path, monkeypatch) -> None:
    from control_api.repositories import market

    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "ignore.txt").write_text("ignored", encoding="utf-8")
    write_report(reports, "report_valid.json", "2026-07-01T00:00:00Z", [asset()])
    monkeypatch.setattr(market, "MAX_MARKET_DIRECTORY_ENTRIES", 1)

    result = LegacyMarketReportRepository(tmp_path, stale_after_seconds=1800).latest()

    assert result.report is None
    assert result.freshness.status is FreshnessStatus.UNKNOWN
