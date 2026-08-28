from __future__ import annotations

import os
from collections.abc import Mapping

import sentry_sdk


def configure_sentry(env: Mapping[str, str] | None = None) -> bool:
    """Initialize Sentry only when this runtime is given a DSN."""

    values = os.environ if env is None else env
    dsn = values.get("SENTRY_DSN", "").strip()
    if not dsn:
        return False
    sentry_sdk.init(
        dsn=dsn,
        environment=values.get("SENTRY_ENVIRONMENT", "development"),
        send_default_pii=False,
    )
    return True
