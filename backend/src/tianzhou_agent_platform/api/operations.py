import asyncio

from fastapi import APIRouter, Request

from tianzhou_agent_platform.api.dependencies import repository, runtime
from tianzhou_agent_platform.core.chat import ApprovalAction, ApprovalRecord, ChatResponse, TraceRecord


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

    @router.get("/admin/summary")
    async def admin_summary(request: Request) -> dict[str, int]:
        data_repository = repository(request)
        conversations, tools, skills, ainas, installations, traces = await asyncio.gather(
            data_repository.list_conversations(),
            data_repository.list_tools(),
            data_repository.list_skills(),
            data_repository.list_ainas(),
            data_repository.list_installations(),
            data_repository.list_traces(),
        )
        memories = await data_repository.list_memories(user_id="anonymous", tenant_id="default")
        return {
            "conversations": len(conversations),
            "tools": len(tools),
            "skills": len(skills),
            "ainas": len(ainas),
            "installations": len(installations),
            "traces": len(traces),
            "memories": len(memories),
        }

    return router
