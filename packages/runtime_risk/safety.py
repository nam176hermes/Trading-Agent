"""Read-only canonical safety evidence for global halt authority."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from packages.domain.runtime_halt import GlobalSafetyObservation
from packages.safety_evidence import (
    CANONICAL_SAFETY_SOURCE_ROOT,
    resolve_kill_switch,
    safety_source_fingerprint,
)

from .canonical import canonical_model_json


class GlobalHaltAuthorityError(RuntimeError):
    """Global halt state cannot be established from exact authority."""


class GlobalSafetyAuthorityVerifier(Protocol):
    """Trusted composition boundary which independently reads safety custody."""

    def verify(
        self,
        *,
        observation: GlobalSafetyObservation,
    ) -> GlobalSafetyObservation: ...


def _canonical_safety(value: object) -> GlobalSafetyObservation:
    if type(value) is not GlobalSafetyObservation:
        raise GlobalHaltAuthorityError("global halt safety authority is invalid")
    try:
        return GlobalSafetyObservation.model_validate_json(canonical_model_json(value))
    except (AttributeError, TypeError, ValueError) as exc:
        raise GlobalHaltAuthorityError("global halt safety authority is invalid") from exc


def observe_global_safety(
    *, source_root: Path, observed_at: datetime
) -> GlobalSafetyObservation:
    """Observe the existing sentinel without modifying safety custody."""

    try:
        root = Path(source_root)
        return GlobalSafetyObservation(
            source_fingerprint=safety_source_fingerprint(root),
            kill_switch_state=resolve_kill_switch(root / ".kill_switch"),
            observed_at=observed_at,
            schema_version="global-safety-observation-v1",
        )
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        raise GlobalHaltAuthorityError("global halt safety observation failed") from exc


@dataclass(frozen=True, slots=True)
class FilesystemGlobalSafetyAuthority:
    """Read and verify one composition-owned safety root without mutation."""

    source_root: Path

    def __post_init__(self) -> None:
        if not isinstance(self.source_root, Path) or not self.source_root.is_absolute():
            raise ValueError("global safety authority root must be an absolute Path")

    def verify(
        self,
        *,
        observation: GlobalSafetyObservation,
    ) -> GlobalSafetyObservation:
        canonical = _canonical_safety(observation)
        observed = observe_global_safety(
            source_root=self.source_root,
            observed_at=canonical.observed_at,
        )
        if observed != canonical:
            raise GlobalHaltAuthorityError("global halt safety authority is invalid")
        return observed


def canonical_global_safety_authority() -> FilesystemGlobalSafetyAuthority:
    """Compose the production verifier pinned to canonical safety custody."""

    return FilesystemGlobalSafetyAuthority(CANONICAL_SAFETY_SOURCE_ROOT)


def verify_global_safety_observation(
    *,
    verifier: GlobalSafetyAuthorityVerifier,
    observation: GlobalSafetyObservation,
) -> GlobalSafetyObservation:
    """Require an exact independent read from a trusted injected verifier."""

    canonical = _canonical_safety(observation)
    try:
        verified = verifier.verify(observation=canonical)
        verified = _canonical_safety(verified)
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
        raise GlobalHaltAuthorityError("global halt safety authority is invalid") from exc
    if verified != canonical:
        raise GlobalHaltAuthorityError("global halt safety authority is invalid")
    return verified


def global_safety_binding_digest(safety: GlobalSafetyObservation) -> str:
    """Bind the stable source and resolved state while excluding observation time."""

    canonical = _canonical_safety(safety)
    encoded = json.dumps(
        {
            "kill_switch_state": canonical.kill_switch_state.value,
            "source_fingerprint": canonical.source_fingerprint,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "FilesystemGlobalSafetyAuthority",
    "GlobalHaltAuthorityError",
    "GlobalSafetyAuthorityVerifier",
    "canonical_global_safety_authority",
    "global_safety_binding_digest",
    "observe_global_safety",
    "verify_global_safety_observation",
]
