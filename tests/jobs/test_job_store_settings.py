from __future__ import annotations

from pathlib import Path

import pytest

from services.job_store.config import JobStoreSettings


BASE_ENV = {
    "TRADING_DATABASE_HOST": "127.0.0.1",
    "TRADING_DATABASE_PORT": "5432",
    "TRADING_DATABASE_NAME": "test_only",
    "TRADING_DATABASE_PASSWORD": "fixed-test-only-password",
}


def _credential_directory(root: Path) -> Path:
    root.mkdir(mode=0o700)
    for name, value in {
        "database-host": "127.0.0.1",
        "database-port": "5432",
        "database-name": "test_only",
        "database-password": "fixed-test-only-password",
    }.items():
        path = root / name
        path.write_text(value, encoding="utf-8")
        path.chmod(0o400)
    return root


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


def test_systemd_credentials_supply_database_settings_without_environment_values(
    tmp_path: Path,
) -> None:
    credential_root = _credential_directory(tmp_path / "credentials")

    settings = JobStoreSettings.from_systemd_credentials(
        {"CREDENTIALS_DIRECTORY": str(credential_root)},
        expected_user="trading_job_worker",
    )

    assert settings.host == "127.0.0.1"
    assert settings.port == 5432
    assert settings.database == "test_only"
    assert settings.user == "trading_job_worker"
    assert settings.password == "fixed-test-only-password"


def test_systemd_credentials_reject_symlinked_secret_without_disclosing_it(
    tmp_path: Path,
) -> None:
    credential_root = _credential_directory(tmp_path / "credentials")
    secret = credential_root / "database-password"
    secret.unlink()
    target = tmp_path / "outside-secret"
    target.write_text("do-not-disclose-this-secret", encoding="utf-8")
    target.chmod(0o400)
    secret.symlink_to(target)

    with pytest.raises(ValueError) as caught:
        JobStoreSettings.from_systemd_credentials(
            {"CREDENTIALS_DIRECTORY": str(credential_root)},
            expected_user="trading_job_worker",
        )

    assert "do-not-disclose-this-secret" not in str(caught.value)


def test_systemd_credentials_reject_symlinked_directory(tmp_path: Path) -> None:
    credential_root = _credential_directory(tmp_path / "credentials")
    alias = tmp_path / "credential-alias"
    alias.symlink_to(credential_root, target_is_directory=True)

    with pytest.raises(ValueError, match="invalid systemd credential"):
        JobStoreSettings.from_systemd_credentials(
            {"CREDENTIALS_DIRECTORY": str(alias)},
            expected_user="trading_job_worker",
        )


def test_systemd_credentials_reject_traversal_directory(tmp_path: Path) -> None:
    credential_root = _credential_directory(tmp_path / "credentials")
    traversing = f"{credential_root.parent}/../{tmp_path.name}/credentials"

    with pytest.raises(ValueError, match="invalid systemd credential"):
        JobStoreSettings.from_systemd_credentials(
            {"CREDENTIALS_DIRECTORY": traversing},
            expected_user="trading_job_worker",
        )


@pytest.mark.parametrize("mode", (0o420, 0o602))
def test_systemd_credentials_reject_unsafe_secret_mode(
    tmp_path: Path,
    mode: int,
) -> None:
    credential_root = _credential_directory(tmp_path / "credentials")
    sensitive_value = "unsafe-secret-must-not-leak"
    secret = credential_root / "database-password"
    secret.chmod(0o600)
    secret.write_text(sensitive_value, encoding="utf-8")
    secret.chmod(mode)

    with pytest.raises(ValueError) as caught:
        JobStoreSettings.from_systemd_credentials(
            {"CREDENTIALS_DIRECTORY": str(credential_root)},
            expected_user="trading_job_worker",
        )

    assert sensitive_value not in str(caught.value)


def test_systemd_credentials_reject_hardlinked_secret(tmp_path: Path) -> None:
    credential_root = _credential_directory(tmp_path / "credentials")
    secret = credential_root / "database-password"
    (tmp_path / "duplicate-secret").hardlink_to(secret)

    with pytest.raises(ValueError, match="invalid systemd credential"):
        JobStoreSettings.from_systemd_credentials(
            {"CREDENTIALS_DIRECTORY": str(credential_root)},
            expected_user="trading_job_worker",
        )


def test_systemd_database_validation_error_does_not_disclose_credential(
    tmp_path: Path,
) -> None:
    credential_root = _credential_directory(tmp_path / "credentials")
    sensitive_value = "private-invalid-port-must-not-leak"
    port = credential_root / "database-port"
    port.chmod(0o600)
    port.write_text(sensitive_value, encoding="utf-8")
    port.chmod(0o400)

    with pytest.raises(ValueError) as caught:
        JobStoreSettings.from_systemd_credentials(
            {"CREDENTIALS_DIRECTORY": str(credential_root)},
            expected_user="trading_job_worker",
        )

    assert sensitive_value not in str(caught.value)
    assert sensitive_value not in repr(caught.value)
