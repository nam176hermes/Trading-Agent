"""Production safety reader pinned to protected runtime authority."""

from __future__ import annotations

from collections.abc import Callable

from packages.runtime_release.config import load_runtime_authority
from services.job_worker.safety import SafetySnapshot
from services.job_worker.safety_state import SafetyStateClient


def authority_bound_safety_provider() -> Callable[[], SafetySnapshot]:
    authority = load_runtime_authority()
    client = SafetyStateClient(
        authority.safety.snapshot_path,
        expected_exporter_commit=authority.safety.exporter_commit,
        expected_source_fingerprint=authority.safety.source_fingerprint,
    )

    def read() -> SafetySnapshot:
        authority.recheck()
        evidence = client.evidence()
        authority.recheck()
        return evidence

    return read


__all__ = ["authority_bound_safety_provider"]
