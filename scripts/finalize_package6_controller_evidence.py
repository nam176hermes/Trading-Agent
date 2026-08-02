#!/usr/bin/env python3
"""Verify and finalize the source-only Package 6 controller closure."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.paper_runtime.evidence import (
    EvidenceIncomplete,
    FinalPublicationFailure,
    finalize_controller_evidence,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-bundle", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cleanup", dest="cleanup_path", type=Path, required=True)
    parser.add_argument("--review", dest="review_path", type=Path, required=True)
    parser.add_argument(
        "--diagnostic-index",
        dest="diagnostic_index_path",
        type=Path,
        required=True,
    )
    parser.add_argument("--candidate-commit", required=True)
    parser.add_argument("--candidate-tree", required=True)
    parser.add_argument("--reviewed-base-commit", required=True)
    parser.add_argument("--patch-algorithm", required=True)
    parser.add_argument("--reviewed-patch-sha256", required=True)
    parser.add_argument("--reviewed-patch-bytes", type=int, required=True)
    parser.add_argument(
        "--reviewed-path",
        dest="reviewed_paths",
        action="append",
        required=True,
    )
    parser.add_argument("--source-diff-sha256", required=True)
    parser.add_argument("--expected-seal-manifest-sha256", required=True)
    parser.add_argument("--review-verdict-sha256", required=True)
    parser.add_argument("--diagnostic-index-sha256", required=True)
    parser.add_argument("--runtime-bundle-index-sha256", required=True)
    parser.add_argument("--cleanup-evidence-sha256", required=True)
    parser.add_argument("--custodian-helper-binary-sha256", required=True)
    parser.add_argument(
        "--custodian-native-source-set-sha256", required=True
    )
    parser.add_argument(
        "--custodian-protocol-version", type=int, required=True
    )
    parser.add_argument(
        "--custodian-protocol-feature",
        dest="custodian_protocol_features",
        action="append",
        default=[],
    )
    parser.add_argument("--custodian-endpoint-authority", required=True)
    parser.add_argument(
        "--custodian-operation",
        dest="custodian_operations",
        action="append",
        required=True,
    )
    parser.add_argument("--custodian-stage-sha256", required=True)
    parser.add_argument("--custodian-fixture-sha256", required=True)
    parser.add_argument(
        "--custodian-publication",
        dest="custodian_publications",
        action="append",
        required=True,
        help="exact component=manifest_sha256 native publication receipt",
    )
    parser.add_argument("--verify-only", action="store_true")
    return parser


def main() -> int:
    arguments = vars(_parser().parse_args())
    try:
        result = finalize_controller_evidence(**arguments)
    except FinalPublicationFailure as error:
        authority = error.authority
        recovered = False
        try:
            if (
                authority.publication_committed
                or authority.publication_commit_uncertain
            ):
                recovered = authority.recover()
        finally:
            if not authority.close():
                raise RuntimeError(
                    "final publication descriptor cleanup is incomplete"
                ) from error
        if recovered:
            raise EvidenceIncomplete(
                "final publication recovered after interrupted finalization"
            ) from error
        raise EvidenceIncomplete(
            "final publication recovery remains incomplete"
        ) from error
    if result is not None:
        try:
            print(
                f"{result.path} sha256={result.canonical_sha256} "
                f"size={result.canonical_byte_size} "
                f"dev={result.device} ino={result.inode}"
            )
        finally:
            if not result.close():
                raise RuntimeError(
                    "final publication descriptor cleanup is incomplete"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
