from __future__ import annotations

import os
from typing import Mapping

import uvicorn

from services.job_store import JobRepository, JobStoreSettings

from .app import create_app
from .config import JobApiSettings


def run(*, env: Mapping[str, str] | None = None) -> None:
    environment = os.environ if env is None else env
    settings = JobApiSettings.from_env(environment)
    authority = settings.load_authority()
    store_settings = JobStoreSettings.from_env(
        environment,
        expected_user="trading_job_api",
    )
    repository = JobRepository(store_settings)
    try:
        uvicorn.run(
            create_app(settings, repository, authority),
            host=settings.host,
            port=settings.port,
            access_log=False,
        )
    finally:
        close = getattr(repository, "close", None)
        if callable(close):
            close()


if __name__ == "__main__":
    run()
