"""Read-only canonical safety evidence for global halt authority."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from packages.domain.runtime_halt import GlobalSafetyObservation
from packages.safety_evidence import resolve_kill_switch, safety_source_fingerprint

from .canonical import canonical_model_json


class GlobalHaltAuthorityError(RuntimeError):
    """Global halt state cannot be established from exact authority."""


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
    "GlobalHaltAuthorityError",
    "global_safety_binding_digest",
    "observe_global_safety",
]
