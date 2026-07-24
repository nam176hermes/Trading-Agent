#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.runtime_release import build_release, phase4_app_release_policy, phase4_backend_policy


def main() -> int:
    parser = argparse.ArgumentParser(description="Build an attested Phase 4 release from an exact Git commit")
    parser.add_argument("source_git_dir")
    parser.add_argument("commit")
    parser.add_argument("destination")
    parser.add_argument("--uid", type=int, default=0)
    parser.add_argument("--gid", type=int, default=0)
    parser.add_argument("--python", default="python3.11")
    parser.add_argument("--exclude", action="append", default=[])
    parser.add_argument("--wheelhouse")
    parser.add_argument("--release-kind", choices=("application", "backend"), default="application")
    args = parser.parse_args()
    if args.release_kind == "backend" and args.exclude:
        parser.error("backend release policy does not accept caller exclusions")
    policy_factory = phase4_backend_policy if args.release_kind == "backend" else phase4_app_release_policy
    result = build_release(
        args.source_git_dir,
        args.commit,
        args.destination,
        policy_factory(
            expected_uid=args.uid,
            expected_gid=args.gid,
            exclusions=tuple(args.exclude),
            python_executable=args.python,
            offline_wheelhouse=args.wheelhouse,
        ),
    )
    print(json.dumps({"commit": result.commit, "digest": result.digest, "manifest": str(result.manifest_path)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
