#!/usr/bin/env python3
"""Run required runtime tests and fail if any selected test is skipped."""

from __future__ import annotations

from collections.abc import Iterable
import sys

import pytest


class _RequiredRuntimePlugin:
    def __init__(self) -> None:
        self.skipped: set[str] = set()

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if report.skipped:
            self.skipped.add(report.nodeid)


def main(argv: Iterable[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        print("required runtime test paths are missing", file=sys.stderr)
        return 2
    plugin = _RequiredRuntimePlugin()
    result = int(pytest.main(["-q", *arguments], plugins=[plugin]))
    if result == 0 and plugin.skipped:
        print(
            f"required runtime tests skipped: {len(plugin.skipped)}",
            file=sys.stderr,
        )
        return 1
    return result


if __name__ == "__main__":
    raise SystemExit(main())
