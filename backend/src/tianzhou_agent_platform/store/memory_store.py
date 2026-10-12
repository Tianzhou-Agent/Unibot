"""Long-term memories, read and written in SQL."""

from __future__ import annotations

from sqlalchemy import func, insert, select

from tianzhou_agent_platform.aina.memory.models import MemoryCreate, MemoryRecord, MemoryStats, MemoryUpdate
from tianzhou_agent_platform.store.memory_repository import memory_stats_of, new_memory, updated_memory
from tianzhou_agent_platform.store.repository_schema import memories_table, table_values
from tianzhou_agent_platform.store.sql import SqlStore

memories = memories_table


class MemoryStore(SqlStore):
    async def create(self, data: MemoryCreate) -> MemoryRecord:
        """Store a new memory, or return the actor's existing memory with the same content.

        The duplicate check and the per-actor cap read before inserting, so two identical memories created at the
        same moment on different replicas can both be stored, and the cap can be exceeded by a concurrent create.
        """
        existing = await self.list_memories(user_id=data.user_id, tenant_id=data.tenant_id)
        normalized = data.content.casefold()
        duplicate = next((memory for memory in existing if memory.content.casefold() == normalized), None)
        if duplicate is not None:
            return duplicate
        memory = new_memory(data, existing_count=len(existing))
        async with self._session() as session, session.begin():
            await session.execute(insert(memories).values(table_values(memories, memory)))
        return memory

    async def get(self, memory_id: str) -> MemoryRecord | None:
        return await self._one(MemoryRecord, select(memories).where(memories.c.id == memory_id))

    async def list_memories(
        self,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
        query: str | None = None,
        category: str | None = None,
    ) -> list[MemoryRecord]:
        statement = select(memories)
        for column, value in (
            (memories.c.user_id, user_id),
            (memories.c.tenant_id, tenant_id),
            (memories.c.category, category),
        ):
            if value is not None:
                statement = statement.where(column == value)
        normalized_query = (query or "").strip().casefold()
        if normalized_query:
            statement = statement.where(func.lower(memories.c.content).contains(normalized_query, autoescape=True))
        return await self._all(MemoryRecord, statement.order_by(memories.c.updated_at.desc(), memories.c.id.desc()))

    async def update(self, memory_id: str, data: MemoryUpdate) -> MemoryRecord | None:
        return await self._update_locked(memories, MemoryRecord, memory_id, lambda memory: updated_memory(memory, data))

    async def remove(self, memory_id: str) -> None:
        await self._delete(memories, memories.c.id == memory_id)

    async def stats(self, *, user_id: str, tenant_id: str) -> MemoryStats:
        return memory_stats_of(await self.list_memories(user_id=user_id, tenant_id=tenant_id))
