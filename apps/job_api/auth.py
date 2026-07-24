from __future__ import annotations

import hmac

from starlette.types import Scope

from packages.job_contracts import ActorIdentity

from .config import JobApiSettings
from .errors import JobApiError


class BearerAuthenticator:
    """Authenticate the server-to-server credential without retaining request data."""

    def __init__(self, settings: JobApiSettings) -> None:
        self._configured_token = (
            settings.bearer_token.encode("utf-8")
            if settings.bearer_token is not None
            else None
        )
        self._principal = settings.principal

    def authenticate(self, scope: Scope) -> ActorIdentity:
        if self._configured_token is None or self._principal is None:
            raise JobApiError(
                503,
                "AUTHENTICATION_UNAVAILABLE",
                "Service authentication is not configured.",
            )
        values = [
            value
            for key, value in scope.get("headers", ())
            if key.lower() == b"authorization"
        ]
        if len(values) != 1:
            raise JobApiError(
                401,
                "AUTHENTICATION_REQUIRED",
                "Valid bearer authentication is required.",
            )
        scheme, separator, credential = values[0].partition(b" ")
        if (
            not separator
            or scheme.lower() != b"bearer"
            or not credential
            or not hmac.compare_digest(credential, self._configured_token)
        ):
            raise JobApiError(
                401,
                "AUTHENTICATION_REQUIRED",
                "Valid bearer authentication is required.",
            )
        return self._principal
