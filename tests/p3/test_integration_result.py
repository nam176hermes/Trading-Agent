"""Fixture receipts must belong to the source of the durable job."""

import hashlib
import io
from dataclasses import replace

import pytest

from packages.engine_contracts.serialization import canonical_json_bytes
from packages.job_contracts import JobType
from services.job_worker.artifacts import ArtifactWriter
from services.job_worker.results import ResultValidationError, ResultValidator
from tests.jobs.test_worker_lifecycle import claim
from tests.p3.test_job_api import _alpha_request


@pytest.mark.parametrize("changed", [
    None, "commit_sha", "tree_sha", "closure_schema_version",
    "closure_policy_sha256", "closure_sha256",
])
def test_integration_receipt_binds_every_source_field(tmp_path, changed):
    payload = _alpha_request().payload.model_copy(update={
        "operation": "PARITY", "logical_trial_id": "p3-integration-fixture-v1",
    })
    job = replace(claim(), job_type=JobType.ALPHA_CAMPAIGN, payload=payload)
    source = payload.expected_source.model_dump(mode="json")
    if changed:
        source[changed] = "other-schema" if changed == "closure_schema_version" else "0" * len(source[changed])
    ref = payload.manifest_ref
    value = {
        "schema_version": "p3-integration-qualified-v1", "source": source,
        "sql_proof_ref": ref, "native_fixture_proof_ref": ref,
        "cleanup_proof_ref": ref, "workflow_run_id": 1, "workflow_attempt": 1,
        "status": "PASS", "authority": {
            "broker": False, "live": False, "network": False, "production": False,
        },
    }
    value["digest"] = hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    root = tmp_path / "artifacts"
    stream = ArtifactWriter(root).capture_stream(
        job.job_id, job.attempt_id, "stdout", io.BytesIO(canonical_json_bytes(value)),
    )
    validator = ResultValidator(tmp_path / "reports", tmp_path / "replay", root)
    if changed:
        with pytest.raises(ResultValidationError, match="source"):
            validator.validate_p3(value["schema_version"], job, stream=stream, exit_code=0)
        assert not (root / "results").exists()
    else:
        result = validator.validate_p3(value["schema_version"], job, stream=stream, exit_code=0)
        assert result.validation_metadata["result_digest"] == value["digest"]
