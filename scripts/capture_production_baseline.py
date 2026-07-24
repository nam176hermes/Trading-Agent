#!/usr/bin/env python3
"""Capture the immutable, paper-only production promotion baseline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.deployment_evidence import EvidenceState, PromotionDecision


BASELINE_HEAD = "e304d83da260d11120ac648d67882359645c68a5"
BASELINE_TREE = "bf4d1fb20944670df8110fc7eee3dbe3bc390b55"


def build_baseline(
    *,
    repo_root: Path,
    head: str,
    tree: str,
    requested_mode: str,
    effective_mode: str,
    live_execution_enabled: bool,
    live_trading_approved: bool,
) -> dict[str, object]:
    observed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if observed != head:
        raise ValueError("source head changed during baseline capture")
    observed_tree = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if observed_tree != tree:
        raise ValueError("source tree changed during baseline capture")
    if (requested_mode, effective_mode) != ("paper", "paper"):
        raise ValueError("promotion baseline must remain paper-only")
    if live_execution_enabled or live_trading_approved:
        raise ValueError("live gates must be false")
    return {
        "schema_version": 2,
        "source": {
            "binding": "HISTORICAL_BASELINE",
            "root": str(repo_root),
            "commit": observed,
            "tree": observed_tree,
        },
        "safety": {
            "requested_mode": requested_mode,
            "effective_mode": effective_mode,
            "live_execution_enabled": False,
            "live_trading_approved": False,
        },
        "decision": PromotionDecision.NO_GO.value,
        "completed_gates": [],
        "deployment_evidence": {
            "state": EvidenceState.UNAVAILABLE.value,
            "schema_path": "ops/evidence/source-release-unit-pid.schema.json",
            "path": None,
            "sha256": None,
        },
    }


def _boolean(value: str) -> bool:
    if value == "true":
        return True
    if value == "false":
        return False
    raise argparse.ArgumentTypeError("expected true or false")


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--requested-mode", required=True)
    parser.add_argument("--effective-mode", required=True)
    parser.add_argument("--live-execution-enabled", required=True, type=_boolean)
    parser.add_argument("--live-trading-approved", required=True, type=_boolean)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    arguments = _arguments()
    document = build_baseline(
        repo_root=arguments.root,
        head=BASELINE_HEAD,
        tree=BASELINE_TREE,
        requested_mode=arguments.requested_mode,
        effective_mode=arguments.effective_mode,
        live_execution_enabled=arguments.live_execution_enabled,
        live_trading_approved=arguments.live_trading_approved,
    )
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
