"""Metadata of AINA installations' private files, read and written in SQL (the bytes live in a blob store)."""

from __future__ import annotations

from sqlalchemy import ColumnElement, func, select

from tianzhou_agent_platform.aina.package.files import AinaFile
from tianzhou_agent_platform.store.repository_schema import aina_files_table, table_values
from tianzhou_agent_platform.store.sql import SqlStore

files = aina_files_table


class AinaFileStore(SqlStore):
    async def get(self, installation_key: str, path: str) -> AinaFile | None:
        return await self._one(AinaFile, select(files).where(files.c.installation_key == installation_key, files.c.path == path))

    async def put(self, file: AinaFile) -> None:
        await self._put(files, table_values(files, file))

    async def delete(self, installation_key: str, path: str) -> AinaFile | None:
        found = await self.get(installation_key, path)
        if found is not None:
            await self._delete(files, files.c.id == found.id)
        return found

    async def list_files(self, installation_key: str, *, prefix: str, limit: int) -> list[AinaFile]:
        statement = select(files).where(files.c.installation_key == installation_key)
        if prefix:
            statement = statement.where(files.c.path.startswith(prefix, autoescape=True))
        return await self._all(AinaFile, statement.order_by(files.c.path).limit(limit))

    async def usage(self, installation_key: str) -> tuple[int, int]:
        statement = select(func.coalesce(func.sum(files.c.size_bytes), 0), func.count()).where(
            files.c.installation_key == installation_key
        )
        async with self._session() as session:
            used_bytes, used_files = (await session.execute(statement)).one()
        return int(used_bytes), int(used_files)

    async def delete_all(self, *, installation_key: str | None, aina_id: str | None) -> list[AinaFile]:
        condition: ColumnElement[bool] = files.c.id.isnot(None)
        if installation_key is not None:
            condition = condition & (files.c.installation_key == installation_key)
        if aina_id is not None:
            condition = condition & (files.c.aina_id == aina_id)
        doomed = await self._all(AinaFile, select(files).where(condition))
        await self._delete(files, condition)
        return doomed
