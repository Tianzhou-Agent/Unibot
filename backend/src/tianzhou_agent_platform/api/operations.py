import asyncio
from typing import Any, cast

from fastapi import APIRouter, Query, Request

from tianzhou_agent_platform.api.dependencies import (
    actor_scope,
    bind_actor,
    repository,
    require_actor_ownership,
    require_platform_admin,
    runtime,
)
from tianzhou_agent_platform.auth.models import AdminUserSummary
from tianzhou_agent_platform.core.chat import ApprovalAction, ApprovalRecord, ChatResponse, LLMCallRecord, TraceRecord
from tianzhou_agent_platform.core.conversation import Conversation
from tianzhou_agent_platform.core.observability_query import ObsQueryService
from tianzhou_agent_platform.core.operations_analytics import OperationsAnalyticsService, operations_bounds


def _obs_query(request: Request) -> ObsQueryService:
    query = getattr(request.app.state, "obs_query", None)
    if query is None:
        raise RuntimeError("OBS query service is not initialized")
    return cast(ObsQueryService, query)


def _operations_analytics(request: Request) -> OperationsAnalyticsService:
    service = getattr(request.app.state, "operations_analytics", None)
    if service is None:
        raise RuntimeError("Operations analytics service is not initialized")
    return cast(OperationsAnalyticsService, service)


def create_operations_router() -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    async def health(request: Request) -> dict[str, Any]:
        """Service health plus discoverable OBS pipeline status (design 15)."""
        payload: dict[str, Any] = {"status": "ok"}
        buffer = getattr(request.app.state, "obs_buffer", None)
        worker = getattr(request.app.state, "obs_ingest_worker", None)
        settings = getattr(request.app.state, "settings", None)
        if buffer is not None:
            payload["obs"] = buffer.metrics.snapshot()
            payload["obs"]["ingest"] = (
                worker.metrics.snapshot() if worker is not None else {}
            )
            payload["obs"]["last_ingest_success_at"] = (
                worker.metrics.ingest_last_success_at.isoformat()
                if worker is not None and worker.metrics.ingest_last_success_at
                else None
            )
            payload["obs"]["enabled"] = True
            if settings is not None:
                payload["obs"]["stream_key"] = settings.obs_redis_stream_key
                payload["obs"]["consumer_group"] = settings.obs_redis_group_name
                payload["obs"]["retention_days"] = settings.obs_retention_days
        else:
            payload["obs"] = {"enabled": False}
        return payload

    @router.get("/obs/overview")
    async def obs_overview(request: Request, range: str = "week") -> dict:
        actor = actor_scope(request)
        return await _obs_query(request).personal_overview(
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            range_name=range,
        )

    @router.get("/obs/sessions/{session_id}")
    async def obs_session_detail(request: Request, session_id: str) -> dict | None:
        actor = actor_scope(request)
        return await _obs_query(request).session_detail(
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            session_id=session_id,
        )

    @router.get("/obs/raw-logs")
    async def obs_raw_logs(
        request: Request,
        trace_id: str,
        span_id: str,
    ) -> dict | None:
        actor = actor_scope(request)
        return await _obs_query(request).raw_log(
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            trace_id=trace_id,
            span_id=span_id,
        )

    @router.get("/admin/obs/overview")
    async def admin_obs_overview(
        request: Request,
        range: str = Query(default="week", pattern="^(day|week|month)$"),
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict:
        require_platform_admin(request)
        return await _obs_query(request).admin_overview(
            tenant_id=tenant_id,
            user_id=user_id,
            range_name=range,
        )

    @router.get("/admin/users")
    async def admin_users(
        request: Request,
        query: str | None = Query(default=None, max_length=160),
        limit: int = Query(default=100, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        require_platform_admin(request)
        page_size = max(1, min(limit, 200))
        users = await repository(request).list_users(
            query=query,
            limit=page_size + 1,
            offset=offset,
        )
        return {
            "items": [
                AdminUserSummary.from_record(user).model_dump(mode="json")
                for user in users[:page_size]
            ],
            "has_more": len(users) > page_size,
        }

    @router.get("/admin/obs/traces")
    async def admin_obs_traces(
        request: Request,
        user_id: str = Query(min_length=1, max_length=160),
        tenant_id: str | None = Query(default=None, max_length=160),
        range: str = Query(default="week", pattern="^(day|week|month)$"),
        limit: int = Query(default=100, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        require_platform_admin(request)
        return await _obs_query(request).admin_trace_list(
            user_id=user_id,
            tenant_id=tenant_id,
            range_name=range,
            limit=limit,
            offset=offset,
        )

    @router.get("/admin/obs/traces/{trace_id}")
    async def admin_obs_trace_detail(
        request: Request,
        trace_id: str,
        user_id: str = Query(min_length=1, max_length=160),
        tenant_id: str | None = Query(default=None, max_length=160),
    ) -> dict[str, Any] | None:
        require_platform_admin(request)
        return await _obs_query(request).admin_trace_detail(
            trace_id=trace_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )

    @router.get("/admin/obs/sessions/{session_id}")
    async def admin_obs_session_detail(
        request: Request,
        session_id: str,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict | None:
        require_platform_admin(request)
        return await _obs_query(request).admin_session_detail(
            session_id=session_id,
            tenant_id=tenant_id,
            user_id=user_id,
        )

    @router.get("/admin/operations/overview")
    async def admin_operations_overview(
        request: Request,
        range: str = Query(default="week", pattern="^(week|month|quarter)$"),
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        require_platform_admin(request)
        start, end, _, _ = operations_bounds(range)
        feedbacks = await repository(request).list_feedbacks(from_at=start, to_at=end)
        if tenant_id is not None:
            feedbacks = [feedback for feedback in feedbacks if feedback.tenant_id == tenant_id]
        return await _operations_analytics(request).overview(
            tenant_id=tenant_id,
            range_name=range,
            feedbacks=feedbacks,
        )

    @router.post("/approvals/{approval_id}/confirm", response_model=ChatResponse)
    async def confirm_approval(
        approval_id: str,
        payload: ApprovalAction,
        request: Request,
    ) -> ChatResponse:
        scoped = bind_actor(request, payload)
        return await runtime(request).confirm(
            approval_id,
            user_id=scoped.user_id,
            tenant_id=scoped.tenant_id,
        )

    @router.post("/approvals/{approval_id}/deny", response_model=ApprovalRecord)
    async def deny_approval(
        approval_id: str,
        payload: ApprovalAction,
        request: Request,
    ) -> ApprovalRecord:
        scoped = bind_actor(request, payload)
        return await runtime(request).deny(
            approval_id,
            user_id=scoped.user_id,
            tenant_id=scoped.tenant_id,
        )

    @router.get("/approvals", response_model=list[ApprovalRecord])
    async def list_approvals(
        request: Request,
        conversation_id: str | None = None,
        user_id: str | None = None,
        tenant_id: str | None = None,
        status: str | None = None,
    ) -> list[ApprovalRecord]:
        actor = actor_scope(request, user_id=user_id, tenant_id=tenant_id)
        return await repository(request).list_approvals(
            conversation_id=conversation_id,
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            status=status,
        )

    @router.get("/traces", response_model=list[TraceRecord])
    async def list_traces(
        request: Request,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> list[TraceRecord]:
        actor = actor_scope(request, user_id=user_id, tenant_id=tenant_id)
        return await repository(request).list_traces(user_id=actor.user_id, tenant_id=actor.tenant_id)

    @router.get("/traces/{trace_id}", response_model=TraceRecord)
    async def get_trace(trace_id: str, request: Request) -> TraceRecord:
        trace = await repository(request).get_trace(trace_id)
        require_actor_ownership(request, user_id=trace.user_id, tenant_id=trace.tenant_id)
        return trace

    @router.get("/llm-calls", response_model=list[LLMCallRecord])
    async def list_llm_calls(
        request: Request,
        limit: int = 200,
        offset: int = 0,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> list[LLMCallRecord]:
        data_repository = repository(request)
        if getattr(request.state, "actor", None) is None and user_id is None and tenant_id is None:
            return await data_repository.list_llm_calls(
                limit=max(1, min(limit, 500)),
                offset=max(0, offset),
            )
        actor = actor_scope(request, user_id=user_id, tenant_id=tenant_id)
        conversations, traces = await asyncio.gather(
            data_repository.list_conversations(user_id=actor.user_id, tenant_id=actor.tenant_id),
            data_repository.list_traces(user_id=actor.user_id, tenant_id=actor.tenant_id),
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
        require_platform_admin(request)
        legacy_actor = (
            actor_scope(request, user_id=user_id, tenant_id=tenant_id)
            if getattr(request.state, "actor", None) is None
            else None
        )
        data_repository = repository(request)
        conversations, tools, skills, ainas, installations, traces, memories, document_tasks = await asyncio.gather(
            data_repository.list_conversations(
                user_id=legacy_actor.user_id if legacy_actor else None,
                tenant_id=legacy_actor.tenant_id if legacy_actor else None,
            ),
            data_repository.list_tools(),
            data_repository.list_skills(),
            data_repository.list_ainas(),
            data_repository.list_installations(
                user_id=legacy_actor.user_id if legacy_actor else None,
                tenant_id=legacy_actor.tenant_id if legacy_actor else None,
            ),
            data_repository.list_traces(
                user_id=legacy_actor.user_id if legacy_actor else None,
                tenant_id=legacy_actor.tenant_id if legacy_actor else None,
            ),
            data_repository.list_memories(
                user_id=legacy_actor.user_id if legacy_actor else None,
                tenant_id=legacy_actor.tenant_id if legacy_actor else None,
            ),
            data_repository.list_document_edit_tasks(
                user_id=legacy_actor.user_id if legacy_actor else None,
                tenant_id=legacy_actor.tenant_id if legacy_actor else None,
            ),
        )
        if legacy_actor:
            trace_ids = {trace.trace_id for trace in traces}
            context_ids = {conversation.id for conversation in conversations} | {task.id for task in document_tasks}
            llm_call_count = await data_repository.count_llm_calls(
                trace_ids=trace_ids,
                context_ids=context_ids,
            )
        else:
            llm_call_count = await data_repository.count_llm_calls()
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

    @router.get("/admin/conversations", response_model=list[Conversation])
    async def admin_conversations(request: Request) -> list[Conversation]:
        require_platform_admin(request)
        return await repository(request).list_conversations()

    @router.get("/admin/traces", response_model=list[TraceRecord])
    async def admin_traces(request: Request) -> list[TraceRecord]:
        require_platform_admin(request)
        return await repository(request).list_traces()

    @router.get("/admin/llm-calls", response_model=list[LLMCallRecord])
    async def admin_llm_calls(
        request: Request,
        limit: int = 200,
        offset: int = 0,
    ) -> list[LLMCallRecord]:
        require_platform_admin(request)
        return await repository(request).list_llm_calls(
            limit=max(1, min(limit, 500)),
            offset=max(0, offset),
        )

    return router
