from __future__ import annotations

import os
from typing import Mapping

import uvicorn

from packages.runtime_release import validate_job_plane_authority
from services.job_store import JobRepository, JobStoreSettings

from .app import create_app
from .config import JobApiSettings


def run(*, env: Mapping[str, str] | None = None) -> None:
    environment = os.environ if env is None else env
    authority = JobApiSettings(
        authority_factory=validate_job_plane_authority
    ).load_authority()
    if "CREDENTIALS_DIRECTORY" in environment:
        settings = JobApiSettings.from_systemd_credentials(environment)
    else:
        settings = JobApiSettings.from_env(environment)
    if "CREDENTIALS_DIRECTORY" in environment:
        store_settings = JobStoreSettings.from_systemd_credentials(
            environment,
            expected_user="trading_job_api",
        )
    else:
        store_settings = JobStoreSettings.from_env(
            environment,
            expected_user="trading_job_api",
        )
    with JobRepository(store_settings) as repository:
        uvicorn.run(
            create_app(settings, repository, authority),
            host=settings.host,
            port=settings.port,
            access_log=False,
        )


if __name__ == "__main__":
    run()
