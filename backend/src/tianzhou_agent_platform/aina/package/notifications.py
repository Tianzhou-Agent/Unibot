"""Notifications an AINA sends its user through the Host API (``notify`` scope, design §8).

They land in the user's inbox in the Superbot shell. An AINA writes plain text only and cannot choose a link: a
notification opens the AINA's own app, or the conversation of the tool call that sent it.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal, Protocol, runtime_checkable

from pydantic import Field

from tianzhou_agent_platform.core.base import StrictModel, utc_now
from tianzhou_agent_platform.core.errors import PlatformError

MAX_PER_HOUR = 30  # per installation
KEEP_PER_USER = 200  # older notifications are dropped
RATE_WINDOW = timedelta(hours=1)

Opens = Literal["app", "conversation"]


class AinaNotification(StrictModel):
    id: str
    tenant_id: str
    user_id: str
    aina_id: str
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(default="", max_length=500)
    opens: Opens = "app"
    conversation_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    read_at: datetime | None = None


class NotificationInput(StrictModel):
    """``POST /aina-host/v1/notifications``."""

    title: str = Field(min_length=1, max_length=120)
    body: str = Field(default="", max_length=500)
    # ``conversation``: the conversation of the tool call whose token sent it (only for calls made in one).
    opens: Opens = "app"


class NotificationReceipt(StrictModel):
    id: str
    created_at: datetime


class NotificationView(StrictModel):
    """One inbox entry as the shell shows it."""

    id: str
    aina_id: str
    aina_name: str
    title: str
    body: str
    opens: Opens
    conversation_id: str | None = None
    created_at: datetime
    read: bool


class NotificationInbox(StrictModel):
    items: list[NotificationView]
    unread: int


def rate_limited() -> PlatformError:
    return PlatformError(
        "RATE_LIMITED",
        f"An AINA may send at most {MAX_PER_HOUR} notifications per hour",
        status_code=429,
        retryable=True,
        source="aina_host",
    )


@runtime_checkable
class AinaNotificationRepository(Protocol):
    async def create_aina_notification(self, notification: AinaNotification) -> AinaNotification: ...

    async def list_aina_notifications(self, *, tenant_id: str, user_id: str, limit: int) -> list[AinaNotification]: ...

    async def count_aina_notifications(
        self,
        *,
        tenant_id: str,
        user_id: str,
        unread: bool = False,
        aina_id: str | None = None,
        since: datetime | None = None,
    ) -> int: ...

    async def mark_aina_notifications_read(
        self, *, tenant_id: str, user_id: str, at: datetime, notification_id: str | None = None
    ) -> int: ...

    async def delete_aina_notifications(
        self, *, aina_id: str, tenant_id: str | None = None, user_id: str | None = None
    ) -> int: ...
