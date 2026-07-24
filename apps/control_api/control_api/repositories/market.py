from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Mapping

from trading_control.db import DatabaseSettings, connect

from ..contracts import DataFreshness, FreshnessStatus, MarketAssetSnapshot, MarketReport
from ..normalization import normalize_asset, parse_datetime
from ._legacy_files import LegacyFileError, iter_directory_candidates, read_json

LOGGER = logging.getLogger(__name__)
# The reviewed legacy root contains 4,627 mixed report-directory entries and
# 2,186 canonical market reports. Keep a finite ceiling with operational headroom.
MAX_MARKET_DIRECTORY_ENTRIES = 8192
MAX_MARKET_REPORT_CANDIDATES = 4096
MAX_MARKET_REPORT_BYTES = 512 * 1024


@dataclass(frozen=True, slots=True)
class MarketReportResult:
    report: MarketReport | None
    freshness: DataFreshness
    invalid_source_count: int


class LegacyMarketReportRepository:
    def __init__(
        self,
        data_root: Path,
        *,
        stale_after_seconds: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.data_root = data_root
        self.stale_after_seconds = stale_after_seconds
        self.clock = clock or (lambda: datetime.now(UTC))

    def latest(self) -> MarketReportResult:
        reports_dir = self.data_root / "reports"
        latest_candidate: tuple[datetime, str, list] | None = None
        invalid = 0
        try:
            for source in iter_directory_candidates(
                reports_dir,
                prefix="report_",
                suffix=".json",
                max_entries=MAX_MARKET_DIRECTORY_ENTRIES,
                max_candidates=MAX_MARKET_REPORT_CANDIDATES,
                fail_on_truncation=True,
            ):
                try:
                    value = read_json(source, max_bytes=MAX_MARKET_REPORT_BYTES)
                    if not isinstance(value, Mapping) or not isinstance(value.get("assets"), list):
                        raise ValueError("market report requires assets")
                    as_of = parse_datetime(value.get("as_of") or value.get("timestamp"))
                    assets = [normalize_asset(item) for item in value["assets"] if isinstance(item, Mapping)]
                    if len(assets) != len(value["assets"]):
                        raise ValueError("market report contains an invalid asset")
                    candidate = (as_of, source.name, assets)
                    if latest_candidate is None or candidate[0] > latest_candidate[0]:
                        latest_candidate = candidate
                except (LegacyFileError, ValueError, TypeError, json.JSONDecodeError):
                    invalid += 1
        except LegacyFileError:
            invalid += 1
            return self._no_data(FreshnessStatus.UNKNOWN, invalid)
        if invalid:
            LOGGER.warning("invalid legacy market reports skipped", extra={"invalid_source_count": invalid})
        if latest_candidate is None:
            return self._no_data(FreshnessStatus.NO_DATA, invalid)
        as_of, source_file, assets = latest_candidate
        age = max(0, int((self.clock().astimezone(UTC) - as_of).total_seconds()))
        status = FreshnessStatus.STALE if age > self.stale_after_seconds else FreshnessStatus.FRESH
        report_id = "report_" + hashlib.sha256(f"{source_file}:{as_of.isoformat()}".encode()).hexdigest()[:24]
        report = MarketReport(
            report_id=report_id,
            as_of=as_of,
            assets=assets,
            source_file=source_file,
            invalid_source_count=invalid,
        )
        return MarketReportResult(
            report=report,
            freshness=DataFreshness(
                status=status,
                as_of=as_of,
                age_seconds=age,
                stale_after_seconds=self.stale_after_seconds,
            ),
            invalid_source_count=invalid,
        )

    def _no_data(self, status: FreshnessStatus, invalid: int) -> MarketReportResult:
        return MarketReportResult(
            report=None,
            freshness=DataFreshness(
                status=status, as_of=None, age_seconds=None,
                stale_after_seconds=self.stale_after_seconds,
            ),
            invalid_source_count=invalid,
        )


class PostgresMarketReportRepository:
    def __init__(
        self,
        settings: DatabaseSettings,
        *,
        stale_after_seconds: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings
        self.stale_after_seconds = stale_after_seconds
        self.clock = clock or (lambda: datetime.now(UTC))

    def latest(self) -> MarketReportResult:
        with connect(self.settings, read_only=True) as connection:
            row = connection.execute(
                """
                SELECT report_id,as_of,source_path
                FROM market_reports
                ORDER BY as_of DESC,report_id DESC LIMIT 1
                """
            ).fetchone()
            invalid = connection.execute(
                "SELECT count(*) FROM migration_errors WHERE source_path LIKE 'reports/%'"
            ).fetchone()[0]
            if row is None:
                return self._no_data(FreshnessStatus.NO_DATA, invalid)
            asset_rows = connection.execute(
                """
                SELECT raw_evidence_ref FROM market_asset_snapshots
                WHERE report_id=%s ORDER BY source_record_index
                """,
                (row[0],),
            ).fetchall()
        assets = []
        for item in asset_rows:
            raw = item[0]
            if not isinstance(raw, str) or not raw.startswith("canonical-json:"):
                raise ValueError("PostgreSQL market snapshot lacks canonical evidence")
            assets.append(MarketAssetSnapshot.model_validate_json(raw.removeprefix("canonical-json:")))
        as_of = row[1].astimezone(UTC)
        age = max(0, int((self.clock().astimezone(UTC) - as_of).total_seconds()))
        status = FreshnessStatus.STALE if age > self.stale_after_seconds else FreshnessStatus.FRESH
        return MarketReportResult(
            report=MarketReport(
                report_id=row[0], as_of=as_of, assets=assets,
                source_file=Path(row[2]).name, invalid_source_count=invalid,
            ),
            freshness=DataFreshness(
                status=status, as_of=as_of, age_seconds=age,
                stale_after_seconds=self.stale_after_seconds,
            ),
            invalid_source_count=invalid,
        )

    def _no_data(self, status: FreshnessStatus, invalid: int) -> MarketReportResult:
        return MarketReportResult(
            report=None,
            freshness=DataFreshness(
                status=status, as_of=None, age_seconds=None,
                stale_after_seconds=self.stale_after_seconds,
            ),
            invalid_source_count=invalid,
        )
