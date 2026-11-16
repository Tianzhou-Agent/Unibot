"""Persistence contract of the user's long-term memories (memory routes, the memory AINA, agent recall)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from tianzhou_agent_platform.aina.memory.models import MemoryCreate, MemoryRecord, MemoryStats, MemoryUpdate


@runtime_checkable
class MemoryRepository(Protocol):
    async def create_memory(self, data: MemoryCreate) -> MemoryRecord: ...

    async def get_memory(self, memory_id: str, *, user_id: str, tenant_id: str) -> MemoryRecord: ...

    async def list_memories(
        self,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
        query: str | None = None,
        category: str | None = None,
    ) -> list[MemoryRecord]: ...

    async def search_memories(
        self,
        query: str,
        *,
        user_id: str,
        tenant_id: str,
        limit: int = 8,
    ) -> list[MemoryRecord]: ...

    async def update_memory(self, memory_id: str, data: MemoryUpdate) -> MemoryRecord: ...

    async def remove_memory(self, memory_id: str, *, user_id: str, tenant_id: str) -> None: ...

    async def memory_stats(self, *, user_id: str, tenant_id: str) -> MemoryStats: ...
