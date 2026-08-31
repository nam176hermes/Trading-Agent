#!/usr/bin/env python3
"""Classify P1-H changed paths without granting runtime authority."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.nautilus_upgrade_authority.lts import (
    P1ChangeClass,
    P1ImpactDisposition,
    classify_p1_change,
)


_P1_H_PATHS = {"Makefile", "packages/domain/recovery.py", "pyproject.toml", "uv.lock"}
_P1_H_PREFIXES = (
    "docs/implementation/p1-real-nautilus/",
    "engines/",
    "packages/engine_contracts/",
    "packages/execution_sandbox/",
    "packages/nautilus_",
    "scripts/classify_p1_h_impact.py",
    "scripts/qualify_p1_",
    "services/job_worker/",
    "services/paper_runtime/",
    "tests/execution_sandbox/",
    "tests/nautilus_",
    "tests/p1_nautilus/",
    "tests/paper_runtime/",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--base")
    parser.add_argument("--head")
    parser.add_argument("--declared-class", choices=("A", "B", "C", "D"), default="A")
    parser.add_argument("--compatibility-changed", action="store_true")
    args = parser.parse_args(argv)
    if (args.base and not args.head) or (args.paths and args.head):
        parser.error("supply paths or one complete --base/--head range")
    paths = args.paths
    if args.head:
        base = args.base
        if not base or base == "0" * 40:
            base = subprocess.check_output(
                ("git", "-C", str(args.repo), "rev-parse", f"{args.head}^"),
                text=True,
                timeout=10,
            ).strip()
        completed = subprocess.run(
            (
                "git",
                "-C",
                str(args.repo),
                "diff",
                "--name-only",
                "--diff-filter=ACMRD",
                base,
                args.head,
            ),
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        paths = [
            path
            for path in completed.stdout.splitlines()
            if path in _P1_H_PATHS or path.startswith(_P1_H_PREFIXES)
        ]
        if not paths:
            print('{"status":"NOT_APPLICABLE"}')
            return 0
    if not paths:
        parser.error("at least one changed path is required")
    decision = classify_p1_change(
        paths,
        P1ChangeClass(args.declared_class),
        args.compatibility_changed,
    )
    document = asdict(decision)
    print(json.dumps(document, separators=(",", ":"), sort_keys=True))
    return 2 if decision.disposition is P1ImpactDisposition.HELD else 0


if __name__ == "__main__":
    raise SystemExit(main())
