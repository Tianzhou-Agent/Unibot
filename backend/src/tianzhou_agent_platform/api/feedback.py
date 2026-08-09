from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Query, Request, Response

from tianzhou_agent_platform.api.dependencies import (
    repository,
    request_actor,
    require_platform_admin,
)
from tianzhou_agent_platform.core.feedback import (
    FeedbackCaseUpdate,
    FeedbackDetail,
    FeedbackMetrics,
    FeedbackRecord,
    FeedbackUpsert,
)
from tianzhou_agent_platform.core.feedback_service import (
    aware,
    compute_feedback_metrics,
    feedback_detail,
    upsert_message_feedback,
)


def create_feedback_router() -> APIRouter:
    router = APIRouter()

    @router.get("/feedback/messages/{message_id}", response_model=FeedbackRecord | None)
    async def get_message_feedback(message_id: str, request: Request) -> FeedbackRecord | None:
        actor = request_actor(request)
        return await repository(request).get_feedback_for_message(
            message_id,
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
        )

    @router.put("/feedback/messages/{message_id}", response_model=FeedbackRecord)
    async def put_message_feedback(
        message_id: str,
        payload: FeedbackUpsert,
        request: Request,
    ) -> FeedbackRecord:
        actor = request_actor(request)
        user = getattr(request.state, "user", None)
        return await upsert_message_feedback(
            repository(request),
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            user_name=getattr(user, "name", None) or actor.user_id,
            user_email=str(getattr(user, "email", "")),
            payload=payload,
            message_id=message_id,
        )

    @router.delete("/feedback/messages/{message_id}", status_code=204)
    async def delete_message_feedback(message_id: str, request: Request) -> Response:
        actor = request_actor(request)
        user = getattr(request.state, "user", None)
        await repository(request).cancel_feedback(
            message_id,
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            actor_name=getattr(user, "name", None) or actor.user_id,
        )
        return Response(status_code=204)

    @router.get("/admin/feedback", response_model=list[FeedbackRecord])
    async def list_admin_feedback(
        request: Request,
        from_at: datetime | None = None,
        to_at: datetime | None = None,
        rating: str | None = Query(default=None, pattern="^(up|down)$"),
        user_query: str | None = Query(default=None, max_length=100),
    ) -> list[FeedbackRecord]:
        require_platform_admin(request)
        return await repository(request).list_feedbacks(
            from_at=aware(from_at),
            to_at=aware(to_at),
            rating=rating,
            user_query=user_query,
        )

    @router.get("/admin/feedback/metrics", response_model=FeedbackMetrics)
    async def admin_feedback_metrics(
        request: Request,
        from_at: datetime,
        to_at: datetime,
    ) -> FeedbackMetrics:
        require_platform_admin(request)
        return await compute_feedback_metrics(
            repository(request),
            from_at=from_at,
            to_at=to_at,
        )

    @router.get("/admin/feedback/{feedback_id}", response_model=FeedbackDetail)
    async def admin_feedback_detail(feedback_id: str, request: Request) -> FeedbackDetail:
        require_platform_admin(request)
        return await feedback_detail(repository(request), feedback_id=feedback_id)

    @router.patch("/admin/feedback/{feedback_id}/case", response_model=FeedbackRecord)
    async def update_admin_feedback_case(
        feedback_id: str,
        payload: FeedbackCaseUpdate,
        request: Request,
    ) -> FeedbackRecord:
        admin = require_platform_admin(request)
        return await repository(request).update_feedback_case(
            feedback_id,
            status=payload.status,
            assignee=payload.assignee.strip(),
            conclusion=payload.conclusion.strip(),
            actor_id=admin.id if admin else "admin",
            actor_name=admin.name if admin else "管理员",
        )

    return router
