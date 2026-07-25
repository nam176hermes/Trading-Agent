"""Exact reviewed file authority for the Phase 4B legacy research backend."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from .manifest import (
    ReleasePolicy, create_manifest, phase4_backend_release_policy, verify_release,
)


APPROVED_PHASE4_BACKEND_COMMIT = "41f055b48033714c660f44cc20498b7545366e75"


# Canonical paper backend authority. The legacy multi-mode orchestrator and all
# exchange, broker, credential and execution modules remain in Git for audit and
# rollback, but the release builder cannot select them.
PHASE4_BACKEND_AUDITED_PATHS = (
    "job_attribution.py",
    "paper_main.py",
    "paper_runtime_manifest.json",
    "research_semantics.py",
)


def phase4_backend_policy(**overrides: object) -> ReleasePolicy:
    overrides.setdefault("install_dependencies", False)
    return phase4_backend_release_policy(
        audited_paths=PHASE4_BACKEND_AUDITED_PATHS,
        **overrides,
    )


def require_approved_backend_commit(commit: str) -> str:
    if commit != APPROVED_PHASE4_BACKEND_COMMIT:
        raise ValueError("backend commit is not approved")
    return commit


def verify_phase4_backend_release(
    release_root: Path, manifest_path: Path, expected_digest: str, *,
    expected_commit: str, expected_python_identity: str,
    expected_uid: int = 0, expected_gid: int = 0,
) -> bool:
    """Verify both the complete release and its exact non-venv source set."""

    policy = phase4_backend_policy(
        expected_git_commit=expected_commit,
        expected_python_identity=expected_python_identity,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    )
    if verify_release(release_root, manifest_path, expected_digest, policy) is not True:
        return False
    observed = create_manifest(release_root, policy)
    source_entries = {
        (entry["path"], entry["type"])
        for entry in observed
        if PurePosixPath(entry["path"]).parts[0] != ".venv"
    }
    expected_entries = {(path, "file") for path in PHASE4_BACKEND_AUDITED_PATHS}
    for path in PHASE4_BACKEND_AUDITED_PATHS:
        pure = PurePosixPath(path)
        for index in range(1, len(pure.parts)):
            expected_entries.add((PurePosixPath(*pure.parts[:index]).as_posix(), "directory"))
    if source_entries != expected_entries:
        raise ValueError("backend release source set mismatch")
    return True


__all__ = [
    "PHASE4_BACKEND_AUDITED_PATHS", "phase4_backend_policy",
    "APPROVED_PHASE4_BACKEND_COMMIT", "require_approved_backend_commit",
    "verify_phase4_backend_release",
]
