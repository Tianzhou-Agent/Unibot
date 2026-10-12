"""Answer feedback and its review cases, read and written in SQL.

One feedback per actor and message is a unique key, so concurrent submissions for the same message update one row.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ColumnElement, func, insert, or_, select
from sqlalchemy.exc import IntegrityError

from tianzhou_agent_platform.core.errors import not_found
from tianzhou_agent_platform.core.feedback import FeedbackRecord
from tianzhou_agent_platform.store.memory_repository import (
    cancelled_feedback,
    resubmitted_feedback,
    updated_feedback_case,
)
from tianzhou_agent_platform.store.repository_schema import feedbacks_table, table_values
from tianzhou_agent_platform.store.sql import SqlStore

feedbacks = feedbacks_table


def _for_message(message_id: str, user_id: str, tenant_id: str) -> ColumnElement[bool]:
    return (
        (feedbacks.c.message_id == message_id) & (feedbacks.c.user_id == user_id) & (feedbacks.c.tenant_id == tenant_id)
    )


class FeedbackStore(SqlStore):
    async def upsert(self, feedback: FeedbackRecord) -> FeedbackRecord:
        # A second attempt covers a concurrent first submission: its insert loses on the unique key, and the retry
        # updates the row the winner created.
        for attempt in range(2):
            existing = await self._one(
                FeedbackRecord,
                select(feedbacks).where(_for_message(feedback.message_id, feedback.user_id, feedback.tenant_id)),
            )
            if existing is not None:
                stored = await self._update_locked(
                    feedbacks, FeedbackRecord, existing.id, lambda current: resubmitted_feedback(current, feedback)
                )
                if stored is not None:
                    return stored
                continue
            try:
                async with self._session() as session, session.begin():
                    await session.execute(insert(feedbacks).values(table_values(feedbacks, feedback)))
                return feedback
            except IntegrityError:
                if attempt == 1:
                    raise
        raise AssertionError("unreachable")

    async def active_for_message(self, message_id: str, *, user_id: str, tenant_id: str) -> FeedbackRecord | None:
        return await self._one(
            FeedbackRecord,
            select(feedbacks).where(_for_message(message_id, user_id, tenant_id), feedbacks.c.active.is_(True)),
        )

    async def get_active(self, feedback_id: str) -> FeedbackRecord | None:
        return await self._one(
            FeedbackRecord, select(feedbacks).where(feedbacks.c.id == feedback_id, feedbacks.c.active.is_(True))
        )

    async def list_active(
        self,
        *,
        from_at: datetime | None,
        to_at: datetime | None,
        rating: str | None,
        user_query: str | None,
    ) -> list[FeedbackRecord]:
        statement = select(feedbacks).where(feedbacks.c.active.is_(True))
        if from_at is not None:
            statement = statement.where(feedbacks.c.created_at >= from_at)
        if to_at is not None:
            statement = statement.where(feedbacks.c.created_at < to_at)
        if rating is not None:
            statement = statement.where(feedbacks.c.rating == rating)
        normalized = (user_query or "").strip().lower()
        if normalized:
            statement = statement.where(
                or_(
                    func.lower(feedbacks.c.user_name).contains(normalized, autoescape=True),
                    func.lower(feedbacks.c.user_email).contains(normalized, autoescape=True),
                )
            )
        return await self._all(FeedbackRecord, statement.order_by(feedbacks.c.created_at.desc(), feedbacks.c.id.desc()))

    async def cancel(self, message_id: str, *, user_id: str, tenant_id: str, actor_name: str) -> None:
        item = await self.active_for_message(message_id, user_id=user_id, tenant_id=tenant_id)
        if item is None:
            return
        await self._update_locked(
            feedbacks,
            FeedbackRecord,
            item.id,
            lambda current: (
                cancelled_feedback(current, user_id=user_id, actor_name=actor_name) if current.active else None
            ),
        )

    async def update_case(
        self, feedback_id: str, *, status: str, assignee: str, conclusion: str, actor_id: str, actor_name: str
    ) -> FeedbackRecord:
        def change(current: FeedbackRecord) -> FeedbackRecord:
            if not current.active:
                raise not_found("Feedback", feedback_id)
            return updated_feedback_case(
                current,
                status=status,
                assignee=assignee,
                conclusion=conclusion,
                actor_id=actor_id,
                actor_name=actor_name,
            )

        updated = await self._update_locked(feedbacks, FeedbackRecord, feedback_id, change)
        if updated is None:
            raise not_found("Feedback", feedback_id)
        return updated
