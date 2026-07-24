from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg_pool import ConnectionPool
from sqlalchemy.engine import URL


class DatabaseUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True, repr=False)
class DatabaseSettings:
    host: str
    port: int
    database: str
    user: str
    password: str
    pool_min: int = 1
    pool_max: int = 5
    statement_timeout_ms: int = 5000

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "DatabaseSettings":
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
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError(f"missing database settings: {', '.join(sorted(missing))}")
        if required["TRADING_DATABASE_HOST"] not in {"127.0.0.1", "localhost"}:
            raise ValueError("database host must be localhost")
        pool_min = int(values.get("TRADING_DB_POOL_MIN", "1"))
        pool_max = int(values.get("TRADING_DB_POOL_MAX", "5"))
        timeout = int(values.get("TRADING_DB_STATEMENT_TIMEOUT_MS", "5000"))
        if pool_min < 1 or pool_max < 1 or pool_min > pool_max:
            raise ValueError("invalid database pool bounds")
        if timeout < 1:
            raise ValueError("statement timeout must be positive")
        return cls(
            host=required["TRADING_DATABASE_HOST"],
            port=int(required["TRADING_DATABASE_PORT"]),
            database=required["TRADING_DATABASE_NAME"],
            user=required["TRADING_DATABASE_USER"],
            password=required["TRADING_DATABASE_PASSWORD"],
            pool_min=pool_min,
            pool_max=pool_max,
            statement_timeout_ms=timeout,
        )

    def __repr__(self) -> str:
        return f"DatabaseSettings({self.redacted_identity()!r})"

    def redacted_identity(self) -> dict[str, str | int]:
        return {
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "role": self.user,
        }

    def conninfo(self) -> str:
        return make_conninfo(
            host=self.host,
            port=self.port,
            dbname=self.database,
            user=self.user,
            password=self.password,
            options=f"-c statement_timeout={self.statement_timeout_ms}",
        )

    def sqlalchemy_url(self) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=self.user,
            password=self.password,
            host=self.host,
            port=self.port,
            database=self.database,
            query={"options": f"-c statement_timeout={self.statement_timeout_ms}"},
        )


def connect(
    settings: DatabaseSettings, *, read_only: bool = False
) -> psycopg.Connection:
    try:
        connection = psycopg.connect(settings.conninfo())
    except psycopg.Error as error:
        identity = settings.redacted_identity()
        raise DatabaseUnavailable(
            "database unavailable "
            f"({identity['host']}:{identity['port']}/{identity['database']} "
            f"role={identity['role']})"
        ) from None
    connection.read_only = read_only
    return connection


def create_pool(
    settings: DatabaseSettings, *, read_only: bool = False
) -> ConnectionPool:
    def configure(connection: psycopg.Connection) -> None:
        connection.read_only = read_only

    return ConnectionPool(
        conninfo=settings.conninfo(),
        min_size=settings.pool_min,
        max_size=settings.pool_max,
        configure=configure,
        open=False,
    )
