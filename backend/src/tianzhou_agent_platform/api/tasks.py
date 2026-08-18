import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from tianzhou_agent_platform.api.dependencies import request_actor, task_runtime
from tianzhou_agent_platform.tasks.models import TaskTreeSnapshot


def create_task_router() -> APIRouter:
    router = APIRouter(prefix="/tasks", tags=["tasks"])

    @router.get("", response_model=TaskTreeSnapshot)
    async def task_snapshot(
        request: Request,
        session_id: str = Query(min_length=1),
    ) -> TaskTreeSnapshot:
        actor = request_actor(request)
        return await task_runtime(request).query(
            session_id,
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
        )

    @router.get("/events")
    async def task_events(
        request: Request,
        session_id: str = Query(min_length=1),
    ) -> StreamingResponse:
        actor = request_actor(request)
        service = task_runtime(request)
        await service.query(
            session_id,
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
        )

        async def stream() -> AsyncIterator[str]:
            async with service.events.subscribe(session_id) as queue:
                snapshot = await service.query(
                    session_id,
                    user_id=actor.user_id,
                    tenant_id=actor.tenant_id,
                )
                last_revision = snapshot.revision
                yield _event(last_revision)
                while True:
                    if await request.is_disconnected():
                        return
                    try:
                        revision = await asyncio.wait_for(queue.get(), timeout=15)
                    except TimeoutError:
                        current = await service.query(
                            session_id,
                            user_id=actor.user_id,
                            tenant_id=actor.tenant_id,
                        )
                        revision = current.revision
                        if revision <= last_revision:
                            yield ": keepalive\n\n"
                            continue
                    if revision <= last_revision:
                        continue
                    last_revision = revision
                    yield _event(revision)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router


def _event(revision: int) -> str:
    payload = json.dumps({"revision": revision}, ensure_ascii=False)
    return f"event: task.changed\ndata: {payload}\n\n"
