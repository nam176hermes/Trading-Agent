from uuid import UUID
import pytest

from packages.alpha_lifecycle.contracts.base import SourceIdentity
from packages.data_contracts import ArtifactRefV1
from packages.job_contracts import AlphaCampaignOperation, AlphaCampaignPayload
from scripts.p3_authority import build_enqueue_body


def _ref(value: str) -> ArtifactRefV1:
    return ArtifactRefV1(
        content_sha256=value * 64,
        size_bytes=1,
        media_type="application/json",
        locator=f"{value * 64}.blob",
    )


def test_dispatch_body_is_closed_and_idempotent_by_authorization_nonce() -> None:
    payload = AlphaCampaignPayload(
        schema_version="p3-alpha-campaign-payload-v1",
        operation=AlphaCampaignOperation.BASELINES,
        manifest_ref=_ref("a"),
        authorization_ref=_ref("b"),
        expected_source=SourceIdentity(
            commit_sha="c" * 40,
            tree_sha="d" * 40,
            closure_schema_version="source-closure-v1",
            closure_policy_sha256="e" * 64,
            closure_sha256="f" * 64,
        ),
        logical_trial_id="p3-baselines-v1",
    )
    body = build_enqueue_body(payload, UUID("00000000-0000-4000-8000-000000000001"))
    assert body.job_type.value == "ALPHA_CAMPAIGN"
    assert body.payload == payload
    assert (
        body.idempotency_key
        == "p3:p3-baselines-v1:00000000-0000-4000-8000-000000000001"
    )
    assert body.priority == 0


@pytest.mark.parametrize(
    "drift",
    (
        None,
        "GITHUB_SHA",
        "GITHUB_RUN_ID",
        "GITHUB_WORKFLOW_REF",
        "GITHUB_REF_PROTECTED",
        "GITHUB_EVENT_NAME",
    ),
)
def test_shape_preflight_does_not_emit_an_execution_approval(
    tmp_path, monkeypatch, drift
):
    from datetime import UTC, datetime, timedelta
    import hashlib
    import json
    from scripts import p3_authority
    from packages.engine_contracts.serialization import canonical_json_bytes
    from tests.p3.test_authority import _request
    from tests.p3.test_operation_input import intent

    request, source = _request("2026-01-01T01:00:00Z")
    now = datetime.now(UTC)
    body = request["authorization"]
    body.update(
        issued_at=(now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
        expires_at=(now + timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
    )
    body.pop("digest")
    body["digest"] = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
    request["operation_input"] = json.loads(intent(input_set_ref=body["input_set_ref"]))
    path = tmp_path / "request.json"
    path.write_bytes(canonical_json_bytes(request))
    path.chmod(0o600)
    monkeypatch.setattr(
        p3_authority,
        "canonical_source_identity",
        lambda root: source.model_dump(mode="json"),
    )
    monkeypatch.setattr(
        p3_authority,
        "derive_project_status",
        lambda root: {
            "gates": {"HWC_SOURCE_READY": "PASS", "PRE_P3_READY": "PASS"},
            "p3_alpha_development_allowed": True,
        },
    )
    context = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_REPOSITORY": "nam176hermes/Trading-Agent",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_SHA": source.commit_sha,
        "GITHUB_REF_PROTECTED": "true",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_RUN_ID": "1",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_WORKFLOW_REF": "nam176hermes/Trading-Agent/.github/workflows/p3-authority.yml@refs/heads/main",
    }
    for key, value in context.items():
        monkeypatch.setenv(key, "wrong" if key == drift else value)
    output = tmp_path / "output"
    if drift:
        from packages.alpha_lifecycle.authority import AuthorityHeld

        with pytest.raises(AuthorityHeld, match="ISSUER"):
            p3_authority.preflight(path, output, "p3-baselines-v1")
        return
    p3_authority.preflight(path, output, "p3-baselines-v1")
    artifact = json.loads((output / "preflight.json").read_bytes())
    assert artifact["status"] == "STRUCTURE_VALIDATED"
    assert artifact["execution_authorized"] is False
    inventory = json.loads((output / "input-inventory.json").read_bytes())
    payload = json.loads((output / "payload.json").read_bytes())
    assert inventory["authorization_ref"] == payload["authorization_ref"]
