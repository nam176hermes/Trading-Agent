"""Production-only composition for operator control."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from packages.safety_evidence import (
    CANONICAL_SAFETY_SOURCE_ROOT,
    safety_source_fingerprint,
)
from services.safety_state.provider import authority_bound_safety_provider

from .journal import CommandJournal
from .safety_adapter import normalize_operator_safety_evidence
from .service import OperatorControlService
from .state_store import OperatorStatePaths, OperatorStateStore


@dataclass(frozen=True, slots=True)
class OperatorControlRuntimeSettings:
    data_root: Path = CANONICAL_SAFETY_SOURCE_ROOT


def build_production_operator_control_service(
    settings: OperatorControlRuntimeSettings,
) -> OperatorControlService:
    root = Path(settings.data_root)
    if root != CANONICAL_SAFETY_SOURCE_ROOT:
        raise ValueError("operator control requires the exact canonical data root")
    paths = OperatorStatePaths(
        data_root=root,
        command_root=root / ".operator-commands",
        mode_path=root / ".mode",
        kill_switch_path=root / ".kill_switch",
    )
    protected_provider = authority_bound_safety_provider()
    fingerprint = safety_source_fingerprint(CANONICAL_SAFETY_SOURCE_ROOT)
    return OperatorControlService(
        state_store=OperatorStateStore(paths),
        journal=CommandJournal(paths),
        safety_provider=lambda: normalize_operator_safety_evidence(
            protected_provider(), source_fingerprint=fingerprint
        ),
    )


__all__ = [
    "OperatorControlRuntimeSettings",
    "build_production_operator_control_service",
]
