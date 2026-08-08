#!/usr/bin/env python3
"""Close sealed Phase-4 research records and emit one canonical closure line."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.engine_contracts import canonical_json_bytes
from packages.research_validation import (
    CampaignEvidenceError,
    ResearchClosureError,
    close_ws04_research_campaign,
    produce_research_campaign_evidence,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--campaign-directory", required=True, type=Path)
    parser.add_argument("--parity-record", required=True, type=Path)
    parser.add_argument("--paper-record", required=True, type=Path)
    parser.add_argument("--legacy-record-directory", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        evidence = produce_research_campaign_evidence(**vars(arguments))
        closure = close_ws04_research_campaign(evidence)
    except (CampaignEvidenceError, ResearchClosureError, OSError, ValueError):
        print("error: Phase-4 research evidence did not close", file=sys.stderr)
        return 1
    sys.stdout.buffer.write(canonical_json_bytes(closure) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
