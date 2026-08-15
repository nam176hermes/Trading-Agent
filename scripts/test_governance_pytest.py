"""Pytest plugin that emits exact node-level governance observations."""

from __future__ import annotations

import fnmatch
import json
import os
from pathlib import Path
import secrets
import stat
from typing import Any

import pytest


_REPORT_ENV = "TEST_GOVERNANCE_REPORT"
_COMPONENT_ENV = "TEST_GOVERNANCE_COMPONENT"
_CUSTODY_POLICY_ENV = "TEST_GOVERNANCE_CUSTODY_POLICY"
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)


_NON_PYTEST_MODULES = {
    "legacy": frozenset({"tests/test_integration.py"}),
}


def _atomic_json(
    path: Path, document: object, *, no_clobber: bool = False,
    canonical: bool = False,
) -> None:
    parent = path.parent
    absolute = parent.absolute()
    below_trusted_sticky_root = False
    for ancestor in reversed([absolute, *absolute.parents]):
        metadata = ancestor.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise RuntimeError("test governance report directory is unsafe")
        if metadata.st_uid not in {0, os.getuid()}:
            raise RuntimeError("test governance report directory is unsafe")
        trusted_sticky = (
            metadata.st_uid == 0 and bool(metadata.st_mode & stat.S_ISVTX)
        )
        writable = metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        if (
            writable
            and metadata.st_uid == os.getuid()
            and (below_trusted_sticky_root or ancestor == absolute)
        ):
            os.chmod(ancestor, 0o700)
            metadata = ancestor.lstat()
            writable = metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        if writable and not trusted_sticky:
            raise RuntimeError("test governance report directory is unsafe")
        below_trusted_sticky_root = below_trusted_sticky_root or trusted_sticky
    os.chmod(parent, 0o700)
    expected = parent.lstat()
    temporary_name = f".{path.name}.{secrets.token_hex(16)}.tmp"
    directory = os.open(
        parent,
        os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC,
    )
    descriptor = -1
    published = False
    accepted = False
    try:
        actual = os.fstat(directory)
        if (
            (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino)
            or actual.st_uid != os.getuid()
            or actual.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        ):
            raise RuntimeError("test governance report directory changed")
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC,
            0o600,
            dir_fd=directory,
        )
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            encoded = (
                json.dumps(
                    document, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                if canonical
                else (json.dumps(document, indent=2, sort_keys=True) + "\n").encode()
            )
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        if no_clobber:
            os.link(temporary_name, path.name, src_dir_fd=directory, dst_dir_fd=directory)
            os.unlink(temporary_name, dir_fd=directory)
        else:
            os.replace(
                temporary_name,
                path.name,
                src_dir_fd=directory,
                dst_dir_fd=directory,
            )
        published = True
        os.fsync(directory)
        current = parent.lstat()
        if (current.st_dev, current.st_ino) != (actual.st_dev, actual.st_ino):
            raise RuntimeError("test governance report directory changed")
        accepted = True
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            cleanup_name = path.name if published and not accepted else temporary_name
            os.unlink(cleanup_name, dir_fd=directory)
        except FileNotFoundError:
            pass
        os.close(directory)


def _skip_reason(longrepr: object) -> str:
    if isinstance(longrepr, tuple) and len(longrepr) == 3:
        reason = str(longrepr[2])
    else:
        reason = str(longrepr)
    if reason.startswith("Skipped: "):
        reason = reason.removeprefix("Skipped: ")
    return " ".join(reason.split())


class _GovernanceReporter:
    def __init__(
        self,
        component: str,
        destination: Path,
        root: Path,
        selected_paths: tuple[Path, ...],
        python_file_patterns: tuple[str, ...],
    ) -> None:
        self.component = component
        self.destination = destination
        self.root = root
        self.selected_paths = selected_paths
        self.python_file_patterns = python_file_patterns
        self.records: dict[str, dict[str, Any]] = {}
        self.collection_integrity_failed = False

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
            "xfailed": 3,
            "xpassed": 4,
            "deselected": 5,
            "skipped": 6,
            "failed": 7,
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

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_collection_modifyitems(
        self,
        session: Any,
        config: Any,
        items: list[Any],
    ):
        before = {item.nodeid for item in items}
        yield
        after = {item.nodeid for item in items}
        for node_id in sorted(before - after):
            current = self.records.get(node_id)
            if current is not None and current["outcome"] == "deselected":
                continue
            self._record(
                node_id,
                "failed",
                reason="collection hook removed selected test",
                phase="collection",
            )
            self.collection_integrity_failed = True

    def pytest_collection_finish(self, session: Any) -> None:
        for item in session.items:
            self._record(item.nodeid, "collected", phase="collection")

    def pytest_runtest_logreport(self, report: Any) -> None:
        if getattr(report, "wasxfail", None):
            outcome = "xpassed" if report.passed else "xfailed"
            self._record(
                report.nodeid,
                outcome,
                reason="pytest xfail marker observed",
                phase=report.when,
            )
            return
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
        collection_only = os.environ.get("TEST_GOVERNANCE_COLLECTION_ONLY") == "1"
        candidates: set[Path] = set()
        for selected in self.selected_paths:
            if selected.is_dir():
                candidates.update(
                    path
                    for path in selected.rglob("*.py")
                    if any(fnmatch.fnmatch(path.name, pattern) for pattern in self.python_file_patterns)
                )
            elif selected.is_file() and any(
                fnmatch.fnmatch(selected.name, pattern) for pattern in self.python_file_patterns
            ):
                candidates.add(selected)
        for path in sorted(candidates):
            try:
                relative = path.relative_to(self.root).as_posix()
            except ValueError:
                continue
            if relative in _NON_PYTEST_MODULES.get(self.component, frozenset()):
                continue
            if not any(
                node_id == relative or node_id.startswith(relative + "::")
                for node_id in self.records
            ):
                self._record(
                    f"{relative}::static-test-inventory",
                    "failed",
                    reason="filesystem test module yielded no pytest observation",
                    phase="collection",
                )
                session.exitstatus = 1
        if not collection_only:
            for node_id, current in tuple(self.records.items()):
                if current["outcome"] == "collected":
                    self._record(
                        node_id,
                        "not_run",
                        reason="collected but not executed",
                        phase="session",
                    )
                    session.exitstatus = 1
        if self.collection_integrity_failed:
            session.exitstatus = 1
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
            "collection_only": collection_only,
            "pytest_exit_status": int(session.exitstatus),
            "summary": counts,
            "tests": records,
        }
        raw_custody_policy = os.environ.get(_CUSTODY_POLICY_ENV)
        if raw_custody_policy is not None:
            try:
                custody_policy = json.loads(raw_custody_policy)
            except json.JSONDecodeError as exc:
                raise RuntimeError("test governance custody policy is malformed") from exc
            if not isinstance(custody_policy, dict):
                raise RuntimeError("test governance custody policy is malformed")
            document["custody_policy"] = custody_policy
        _atomic_json(
            self.destination,
            document,
            no_clobber=os.environ.get("TEST_GOVERNANCE_NO_CLOBBER") == "1",
            canonical=collection_only,
        )


def pytest_configure(config: Any) -> None:
    destination = os.environ.get(_REPORT_ENV)
    component = os.environ.get(_COMPONENT_ENV)
    if not destination or not component:
        raise RuntimeError(
            f"{_REPORT_ENV} and {_COMPONENT_ENV} are required for test governance"
        )
    root = Path(config.rootpath)
    selected_paths = tuple(
        path if path.is_absolute() else root / path
        for value in config.args
        if (path := Path(str(value).split("::", 1)[0])).exists()
    ) or (root / "tests",)
    config.pluginmanager.register(
        _GovernanceReporter(
            component,
            Path(destination),
            root,
            selected_paths,
            tuple(config.getini("python_files")),
        ),
        "test-governance-reporter",
    )
