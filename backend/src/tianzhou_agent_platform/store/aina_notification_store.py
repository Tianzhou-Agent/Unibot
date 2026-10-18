"""Inbox notifications from AINAs, read and written in SQL."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select, update

from tianzhou_agent_platform.aina.package.notifications import KEEP_PER_USER, AinaNotification
from tianzhou_agent_platform.store.repository_schema import aina_notifications_table, table_values
from tianzhou_agent_platform.store.sql import SqlStore

notes = aina_notifications_table


class AinaNotificationStore(SqlStore):
    async def create(self, notification: AinaNotification) -> None:
        await self._put(notes, table_values(notes, notification))
        owner = (notes.c.tenant_id == notification.tenant_id) & (notes.c.user_id == notification.user_id)
        kept = select(notes.c.id).where(owner).order_by(notes.c.created_at.desc()).limit(KEEP_PER_USER)
        async with self._session() as session:
            keep = {row[0] for row in (await session.execute(kept)).all()}
        stale = owner & notes.c.id.notin_(keep)
        await self._delete(notes, stale)

    async def list(self, *, tenant_id: str, user_id: str, limit: int) -> list[AinaNotification]:
        statement = select(notes).where(notes.c.tenant_id == tenant_id, notes.c.user_id == user_id)
        return await self._all(AinaNotification, statement.order_by(notes.c.created_at.desc()).limit(limit))

    async def count(
        self, *, tenant_id: str, user_id: str, unread: bool = False, aina_id: str | None = None, since: datetime | None = None
    ) -> int:
        statement = select(func.count()).select_from(notes).where(notes.c.tenant_id == tenant_id, notes.c.user_id == user_id)
        if unread:
            statement = statement.where(notes.c.read_at.is_(None))
        if aina_id is not None:
            statement = statement.where(notes.c.aina_id == aina_id)
        if since is not None:
            statement = statement.where(notes.c.created_at >= since)
        async with self._session() as session:
            return int((await session.execute(statement)).scalar_one())

    async def mark_read(self, *, tenant_id: str, user_id: str, at: datetime, notification_id: str | None) -> int:
        condition = (notes.c.tenant_id == tenant_id) & (notes.c.user_id == user_id) & notes.c.read_at.is_(None)
        if notification_id is not None:
            condition = condition & (notes.c.id == notification_id)
        async with self._session() as session, session.begin():
            result = await session.execute(update(notes).where(condition).values(read_at=at))
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def delete(self, *, tenant_id: str | None, user_id: str | None, aina_id: str) -> int:
        condition = notes.c.aina_id == aina_id
        if tenant_id is not None and user_id is not None:
            condition = condition & (notes.c.tenant_id == tenant_id) & (notes.c.user_id == user_id)
        return await self._delete(notes, condition)
