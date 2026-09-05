from uuid import UUID

from packages.alpha_lifecycle.contracts.base import SourceIdentity
from packages.data_contracts import ArtifactRefV1
from packages.job_contracts import AlphaCampaignOperation, AlphaCampaignPayload
from scripts.p3_authority import build_enqueue_body


def _ref(value: str) -> ArtifactRefV1:
    return ArtifactRefV1(
        content_sha256=value * 64, size_bytes=1,
        media_type="application/json", locator=f"{value * 64}.blob",
    )


def test_dispatch_body_is_closed_and_idempotent_by_authorization_nonce() -> None:
    payload = AlphaCampaignPayload(
        schema_version="p3-alpha-campaign-payload-v1",
        operation=AlphaCampaignOperation.BASELINES,
        manifest_ref=_ref("a"), authorization_ref=_ref("b"),
        expected_source=SourceIdentity(
            commit_sha="c" * 40, tree_sha="d" * 40,
            closure_schema_version="source-closure-v1",
            closure_policy_sha256="e" * 64, closure_sha256="f" * 64,
        ),
        logical_trial_id="p3-baselines-v1",
    )
    body = build_enqueue_body(payload, UUID("00000000-0000-4000-8000-000000000001"))
    assert body.job_type.value == "ALPHA_CAMPAIGN"
    assert body.payload == payload
    assert body.idempotency_key == "p3:p3-baselines-v1:00000000-0000-4000-8000-000000000001"
    assert body.priority == 0
