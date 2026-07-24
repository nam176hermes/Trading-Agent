from __future__ import annotations

import pytest

from services.job_store.config import JobStoreSettings


BASE_ENV = {
    "TRADING_DATABASE_HOST": "127.0.0.1",
    "TRADING_DATABASE_PORT": "5432",
    "TRADING_DATABASE_NAME": "test_only",
    "TRADING_DATABASE_PASSWORD": "fixed-test-only-password",
}


@pytest.mark.parametrize(
    "expected_user",
    (
        "trading_job_api",
        "trading_job_worker",
        "trading_job_scheduler",
    ),
)
def test_from_env_requires_the_exact_job_plane_role(expected_user: str) -> None:
    settings = JobStoreSettings.from_env(
        {**BASE_ENV, "TRADING_DATABASE_USER": expected_user},
        expected_user=expected_user,
    )

    assert settings.user == expected_user
    assert settings.require_user(expected_user) is settings


@pytest.mark.parametrize(
    ("configured_user", "expected_user"),
    (
        ("trading_job_worker", "trading_job_api"),
        ("trading_job_api", "trading_job_scheduler"),
        ("trading_jobs", "trading_job_api"),
        ("trading_reader", "trading_job_worker"),
    ),
)
def test_from_env_rejects_shared_or_cross_role_reuse(
    configured_user: str,
    expected_user: str,
) -> None:
    with pytest.raises(ValueError, match="job database user does not match"):
        JobStoreSettings.from_env(
            {**BASE_ENV, "TRADING_DATABASE_USER": configured_user},
            expected_user=expected_user,
        )


@pytest.mark.parametrize(
    "expected_user",
    ("trading_jobs", "trading_reader", "postgres", "", "TRADING_JOB_API"),
)
def test_expected_role_itself_is_closed_to_unknown_or_shared_roles(
    expected_user: str,
) -> None:
    fixed_value = "fixed-test-only-password"
    settings = JobStoreSettings(
        host="127.0.0.1",
        port=5432,
        database="test_only",
        user="trading_job_api",
        password=fixed_value,
    )

    with pytest.raises(ValueError, match="expected job database user is not allowed"):
        settings.require_user(expected_user)


def test_from_env_cannot_omit_expected_role() -> None:
    with pytest.raises(TypeError, match="expected_user"):
        JobStoreSettings.from_env(
            {**BASE_ENV, "TRADING_DATABASE_USER": "trading_job_api"}
        )


def test_role_failure_and_representation_do_not_expose_password() -> None:
    sensitive_value = "do-not-render-this-fixed-test-value"
    values = {
        **BASE_ENV,
        "TRADING_DATABASE_USER": "trading_job_worker",
        "TRADING_DATABASE_PASSWORD": sensitive_value,
    }

    with pytest.raises(ValueError) as caught:
        JobStoreSettings.from_env(values, expected_user="trading_job_api")

    assert sensitive_value not in str(caught.value)
    settings = JobStoreSettings.from_env(
        values, expected_user="trading_job_worker"
    )
    assert sensitive_value not in repr(settings)
