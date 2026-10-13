"""Conversations, their messages and approvals, read and written in SQL.

Every replica reads the database directly, so no copy can go stale. Read-modify-write updates lock the conversation
row (``SELECT ... FOR UPDATE``) for the length of one transaction.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import ColumnElement, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tianzhou_agent_platform.conversations.models import Conversation, Message
from tianzhou_agent_platform.conversations.schemas import ApprovalRecord
from tianzhou_agent_platform.core.errors import not_found
from tianzhou_agent_platform.store.repository_schema import (
    approvals_table,
    conversations_table,
    messages_table,
    model_from_mapping,
    table_values,
)
from tianzhou_agent_platform.store.sql import SqlStore

# Bounds the IN list when loading the messages of many conversations at once.
_MESSAGE_BATCH = 500


class ConversationStore(SqlStore):
    async def create(self, conversation: Conversation) -> None:
        async with self._session() as session, session.begin():
            await session.execute(insert(conversations_table).values(_conversation_row(conversation)))
            if conversation.messages:
                await session.execute(
                    insert(messages_table),
                    [
                        _message_row(conversation.id, sequence_no, message)
                        for sequence_no, message in enumerate(conversation.messages)
                    ],
                )

    async def get(self, conversation_id: str, *, include_deleted: bool = False) -> Conversation | None:
        async with self._session() as session:
            row = (
                (await session.execute(select(conversations_table).where(conversations_table.c.id == conversation_id)))
                .mappings()
                .first()
            )
            if row is None or (row["status"] == "deleted" and not include_deleted):
                return None
            messages = await _load_messages(session, [conversation_id])
        return model_from_mapping(Conversation, row, messages=messages[conversation_id])

    async def list_conversations(
        self,
        *,
        user_id: str | None,
        tenant_id: str | None,
        category: str | None,
        workspace_id: str | None,
    ) -> list[Conversation]:
        table = conversations_table
        statement = select(table).where(table.c.status != "deleted")
        for column, value in (
            (table.c.user_id, user_id),
            (table.c.tenant_id, tenant_id),
            (table.c.category, category),
            (table.c.workspace_id, workspace_id),
        ):
            if value is not None:
                statement = statement.where(column == value)
        statement = statement.order_by(table.c.updated_at.desc(), table.c.id.desc())
        async with self._session() as session:
            rows = (await session.execute(statement)).mappings().all()
            messages = await _load_messages(session, [row["id"] for row in rows])
        return [model_from_mapping(Conversation, row, messages=messages[row["id"]]) for row in rows]

    async def mutate(
        self,
        conversation_id: str,
        change: Callable[[Conversation], Conversation | None],
        *,
        include_deleted: bool = False,
    ) -> Conversation:
        """Apply ``change`` to the locked conversation; it returns the updated record, or None to keep it as is.

        The record passed to ``change`` has no messages, and ``change`` must not touch them.
        """
        async with self._session() as session:
            async with session.begin():
                row = await _lock_conversation(session, conversation_id)
                if row is None or (row["status"] == "deleted" and not include_deleted):
                    raise not_found("Conversation", conversation_id)
                updated = change(model_from_mapping(Conversation, row))
                if updated is not None:
                    values = _conversation_row(updated)
                    del values["id"], values["next_message_sequence"]
                    await session.execute(
                        update(conversations_table).where(conversations_table.c.id == conversation_id).values(values)
                    )
            return await self._reload(session, conversation_id)

    async def append(
        self,
        conversation_id: str,
        messages: Iterable[Message],
        *,
        require_active: bool,
    ) -> list[Message] | None:
        """Append ``messages`` in order, skipping ids the conversation already has; None if it does not exist.

        With ``require_active``, a conversation that is not active is reported as not found.
        """
        candidates = list(messages)
        async with self._session() as session, session.begin():
            row = await _lock_conversation(session, conversation_id)
            if row is None:
                return None
            if require_active and row["status"] != "active":
                raise not_found("Conversation", conversation_id)
            existing: set[str] = set()
            if candidates:
                result = await session.execute(
                    select(messages_table.c.id).where(
                        messages_table.c.conversation_id == conversation_id,
                        messages_table.c.id.in_([message.id for message in candidates]),
                    )
                )
                existing.update(result.scalars())
            appended: list[Message] = []
            for message in candidates:
                if message.id not in existing:
                    existing.add(message.id)
                    appended.append(message)
            sequence = row["next_message_sequence"]
            if appended:
                await session.execute(
                    insert(messages_table),
                    [
                        _message_row(conversation_id, sequence + offset, message)
                        for offset, message in enumerate(appended)
                    ],
                )
            await session.execute(
                update(conversations_table)
                .where(conversations_table.c.id == conversation_id)
                .values(next_message_sequence=sequence + len(appended), updated_at=datetime.now(UTC))
            )
        return appended

    async def create_approval(self, approval: ApprovalRecord) -> None:
        async with self._session() as session, session.begin():
            await session.execute(insert(approvals_table).values(_approval_row(approval)))

    async def get_approval(self, approval_id: str) -> ApprovalRecord | None:
        async with self._session() as session:
            row = (
                (await session.execute(select(approvals_table).where(approvals_table.c.id == approval_id)))
                .mappings()
                .first()
            )
        return None if row is None else model_from_mapping(ApprovalRecord, row)

    async def list_approvals(
        self,
        *,
        conversation_id: str | None,
        user_id: str | None,
        tenant_id: str | None,
        status: str | None,
    ) -> list[ApprovalRecord]:
        table = approvals_table
        statement = select(table)
        for column, value in (
            (table.c.conversation_id, conversation_id),
            (table.c.user_id, user_id),
            (table.c.tenant_id, tenant_id),
            (table.c.status, status),
        ):
            if value is not None:
                statement = statement.where(column == value)
        statement = statement.order_by(table.c.created_at.desc(), table.c.id.desc())
        async with self._session() as session:
            rows = (await session.execute(statement)).mappings().all()
        return [model_from_mapping(ApprovalRecord, row) for row in rows]

    async def set_approval_status(self, approval_id: str, status: str) -> ApprovalRecord | None:
        resolved = await self._resolve_approvals(approvals_table.c.id == approval_id, status)
        return resolved[0] if resolved else None

    async def deny_pending_approvals(self, conversation_id: str) -> list[ApprovalRecord]:
        return await self._resolve_approvals(
            (approvals_table.c.conversation_id == conversation_id) & (approvals_table.c.status == "pending"),
            "denied",
        )

    async def _resolve_approvals(self, condition: ColumnElement[bool], status: str) -> list[ApprovalRecord]:
        """Set ``status`` and the resolution time on every approval matching ``condition``; returns them updated."""
        resolved_at = datetime.now(UTC)
        async with self._session() as session, session.begin():
            rows = (await session.execute(select(approvals_table).where(condition).with_for_update())).mappings().all()
            if rows:
                await session.execute(
                    update(approvals_table)
                    .where(approvals_table.c.id.in_([row["id"] for row in rows]))
                    .values(status=status, resolved_at=resolved_at)
                )
        return [model_from_mapping(ApprovalRecord, row, status=status, resolved_at=resolved_at) for row in rows]

    async def _reload(self, session: AsyncSession, conversation_id: str) -> Conversation:
        row = (
            (await session.execute(select(conversations_table).where(conversations_table.c.id == conversation_id)))
            .mappings()
            .one()
        )
        messages = await _load_messages(session, [conversation_id])
        return model_from_mapping(Conversation, row, messages=messages[conversation_id])


async def _lock_conversation(session: AsyncSession, conversation_id: str) -> Any:
    return (
        (
            await session.execute(
                select(conversations_table).where(conversations_table.c.id == conversation_id).with_for_update()
            )
        )
        .mappings()
        .first()
    )


async def _load_messages(session: AsyncSession, conversation_ids: Sequence[str]) -> dict[str, list[Message]]:
    messages: dict[str, list[Message]] = defaultdict(list)
    for start in range(0, len(conversation_ids), _MESSAGE_BATCH):
        batch = conversation_ids[start : start + _MESSAGE_BATCH]
        rows = (
            await session.execute(
                select(messages_table)
                .where(messages_table.c.conversation_id.in_(batch))
                .order_by(messages_table.c.conversation_id, messages_table.c.sequence_no)
            )
        ).mappings()
        for row in rows:
            messages[row["conversation_id"]].append(model_from_mapping(Message, row))
    return messages


def _conversation_row(conversation: Conversation) -> dict[str, Any]:
    return table_values(conversations_table, conversation, next_message_sequence=len(conversation.messages))


def _message_row(conversation_id: str, sequence_no: int, message: Message) -> dict[str, Any]:
    return table_values(messages_table, message, conversation_id=conversation_id, sequence_no=sequence_no)


def _approval_row(approval: ApprovalRecord) -> dict[str, Any]:
    return table_values(approvals_table, approval)
