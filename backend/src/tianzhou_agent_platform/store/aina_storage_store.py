"""Plugin-private key-value storage of AINA installations, read and written in SQL."""

from __future__ import annotations

from sqlalchemy import ColumnElement, func, select

from tianzhou_agent_platform.aina.package.storage import AinaStorageItem, check_quota
from tianzhou_agent_platform.store.repository_schema import aina_storage_table, table_values
from tianzhou_agent_platform.store.sql import SqlStore

storage = aina_storage_table


class AinaStorageStore(SqlStore):
    async def get(self, installation_key: str, key: str) -> AinaStorageItem | None:
        return await self._one(
            AinaStorageItem,
            select(storage).where(storage.c.installation_key == installation_key, storage.c.key == key),
        )

    async def put(self, item: AinaStorageItem) -> None:
        # The quota is checked before the write, so concurrent writers of one installation may overshoot it
        # by at most one value each.
        async with self._session() as session:
            used_bytes, used_items = (
                await session.execute(
                    select(func.coalesce(func.sum(storage.c.size_bytes), 0), func.count()).where(
                        storage.c.installation_key == item.installation_key
                    )
                )
            ).one()
        check_quota(
            used_bytes=int(used_bytes),
            used_items=int(used_items),
            replaced=await self._one(AinaStorageItem, select(storage).where(storage.c.id == item.id)),
            item=item,
        )
        await self._put(storage, table_values(storage, item))

    async def delete(self, installation_key: str, key: str) -> bool:
        condition = (storage.c.installation_key == installation_key) & (storage.c.key == key)
        return await self._delete(storage, condition) > 0

    async def list(self, installation_key: str, *, prefix: str, limit: int) -> list[AinaStorageItem]:
        statement = select(storage).where(storage.c.installation_key == installation_key)
        if prefix:
            statement = statement.where(storage.c.key.startswith(prefix, autoescape=True))
        return await self._all(AinaStorageItem, statement.order_by(storage.c.key).limit(limit))

    async def delete_all(self, *, installation_key: str | None, aina_id: str | None) -> int:
        condition: ColumnElement[bool] = storage.c.id.isnot(None)
        if installation_key is not None:
            condition = condition & (storage.c.installation_key == installation_key)
        if aina_id is not None:
            condition = condition & (storage.c.aina_id == aina_id)
        return await self._delete(storage, condition)
