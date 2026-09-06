from pathlib import Path
import hashlib
import json

import pytest

from packages.alpha_lifecycle.authority import (
    AuthorityHeld,
    build_alpha_campaign_payload,
    validate_request,
    validate_workflow_operation,
)
from packages.alpha_lifecycle.contracts.authority import RunAuthorization
from packages.alpha_lifecycle.contracts.base import SourceIdentity
from packages.engine_contracts.serialization import canonical_json_bytes


def test_workflow_operation_is_a_closed_enum() -> None:
    assert validate_workflow_operation("p3-baselines-v1") == "BASELINES"
    assert validate_workflow_operation("p3-oos-a3-v1") == "OOS"
    with pytest.raises(AuthorityHeld):
        validate_workflow_operation("p3-oos-a4-v1")


def test_authority_request_rejects_group_readable_file(tmp_path) -> None:
    path = tmp_path/"request.json"; path.write_text("{}")
    path.chmod(0o640)
    with pytest.raises(AuthorityHeld,match="0600"):
        validate_request(path,expected_source=None,operation="BASELINES")


def _request(expires_at: str) -> tuple[dict[str, object], SourceIdentity]:
    source = SourceIdentity(
        commit_sha="a" * 40,
        tree_sha="b" * 40,
        closure_schema_version="pre-p3-source-closure-v1",
        closure_policy_sha256="c" * 64,
        closure_sha256="d" * 64,
    )
    ref = {
        "content_sha256": "e" * 64,
        "size_bytes": 1,
        "media_type": "application/json",
        "locator": f"{'e' * 64}.blob",
    }
    authorization = {
        "schema_version": "p3-run-authorization-v1",
        "input_set_ref": ref,
        "review_ref": ref,
        "operation": "BASELINES",
        "allowed_alpha_ids": [],
        "issued_at": "2026-01-01T00:00:00Z",
        "expires_at": expires_at,
        "nonce": "00000000-0000-4000-8000-000000000001",
        "issuer_workflow": "p3-authority.yml",
        "issuer_run_id": 1,
        "issuer_attempt": 1,
        "authority": {"broker": False, "live": False, "network": False, "production": False},
    }
    authorization["digest"] = hashlib.sha256(canonical_json_bytes(authorization)).hexdigest()
    return {
        "schema_version": "p3-authority-request-file-v1",
        "execution_source": source.model_dump(mode="json"),
        "authorization": authorization,
    }, source


def test_authority_request_is_source_bound_and_unexpired(tmp_path) -> None:
    request, source = _request("2026-01-01T01:00:00Z")
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request))
    path.chmod(0o600)
    with pytest.raises(AuthorityHeld, match="not current"):
        validate_request(path.resolve(), source, "BASELINES")


def test_protected_request_builds_one_closed_worker_payload() -> None:
    request, source = _request("2026-01-01T01:00:00Z")
    authorization = RunAuthorization.model_validate_json(
        json.dumps(request["authorization"])
    )
    payload = build_alpha_campaign_payload(
        authorization, source, "p3-baselines-v1"
    )
    assert payload.operation == "BASELINES"
    assert payload.logical_trial_id == "p3-baselines-v1"
    assert payload.manifest_ref == authorization.input_set_ref
    assert payload.authorization_ref.locator == (
        f"{payload.authorization_ref.content_sha256}.blob"
    )
