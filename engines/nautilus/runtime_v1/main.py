#!/usr/bin/python3.12
"""Closed entrypoint for the sealed P1 Nautilus runtime."""

from __future__ import annotations

import sys
from importlib.metadata import version as package_version

if __package__ in {None, ""}:
    sys.path.insert(0, "/engine")
    __package__ = "runtime_v1"

from .bootstrap import (  # noqa: E402
    load_product_lineage,
    require_engine_version,
    require_runtime_entry,
)
from .diagnostics import emit_diagnostic  # noqa: E402


def main() -> int:
    try:
        require_runtime_entry(
            module_name=__name__, module_spec=__spec__, module_file=__file__
        )
        require_engine_version(package_version("nautilus_trader"))
        load_product_lineage()
    except Exception:
        emit_diagnostic("E_BOOTSTRAP")
        return 70
    emit_diagnostic("E_RUNTIME_NOT_READY")
    return 70


if __name__ == "__main__":
    raise SystemExit(main())
