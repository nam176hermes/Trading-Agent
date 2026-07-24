from __future__ import annotations

import pytest

from control_api.config import Settings
from trading_control.db import DatabaseSettings


def valid_env(**overrides: str) -> dict[str, str]:
    values = {
        "TRADING_DATABASE_HOST": "127.0.0.1",
        "TRADING_DATABASE_PORT": "55432",
        "TRADING_DATABASE_NAME": "trading_agent",
        "TRADING_DATABASE_USER": "trading_reader",
        "TRADING_DATABASE_PASSWORD": "do-not-leak",
        "TRADING_DB_POOL_MIN": "1",
        "TRADING_DB_POOL_MAX": "5",
        "TRADING_DB_STATEMENT_TIMEOUT_MS": "5000",
    }
    values.update(overrides)
    return values


def test_database_settings_are_local_and_redacted() -> None:
    settings = DatabaseSettings.from_env(valid_env())

    assert "do-not-leak" not in repr(settings)
    assert settings.redacted_identity() == {
        "host": "127.0.0.1",
        "port": 55432,
        "database": "trading_agent",
        "role": "trading_reader",
    }


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("TRADING_DATABASE_HOST", "0.0.0.0"),
        ("TRADING_DB_POOL_MIN", "0"),
        ("TRADING_DB_POOL_MAX", "0"),
        ("TRADING_DB_STATEMENT_TIMEOUT_MS", "0"),
    ],
)
def test_database_settings_reject_unsafe_values(key: str, value: str) -> None:
    with pytest.raises(ValueError):
        DatabaseSettings.from_env(valid_env(**{key: value}))


def test_pool_min_cannot_exceed_pool_max() -> None:
    with pytest.raises(ValueError):
        DatabaseSettings.from_env(
            valid_env(TRADING_DB_POOL_MIN="6", TRADING_DB_POOL_MAX="5")
        )


def test_store_backend_accepts_only_legacy_or_postgres(tmp_path) -> None:
    legacy = Settings.from_env({
        "TRADING_DATA_ROOT": str(tmp_path),
        "TRADING_STORE_BACKEND": "legacy",
    })
    postgres = Settings.from_env({
        "TRADING_DATA_ROOT": str(tmp_path),
        "TRADING_STORE_BACKEND": "postgres",
    })
    assert legacy.store_backend == "legacy"
    assert postgres.store_backend == "postgres"
    with pytest.raises(ValueError, match="TRADING_STORE_BACKEND"):
        Settings.from_env({
            "TRADING_DATA_ROOT": str(tmp_path),
            "TRADING_STORE_BACKEND": "automatic",
        })
