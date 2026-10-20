"""Metadata of files attached to chat messages, read and written in SQL (the bytes live in a blob store)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from tianzhou_agent_platform.conversations.attachments import Attachment
from tianzhou_agent_platform.store.repository_schema import chat_attachments_table, table_values
from tianzhou_agent_platform.store.sql import SqlStore

attachments = chat_attachments_table


class ChatAttachmentStore(SqlStore):
    async def get(self, file_ref: str) -> Attachment | None:
        return await self._one(Attachment, select(attachments).where(attachments.c.id == file_ref))

    async def put(self, attachment: Attachment) -> None:
        await self._put(attachments, table_values(attachments, attachment))

    async def list_refs(self, file_refs: list[str]) -> list[Attachment]:
        if not file_refs:
            return []
        return await self._all(Attachment, select(attachments).where(attachments.c.id.in_(file_refs)))

    async def bind(self, file_ref: str, *, conversation_id: str, workspace_id: str | None) -> Attachment | None:
        """The attachment, bound to the conversation unless a message had already bound it."""

        def claim(current: Attachment) -> Attachment | None:
            if current.conversation_id is not None:
                return None
            return current.model_copy(update={"conversation_id": conversation_id, "workspace_id": workspace_id})

        return await self._update_locked(attachments, Attachment, file_ref, claim)

    async def delete(self, file_ref: str) -> None:
        await self._delete(attachments, attachments.c.id == file_ref)

    async def list_unsent(self, *, tenant_id: str, user_id: str, created_before: datetime) -> list[Attachment]:
        statement = select(attachments).where(
            attachments.c.tenant_id == tenant_id,
            attachments.c.user_id == user_id,
            attachments.c.created_at < created_before,
            attachments.c.conversation_id.is_(None),
        )
        return await self._all(Attachment, statement)
