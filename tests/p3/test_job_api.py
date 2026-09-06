from pathlib import Path

from packages.job_contracts import EnqueueJobRequest
from services.job_store.records import EnqueueOutcome
from tests.jobs.test_repository_transition_capabilities import (
    _Connection,
    _job_row,
    _repository,
)


def _alpha_request() -> EnqueueJobRequest:
    def ref(value: str) -> dict[str, object]:
        return {
            "content_sha256": value * 64,
            "size_bytes": 1,
            "media_type": "application/json",
            "locator": f"{value * 64}.blob",
        }
    return EnqueueJobRequest.model_validate({
        "job_type": "ALPHA_CAMPAIGN",
        "payload": {
            "schema_version": "p3-alpha-campaign-payload-v1",
            "operation": "BASELINES",
            "manifest_ref": ref("a"),
            "authorization_ref": ref("b"),
            "expected_source": {
                "commit_sha": "c" * 40,
                "tree_sha": "d" * 40,
                "closure_schema_version": "source-closure-v1",
                "closure_policy_sha256": "e" * 64,
                "closure_sha256": "f" * 64,
            },
            "logical_trial_id": "p3-baselines-v1",
        },
        "idempotency_key": "p3:baselines:1",
        "actor": {"actor_type": "OPERATOR", "actor_id": "operator-p3"},
    })


def test_job_api_routes_alpha_campaign_to_the_scoped_sql_capability() -> None:
    request = _alpha_request()
    connection = _Connection(
        {"job_id": "job_fixed", "outcome": "ENQUEUED"},
        _job_row(request),
    )
    repository = _repository(connection)
    generated = iter(("job_fixed", "event_fixed"))
    repository._new_id = lambda _prefix: next(generated)

    result = repository.enqueue(request, trace_id="p3:enqueue")

    assert result.outcome is EnqueueOutcome.ENQUEUED
    assert result.job.job_id == "job_fixed"
    assert "job_plane.api_enqueue_alpha_campaign" in connection.calls[0][0]
    assert "p_payload_text" not in connection.calls[0][0]


def test_alpha_campaign_requires_the_explicit_p3_api_profile() -> None:
    source = (Path(__file__).parents[2]/"apps/job_api/app.py").read_text()
    assert "def create_p3_app(" in source
    assert "expected_revision == P3_DISPOSABLE_DATABASE_REVISION" in source
    assert "JOB_TYPE_NOT_AUTHORIZED" in source
