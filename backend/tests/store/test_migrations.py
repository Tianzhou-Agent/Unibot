"""Alembic baseline and startup revision check against a disposable live MySQL database.

Set TZ_STORAGE_E2E=1 and start backend/docker-compose.storage.yml. The test creates and drops its own database, so it
needs the root account (TZ_STORAGE_E2E_MYSQL_ROOT_DSN).
"""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tianzhou_agent_platform.store.migrations import (
    APP_METADATA,
    DatabaseRevisionError,
    alembic_config,
    require_database_revision,
    upgrade_database,
)

pytestmark = pytest.mark.skipif(
    os.getenv("TZ_STORAGE_E2E") != "1",
    reason="Set TZ_STORAGE_E2E=1 and start backend/docker-compose.storage.yml to run live storage E2E tests",
)

ROOT_DSN = os.getenv("TZ_STORAGE_E2E_MYSQL_ROOT_DSN", "mysql+aiomysql://root:unibot_root@127.0.0.1:13306/mysql")


async def test_baseline_creates_every_table_and_startup_requires_head() -> None:
    database = f"unibot_migration_{uuid4().hex[:12]}"
    admin = create_async_engine(ROOT_DSN)
    dsn = f"{ROOT_DSN.rsplit('/', 1)[0]}/{database}"
    engine = create_async_engine(dsn)
    async with admin.begin() as connection:
        await connection.execute(text(f"CREATE DATABASE {database}"))
    try:
        with pytest.raises(DatabaseRevisionError, match="tianzhou-agent-platform-migrate"):
            await require_database_revision(engine)

        # Migrations run their own event loop, so they cannot run on the test's loop.
        await asyncio.to_thread(upgrade_database, dsn)
        await require_database_revision(engine)

        async with engine.connect() as connection:
            result = await connection.execute(
                text("SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE()")
            )
            tables = {row[0] for row in result}
        expected = {table for metadata in APP_METADATA for table in metadata.tables}
        assert tables == expected | {"alembic_version"}

        await asyncio.to_thread(command.downgrade, alembic_config(dsn), "base")
        with pytest.raises(DatabaseRevisionError):
            await require_database_revision(engine)
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f"DROP DATABASE IF EXISTS {database}"))
        await admin.dispose()
