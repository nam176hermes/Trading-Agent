"""Production safety reader pinned to protected runtime authority."""

from __future__ import annotations

from collections.abc import Callable

from packages.runtime_release.config import load_runtime_authority
from services.job_worker.safety_state import SafetyEvidence, SafetyStateClient


def authority_bound_safety_provider() -> Callable[[], SafetyEvidence]:
    authority = load_runtime_authority()
    client = SafetyStateClient(
        authority.safety.snapshot_path,
        expected_exporter_commit=authority.safety.exporter_commit,
        expected_source_fingerprint=authority.safety.source_fingerprint,
    )

    def read() -> SafetyEvidence:
        authority.recheck()
        evidence = client.evidence()
        authority.recheck()
        return evidence

    return read


__all__ = ["authority_bound_safety_provider"]
