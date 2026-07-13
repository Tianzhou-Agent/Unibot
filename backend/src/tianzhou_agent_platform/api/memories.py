from fastapi import APIRouter, Query, Request, Response, status

from tianzhou_agent_platform.aina.memory.models import (
    MemoryCategory,
    MemoryCreate,
    MemoryListResponse,
    MemoryRecord,
    MemoryStats,
    MemoryUpdate,
)
from tianzhou_agent_platform.api.dependencies import repository


def create_memory_router() -> APIRouter:
    router = APIRouter()

    @router.post("/memories", response_model=MemoryRecord, status_code=status.HTTP_201_CREATED)
    async def create_memory(payload: MemoryCreate, request: Request) -> MemoryRecord:
        return await repository(request).create_memory(payload)

    @router.get("/memories", response_model=MemoryListResponse)
    async def list_memories(
        request: Request,
        user_id: str = "anonymous",
        tenant_id: str = "default",
        q: str | None = None,
        category: MemoryCategory | None = None,
    ) -> MemoryListResponse:
        items = await repository(request).list_memories(
            user_id=user_id,
            tenant_id=tenant_id,
            query=q,
            category=category,
        )
        return MemoryListResponse(items=items, total=len(items))

    @router.get("/memories/stats", response_model=MemoryStats)
    async def memory_stats(
        request: Request,
        user_id: str = "anonymous",
        tenant_id: str = "default",
    ) -> MemoryStats:
        return await repository(request).memory_stats(user_id=user_id, tenant_id=tenant_id)

    @router.patch("/memories/{memory_id}", response_model=MemoryRecord)
    async def update_memory(memory_id: str, payload: MemoryUpdate, request: Request) -> MemoryRecord:
        return await repository(request).update_memory(memory_id, payload)

    @router.delete("/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_memory(
        memory_id: str,
        request: Request,
        user_id: str = Query(default="anonymous"),
        tenant_id: str = Query(default="default"),
    ) -> Response:
        await repository(request).remove_memory(memory_id, user_id=user_id, tenant_id=tenant_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router
