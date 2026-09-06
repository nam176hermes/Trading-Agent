import hashlib

import pytest

from packages.alpha_lifecycle.contracts.authority import CustodyRecord
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import canonical_json_bytes


def test_holdout_custodian_must_be_distinct_from_researcher() -> None:
    digest = "a"*64
    ref = ArtifactRefV1(content_sha256=digest,size_bytes=1,media_type="application/octet-stream",locator=f"{digest}.blob")
    payload = {
        "schema_version":"p3-custody-record-v1","holdout_commitment":"b"*64,
        "ciphertext_ref":ref,"plaintext_bundle_digest":"c"*64,
        "custodian_identity":"same","research_identity":"same",
        "custodian_attestation_ref":ref,"access_policy_digest":"d"*64,
        "classification":"HISTORICAL_ACCESS_CONTROLLED_NOT_INFORMATIONALLY_BLIND",
    }
    payload["digest"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    with pytest.raises(ValueError,match="distinct"):
        CustodyRecord.model_validate(payload)
