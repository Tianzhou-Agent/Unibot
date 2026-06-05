from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Table

from tianzhou_agent_platform.store.database.mysql import MySqlStore
from tianzhou_agent_platform.store.nas.filesystem import NasStore
from tianzhou_agent_platform.store.redis.client import RedisStore
from tianzhou_agent_platform.store.settings import StorageSettings


@dataclass(slots=True)
class StorageStores:
    mysql: MySqlStore
    redis: RedisStore
    nas: NasStore

    async def close(self) -> None:
        await self.redis.close()
        await self.mysql.close()


def create_storage_stores(
    settings: StorageSettings,
    *,
    mysql_resource_tables: dict[str, Table] | None = None,
) -> StorageStores:
    mysql = MySqlStore.from_dsn(
        settings.mysql_dsn.get_secret_value(),
        pool_size=settings.mysql_pool_size,
        max_overflow=settings.mysql_max_overflow,
        resource_tables=mysql_resource_tables,
    )
    redis = RedisStore.from_url(
        settings.redis_dsn.get_secret_value(),
        socket_timeout=settings.redis_timeout_seconds,
        default_ttl_seconds=settings.redis_default_ttl_seconds,
    )
    nas = NasStore(settings.nas_root_path, max_file_size_bytes=settings.nas_max_file_size_bytes)
    return StorageStores(mysql=mysql, redis=redis, nas=nas)
