"""Pytest plugin that emits exact node-level governance observations."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


_REPORT_ENV = "TEST_GOVERNANCE_REPORT"
_COMPONENT_ENV = "TEST_GOVERNANCE_COMPONENT"


def _skip_reason(longrepr: object) -> str:
    if isinstance(longrepr, tuple) and len(longrepr) == 3:
        reason = str(longrepr[2])
    else:
        reason = str(longrepr)
    if reason.startswith("Skipped: "):
        reason = reason.removeprefix("Skipped: ")
    return " ".join(reason.split())


class _GovernanceReporter:
    def __init__(self, component: str, destination: Path) -> None:
        self.component = component
        self.destination = destination
        self.records: dict[str, dict[str, Any]] = {}

    def _record(
        self,
        node_id: str,
        outcome: str,
        *,
        reason: str = "",
        phase: str = "call",
    ) -> None:
        current = self.records.get(node_id)
        priority = {
            "collected": 0,
            "not_run": 1,
            "passed": 2,
            "deselected": 3,
            "skipped": 4,
            "failed": 5,
        }
        if current is not None and priority[current["outcome"]] > priority[outcome]:
            return
        self.records[node_id] = {
            "test_node_id": node_id,
            "component": self.component,
            "outcome": outcome,
            "reason": reason,
            "phase": phase,
        }

    def pytest_deselected(self, items: list[Any]) -> None:
        for item in items:
            markers = sorted(
                marker.name
                for marker in item.iter_markers()
                if marker.name in {"host_coupled", "runtime_postgres"}
            )
            reason = "marker expression deselected"
            if markers:
                reason += f": {', '.join(markers)}"
            self._record(item.nodeid, "deselected", reason=reason, phase="collection")

    def pytest_collection_finish(self, session: Any) -> None:
        for item in session.items:
            self._record(item.nodeid, "collected", phase="collection")

    def pytest_runtest_logreport(self, report: Any) -> None:
        if report.skipped:
            self._record(
                report.nodeid,
                "skipped",
                reason=_skip_reason(report.longrepr),
                phase=report.when,
            )
            return
        if report.failed:
            self._record(
                report.nodeid,
                "failed",
                reason=f"pytest {report.when} failure",
                phase=report.when,
            )
            return
        if report.when == "call" and report.passed:
            self._record(report.nodeid, "passed", phase="call")

    def pytest_sessionfinish(self, session: Any, exitstatus: int) -> None:
        for node_id, current in tuple(self.records.items()):
            if current["outcome"] == "collected":
                self._record(
                    node_id,
                    "not_run",
                    reason="collected but not executed",
                    phase="session",
                )
        records = sorted(
            self.records.values(),
            key=lambda item: (item["component"], item["test_node_id"]),
        )
        counts: dict[str, int] = {}
        for item in records:
            counts[item["outcome"]] = counts.get(item["outcome"], 0) + 1
        document = {
            "schema_version": 1,
            "component": self.component,
            "pytest_exit_status": int(exitstatus),
            "summary": counts,
            "tests": records,
        }
        self.destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.destination.with_suffix(self.destination.suffix + ".tmp")
        temporary.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.destination)


def pytest_configure(config: Any) -> None:
    destination = os.environ.get(_REPORT_ENV)
    component = os.environ.get(_COMPONENT_ENV)
    if not destination or not component:
        raise RuntimeError(
            f"{_REPORT_ENV} and {_COMPONENT_ENV} are required for test governance"
        )
    config.pluginmanager.register(
        _GovernanceReporter(component, Path(destination)),
        "test-governance-reporter",
    )
