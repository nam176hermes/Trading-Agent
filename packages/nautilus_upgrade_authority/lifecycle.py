"""Engine lifecycle identities shared by P1-H qualification."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
import hashlib
import json


class EngineLifecycle(str, Enum):
    ACTIVE = "ACTIVE"
    CANDIDATE = "CANDIDATE"
    ROLLBACK = "ROLLBACK"
    HELD = "HELD"
    RETIRED = "RETIRED"


class CheckpointCompatibility(str, Enum):
    EXACT = "EXACT"
    REPLAY_REQUIRED = "REPLAY_REQUIRED"
    INCOMPATIBLE = "INCOMPATIBLE"


@dataclass(frozen=True, slots=True)
class EventApiEpoch:
    request_protocol: str
    event_schema: str
    paper_schema: str
    result_validator: str
    manifest_schema: int

    @property
    def sha256(self) -> str:
        document = {
            "event_schema": self.event_schema,
            "manifest_schema": self.manifest_schema,
            "paper_schema": self.paper_schema,
            "request_protocol": self.request_protocol,
            "result_validator": self.result_validator,
        }
        return hashlib.sha256(_canonical(document)).hexdigest()


@dataclass(frozen=True, slots=True)
class EngineRegistryEntry:
    runtime_family: str
    engine_version: str
    lifecycle: EngineLifecycle


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True) + "\n"
    ).encode()


def validate_engine_registry(
    entries: Iterable[EngineRegistryEntry],
) -> tuple[EngineRegistryEntry, ...]:
    registry = tuple(entries)
    families = {entry.runtime_family for entry in registry}
    if not registry or any(
        sum(
            entry.lifecycle is EngineLifecycle.ACTIVE
            for entry in registry
            if entry.runtime_family == family
        )
        != 1
        for family in families
    ):
        raise ValueError("engine registry requires exactly one ACTIVE per runtime family")
    if len({(entry.runtime_family, entry.engine_version) for entry in registry}) != len(
        registry
    ):
        raise ValueError("engine registry contains a duplicate identity")
    return registry


def classify_checkpoint_compatibility(
    active_api_epoch: str,
    checkpoint_api_epoch: str,
    active_checkpoint_schema: str,
    checkpoint_schema: str,
) -> CheckpointCompatibility:
    if active_api_epoch != checkpoint_api_epoch:
        return CheckpointCompatibility.INCOMPATIBLE
    if active_checkpoint_schema == checkpoint_schema:
        return CheckpointCompatibility.EXACT
    if (
        active_checkpoint_schema,
        checkpoint_schema,
    ) == ("sandbox-recovery-checkpoint-v2", "sandbox-recovery-checkpoint-v1"):
        return CheckpointCompatibility.REPLAY_REQUIRED
    return CheckpointCompatibility.INCOMPATIBLE


def golden_registry_sha256(scenario_ids: Iterable[str], source_sha256: str) -> str:
    scenarios = tuple(scenario_ids)
    if len(scenarios) != 8 or len(set(scenarios)) != 8:
        raise ValueError("golden registry requires exactly eight unique scenarios")
    if len(source_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in source_sha256
    ):
        raise ValueError("golden registry source SHA-256 is invalid")
    return hashlib.sha256(
        _canonical({"scenario_ids": sorted(scenarios), "source_sha256": source_sha256})
    ).hexdigest()


__all__ = [
    "CheckpointCompatibility",
    "EngineLifecycle",
    "EngineRegistryEntry",
    "EventApiEpoch",
    "classify_checkpoint_compatibility",
    "golden_registry_sha256",
    "validate_engine_registry",
]
