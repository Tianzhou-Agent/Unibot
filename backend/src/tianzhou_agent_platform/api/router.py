from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any, cast

from fastapi import APIRouter, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from tianzhou_agent_platform.aina.gateway import RemoteCapabilityGateway
from tianzhou_agent_platform.core.agent import AgentRuntime
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.core.models import (
    AinaInstallation,
    AinaManifest,
    AinaRecord,
    ApprovalAction,
    ApprovalRecord,
    ChatRequest,
    ChatResponse,
    Conversation,
    ConversationCreate,
    ConversationUpdate,
    InstallationRequest,
    PermissionUpdate,
    SkillCreate,
    SkillRecord,
    ToolCreate,
    ToolRecord,
    TraceRecord,
)
from tianzhou_agent_platform.core.repository import InMemoryRepository
from tianzhou_agent_platform.core.schema import validate_schema


def _repository(request: Request) -> InMemoryRepository:
    return cast(InMemoryRepository, request.app.state.repository)


def _runtime(request: Request) -> AgentRuntime:
    return cast(AgentRuntime, request.app.state.agent_runtime)


def _gateway(request: Request) -> RemoteCapabilityGateway:
    return cast(RemoteCapabilityGateway, request.app.state.capability_gateway)


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @router.post("/chat", response_model=ChatResponse)
    async def chat(payload: ChatRequest, request: Request) -> ChatResponse:
        return await _runtime(request).chat(payload)

    @router.post("/chat/stream")
    async def stream_chat(payload: ChatRequest, request: Request) -> StreamingResponse:
        runtime = _runtime(request)

        async def stream() -> AsyncIterator[str]:
            queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

            async def sink(event: dict[str, Any]) -> None:
                await queue.put(event)

            async def produce() -> None:
                try:
                    result = await runtime.chat(payload, event_sink=sink)
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
                finally:
                    await queue.put(None)

            task = asyncio.create_task(produce())
            try:
                while True:
                    event = await queue.get()
                    if event is None:
                        break
                    event_type = event.get("type", "message")
                    yield f"event: {event_type}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @router.post("/conversations", response_model=Conversation, status_code=status.HTTP_201_CREATED)
    async def create_conversation(payload: ConversationCreate, request: Request) -> Conversation:
        return await _repository(request).create_conversation(payload)

    @router.get("/conversations", response_model=list[Conversation])
    async def list_conversations(
        request: Request,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> list[Conversation]:
        return await _repository(request).list_conversations(user_id=user_id, tenant_id=tenant_id)

    @router.get("/conversations/{conversation_id}", response_model=Conversation)
    async def get_conversation(conversation_id: str, request: Request) -> Conversation:
        return await _repository(request).get_conversation(conversation_id)

    @router.patch("/conversations/{conversation_id}", response_model=Conversation)
    async def update_conversation(
        conversation_id: str,
        payload: ConversationUpdate,
        request: Request,
    ) -> Conversation:
        return await _repository(request).update_conversation(conversation_id, payload)

    @router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_conversation(conversation_id: str, request: Request) -> Response:
        await _repository(request).set_conversation_status(conversation_id, "deleted")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/conversations/{conversation_id}/restore", response_model=Conversation)
    async def restore_conversation(conversation_id: str, request: Request) -> Conversation:
        return await _repository(request).set_conversation_status(conversation_id, "active")

    @router.post("/tools", response_model=ToolRecord, status_code=status.HTTP_201_CREATED)
    async def register_tool(payload: ToolCreate, request: Request) -> ToolRecord:
        validate_schema(payload.input_schema, label="input_schema")
        if payload.output_schema:
            validate_schema(payload.output_schema, label="output_schema")
        values = {name: getattr(payload, name) for name in ToolCreate.model_fields}
        return await _repository(request).register_tool(ToolRecord(**values))

    @router.get("/tools", response_model=list[ToolRecord])
    async def list_tools(request: Request) -> list[ToolRecord]:
        return await _repository(request).list_tools()

    @router.get("/tools/{tool_id}", response_model=ToolRecord)
    async def get_tool(tool_id: str, request: Request) -> ToolRecord:
        return await _repository(request).get_tool(tool_id)

    @router.delete("/tools/{tool_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_tool(tool_id: str, request: Request) -> Response:
        await _repository(request).remove_tool(tool_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/skills", response_model=SkillRecord, status_code=status.HTTP_201_CREATED)
    async def register_skill(payload: SkillCreate, request: Request) -> SkillRecord:
        validate_schema(payload.input_schema, label="input_schema")
        validate_schema(payload.output_schema, label="output_schema")
        repository = _repository(request)
        for tool_id in payload.tools:
            await repository.get_tool(tool_id)
        return await repository.register_skill(SkillRecord(**payload.model_dump()))

    @router.get("/skills", response_model=list[SkillRecord])
    async def list_skills(request: Request) -> list[SkillRecord]:
        return await _repository(request).list_skills()

    @router.get("/skills/{skill_id}", response_model=SkillRecord)
    async def get_skill(skill_id: str, request: Request) -> SkillRecord:
        return await _repository(request).get_skill(skill_id)

    @router.delete("/skills/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_skill(skill_id: str, request: Request) -> Response:
        await _repository(request).remove_skill(skill_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/ainas", response_model=AinaRecord, status_code=status.HTTP_201_CREATED)
    async def register_aina(payload: AinaManifest, request: Request) -> AinaRecord:
        for capability in [*payload.capabilities.skills, *payload.capabilities.tools]:
            validate_schema(capability.input_schema, label=f"capability {capability.id} input_schema")
        health = await _gateway(request).probe_aina(payload)
        return await _repository(request).register_aina(AinaRecord(manifest=payload, last_health=health))

    @router.get("/ainas", response_model=list[AinaRecord])
    async def list_ainas(request: Request) -> list[AinaRecord]:
        return await _repository(request).list_ainas()

    @router.get("/ainas/{aina_id}", response_model=AinaRecord)
    async def get_aina(aina_id: str, request: Request) -> AinaRecord:
        return await _repository(request).get_aina(aina_id)

    @router.delete("/ainas/{aina_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_aina(aina_id: str, request: Request) -> Response:
        await _repository(request).remove_aina(aina_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/ainas/{aina_id}/install", response_model=AinaInstallation)
    async def install_aina(
        aina_id: str,
        payload: InstallationRequest,
        request: Request,
    ) -> AinaInstallation:
        repository = _repository(request)
        record = await repository.get_aina(aina_id)
        _validate_permission_subset(record, payload.granted_permissions)
        installation = AinaInstallation(
            **payload.model_dump(),
            aina_id=aina_id,
            installed_version=record.manifest.aina.version,
        )
        return await repository.put_installation(installation)

    @router.patch("/ainas/{aina_id}/installation", response_model=AinaInstallation)
    async def update_aina_permissions(
        aina_id: str,
        payload: PermissionUpdate,
        request: Request,
    ) -> AinaInstallation:
        repository = _repository(request)
        record = await repository.get_aina(aina_id)
        _validate_permission_subset(record, payload.granted_permissions)
        installation = await repository.get_installation(
            tenant_id=payload.tenant_id,
            user_id=payload.user_id,
            aina_id=aina_id,
        )
        updated = installation.model_copy(update={"granted_permissions": payload.granted_permissions}, deep=True)
        return await repository.put_installation(updated)

    @router.delete("/ainas/{aina_id}/install", status_code=status.HTTP_204_NO_CONTENT)
    async def uninstall_aina(
        aina_id: str,
        request: Request,
        user_id: str = Query(default="anonymous"),
        tenant_id: str = Query(default="default"),
    ) -> Response:
        await _repository(request).remove_installation(
            tenant_id=tenant_id,
            user_id=user_id,
            aina_id=aina_id,
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/installations", response_model=list[AinaInstallation])
    async def list_installations(
        request: Request,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> list[AinaInstallation]:
        return await _repository(request).list_installations(user_id=user_id, tenant_id=tenant_id)

    @router.post("/approvals/{approval_id}/confirm", response_model=ChatResponse)
    async def confirm_approval(
        approval_id: str,
        payload: ApprovalAction,
        request: Request,
    ) -> ChatResponse:
        return await _runtime(request).confirm(
            approval_id,
            user_id=payload.user_id,
            tenant_id=payload.tenant_id,
        )

    @router.post("/approvals/{approval_id}/deny", response_model=ApprovalRecord)
    async def deny_approval(
        approval_id: str,
        payload: ApprovalAction,
        request: Request,
    ) -> ApprovalRecord:
        return await _runtime(request).deny(
            approval_id,
            user_id=payload.user_id,
            tenant_id=payload.tenant_id,
        )

    @router.get("/approvals", response_model=list[ApprovalRecord])
    async def list_approvals(
        request: Request,
        conversation_id: str | None = None,
        user_id: str | None = None,
        tenant_id: str | None = None,
        status: str | None = None,
    ) -> list[ApprovalRecord]:
        return await _repository(request).list_approvals(
            conversation_id=conversation_id,
            user_id=user_id,
            tenant_id=tenant_id,
            status=status,
        )

    @router.get("/traces", response_model=list[TraceRecord])
    async def list_traces(
        request: Request,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> list[TraceRecord]:
        return await _repository(request).list_traces(user_id=user_id, tenant_id=tenant_id)

    @router.get("/traces/{trace_id}", response_model=TraceRecord)
    async def get_trace(trace_id: str, request: Request) -> TraceRecord:
        return await _repository(request).get_trace(trace_id)

    @router.get("/admin/summary")
    async def admin_summary(request: Request) -> dict[str, int]:
        repository = _repository(request)
        conversations, tools, skills, ainas, installations, traces = await asyncio.gather(
            repository.list_conversations(),
            repository.list_tools(),
            repository.list_skills(),
            repository.list_ainas(),
            repository.list_installations(),
            repository.list_traces(),
        )
        return {
            "conversations": len(conversations),
            "tools": len(tools),
            "skills": len(skills),
            "ainas": len(ainas),
            "installations": len(installations),
            "traces": len(traces),
        }

    return router


def _validate_permission_subset(record: AinaRecord, granted_permissions: list[str]) -> None:
    undeclared = set(granted_permissions) - set(record.manifest.permissions)
    if undeclared:
        raise PlatformError(
            "INVALID_REQUEST",
            f"Cannot grant undeclared permissions: {', '.join(sorted(undeclared))}",
            status_code=422,
        )
