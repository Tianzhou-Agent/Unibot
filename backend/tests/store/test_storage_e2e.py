from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tianzhou_agent_platform.store import MySqlStore, NasStore, RedisStore, StoragePath, StoreCondition, StoreQuery

pytestmark = pytest.mark.skipif(
    os.getenv("TZ_STORAGE_E2E") != "1",
    reason="Set TZ_STORAGE_E2E=1 and start backend/docker-compose.storage.yml to run live storage E2E tests",
)


@pytest.mark.asyncio
async def test_storage_stores_against_docker_services() -> None:
    mysql_dsn = os.getenv(
        "TZ_STORAGE_E2E_MYSQL_DSN",
        "mysql+aiomysql://unibot:unibot@127.0.0.1:13306/unibot_storage_e2e",
    )
    redis_url = os.getenv("TZ_STORAGE_E2E_REDIS_URL", "redis://127.0.0.1:16379/0")
    nas_root = Path(os.getenv("TZ_STORAGE_E2E_NAS_ROOT", str(Path(__file__).parents[2] / ".docker" / "nas")))
    nas_root.mkdir(parents=True, exist_ok=True)

    metadata = MetaData()
    table = Table(
        "storage_e2e_items",
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("name", String(100), nullable=False),
        Column("status", String(20), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
    )

    engine = create_async_engine(mysql_dsn)
    async with engine.begin() as connection:
        await connection.run_sync(metadata.drop_all)
        await connection.run_sync(metadata.create_all)

    mysql_store = MySqlStore(engine, async_sessionmaker(engine, expire_on_commit=False), {"items": table})
    redis_store = RedisStore.from_url(redis_url)
    nas_store = NasStore(nas_root)

    unique_name = f"item-{uuid4().hex}"
    cache_key = f"key-{uuid4().hex}"
    file_path = StoragePath(relative_path=f"e2e/{uuid4().hex}.txt")
    created_at = datetime.now(timezone.utc)

    try:
        created = await mysql_store.create("items", {"name": unique_name, "status": "new", "created_at": created_at})
        assert created.values["name"] == unique_name

        read = await mysql_store.read("items", created.id)
        assert read is not None
        assert read.values["status"] == "new"

        updated = await mysql_store.update("items", created.id, {"status": "done"})
        assert updated.values["status"] == "done"

        page = await mysql_store.query("items", StoreQuery(filters={"status": "done"}))
        assert [item.id for item in page.items] == [created.id]

        interval_page = await mysql_store.query(
            "items",
            StoreQuery(
                conditions=[
                    StoreCondition(field="created_at", op="ge", value=created_at - timedelta(seconds=1)),
                    StoreCondition(field="created_at", op="lt", value=created_at + timedelta(seconds=1)),
                ]
            ),
        )
        assert [item.id for item in interval_page.items] == [created.id]

        await redis_store.delete("e2e", cache_key)
        assert await redis_store.get("e2e", cache_key) is None
        assert (await redis_store.set("e2e", cache_key, {"mysql_id": created.id}, ttl_seconds=60)).written is True

        cache_entry = await redis_store.get("e2e", cache_key)
        assert cache_entry is not None
        assert cache_entry.value == {"mysql_id": created.id}
        assert await redis_store.exists("e2e", cache_key) is True

        file_metadata = await nas_store.write(file_path, b"storage e2e")
        assert file_metadata.size_bytes == len(b"storage e2e")
        assert await nas_store.read(file_path) == b"storage e2e"
        assert await nas_store.exists(file_path) is True

        assert (await nas_store.delete(file_path)).deleted is True
        assert (await redis_store.delete("e2e", cache_key)).deleted is True
        assert (await mysql_store.delete("items", created.id)).deleted is True
    finally:
        await redis_store.close()
        await mysql_store.close()
