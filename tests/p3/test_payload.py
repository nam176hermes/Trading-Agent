import json

import pytest
from pydantic import ValidationError

from packages.job_contracts import AlphaCampaignOperation, JobType, parse_alpha_campaign_payload, parse_payload


def _payload() -> dict[str, object]:
    ref = {"content_sha256":"a"*64,"size_bytes":1,"media_type":"application/json","locator":f"{'a'*64}.blob"}
    return {
        "schema_version":"p3-alpha-campaign-payload-v1",
        "operation":"BASELINES",
        "manifest_ref":ref,
        "authorization_ref":ref,
        "expected_source":{
            "commit_sha":"b"*40,"tree_sha":"c"*40,
            "closure_schema_version":"pre-p3-source-closure-v1",
            "closure_policy_sha256":"d"*64,"closure_sha256":"e"*64,
        },
        "logical_trial_id":"p3-baselines-001",
    }


def test_alpha_campaign_payload_is_closed_and_body_bounded() -> None:
    raw = json.dumps(_payload(),sort_keys=True,separators=(",",":")).encode()
    value = parse_alpha_campaign_payload(raw)
    assert value.operation is AlphaCampaignOperation.BASELINES
    assert parse_payload(JobType.ALPHA_CAMPAIGN,value.model_dump(mode="json")) == value
    with pytest.raises(ValueError,match="16 KiB"):
        parse_alpha_campaign_payload(raw + b" " * 16_384)


def test_alpha_campaign_payload_rejects_paths_and_raw_parameters() -> None:
    payload = _payload()
    payload["path"] = "/tmp/input"
    with pytest.raises(ValidationError):
        parse_alpha_campaign_payload(json.dumps(payload).encode())

