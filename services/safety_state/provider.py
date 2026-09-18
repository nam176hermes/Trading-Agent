"""Production safety reader pinned to protected runtime authority."""

from __future__ import annotations

from functools import partial

from collections.abc import Callable

from packages.runtime_release.config import RuntimeAuthority, load_runtime_authority
from services.job_worker.safety_state import SafetyEvidence, SafetyStateClient


def authority_bound_safety_provider(*, authority: RuntimeAuthority | None = None) -> Callable[[], SafetyEvidence]:
    authority = load_runtime_authority() if authority is None else authority
    recheck = partial(authority.recheck, deployment_role="reader")
    recheck()
    client = SafetyStateClient(
        authority.safety.snapshot_path,
        expected_exporter_commit=authority.safety.exporter_commit,
        expected_source_fingerprint=authority.safety.source_fingerprint,
    )

    def read() -> SafetyEvidence:
        recheck()
        evidence = client.evidence()
        recheck()
        return evidence

    return read


__all__ = ["authority_bound_safety_provider"]
