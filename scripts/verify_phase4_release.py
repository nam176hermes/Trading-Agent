#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.runtime_release import phase4_app_release_policy, phase4_backend_policy, verify_release


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify an immutable Phase 4 release")
    parser.add_argument("release_root")
    parser.add_argument("manifest_path")
    parser.add_argument("expected_digest")
    parser.add_argument("--commit", required=True)
    parser.add_argument("--python-identity", required=True)
    parser.add_argument("--uid", type=int, default=0)
    parser.add_argument("--gid", type=int, default=0)
    parser.add_argument("--release-kind", choices=("application", "backend"), default="application")
    args = parser.parse_args()
    policy_factory = phase4_backend_policy if args.release_kind == "backend" else phase4_app_release_policy
    verify_release(
        args.release_root,
        args.manifest_path,
        args.expected_digest,
        policy_factory(
            expected_uid=args.uid,
            expected_gid=args.gid,
            expected_git_commit=args.commit,
            expected_python_identity=args.python_identity,
        ),
    )
    print("release verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
