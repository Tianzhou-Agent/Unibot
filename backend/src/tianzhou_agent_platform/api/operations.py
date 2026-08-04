import asyncio

from fastapi import APIRouter, Request

from tianzhou_agent_platform.api.dependencies import repository, runtime
from tianzhou_agent_platform.core.chat import ApprovalAction, ApprovalRecord, ChatResponse, LLMCallRecord, TraceRecord


def create_operations_router() -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @router.post("/approvals/{approval_id}/confirm", response_model=ChatResponse)
    async def confirm_approval(
        approval_id: str,
        payload: ApprovalAction,
        request: Request,
    ) -> ChatResponse:
        return await runtime(request).confirm(
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
        return await runtime(request).deny(
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
        return await repository(request).list_approvals(
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
        return await repository(request).list_traces(user_id=user_id, tenant_id=tenant_id)

    @router.get("/traces/{trace_id}", response_model=TraceRecord)
    async def get_trace(trace_id: str, request: Request) -> TraceRecord:
        return await repository(request).get_trace(trace_id)

    @router.get("/llm-calls", response_model=list[LLMCallRecord])
    async def list_llm_calls(
        request: Request,
        limit: int = 200,
        offset: int = 0,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> list[LLMCallRecord]:
        data_repository = repository(request)
        if user_id is None and tenant_id is None:
            return await data_repository.list_llm_calls(
                limit=max(1, min(limit, 500)),
                offset=max(0, offset),
            )
        conversations, traces = await asyncio.gather(
            data_repository.list_conversations(user_id=user_id, tenant_id=tenant_id),
            data_repository.list_traces(user_id=user_id, tenant_id=tenant_id),
        )
        return await data_repository.list_llm_calls(
            limit=max(1, min(limit, 500)),
            offset=max(0, offset),
            trace_ids={trace.trace_id for trace in traces},
            context_ids={conversation.id for conversation in conversations},
        )

    @router.get("/admin/summary")
    async def admin_summary(
        request: Request,
        user_id: str = "anonymous",
        tenant_id: str = "default",
    ) -> dict[str, int]:
        data_repository = repository(request)
        conversations, tools, skills, ainas, installations, traces, memories, document_tasks = await asyncio.gather(
            data_repository.list_conversations(user_id=user_id, tenant_id=tenant_id),
            data_repository.list_tools(),
            data_repository.list_skills(),
            data_repository.list_ainas(),
            data_repository.list_installations(user_id=user_id, tenant_id=tenant_id),
            data_repository.list_traces(user_id=user_id, tenant_id=tenant_id),
            data_repository.list_memories(user_id=user_id, tenant_id=tenant_id),
            data_repository.list_document_edit_tasks(user_id=user_id, tenant_id=tenant_id),
        )
        trace_ids = {trace.trace_id for trace in traces}
        context_ids = {conversation.id for conversation in conversations} | {task.id for task in document_tasks}
        llm_call_count = await data_repository.count_llm_calls(
            trace_ids=trace_ids,
            context_ids=context_ids,
        )
        tool_ids = {tool.tool_id for tool in tools}
        skill_ids = {skill.skill_id for skill in skills}
        for aina in ainas:
            tool_ids.update(capability.id for capability in aina.manifest.capabilities.tools)
            skill_ids.update(capability.id for capability in aina.manifest.capabilities.skills)
        return {
            "conversations": len(conversations),
            "tools": len(tool_ids),
            "skills": len(skill_ids),
            "ainas": len(ainas),
            "installations": len(installations),
            "traces": len(traces),
            "llm_calls": llm_call_count,
            "memories": len(memories),
        }

    return router
