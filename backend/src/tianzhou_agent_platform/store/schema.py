"""Creates every MySQL table the application owns at startup.

There are no migrations. The table definitions are fingerprinted; when the fingerprint stored in the database is
missing or differs from the code's, every application-owned table is dropped and recreated, so a schema change
discards all stored data.
"""

from __future__ import annotations

import hashlib
import logging

from sqlalchemy import Column, MetaData, String, Table, inspect, select, text
from sqlalchemy.engine import Connection, Dialect
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.schema import CreateIndex, CreateTable

from tianzhou_agent_platform.store.checkpoint import graph_checkpoint_metadata
from tianzhou_agent_platform.store.observability_store import OBS_METADATA
from tianzhou_agent_platform.store.repository_schema import repository_metadata
from tianzhou_agent_platform.store.runtime_check import runtime_check_metadata
from tianzhou_agent_platform.tasks.store import task_metadata

logger = logging.getLogger(__name__)

APP_METADATA: list[MetaData] = [
    repository_metadata,
    graph_checkpoint_metadata,
    OBS_METADATA,
    task_metadata,
    runtime_check_metadata,
]

_schema_metadata = MetaData()
SCHEMA_TABLE = Table("unibot_schema", _schema_metadata, Column("fingerprint", String(64), primary_key=True))
# Left behind by the Alembic migrations this module replaced.
_RETIRED_TABLES = ("alembic_version",)
_LOCK_NAME = "unibot_schema"
_LOCK_TIMEOUT_SECONDS = 60


def schema_fingerprint(dialect: Dialect) -> str:
    """A hash of the DDL for every application-owned table and index, as compiled for ``dialect``."""
    statements = []
    for metadata in APP_METADATA:
        for table in metadata.sorted_tables:
            statements.append(str(CreateTable(table).compile(dialect=dialect)).strip())
            for index in sorted(table.indexes, key=lambda item: item.name or ""):
                statements.append(str(CreateIndex(index).compile(dialect=dialect)).strip())
    return hashlib.sha256("\n".join(sorted(statements)).encode()).hexdigest()


async def ensure_schema(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        await connection.run_sync(_ensure_schema)


def _ensure_schema(connection: Connection) -> None:
    mysql = connection.dialect.name == "mysql"
    # Serialize backend nodes that start together, so only one of them resets the schema.
    lock = text("SELECT GET_LOCK(:name, :timeout)")
    if mysql and connection.execute(lock, {"name": _LOCK_NAME, "timeout": _LOCK_TIMEOUT_SECONDS}).scalar() != 1:
        raise RuntimeError(f"Timed out waiting for the {_LOCK_NAME!r} schema lock")
    try:
        expected = schema_fingerprint(connection.dialect)
        existing = set(inspect(connection).get_table_names())
        stored = []
        if SCHEMA_TABLE.name in existing:
            stored = connection.execute(select(SCHEMA_TABLE.c.fingerprint)).scalars().all()
        if stored == [expected]:
            return
        logger.warning("Database schema differs from the application; dropping and recreating all application tables")
        if mysql:
            connection.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        for metadata in [*reversed(APP_METADATA), _schema_metadata]:
            metadata.drop_all(connection)
        for name in _RETIRED_TABLES:
            if name in existing:
                connection.execute(text(f"DROP TABLE {connection.dialect.identifier_preparer.quote(name)}"))
        if mysql:
            connection.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
        for metadata in [*APP_METADATA, _schema_metadata]:
            metadata.create_all(connection)
        connection.execute(SCHEMA_TABLE.insert().values(fingerprint=expected))
        connection.commit()
    finally:
        if mysql:
            connection.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": _LOCK_NAME})
