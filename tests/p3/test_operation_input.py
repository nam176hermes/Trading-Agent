"""Private operation intents grant no host or database authority."""

import hashlib

import pytest

from packages.alpha_lifecycle.operation_input import P3OperationInput
from packages.engine_contracts.serialization import canonical_json_bytes
from tests.p3.test_replay import _ref


def intent(**updates):
    payload = {
        "schema_version": "p3-operation-input-v1",
        "workflow_operation": "p3-baselines-v1",
        "operation": "BASELINES",
        "input_set_ref": _ref("1" * 64).model_dump(mode="json"),
        "allowed_alpha_ids": [],
        "body": {"baseline_manifest_ref": _ref("2" * 64).model_dump(mode="json")},
        **updates,
    }
    payload["digest"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return canonical_json_bytes(payload)


def test_baseline_intent_has_exact_operation_roots_and_roundtrips():
    raw = intent()
    value = P3OperationInput.model_validate_json(raw)
    assert canonical_json_bytes(value) == raw
    assert value.workflow_operation == "p3-baselines-v1"
    assert value.allowed_alpha_ids == ()


@pytest.mark.parametrize(
    "updates",
    (
        {"workflow_operation": "p3-integration-fixture-v1", "operation": "PARITY"},
        {"workflow_operation": "p3-oos-a1-v1", "operation": "OOS"},
        {"operation": "HOLDOUT"},
        {"allowed_alpha_ids": ["a0.example"]},
        {"review_ref": _ref("3" * 64).model_dump(mode="json")},
        {
            "body": {
                "baseline_manifest_ref": _ref("2" * 64).model_dump(mode="json"),
                "authorization_ref": _ref("3" * 64).model_dump(mode="json"),
            }
        },
    ),
)
def test_intent_rejects_operation_confusion_and_review_cycles(updates):
    with pytest.raises(ValueError):
        P3OperationInput.model_validate_json(intent(**updates))


def test_oos_intent_authorizes_exactly_one_alpha_for_its_workflow():
    body = {"evaluation_manifest_ref": _ref("2" * 64).model_dump(mode="json")}
    raw = intent(
        workflow_operation="p3-oos-a0-v1",
        operation="OOS",
        allowed_alpha_ids=["a0.donchian-20-10-close-confirm"],
        body=body,
    )
    assert P3OperationInput.model_validate_json(raw).operation == "OOS"
    for ids in ([], ["a1.dual-sma-50-200"], ["a0.donchian-20-10-close-confirm"] * 2):
        with pytest.raises(ValueError):
            P3OperationInput.model_validate_json(
                intent(
                    workflow_operation="p3-oos-a0-v1",
                    operation="OOS",
                    allowed_alpha_ids=ids,
                    body=body,
                )
            )


def test_official_payload_cannot_treat_an_input_set_as_an_execution_manifest():
    import json
    from packages.alpha_lifecycle.authority import (
        AuthorityHeld,
        build_alpha_campaign_payload,
    )
    from packages.alpha_lifecycle.contracts.authority import RunAuthorization
    from tests.p3.test_authority import _request

    request, source = _request("2026-01-01T01:00:00Z")
    authorization = RunAuthorization.model_validate_json(
        json.dumps(request["authorization"])
    )
    with pytest.raises(AuthorityHeld, match="operation input"):
        build_alpha_campaign_payload(authorization, source, "p3-baselines-v1")


def test_staging_rejects_a_review_that_never_approved_the_operation(tmp_path):
    from packages.alpha_lifecycle.authority import (
        AuthorityHeld,
        stage_alpha_campaign_payload,
    )
    from packages.alpha_lifecycle.contracts.authority import RunAuthorization
    from packages.data_catalog.artifact_store import LocalArtifactStore
    from tests.p3.test_authority import _request

    tmp_path.chmod(0o700)
    store = LocalArtifactStore(tmp_path)
    review_ref = store.put_bytes(b"{}", media_type="application/json")
    request, source = _request("2026-01-01T01:00:00Z")
    body = request["authorization"]
    body["review_ref"] = review_ref.model_dump(mode="json")
    body.pop("digest")
    body["digest"] = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
    authorization = RunAuthorization.model_validate_json(canonical_json_bytes(body))
    operation_input = P3OperationInput.model_validate_json(
        intent(input_set_ref=body["input_set_ref"])
    )
    with pytest.raises(AuthorityHeld, match="REVIEW"):
        stage_alpha_campaign_payload(
            store,
            authorization,
            source,
            "p3-baselines-v1",
            canonical_json_bytes(operation_input),
            operation_input=operation_input,
        )


@pytest.mark.parametrize(
    "edge", ("authorization_predates_review", "authorization_outlives_review")
)
def test_staging_requires_authorization_inside_its_review_window(tmp_path, edge):
    from datetime import UTC, datetime, timedelta
    from packages.alpha_lifecycle.authority import (
        AuthorityHeld,
        stage_alpha_campaign_payload,
    )
    from packages.alpha_lifecycle.contracts.authority import RunAuthorization
    from packages.data_catalog.artifact_store import LocalArtifactStore
    from tests.p3.test_authority import _request
    from tests.p3.test_replica_execution import _seal

    tmp_path.chmod(0o700)
    store = LocalArtifactStore(tmp_path)
    request, source = _request("2026-01-01T01:00:00Z")
    body = request["authorization"]
    operation_input = P3OperationInput.model_validate_json(
        intent(input_set_ref=body["input_set_ref"])
    )
    now = datetime.now(UTC)
    utc = lambda value: value.isoformat().replace("+00:00", "Z")
    evidence = store.put_bytes(b"{}", media_type="application/json")
    review = _seal(
        store,
        schema_version="p3-review-approval-v1",
        source=source,
        subject_digests=[operation_input.digest],
        operator_identity="operator",
        reviewer_identity="reviewer",
        review_execution_id="synthetic",
        verdict="APPROVED",
        issued_at=utc(now - timedelta(minutes=2)),
        expires_at=utc(now + timedelta(minutes=2)),
        evidence_ref=evidence,
        authority=body["authority"],
    )
    body.update(
        review_ref=review.model_dump(mode="json"),
        issued_at=utc(
            now - timedelta(minutes=3 if edge == "authorization_predates_review" else 1)
        ),
        expires_at=utc(
            now + timedelta(minutes=3 if edge == "authorization_outlives_review" else 1)
        ),
    )
    body.pop("digest")
    body["digest"] = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
    authorization = RunAuthorization.model_validate_json(canonical_json_bytes(body))
    with pytest.raises(AuthorityHeld, match="REVIEW"):
        stage_alpha_campaign_payload(
            store,
            authorization,
            source,
            "p3-baselines-v1",
            canonical_json_bytes(operation_input),
            operation_input=operation_input,
        )


@pytest.mark.parametrize(
    ("workflow", "operation", "fields", "whole_family"),
    (
        (
            "p3-register-family-v1",
            "REGISTER_FAMILY",
            ("baseline_selection_ref", "candidate_spec_refs", "candidate_record_refs"),
            True,
        ),
        ("p3-select-primary-v1", "OOS", ("family_review_ref",), True),
        (
            "p3-holdout-primary-v1",
            "HOLDOUT",
            (
                "primary_selection_ref",
                "candidate_spec_ref",
                "registration_proof_ref",
                "custody_record_ref",
                "holdout_input_set_ref",
                "holdout_dataset_ref",
                "context_dataset_ref",
                "buffer_ref",
                "environment_ref",
                "policy_digest",
            ),
            False,
        ),
        (
            "p3-native-parity-v1",
            "PARITY",
            (
                "holdout_manifest_ref",
                "primary_reference_ref",
                "baseline_reference_ref",
                "instrument_spec_ref",
                "native_request_ref",
            ),
            False,
        ),
        (
            "p3-phase-exit-v1",
            "PHASE_EXIT",
            (
                "primary_selection_ref",
                "primary_qualification_ref",
                "baseline_selection_ref",
                "holdout_request_ref",
                "holdout_evaluation_ref",
                "holdout_replay_ref",
                "executable_ref",
                "baseline_executable_ref",
                "parity_ref",
                "current_primary_head_ref",
            ),
            False,
        ),
    ),
)
def test_each_operation_has_one_closed_body_and_alpha_scope(
    workflow, operation, fields, whole_family
):
    family = [
        "a0.donchian-20-10-close-confirm",
        "a1.dual-sma-50-200",
        "a2.zscore-20-long-reversion",
        "a3.tsmom-21-63-126",
    ]
    ref = _ref("2" * 64).model_dump(mode="json")
    body = {
        name: "3" * 64
        if name == "policy_digest"
        else [ref] * 4
        if name.endswith("_refs")
        else ref
        for name in fields
    }
    ids = family if whole_family else family[:1]
    value = P3OperationInput.model_validate_json(
        intent(
            workflow_operation=workflow,
            operation=operation,
            allowed_alpha_ids=ids,
            body=body,
        )
    )
    assert value.workflow_operation == workflow
    with pytest.raises(ValueError):
        P3OperationInput.model_validate_json(
            intent(
                workflow_operation=workflow,
                operation=operation,
                allowed_alpha_ids=ids,
                body={"baseline_manifest_ref": ref},
            )
        )
    with pytest.raises(ValueError):
        P3OperationInput.model_validate_json(
            intent(
                workflow_operation=workflow,
                operation=operation,
                allowed_alpha_ids=ids[:-1] if whole_family else family,
                body=body,
            )
        )
