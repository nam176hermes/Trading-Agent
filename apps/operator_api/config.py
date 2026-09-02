"""Strict fixed-principal Operator API settings."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


_PRINCIPAL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_ENVIRONMENT_KEYS = (
    "OPERATOR_API_WEB_TOKEN_FILE",
    "OPERATOR_API_WEB_PRINCIPAL_ID",
    "OPERATOR_API_CLI_TOKEN_FILE",
    "OPERATOR_API_CLI_PRINCIPAL_ID",
)


class OperatorApiConfigurationError(ValueError):
    """Operator API configuration or credential authority is unavailable."""


def _path(value: object) -> Path:
    if not isinstance(value, (str, Path)):
        raise OperatorApiConfigurationError("operator API configuration is invalid")
    raw = os.fspath(value)
    path = Path(raw)
    if (
        not path.is_absolute()
        or path.anchor != "/"
        or path == Path("/")
        or os.path.normpath(raw) != raw
        or path.as_posix() != raw
        or any(part in {"", ".", ".."} for part in path.parts[1:])
    ):
        raise OperatorApiConfigurationError("operator API configuration is invalid")
    return path


def _principal(value: object) -> str:
    if not isinstance(value, str) or _PRINCIPAL.fullmatch(value) is None:
        raise OperatorApiConfigurationError("operator API configuration is invalid")
    return value


@dataclass(frozen=True, slots=True)
class OperatorApiSettings:
    web_token_file: Path
    web_principal_id: str
    cli_token_file: Path
    cli_principal_id: str
    bind_host: Literal["127.0.0.1"] = "127.0.0.1"
    bind_port: Literal[8402] = 8402

    def __post_init__(self) -> None:
        object.__setattr__(self, "web_token_file", _path(self.web_token_file))
        object.__setattr__(self, "cli_token_file", _path(self.cli_token_file))
        object.__setattr__(self, "web_principal_id", _principal(self.web_principal_id))
        object.__setattr__(self, "cli_principal_id", _principal(self.cli_principal_id))
        if (
            self.bind_host != "127.0.0.1"
            or self.bind_port != 8402
            or self.web_principal_id == self.cli_principal_id
        ):
            raise OperatorApiConfigurationError("operator API configuration is invalid")

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None
    ) -> "OperatorApiSettings":
        source = os.environ if env is None else env
        try:
            values = [source[key] for key in _ENVIRONMENT_KEYS]
        except (KeyError, TypeError):
            raise OperatorApiConfigurationError(
                "operator API configuration is invalid"
            ) from None
        return cls(_path(values[0]), values[1], _path(values[2]), values[3])


__all__ = ["OperatorApiConfigurationError", "OperatorApiSettings"]
