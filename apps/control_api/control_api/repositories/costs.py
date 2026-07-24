from __future__ import annotations

import json
from heapq import nlargest
from pathlib import Path

from trading_control.db import DatabaseSettings, connect

from ..contracts import CostEvidenceQuality, CostSession, CostSummary
from ..normalization import normalize_symbol_list
from ._legacy_files import LegacyFileError, iter_directory_candidates, iter_jsonl


MAX_COST_DIRECTORY_ENTRIES = 4096
MAX_COST_CANDIDATES = 4096
MAX_COST_SESSIONS = 20
MAX_COST_JSONL_BYTES = 2 * 1024 * 1024
MAX_COST_JSONL_LINE_BYTES = 64 * 1024
MAX_COST_JSONL_RECORDS = 10_000


class LegacyCostRepository:
    def __init__(self, data_root: Path) -> None:
        self.data_root = data_root

    def get(self) -> CostSummary:
        sessions: list[CostSession] = []
        directory = self.data_root / ".dexter" / "scratchpad"
        sources = nlargest(
            MAX_COST_SESSIONS,
            iter_directory_candidates(
                directory,
                prefix="",
                suffix=".jsonl",
                max_entries=MAX_COST_DIRECTORY_ENTRIES,
                max_candidates=MAX_COST_CANDIDATES,
            ),
        )
        total_llm = 0
        total_tools = 0
        for source in sources:
            steps = llm_calls = tool_calls = decisions = 0
            symbols: list[str] = []
            try:
                for _, raw in iter_jsonl(
                    source,
                    max_bytes=MAX_COST_JSONL_BYTES,
                    max_line_bytes=MAX_COST_JSONL_LINE_BYTES,
                    max_records=MAX_COST_JSONL_RECORDS,
                ):
                    try:
                        value = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(value, dict):
                        continue
                    steps += 1
                    event_type = value.get("type")
                    llm_calls += int(event_type == "llm_call")
                    tool_calls += int(event_type == "tool_result")
                    decisions += int(event_type in {"decision", "final_decision"})
                    if not symbols and isinstance(value.get("symbols"), list):
                        symbols = normalize_symbol_list(value["symbols"])
            except LegacyFileError:
                continue
            sessions.append(
                CostSession(
                    session=source.stem,
                    symbols=symbols,
                    steps=steps,
                    llm_calls=llm_calls,
                    tool_calls=tool_calls,
                    decisions=decisions,
                    estimated_cost=round(llm_calls * 0.005, 6),
                )
            )
            total_llm += llm_calls
            total_tools += tool_calls
        if total_llm:
            return CostSummary(
                evidence_quality=CostEvidenceQuality.ESTIMATED,
                currency="USD",
                total_sessions=len(sessions),
                total_llm_calls=total_llm,
                total_tool_calls=total_tools,
                amount=round(total_llm * 0.005, 6),
                sessions=sessions,
                note="Estimated from observed legacy llm_call events; token accounting is unavailable.",
            )
        return CostSummary(
            evidence_quality=CostEvidenceQuality.UNKNOWN,
            currency="USD",
            total_sessions=len(sessions),
            total_llm_calls=None,
            total_tool_calls=total_tools if sessions else None,
            amount=None,
            sessions=sessions,
            note="No complete token or provider billing evidence is available.",
        )


class PostgresCostRepository:
    def __init__(self, settings: DatabaseSettings) -> None:
        self.settings = settings

    def get(self) -> CostSummary:
        with connect(self.settings, read_only=True) as connection:
            summary = connection.execute(
                """
                SELECT cost_summary_id,evidence_quality,currency,total_sessions,
                  total_llm_calls,total_tool_calls,amount
                FROM cost_summaries ORDER BY ingested_at DESC,cost_summary_id DESC LIMIT 1
                """
            ).fetchone()
            if summary is None:
                return CostSummary(
                    evidence_quality=CostEvidenceQuality.UNKNOWN, currency="USD",
                    total_sessions=0, total_llm_calls=None, total_tool_calls=None,
                    amount=None, sessions=[],
                    note="No complete token or provider billing evidence is available.",
                )
            rows = connection.execute(
                """
                SELECT cs.session,cs.steps,cs.llm_calls,cs.tool_calls,cs.decisions,
                  cs.estimated_cost,
                  COALESCE(array_agg(a.symbol ORDER BY a.symbol)
                    FILTER (WHERE a.symbol IS NOT NULL),ARRAY[]::text[])
                FROM cost_sessions cs
                LEFT JOIN cost_session_assets csa
                  ON csa.cost_session_id=cs.cost_session_id
                LEFT JOIN assets a ON a.asset_id=csa.asset_id
                WHERE cs.cost_summary_id=%s
                GROUP BY cs.cost_session_id,cs.session,cs.steps,cs.llm_calls,
                  cs.tool_calls,cs.decisions,cs.estimated_cost
                ORDER BY cs.session DESC
                """,
                (summary[0],),
            ).fetchall()
        sessions = [
            CostSession(
                session=row[0], symbols=normalize_symbol_list(row[6]), steps=row[1], llm_calls=row[2],
                tool_calls=row[3], decisions=row[4], estimated_cost=float(row[5]),
            )
            for row in rows
        ]
        quality = CostEvidenceQuality(summary[1])
        note = (
            "Estimated from observed legacy llm_call events; token accounting is unavailable."
            if quality is CostEvidenceQuality.ESTIMATED
            else "No complete token or provider billing evidence is available."
        )
        return CostSummary(
            evidence_quality=quality, currency=summary[2],
            total_sessions=summary[3], total_llm_calls=summary[4],
            total_tool_calls=summary[5],
            amount=float(summary[6]) if summary[6] is not None else None,
            sessions=sessions, note=note,
        )
