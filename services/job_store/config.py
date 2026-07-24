from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from psycopg.conninfo import make_conninfo


JOB_PLANE_DATABASE_USERS = frozenset(
    {
        "trading_job_api",
        "trading_job_worker",
        "trading_job_scheduler",
    }
)


@dataclass(frozen=True, slots=True, repr=False)
class JobStoreSettings:
    """Local PostgreSQL settings whose representation never includes credentials."""

    host: str
    port: int
    database: str
    user: str
    password: str
    pool_min: int = 1
    pool_max: int = 5
    statement_timeout_ms: int = 5_000

    def __post_init__(self) -> None:
        if self.host not in {"127.0.0.1", "localhost"}:
            raise ValueError("job database host must be localhost")
        if self.port < 1 or self.port > 65_535:
            raise ValueError("invalid job database port")
        if not self.database or not self.user or not self.password:
            raise ValueError("job database name, user, and password are required")
        if self.pool_min < 1 or self.pool_max < self.pool_min:
            raise ValueError("invalid job database pool bounds")
        if self.statement_timeout_ms < 1:
            raise ValueError("job database statement timeout must be positive")

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        expected_user: str,
    ) -> "JobStoreSettings":
        values = os.environ if env is None else env
        required = {
            name: values.get(name, "")
            for name in (
                "TRADING_DATABASE_HOST",
                "TRADING_DATABASE_PORT",
                "TRADING_DATABASE_NAME",
                "TRADING_DATABASE_USER",
                "TRADING_DATABASE_PASSWORD",
            )
        }
        missing = sorted(name for name, value in required.items() if not value)
        if missing:
            raise ValueError(f"missing job database settings: {', '.join(missing)}")
        settings = cls(
            host=required["TRADING_DATABASE_HOST"],
            port=int(required["TRADING_DATABASE_PORT"]),
            database=required["TRADING_DATABASE_NAME"],
            user=required["TRADING_DATABASE_USER"],
            password=required["TRADING_DATABASE_PASSWORD"],
            pool_min=int(values.get("TRADING_DB_POOL_MIN", "1")),
            pool_max=int(values.get("TRADING_DB_POOL_MAX", "5")),
            statement_timeout_ms=int(
                values.get("TRADING_DB_STATEMENT_TIMEOUT_MS", "5000")
            ),
        )
        return settings.require_user(expected_user)

    def require_user(self, expected_user: str) -> "JobStoreSettings":
        """Require one explicit service identity before repository creation."""

        if expected_user not in JOB_PLANE_DATABASE_USERS:
            raise ValueError("expected job database user is not allowed")
        if self.user != expected_user:
            raise ValueError("job database user does not match expected service role")
        return self

    def __repr__(self) -> str:
        return (
            "JobStoreSettings("
            f"host={self.host!r}, port={self.port!r}, database={self.database!r}, "
            f"user={self.user!r}, pool_min={self.pool_min!r}, "
            f"pool_max={self.pool_max!r}, "
            f"statement_timeout_ms={self.statement_timeout_ms!r})"
        )

    def conninfo(self) -> str:
        return make_conninfo(
            host=self.host,
            port=self.port,
            dbname=self.database,
            user=self.user,
            password=self.password,
            options=f"-c statement_timeout={self.statement_timeout_ms}",
        )
