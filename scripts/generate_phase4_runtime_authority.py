#!/usr/bin/env python3
"""Generate a fully bound Phase 4B protected runtime authority document."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.runtime_release.manifest import ReleasePolicy, verify_release
from packages.runtime_release.backend_policy import (
    require_approved_backend_commit, verify_phase4_backend_release,
)
from packages.runtime_release.provisioning import (
    build_command_manifest_document,
    build_runtime_authority_document,
    canonical_document_bytes,
    publish_canonical_document,
    read_canonical_document_file,
)
from packages.safety_evidence import CANONICAL_SAFETY_SOURCE_ROOT, safety_source_fingerprint


def _release(args: argparse.Namespace, prefix: str, kind: str):
    commit = getattr(args, f"{prefix}_commit")
    identity = getattr(args, f"{prefix}_python_identity")
    source = getattr(args, f"{prefix}_release_source")
    manifest_source = getattr(args, f"{prefix}_manifest_source")
    digest = getattr(args, f"{prefix}_manifest_sha256")
    if kind == "backend":
        require_approved_backend_commit(commit)
        verified = verify_phase4_backend_release(
            source, manifest_source, digest, expected_commit=commit,
            expected_python_identity=identity, expected_uid=args.staging_uid,
            expected_gid=args.staging_gid,
        )
    else:
        verified = verify_release(
            source, manifest_source, digest,
            ReleasePolicy(
                release_type="phase4-app", expected_git_commit=commit,
                expected_python_identity=identity, expected_uid=args.staging_uid,
                expected_gid=args.staging_gid,
            ),
        )
    if verified is not True:
        raise ValueError
    root = Path(f"/opt/trading-agent-phase4/releases/{kind}-{commit}")
    return SimpleNamespace(
        git_commit=commit, release_root=root,
        manifest_path=Path(f"/opt/trading-agent-phase4/manifests/{kind}-{commit}.manifest.json"),
        manifest_sha256=digest, python_path=root / ".venv/bin/python3.11",
        python_identity=identity,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for prefix in ("application", "backend"):
        parser.add_argument(f"--{prefix}-commit", required=True)
        parser.add_argument(f"--{prefix}-release-source", required=True, type=Path)
        parser.add_argument(f"--{prefix}-manifest-source", required=True, type=Path)
        parser.add_argument(f"--{prefix}-manifest-sha256", required=True)
        parser.add_argument(f"--{prefix}-python-identity", required=True)
    parser.add_argument("--command-manifest-source", required=True, type=Path)
    parser.add_argument("--command-manifest-sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--staging-uid", required=True, type=int)
    parser.add_argument("--staging-gid", required=True, type=int)
    parser.add_argument("--runtime-uid", required=True, type=int)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        application = _release(args, "application", "app")
        backend = _release(args, "backend", "backend")
        command_raw = read_canonical_document_file(
            args.command_manifest_source, args.command_manifest_sha256,
        )
        expected_command = canonical_document_bytes(
            build_command_manifest_document(SimpleNamespace(backend=backend))
        )
        if command_raw != expected_command:
            raise ValueError
        command_path = Path(
            f"/opt/trading-agent-phase4/manifests/commands-{backend.git_commit}.json"
        )
        document = build_runtime_authority_document(
            application=application, backend=backend,
            command_manifest_path=command_path,
            command_manifest_sha256=args.command_manifest_sha256,
            semantic_authority_path=Path(
                "/etc/trading-agent/research-input-manifests/phase4-v1.json"
            ),
            safety_snapshot_path=Path(
                f"/run/user/{args.runtime_uid}/trading-agent/safety-state.json"
            ),
            exporter_commit=application.git_commit,
            safety_source_fingerprint=safety_source_fingerprint(CANONICAL_SAFETY_SOURCE_ROOT),
            runtime_uid=args.runtime_uid,
        )
        digest = publish_canonical_document(
            document, args.output, apply=args.apply,
            expected_uid=args.staging_uid, expected_gid=args.staging_gid,
        )
    except Exception:
        print("runtime authority generation rejected", file=sys.stderr)
        return 2
    print(json.dumps({"applied": args.apply, "document_sha256": digest}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
