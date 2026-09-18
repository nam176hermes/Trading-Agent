"""Composition root for the Phase 4B safety-state exporter."""

from __future__ import annotations

from functools import partial

import argparse
import os
import time
from typing import Mapping

from packages.runtime_release.config import load_runtime_authority

from .exporter import (
    CANONICAL_SOURCE_ROOT,
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
    authority = load_runtime_authority()
    if commit != authority.safety.exporter_commit:
        raise ValueError("safety exporter commit differs from protected authority")
    recheck = partial(authority.recheck, deployment_role="exporter")
    recheck()
    binding = authority.require_deployment() if authority.deployment is not None else None
    if binding is not None and (os.geteuid(), os.getegid()) != (binding.runtime_uid, binding.runtime_gid):
        raise ValueError("safety exporter identity differs from protected authority")
    return SafetyStateExporter(
        canonical_source_root=binding.safety_source_root if binding else CANONICAL_SOURCE_ROOT,
        mounted_source_root=binding.safety_mounted_root if binding else MOUNTED_SOURCE_ROOT,
        output_path=authority.safety.snapshot_path,
        exporter_commit=commit,
        authority_recheck=recheck,
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
