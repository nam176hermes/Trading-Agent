from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from typing import Mapping

from psycopg.conninfo import make_conninfo


CANONICAL_DATABASE_REVISION = "0011_engine_backtest_worker_authority"
P1_DISPOSABLE_DATABASE_REVISION = "0015_p1_accounting_closure_rotation"
JOB_PLANE_DATABASE_USERS = frozenset(
    {
        "trading_job_api",
        "trading_job_worker",
        "trading_job_scheduler",
    }
)

_CREDENTIAL_DIRECTORY_KEY = "CREDENTIALS_DIRECTORY"
_MAX_CREDENTIAL_BYTES = 4096


def _open_credential_directory(path: str) -> int:
    if not path.startswith("/") or path.endswith("/"):
        raise ValueError
    components = path.split("/")[1:]
    if not components or any(component in {"", ".", ".."} for component in components):
        raise ValueError
    current = os.open(
        "/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    )
    try:
        for index, component in enumerate(components):
            child = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=current,
            )
            os.close(current)
            current = child
            info = os.fstat(current)
            mode = stat.S_IMODE(info.st_mode)
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid not in {0, os.geteuid()}
                or (
                    mode & 0o022
                    and not (info.st_uid == 0 and mode & stat.S_ISVTX)
                )
                or (index == len(components) - 1 and mode & 0o077)
            ):
                raise ValueError
        return current
    except Exception:
        os.close(current)
        raise


def read_systemd_credential(values: Mapping[str, str], name: str) -> str:
    """Read one fixed systemd credential without following mutable links."""

    directory_fd = -1
    credential_fd = -1
    try:
        if not name or any(
            character not in "abcdefghijklmnopqrstuvwxyz0123456789-"
            for character in name
        ):
            raise ValueError
        directory = values.get(_CREDENTIAL_DIRECTORY_KEY, "")
        directory_fd = _open_credential_directory(directory)
        credential_fd = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=directory_fd,
        )
        before = os.fstat(credential_fd)
        mode = stat.S_IMODE(before.st_mode)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid not in {0, os.geteuid()}
            or mode & 0o022
            or before.st_size < 1
            or before.st_size > _MAX_CREDENTIAL_BYTES
        ):
            raise ValueError
        raw = bytearray()
        while len(raw) <= _MAX_CREDENTIAL_BYTES:
            chunk = os.read(credential_fd, _MAX_CREDENTIAL_BYTES + 1 - len(raw))
            if not chunk:
                break
            raw.extend(chunk)
        after = os.fstat(credential_fd)
        if (
            len(raw) != before.st_size
            or after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
            or after.st_size != before.st_size
        ):
            raise ValueError
        value = bytes(raw).decode("utf-8")
        if (
            not value
            or value.strip() != value
            or "\x00" in value
            or "\n" in value
            or "\r" in value
        ):
            raise ValueError
        return value
    except Exception:
        raise ValueError("invalid systemd credential") from None
    finally:
        if credential_fd >= 0:
            os.close(credential_fd)
        if directory_fd >= 0:
            os.close(directory_fd)


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

    @classmethod
    def from_systemd_credentials(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        expected_user: str,
    ) -> "JobStoreSettings":
        values = os.environ if env is None else env
        try:
            settings = cls(
                host=read_systemd_credential(values, "database-host"),
                port=int(read_systemd_credential(values, "database-port")),
                database=read_systemd_credential(values, "database-name"),
                user=expected_user,
                password=read_systemd_credential(values, "database-password"),
            )
            return settings.require_user(expected_user)
        except Exception:
            raise ValueError("invalid systemd credential") from None

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
