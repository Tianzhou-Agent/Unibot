import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, cast

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import StreamingResponse

from tianzhou_agent_platform.api.dependencies import bind_actor, chat_service
from tianzhou_agent_platform.conversations.schemas import ChatRequest, ChatResponse, ConversationAction
from tianzhou_agent_platform.core.errors import PlatformError

EventSink = Callable[[dict[str, Any]], Awaitable[None]]


def create_chat_router() -> APIRouter:
    router = APIRouter()

    @router.post("/chat", response_model=ChatResponse)
    async def chat(payload: ChatRequest, request: Request) -> ChatResponse:
        return await chat_service(request).run_turn(bind_actor(request, payload))

    @router.post("/chat/stream")
    async def stream_chat(payload: ChatRequest, request: Request) -> StreamingResponse:
        service = chat_service(request)
        scoped_payload = bind_actor(request, payload)
        return _event_stream(request, lambda sink: service.run_turn(scoped_payload, event_sink=sink))

    @router.post("/conversations/{conversation_id}/stop", status_code=status.HTTP_202_ACCEPTED)
    async def stop_conversation(conversation_id: str, payload: ConversationAction, request: Request) -> Response:
        scoped = bind_actor(request, payload)
        await chat_service(request).stop(conversation_id, user_id=scoped.user_id, tenant_id=scoped.tenant_id)
        return Response(status_code=status.HTTP_202_ACCEPTED)

    @router.post("/conversations/{conversation_id}/resume", response_model=ChatResponse)
    async def resume_conversation(conversation_id: str, payload: ConversationAction, request: Request) -> ChatResponse:
        scoped = bind_actor(request, payload)
        return await chat_service(request).resume(
            conversation_id, user_id=scoped.user_id, tenant_id=scoped.tenant_id
        )

    @router.post("/conversations/{conversation_id}/resume/stream")
    async def stream_resume_conversation(
        conversation_id: str,
        payload: ConversationAction,
        request: Request,
    ) -> StreamingResponse:
        service = chat_service(request)
        scoped = bind_actor(request, payload)
        return _event_stream(
            request,
            lambda sink: service.resume(
                conversation_id, user_id=scoped.user_id, tenant_id=scoped.tenant_id, event_sink=sink
            ),
        )

    return router


def _event_stream(request: Request, run: Callable[[EventSink], Awaitable[ChatResponse]]) -> StreamingResponse:
    """SSE response of a run executed in the background, so a closed stream does not cancel it."""
    background_tasks = cast(set[asyncio.Task[None]], request.app.state.background_tasks)

    async def stream() -> AsyncIterator[str]:
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        async def sink(event: dict[str, Any]) -> None:
            await queue.put(event)

        async def produce() -> None:
            try:
                result = await run(sink)
                await queue.put({"type": "message.completed", "response": result.model_dump(mode="json")})
            except PlatformError as exc:
                await queue.put(
                    {
                        "type": "error",
                        "error": {
                            "code": exc.code,
                            "message": exc.user_message or exc.message,
                            "retryable": exc.retryable,
                            "source": exc.source,
                        },
                    }
                )
            except Exception:
                await queue.put(
                    {
                        "type": "error",
                        "error": {
                            "code": "INTERNAL_ERROR",
                            "message": "The agent run failed unexpectedly.",
                            "retryable": True,
                            "source": "platform",
                        },
                    }
                )
            finally:
                await queue.put(None)

        task = asyncio.create_task(produce())
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)
        while True:
            event = await queue.get()
            if event is None:
                break
            event_type = event.get("type", "message")
            yield f"event: {event_type}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")
