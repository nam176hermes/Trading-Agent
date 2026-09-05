from pathlib import Path


def test_job_api_routes_alpha_campaign_to_the_scoped_sql_capability() -> None:
    source = (Path(__file__).parents[2]/"services/job_store/repository.py").read_text()
    assert "api_enqueue_alpha_campaign" in source
    assert "p_payload_text" not in source


def test_alpha_campaign_requires_the_explicit_p3_api_profile() -> None:
    source = (Path(__file__).parents[2]/"apps/job_api/app.py").read_text()
    assert "def create_p3_app(" in source
    assert "expected_revision == P3_DISPOSABLE_DATABASE_REVISION" in source
    assert "JOB_TYPE_NOT_AUTHORIZED" in source
