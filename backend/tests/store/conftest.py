from __future__ import annotations

import asyncio
import os

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from tianzhou_agent_platform.store.schema import ensure_schema


@pytest.fixture(scope="session", autouse=True)
def live_database_schemas() -> None:
    """Create the application schema in each opted-in live MySQL database, as the backend does at startup."""
    dsns = set()
    if os.getenv("TZ_STORAGE_E2E") == "1":
        dsns.add(os.getenv("TZ_STORAGE_E2E_MYSQL_DSN", "mysql+aiomysql://unibot:unibot@127.0.0.1:13306/unibot"))
    if os.getenv("OBS_TEST_MYSQL_DSN"):
        dsns.add(os.environ["OBS_TEST_MYSQL_DSN"])
    for dsn in dsns:
        asyncio.run(_ensure_schema(dsn))


async def _ensure_schema(dsn: str) -> None:
    engine = create_async_engine(dsn)
    try:
        await ensure_schema(engine)
    finally:
        await engine.dispose()
