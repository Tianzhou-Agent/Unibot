"""Metadata of files attached to chat messages, read and written in SQL (the bytes live in a blob store)."""

from __future__ import annotations

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
