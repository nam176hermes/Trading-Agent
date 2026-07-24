"""Composition root for the Phase 4B safety-state exporter."""

from __future__ import annotations

import argparse
import os
import time
from typing import Mapping

from .exporter import (
    CANONICAL_SOURCE_ROOT,
    DEFAULT_SNAPSHOT_PATH,
    EXPORT_INTERVAL_SECONDS,
    MOUNTED_SOURCE_ROOT,
    SafetyStateExporter,
)


def build_exporter(source: Mapping[str, str] | None = None) -> SafetyStateExporter:
    values = os.environ if source is None else source
    if "TRADING_SAFETY_STATE_PATH" in values:
        raise ValueError("safety snapshot path overrides are forbidden")
    if {
        "TRADING_CANONICAL_SAFETY_ROOT",
        "TRADING_MOUNTED_SAFETY_ROOT",
        "TRADING_SAFETY_SOURCE_ROOT",
    }.intersection(values):
        raise ValueError("safety source root overrides are forbidden")
    commit = values.get("TRADING_SAFETY_EXPORTER_COMMIT")
    if commit is None:
        raise ValueError("safety exporter commit is required")
    return SafetyStateExporter(
        canonical_source_root=CANONICAL_SOURCE_ROOT,
        mounted_source_root=MOUNTED_SOURCE_ROOT,
        output_path=DEFAULT_SNAPSHOT_PATH,
        exporter_commit=commit,
        gate_source={key: values[key] for key in (
            "LIVE_EXECUTION_ENABLED", "LIVE_TRADING_APPROVED",
        ) if key in values},
    )


def serve(exporter: SafetyStateExporter) -> None:
    while True:
        exporter.export_once()
        time.sleep(EXPORT_INTERVAL_SECONDS)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    exporter = build_exporter()
    if args.once:
        exporter.export_once()
    else:
        serve(exporter)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["build_exporter", "main", "serve"]
