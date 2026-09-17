"""Production-only composition for operator control."""

from __future__ import annotations

from functools import partial

from dataclasses import dataclass
from pathlib import Path
import os

from packages.runtime_release.config import load_runtime_authority

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
    data_root: Path | None = None


def build_production_operator_control_service(
    settings: OperatorControlRuntimeSettings,
) -> OperatorControlService:
    authority = load_runtime_authority()
    recheck = partial(authority.recheck, deployment_role="operator")
    recheck()
    binding = authority.require_deployment() if authority.deployment is not None else None
    root = binding.safety_source_root if binding else CANONICAL_SAFETY_SOURCE_ROOT
    if settings.data_root is not None and Path(settings.data_root) != root:
        raise ValueError("operator control requires the exact canonical data root")
    if binding is not None and (os.geteuid(), os.getegid()) != (binding.runtime_uid, binding.runtime_gid):
        raise ValueError("operator identity differs from protected authority")
    paths = OperatorStatePaths(
        data_root=root,
        command_root=root / ".operator-commands",
        mode_path=root / ".mode",
        kill_switch_path=root / ".kill_switch",
    )
    protected_provider = authority_bound_safety_provider(authority=authority)
    fingerprint = safety_source_fingerprint(root)
    return OperatorControlService(
        state_store=OperatorStateStore(paths),
        journal=CommandJournal(paths),
        authority_recheck=recheck,
        safety_provider=lambda: normalize_operator_safety_evidence(
            protected_provider(), source_fingerprint=fingerprint
        ),
    )


__all__ = [
    "OperatorControlRuntimeSettings",
    "build_production_operator_control_service",
]
