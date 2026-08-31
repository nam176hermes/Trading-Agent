from __future__ import annotations

import pytest


def test_registry_rejects_two_active_engines() -> None:
    from packages.nautilus_upgrade_authority.lts import (
        EngineLifecycle,
        EngineRegistryEntry,
        validate_engine_registry,
    )

    active = EngineRegistryEntry("cython-v1", "1.231.0", EngineLifecycle.ACTIVE)

    with pytest.raises(ValueError, match="exactly one ACTIVE"):
        validate_engine_registry((active, active))


def test_unknown_path_is_class_d_and_held() -> None:
    from packages.nautilus_upgrade_authority.lts import (
        P1ChangeClass,
        P1ImpactDisposition,
        classify_changed_paths,
    )

    decision = classify_changed_paths(("new_engine_surface.py",))

    assert decision.change_class is P1ChangeClass.D
    assert decision.disposition is P1ImpactDisposition.HELD


def test_event_api_epoch_changes_when_any_bound_contract_changes() -> None:
    from packages.nautilus_upgrade_authority.lts import EventApiEpoch

    epoch = EventApiEpoch("1.0.0", "events-v1", "paper-v2", "validator-v1", 8)
    changed = EventApiEpoch("1.0.0", "events-v1", "paper-v2", "validator-v2", 8)

    assert epoch.sha256 != changed.sha256
    assert epoch.sha256 == epoch.sha256


def test_checkpoint_policy_requires_replay_or_rejects_epoch_change() -> None:
    from packages.nautilus_upgrade_authority.lts import (
        CheckpointCompatibility,
        classify_checkpoint_compatibility,
    )

    v1 = "sandbox-recovery-checkpoint-v1"
    v2 = "sandbox-recovery-checkpoint-v2"
    assert classify_checkpoint_compatibility("api-1", "api-1", v2, v2) is CheckpointCompatibility.EXACT
    assert classify_checkpoint_compatibility("api-1", "api-1", v2, v1) is CheckpointCompatibility.REPLAY_REQUIRED
    assert classify_checkpoint_compatibility("api-1", "api-2", v2, v2) is CheckpointCompatibility.INCOMPATIBLE
    assert classify_checkpoint_compatibility("api-1", "api-1", v2, "unknown") is CheckpointCompatibility.INCOMPATIBLE


def test_golden_registry_rejects_missing_or_duplicate_scenarios() -> None:
    from packages.nautilus_upgrade_authority.lts import golden_registry_sha256

    with pytest.raises(ValueError, match="exactly eight unique"):
        golden_registry_sha256(("one",) * 8, "a" * 64)
