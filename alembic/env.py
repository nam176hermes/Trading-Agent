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


def _run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, version_table_schema="public")
    with context.begin_transaction():
        prior_path = connection.exec_driver_sql(
            "SELECT pg_catalog.current_setting('search_path')"
        ).scalar_one()
        prior_map = connection.get_execution_options().get("schema_translate_map")
        # Catalog operators win before Alembic reflection; unqualified DDL still targets public.
        connection.execution_options(schema_translate_map={None: "public"})
        connection.exec_driver_sql(
            "SELECT pg_catalog.set_config('search_path', 'pg_catalog, public', true)"
        )
        try:
            context.run_migrations()
            connection.exec_driver_sql(
                "SELECT pg_catalog.set_config('search_path', %s, true)", (prior_path,)
            )
        finally:
            connection.execution_options(schema_translate_map=prior_map)


def run_migrations_online() -> None:
    shared = config.attributes.get("connection")
    if shared is not None:
        _run_migrations(shared)
        return
    engine = create_engine(
        DatabaseSettings.from_env().sqlalchemy_url(), poolclass=pool.NullPool
    )
    with engine.connect() as connection:
        _run_migrations(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
