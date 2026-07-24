from __future__ import annotations

from pathlib import Path

from trading_control.db import DatabaseSettings, connect

from ..contracts import CapabilityEvidence, CapabilityStatus

CAPABILITIES = (
    ("file_operations", "File Operations"),
    ("terminal_shell", "Terminal & Shell"),
    ("data_processing", "Data Processing"),
    ("web_research", "Web Research"),
    ("technical_analysis", "Technical Analysis"),
    ("adversarial_debate", "Adversarial Debate"),
    ("risk_assessment", "Risk Assessment"),
    ("memory_learning", "Memory & Learning"),
    ("multi_step_planning", "Multi-Step Planning"),
)


class LegacyCapabilityRepository:
    def __init__(self, data_root: Path) -> None:
        self.data_root = data_root

    def list(self) -> list[CapabilityEvidence]:
        return [
            CapabilityEvidence(
                capability_id=identifier,
                name=name,
                status=CapabilityStatus.UNKNOWN,
                last_run_at=None,
                valid_until=None,
                benchmark_run_id=None,
                metric=None,
                threshold=None,
                evidence_ref=None,
            )
            for identifier, name in CAPABILITIES
        ]


class PostgresCapabilityRepository:
    def __init__(self, settings: DatabaseSettings) -> None:
        self.settings = settings

    def list(self) -> list[CapabilityEvidence]:
        names = dict(CAPABILITIES)
        with connect(self.settings, read_only=True) as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT ON (capability_id) capability_id,status,last_run_at,
                  valid_until,benchmark_run_id,metric,threshold,evidence_ref
                FROM capability_evidence
                ORDER BY capability_id,last_run_at DESC NULLS LAST,evidence_id DESC
                """
            ).fetchall()
        by_id = {row[0]: row for row in rows}
        return [
            CapabilityEvidence(
                capability_id=identifier, name=names[identifier],
                status=CapabilityStatus(by_id[identifier][1]),
                last_run_at=by_id[identifier][2],
                valid_until=by_id[identifier][3],
                benchmark_run_id=by_id[identifier][4],
                metric=(float(by_id[identifier][5])
                        if by_id[identifier][5] is not None else None),
                threshold=(float(by_id[identifier][6])
                           if by_id[identifier][6] is not None else None),
                evidence_ref=by_id[identifier][7],
            )
            for identifier, _ in CAPABILITIES
        ]
