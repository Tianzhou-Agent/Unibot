"""The user's inbox of notifications sent by AINAs (Host API ``notify``)."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request, Response, status

from tianzhou_agent_platform.aina.package.notifications import NotificationInbox, NotificationView
from tianzhou_agent_platform.api.dependencies import repository, request_actor
from tianzhou_agent_platform.core.base import utc_now
from tianzhou_agent_platform.core.errors import PlatformError, not_found


def create_notification_router() -> APIRouter:
    router = APIRouter(prefix="/notifications", tags=["notifications"])

    @router.get("", response_model=NotificationInbox)
    async def list_notifications(request: Request, limit: int = Query(default=30, ge=1, le=100)) -> NotificationInbox:
        actor = request_actor(request)
        repo = repository(request)
        stored = await repo.list_aina_notifications(tenant_id=actor.tenant_id, user_id=actor.user_id, limit=limit)
        names: dict[str, str] = {}
        for aina_id in {item.aina_id for item in stored}:
            try:
                names[aina_id] = (await repo.get_aina(aina_id)).manifest.aina.name
            except PlatformError:
                names[aina_id] = aina_id
        return NotificationInbox(
            items=[
                NotificationView(
                    id=item.id,
                    aina_id=item.aina_id,
                    aina_name=names[item.aina_id],
                    title=item.title,
                    body=item.body,
                    opens=item.opens,
                    conversation_id=item.conversation_id,
                    created_at=item.created_at,
                    read=item.read_at is not None,
                )
                for item in stored
            ],
            unread=await repo.count_aina_notifications(tenant_id=actor.tenant_id, user_id=actor.user_id, unread=True),
        )

    @router.post("/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
    async def mark_read(notification_id: str, request: Request) -> Response:
        actor = request_actor(request)
        repo = repository(request)
        marked = await repo.mark_aina_notifications_read(
            tenant_id=actor.tenant_id, user_id=actor.user_id, at=utc_now(), notification_id=notification_id
        )
        if not marked and notification_id not in {
            item.id for item in await repo.list_aina_notifications(tenant_id=actor.tenant_id, user_id=actor.user_id, limit=200)
        }:
            raise not_found("Notification", notification_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/read-all", status_code=status.HTTP_204_NO_CONTENT)
    async def mark_all_read(request: Request) -> Response:
        actor = request_actor(request)
        await repository(request).mark_aina_notifications_read(tenant_id=actor.tenant_id, user_id=actor.user_id, at=utc_now())
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router
