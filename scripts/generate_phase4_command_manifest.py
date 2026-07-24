#!/usr/bin/env python3
"""Generate the exact external command manifest from code-owned policy."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.runtime_release.backend_policy import (
    require_approved_backend_commit, verify_phase4_backend_release,
)
from packages.runtime_release.provisioning import (
    build_command_manifest_document,
    publish_canonical_document,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-commit", required=True)
    parser.add_argument("--release-source", required=True, type=Path)
    parser.add_argument("--manifest-source", required=True, type=Path)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--python-identity", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--staging-uid", required=True, type=int)
    parser.add_argument("--staging-gid", required=True, type=int)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        require_approved_backend_commit(args.backend_commit)
        verified = verify_phase4_backend_release(
            args.release_source, args.manifest_source, args.manifest_sha256,
            expected_commit=args.backend_commit,
            expected_python_identity=args.python_identity,
            expected_uid=args.staging_uid, expected_gid=args.staging_gid,
        )
        if verified is not True:
            raise ValueError
        canonical_root = Path(
            f"/opt/trading-agent-phase4/releases/backend-{args.backend_commit}"
        )
        backend = SimpleNamespace(
            git_commit=args.backend_commit,
            release_root=canonical_root,
            manifest_path=Path(
                f"/opt/trading-agent-phase4/manifests/backend-{args.backend_commit}.manifest.json"
            ),
            manifest_sha256=args.manifest_sha256,
            python_path=canonical_root / ".venv/bin/python3.11",
            python_identity=args.python_identity,
        )
        digest = publish_canonical_document(
            build_command_manifest_document(SimpleNamespace(backend=backend)),
            args.output, apply=args.apply,
            expected_uid=args.staging_uid, expected_gid=args.staging_gid,
        )
    except Exception:
        print("command manifest generation rejected", file=sys.stderr)
        return 2
    print(json.dumps({"applied": args.apply, "document_sha256": digest}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
