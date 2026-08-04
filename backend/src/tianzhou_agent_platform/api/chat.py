import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any, cast

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from tianzhou_agent_platform.api.dependencies import bind_actor, runtime
from tianzhou_agent_platform.core.chat import ChatRequest, ChatResponse
from tianzhou_agent_platform.core.errors import PlatformError


def create_chat_router() -> APIRouter:
    router = APIRouter()

    @router.post("/chat", response_model=ChatResponse)
    async def chat(payload: ChatRequest, request: Request) -> ChatResponse:
        return await runtime(request).chat(bind_actor(request, payload))

    @router.post("/chat/stream")
    async def stream_chat(payload: ChatRequest, request: Request) -> StreamingResponse:
        agent_runtime = runtime(request)
        scoped_payload = bind_actor(request, payload)
        background_tasks = cast(set[asyncio.Task[None]], request.app.state.background_tasks)

        async def stream() -> AsyncIterator[str]:
            queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

            async def sink(event: dict[str, Any]) -> None:
                await queue.put(event)

            async def produce() -> None:
                try:
                    result = await agent_runtime.chat(scoped_payload, event_sink=sink)
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

    return router
