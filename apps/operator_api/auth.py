"""Descriptor-safe two-principal bearer authentication."""

from __future__ import annotations

import hmac
from pathlib import Path

from starlette.types import Scope

from packages.operator_control.contracts import OperatorActorV1
from packages.operator_control.credentials import (
    PrivateTokenError,
    load_private_token as _load_private_token,
)

from .config import OperatorApiConfigurationError, OperatorApiSettings
from .errors import OperatorApiError


def load_private_token(path: Path) -> bytes:
    try:
        return _load_private_token(path)
    except PrivateTokenError:
        raise OperatorApiConfigurationError(
            "operator credential authority is unavailable"
        ) from None


class OperatorAuthenticator:
    def __init__(self, settings: OperatorApiSettings) -> None:
        self._web_token = load_private_token(settings.web_token_file)
        self._cli_token = load_private_token(settings.cli_token_file)
        if hmac.compare_digest(self._web_token, self._cli_token):
            raise OperatorApiConfigurationError(
                "operator credential authority is unavailable"
            )
        self._web_principal = settings.web_principal_id
        self._cli_principal = settings.cli_principal_id

    def authenticate(self, scope: Scope) -> OperatorActorV1:
        values = [
            value
            for key, value in scope.get("headers", ())
            if key.lower() == b"authorization"
        ]
        valid = len(values) == 1
        raw = values[0] if valid else b""
        scheme, separator, credential = raw.partition(b" ")
        valid = bool(
            valid
            and separator
            and scheme.lower() == b"bearer"
            and credential
            and b" " not in credential
        )
        candidate = credential if valid else b""
        web = hmac.compare_digest(candidate, self._web_token)
        cli = hmac.compare_digest(candidate, self._cli_token)
        if not valid or web == cli:
            raise OperatorApiError(
                401,
                "AUTHENTICATION_REQUIRED",
                "Valid bearer authentication is required.",
            )
        return OperatorActorV1(
            schema_version="operator-actor-v1",
            principal_id=self._web_principal if web else self._cli_principal,
            interface="WEB" if web else "CLI",
        )


__all__ = ["OperatorAuthenticator", "load_private_token"]
