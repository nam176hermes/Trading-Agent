from __future__ import annotations

import os
from collections.abc import Mapping

import uvicorn

from services.operator_control.composition import (
    OperatorControlRuntimeSettings,
    build_production_operator_control_service,
)

from .app import create_app
from .auth import OperatorAuthenticator
from .config import OperatorApiSettings


def run(*, env: Mapping[str, str] | None = None) -> None:
    environment = os.environ if env is None else env
    settings = OperatorApiSettings.from_env(environment)
    authenticator = OperatorAuthenticator(settings)
    service = build_production_operator_control_service(
        OperatorControlRuntimeSettings()
    )
    uvicorn.run(
        create_app(settings, service, authenticator),
        host=settings.bind_host,
        port=settings.bind_port,
        access_log=False,
    )


if __name__ == "__main__":
    run()
