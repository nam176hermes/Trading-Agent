from __future__ import annotations

import json
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Iterator, Mapping

from trading_control.db import DatabaseSettings, connect

from ..contracts import DecisionAction, DecisionRecord, DecisionSignals, PaginatedResponse
from ..normalization import normalize_action, normalize_decision
from ._legacy_files import LegacyFileError, iter_jsonl


MAX_DECISION_PAGE_SIZE = 200
MAX_DECISION_WINDOW = 20_000
# The reviewed 16,653-record legacy ledger is about 30 MiB.
MAX_DECISION_JSONL_BYTES = 64 * 1024 * 1024
MAX_DECISION_JSONL_LINE_BYTES = 64 * 1024
MAX_DECISION_JSONL_RECORDS = 20_000


def validate_page_window(page: int, page_size: int) -> None:
    if page < 1 or page_size < 1 or page_size > MAX_DECISION_PAGE_SIZE:
        raise ValueError("decision page is invalid")
    if page * page_size > MAX_DECISION_WINDOW:
        raise ValueError("decision page window exceeds the maximum")


class LegacyDecisionRepository:
    def __init__(self, data_root: Path) -> None:
        self.source = data_root / "memory" / "decisions.jsonl"
        self.invalid_line_count = 0

    def _records(self) -> Iterator[DecisionRecord]:
        self.invalid_line_count = 0
        records: list[DecisionRecord] = []
        try:
            for line_number, raw_line in iter_jsonl(
                self.source,
                max_bytes=MAX_DECISION_JSONL_BYTES,
                max_line_bytes=MAX_DECISION_JSONL_LINE_BYTES,
                max_records=MAX_DECISION_JSONL_RECORDS,
            ):
                try:
                    value = json.loads(raw_line)
                    if not isinstance(value, Mapping):
                        raise ValueError("decision must be an object")
                    records.append(normalize_decision(value, line_number=line_number, raw_line=raw_line))
                except (ValueError, TypeError, json.JSONDecodeError):
                    self.invalid_line_count += 1
        except LegacyFileError:
            self.invalid_line_count += 1
            return
        yield from records

    def list(
        self,
        *,
        page: int,
        page_size: int,
        asset: str | None = None,
        action: DecisionAction | str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> PaginatedResponse:
        validate_page_window(page, page_size)
        canonical_action = normalize_action(action) if isinstance(action, str) else action
        normalized_asset = asset.upper() if asset else None
        end = page * page_size
        selected: deque[DecisionRecord] = deque(maxlen=end)
        total = 0
        for record in self._records():
            if normalized_asset and record.asset != normalized_asset:
                continue
            if canonical_action and record.action is not canonical_action:
                continue
            if date_from and record.decision_at < date_from:
                continue
            if date_to and record.decision_at > date_to:
                continue
            total += 1
            selected.append(record)
        newest = list(reversed(selected))
        start = (page - 1) * page_size
        return PaginatedResponse(
            items=newest[start : start + page_size],
            page=page,
            page_size=page_size,
            total=total,
            has_next=end < total,
        )

    def get(self, decision_id: str) -> DecisionRecord | None:
        for record in self._records():
            if record.decision_id == decision_id:
                return record
        return None


class PostgresDecisionRepository:
    def __init__(self, settings: DatabaseSettings) -> None:
        self.settings = settings

    @staticmethod
    def _record(row) -> DecisionRecord:
        if row[6] is None:
            raise ValueError("canonical decision price is explicitly unknown")
        return DecisionRecord(
            decision_id=row[0], asset=row[1], action=DecisionAction(row[2]),
            confidence=float(row[3]), decision_at=row[4],
            price_at_decision=float(row[6]), reflected=False,
            signals=DecisionSignals(
                symbol=row[5], close=float(row[7]), rsi_14=float(row[8]),
                macd_line=float(row[9]), macd_signal_line=float(row[10]),
                macd_histogram=float(row[11]), sma_200=float(row[12]),
                price_vs_sma200=row[13], volume_24h=float(row[14]),
                volume_30d_avg=float(row[15]), volume_trend_ratio=float(row[16]),
                signal=row[17], calculated_at=row[18],
            ),
            report_snippet=row[19] or "",
        )

    @staticmethod
    def _select() -> str:
        return """
        SELECT d.decision_id,a.symbol,d.action,d.confidence,d.as_of,
          s.symbol,d.price_at_decision,s.close,s.rsi_14,s.macd_line,s.macd_signal_line,
          s.macd_histogram,s.sma_200,s.price_vs_sma200,s.volume_24h,
          s.volume_30d_avg,s.volume_trend_ratio,s.signal,s.calculated_at,
          d.report_snippet
        FROM decisions d JOIN assets a ON a.asset_id=d.asset_id
        JOIN decision_signal_snapshots s ON s.decision_id=d.decision_id
        """

    def list(
        self,
        *,
        page: int,
        page_size: int,
        asset: str | None = None,
        action: DecisionAction | str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> PaginatedResponse:
        validate_page_window(page, page_size)
        canonical_action = normalize_action(action) if isinstance(action, str) else action
        clauses: list[str] = []
        params: list[object] = []
        if asset:
            clauses.append("a.symbol=%s")
            params.append(asset.upper())
        if canonical_action:
            clauses.append("d.action=%s")
            params.append(canonical_action.value)
        if date_from:
            clauses.append("d.as_of>=%s")
            params.append(date_from)
        if date_to:
            clauses.append("d.as_of<=%s")
            params.append(date_to)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with connect(self.settings, read_only=True) as connection:
            total = connection.execute(
                "SELECT count(*) FROM decisions d JOIN assets a ON a.asset_id=d.asset_id" + where,
                params,
            ).fetchone()[0]
            rows = connection.execute(
                self._select() + where
                + " ORDER BY d.as_of DESC,d.decision_id DESC LIMIT %s OFFSET %s",
                (*params, page_size, (page - 1) * page_size),
            ).fetchall()
        return PaginatedResponse(
            items=[self._record(row) for row in rows], page=page,
            page_size=page_size, total=total, has_next=page * page_size < total,
        )

    def get(self, decision_id: str) -> DecisionRecord | None:
        with connect(self.settings, read_only=True) as connection:
            row = connection.execute(
                self._select() + " WHERE d.decision_id=%s", (decision_id,)
            ).fetchone()
        return self._record(row) if row else None
