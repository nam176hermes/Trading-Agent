from __future__ import annotations

import argparse
import json
from pathlib import Path

from trading_control.phase3b_sources import analyze_phase3b_sources


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze immutable Phase 3B sources")
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--format", choices=("json",), default="json")
    args = parser.parse_args(argv)
    analysis = analyze_phase3b_sources(args.source_root)
    print(json.dumps(analysis.to_dict(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
