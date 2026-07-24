from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from trading_control.db import DatabaseSettings

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

target_metadata = None


def run_migrations_offline() -> None:
    settings = DatabaseSettings.from_env()
    context.configure(
        url=settings.sqlalchemy_url().render_as_string(hide_password=False),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    shared = config.attributes.get("connection")
    if shared is not None:
        context.configure(connection=shared, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_engine(
        DatabaseSettings.from_env().sqlalchemy_url(), poolclass=pool.NullPool
    )
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
