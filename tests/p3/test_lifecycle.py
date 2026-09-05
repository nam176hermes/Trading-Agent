from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from packages.alpha_lifecycle.contracts.lifecycle import PrePublicationEvidence
from packages.alpha_lifecycle.lifecycle import plan_transition
from packages.alpha_lifecycle.registry import AlphaLifecycleStatus, AlphaRecordV1, AlphaRegistryError, QualificationDecision
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import canonical_json_bytes


def _ref(value: str) -> ArtifactRefV1:
    digest = value * 64
    return ArtifactRefV1(content_sha256=digest, size_bytes=1, media_type="application/json", locator=f"{digest}.blob")


def _record(status: AlphaLifecycleStatus) -> AlphaRecordV1:
    return AlphaRecordV1(
        alpha_id="a0.donchian-20-10-close-confirm", version="1.0.0", source_sha="a"*40,
        implementation_identity="packages.alpha_lifecycle.candidates:run_candidate",
        dataset_snapshot_sha256="b"*64, feature_set=("close", "high", "low"),
        parameter_set_sha256="c"*64,
        training_start_at=datetime(2018,1,1,tzinfo=UTC), training_end_at=datetime(2021,8,31,tzinfo=UTC),
        validation_start_at=datetime(2021,9,1,tzinfo=UTC), validation_end_at=datetime(2022,8,29,tzinfo=UTC),
        oos_start_at=datetime(2022,9,1,tzinfo=UTC), oos_end_at=datetime(2025,8,31,tzinfo=UTC),
        universe=("BTCUSDT.BINANCE",), cost_model_sha256="d"*64,
        baseline_id="B0_CASH", baseline_version="1.0.0", metrics_sha256=None,
        robustness_sha256=None, qualification_decision=QualificationDecision.NOT_EVALUATED,
        qualification_reason="preregistered", artifact_digests=("e"*64,), lineage=("p3-btc-d1-e1",),
        superseded_version=None, lifecycle_status=status,
    )


def _evidence() -> PrePublicationEvidence:
    payload = {
        "schema_version":"p3-pre-publication-evidence-v1", "stage":"REGISTER",
        "input_set_ref":_ref("1"), "baseline_selection_ref":_ref("2"),
        "qualification_bundle_ref":None, "exit_result_ref":None,
        "candidate_record_refs":(_ref("3"),),
    }
    payload["digest"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return PrePublicationEvidence.model_validate(payload)


def test_transition_planning_is_hash_bound_but_not_durable_acceptance() -> None:
    event = plan_transition(_record(AlphaLifecycleStatus.IDEA), None, _evidence())
    assert event.sequence == 1
    assert event.artifact.content_sha256 == event.event_sha256
    with pytest.raises(AlphaRegistryError, match="start at IDEA"):
        plan_transition(_record(AlphaLifecycleStatus.CANDIDATE), None, _evidence())
