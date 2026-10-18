"""Developer tokens of the ``aina`` CLI, read and written in SQL."""

from __future__ import annotations

from sqlalchemy import select, update

from tianzhou_agent_platform.auth.models import DeveloperToken
from tianzhou_agent_platform.store.repository_schema import developer_tokens_table, table_values
from tianzhou_agent_platform.store.sql import SqlStore

tokens = developer_tokens_table


class DeveloperTokenStore(SqlStore):
    async def create(self, token: DeveloperToken) -> None:
        await self._put(tokens, table_values(tokens, token))

    async def list(self, *, tenant_id: str, user_id: str) -> list[DeveloperToken]:
        statement = select(tokens).where(tokens.c.tenant_id == tenant_id, tokens.c.user_id == user_id)
        return await self._all(DeveloperToken, statement.order_by(tokens.c.created_at.desc()))

    async def find(self, token_hash: str) -> DeveloperToken | None:
        return await self._one(DeveloperToken, select(tokens).where(tokens.c.token_hash == token_hash))

    async def touch(self, token: DeveloperToken) -> None:
        async with self._session() as session, session.begin():
            await session.execute(
                update(tokens).where(tokens.c.id == token.id).values(last_used_at=token.last_used_at)
            )

    async def delete(self, token_id: str, *, tenant_id: str, user_id: str) -> bool:
        condition = (tokens.c.id == token_id) & (tokens.c.tenant_id == tenant_id) & (tokens.c.user_id == user_id)
        return await self._delete(tokens, condition) > 0
