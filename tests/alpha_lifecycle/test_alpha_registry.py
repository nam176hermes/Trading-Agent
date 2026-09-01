from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from packages.alpha_lifecycle.registry import (
    AlphaLifecycleStatus,
    AlphaRecordV1,
    AlphaRegistry,
    AlphaRegistryError,
    QualificationDecision,
)
from packages.data_catalog.artifact_store import LocalArtifactStore


def _record(status: AlphaLifecycleStatus, **updates: object) -> AlphaRecordV1:
    values: dict[str, object] = {
        "alpha_id": "alpha-fixture",
        "version": "1.0.0",
        "source_sha": "a" * 40,
        "implementation_identity": "packages.alpha_fixture:AlphaV1",
        "dataset_snapshot_sha256": "b" * 64,
        "feature_set": ("close", "volume"),
        "parameter_set_sha256": "c" * 64,
        "training_start_at": datetime(2022, 1, 1, tzinfo=UTC),
        "training_end_at": datetime(2022, 12, 31, tzinfo=UTC),
        "validation_start_at": datetime(2023, 1, 1, tzinfo=UTC),
        "validation_end_at": datetime(2023, 12, 31, tzinfo=UTC),
        "oos_start_at": datetime(2024, 1, 1, tzinfo=UTC),
        "oos_end_at": datetime(2024, 12, 31, tzinfo=UTC),
        "universe": ("BTCUSDT.BINANCE",),
        "cost_model_sha256": "d" * 64,
        "baseline_id": "B3_SIMPLE_MOMENTUM",
        "baseline_version": "1.0.0",
        "metrics_sha256": None,
        "robustness_sha256": None,
        "qualification_decision": QualificationDecision.NOT_EVALUATED,
        "qualification_reason": "awaiting deterministic research",
        "artifact_digests": ("e" * 64,),
        "lineage": ("idea-001",),
        "superseded_version": None,
        "lifecycle_status": status,
    }
    values.update(updates)
    return AlphaRecordV1(**values)


def test_registry_is_append_only_hash_chained_and_has_no_execution_authority(
    tmp_path: Path,
) -> None:
    registry = AlphaRegistry(LocalArtifactStore(tmp_path))
    idea = registry.append(_record(AlphaLifecycleStatus.IDEA), predecessor_sha256=None)
    candidate = registry.append(
        _record(AlphaLifecycleStatus.CANDIDATE),
        predecessor_sha256=idea.event_sha256,
    )

    assert candidate.sequence == 2
    assert candidate.predecessor_sha256 == idea.event_sha256
    assert registry.head("alpha-fixture", "1.0.0") == candidate
    assert not {
        "network_authorized",
        "broker_authorized",
        "live_enabled",
        "execution_authority",
    } & set(type(candidate.record).model_fields)

    restarted = AlphaRegistry(LocalArtifactStore(tmp_path), registry.export_history())
    assert restarted.head("alpha-fixture", "1.0.0") == candidate
    assert restarted.export_history() == (idea, candidate)


def test_registry_rejects_forks_skips_and_identity_drift(tmp_path: Path) -> None:
    registry = AlphaRegistry(LocalArtifactStore(tmp_path))
    idea = registry.append(_record(AlphaLifecycleStatus.IDEA), predecessor_sha256=None)

    with pytest.raises(AlphaRegistryError, match="transition"):
        registry.append(
            _record(
                AlphaLifecycleStatus.QUALIFIED,
                qualification_decision=QualificationDecision.PASS,
                metrics_sha256="2" * 64,
                robustness_sha256="3" * 64,
            ),
            predecessor_sha256=idea.event_sha256,
        )
    with pytest.raises(AlphaRegistryError, match="predecessor"):
        registry.append(
            _record(AlphaLifecycleStatus.CANDIDATE), predecessor_sha256="0" * 64
        )
    with pytest.raises(AlphaRegistryError, match="identity"):
        registry.append(
            _record(
                AlphaLifecycleStatus.CANDIDATE,
                dataset_snapshot_sha256="1" * 64,
            ),
            predecessor_sha256=idea.event_sha256,
        )
