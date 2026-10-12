from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from tianzhou_agent_platform.store.migrations import APP_METADATA
from tianzhou_agent_platform.store.settings import StorageSettings

config = context.config
OWNED_TABLES = {table for metadata in APP_METADATA for table in metadata.tables}


def include_name(name: str | None, type_: str, parent_names: object) -> bool:
    # Autogenerate must never propose dropping tables the application does not own.
    return type_ != "table" or name in OWNED_TABLES


def _configure(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=APP_METADATA,
        include_name=include_name,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_online() -> None:
    dsn = config.attributes.get("dsn") or StorageSettings().mysql_dsn.get_secret_value()
    engine = create_async_engine(dsn)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_configure)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("Offline SQL generation is not supported; run migrations against a database.")
asyncio.run(_run_online())
