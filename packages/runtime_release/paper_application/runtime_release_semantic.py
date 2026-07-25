"""Canonical semantic evidence identity for the paper worker."""

from dataclasses import dataclass
from datetime import datetime

@dataclass(frozen=True, slots=True)
class SemanticEvidence:
    active_authority_sha256: str
    version_manifest_sha256: str
    semantic_input_fingerprint: str
    manifest_version: str
    generated_at: datetime
    expires_at: datetime
    policy_sha256: str

__all__ = ["SemanticEvidence"]
