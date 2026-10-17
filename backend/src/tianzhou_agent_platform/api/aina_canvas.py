"""The shell's side of canvas tools (design §9.5): the viewer channel, call results and the canvas's model context.

Every route acts on a conversation the signed-in user owns. A call result is accepted only from the viewer the call
was sent to, and only while the run still waits for it.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, cast

from fastapi import APIRouter, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import Field

from tianzhou_agent_platform.aina.package.canvas_calls import MAX_MODEL_CONTEXT_CHARS, CanvasCallBroker
from tianzhou_agent_platform.api.dependencies import conversation_service, require_actor_ownership
from tianzhou_agent_platform.core.base import StrictModel
from tianzhou_agent_platform.core.errors import PlatformError

CLIENT_ID = r"^[A-Za-z0-9_-]{8,64}$"
_KEEPALIVE_SECONDS = 10.0


class ViewerHeartbeat(StrictModel):
    client_id: str = Field(pattern=CLIENT_ID)
    # Whether the user is looking at this tab; the most recently visible viewer receives canvas calls.
    visible: bool = True


class CanvasCallResult(StrictModel):
    client_id: str = Field(pattern=CLIENT_ID)
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class CanvasModelContext(StrictModel):
    value: str = Field(max_length=MAX_MODEL_CONTEXT_CHARS)


def canvas_calls(request: Request) -> CanvasCallBroker:
    return cast(CanvasCallBroker, request.app.state.canvas_calls)


async def _owned(request: Request, conversation_id: str) -> None:
    conversation = await conversation_service(request).get(conversation_id)
    require_actor_ownership(request, user_id=conversation.user_id, tenant_id=conversation.tenant_id)


def create_aina_canvas_router() -> APIRouter:
    router = APIRouter(tags=["aina-canvas"])

    @router.get("/conversations/{conversation_id}/canvas-viewer")
    async def canvas_viewer(
        conversation_id: str,
        request: Request,
        client_id: str = Query(pattern=CLIENT_ID),
        visible: bool = Query(default=True),
    ) -> StreamingResponse:
        """SSE stream of ``canvas.tool.requested`` events for one tab showing the conversation."""
        await _owned(request, conversation_id)
        broker = canvas_calls(request)
        await broker.heartbeat(conversation_id, client_id, visible=visible)

        async def stream() -> AsyncIterator[str]:
            try:
                async with broker.listen(client_id) as next_event:
                    yield ": connected\n\n"
                    while not await request.is_disconnected():
                        event = await next_event(_KEEPALIVE_SECONDS)
                        if event is None:
                            await broker.heartbeat(conversation_id, client_id, visible=False)
                            yield ": keepalive\n\n"
                            continue
                        yield f"event: {event.get('type', 'message')}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            finally:
                await broker.disconnect(conversation_id, client_id)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})

    @router.post("/conversations/{conversation_id}/canvas-viewer/heartbeat", status_code=status.HTTP_204_NO_CONTENT)
    async def canvas_viewer_heartbeat(conversation_id: str, payload: ViewerHeartbeat, request: Request) -> Response:
        await _owned(request, conversation_id)
        await canvas_calls(request).heartbeat(conversation_id, payload.client_id, visible=payload.visible)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/conversations/{conversation_id}/canvas-calls/{call_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def canvas_call_result(
        conversation_id: str, call_id: str, payload: CanvasCallResult, request: Request
    ) -> Response:
        """The canvas's answer to one canvas tool call; a late or foreign answer is refused."""
        await _owned(request, conversation_id)
        body: dict[str, Any] = (
            {"error": payload.error} if payload.error is not None else {"result": payload.result or {}}
        )
        accepted = await canvas_calls(request).resolve(
            call_id, conversation_id=conversation_id, client_id=payload.client_id, result=body
        )
        if not accepted:
            raise PlatformError(
                "CONFLICT",
                "No canvas call is waiting for this result",
                status_code=409,
                source="canvas",
            )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.put("/conversations/{conversation_id}/canvas-context/{aina_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def canvas_model_context(
        conversation_id: str, aina_id: str, payload: CanvasModelContext, request: Request
    ) -> Response:
        """``setModelContext``: a compact summary of the canvas, added to the agent's requests (design §9.5)."""
        await _owned(request, conversation_id)
        await canvas_calls(request).set_model_context(conversation_id, aina_id, payload.value)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router
