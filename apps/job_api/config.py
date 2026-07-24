from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Mapping

from packages.job_contracts import ActorIdentity
from packages.runtime_release import (
    ProtectedAuthorityError,
    ValidatedJobPlaneAuthority,
    validate_job_plane_authority,
)

JOB_API_PORT = 8401
EXPECTED_REVISION = "0006_job_transition_database_authority"
_FORBIDDEN_AUTHORITY_KEYS = frozenset({
    "TRADING_APP_MANIFEST_SHA256",
    "TRADING_BACKEND_MANIFEST_SHA256",
    "TRADING_COMMAND_MANIFEST_SHA256",
    "TRADING_SEMANTIC_MANIFEST_SHA256",
})


@dataclass(frozen=True, slots=True, repr=False)
class JobApiSettings:
    """Loopback service settings whose representation excludes auth material."""

    bearer_token: str | None = field(default=None, repr=False)
    principal: ActorIdentity | None = None
    host: str = "127.0.0.1"
    port: int = JOB_API_PORT
    expected_revision: str = EXPECTED_REVISION
    authority_factory: Callable[[], ValidatedJobPlaneAuthority] = field(
        default=validate_job_plane_authority, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if self.host != "127.0.0.1":
            raise ValueError("job API must bind explicitly to 127.0.0.1")
        if self.port != JOB_API_PORT:
            raise ValueError(f"job API port must be exactly {JOB_API_PORT}")
        if self.bearer_token is not None and (
            not self.bearer_token or self.bearer_token.strip() != self.bearer_token
        ):
            raise ValueError("job API bearer token must be non-empty and unpadded")
        if (self.bearer_token is None) != (self.principal is None):
            raise ValueError("job API bearer token and principal must be configured together")
        if self.expected_revision != EXPECTED_REVISION:
            raise ValueError(
                f"expected database revision must be exactly {EXPECTED_REVISION}"
            )
        if not callable(self.authority_factory):
            raise ValueError("runtime authority factory is required")

    def load_authority(self) -> ValidatedJobPlaneAuthority:
        """Obtain and recheck the opaque authority without leaking factory errors."""

        try:
            authority = self.authority_factory()
            if not isinstance(authority, ValidatedJobPlaneAuthority):
                raise TypeError("invalid authority capability")
            return authority.recheck_mutation()
        except Exception:
            raise ProtectedAuthorityError("JOB_PLANE_AUTHORITY_INVALID") from None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "JobApiSettings":
        values = os.environ if env is None else env
        if _FORBIDDEN_AUTHORITY_KEYS.intersection(values):
            raise ValueError("digests must come from protected runtime authority")
        principal_type = values.get("TRADING_JOB_API_PRINCIPAL_TYPE") or None
        principal_id = values.get("TRADING_JOB_API_PRINCIPAL_ID") or None
        if (principal_type is None) != (principal_id is None):
            raise ValueError("job API principal type and id must be configured together")
        principal = (
            ActorIdentity.model_validate(
                {"actor_type": principal_type, "actor_id": principal_id}
            )
            if principal_type is not None
            else None
        )
        return cls(
            bearer_token=values.get("TRADING_JOB_API_TOKEN") or None,
            principal=principal,
            host=values.get("TRADING_JOB_API_HOST", "127.0.0.1"),
            port=int(values.get("TRADING_JOB_API_PORT", str(JOB_API_PORT))),
            expected_revision=values.get(
                "TRADING_JOB_API_EXPECTED_REVISION", EXPECTED_REVISION
            ),
        )

    def __repr__(self) -> str:
        return (
            "JobApiSettings("
            f"host={self.host!r}, port={self.port!r}, "
            f"expected_revision={self.expected_revision!r}, "
            f"bearer_token_configured={self.bearer_token is not None!r}, "
            f"principal_configured={self.principal is not None!r})"
        )
