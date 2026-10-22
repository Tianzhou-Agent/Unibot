"""Uploading files for a chat message, storing them in the file tree when the message is sent, and reading them.

See ``attachments.py`` for an attachment's life. Uploads need the document NAS storage: without it there is no file
tree to store them in, and uploading is refused.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import TYPE_CHECKING, Any

from tianzhou_agent_platform.aina.document.file_types import file_kind
from tianzhou_agent_platform.conversations.attachments import (
    UNSENT_LIFETIME,
    Attachment,
    MessageAttachment,
    blob_key,
    clean_name,
    new_file_ref,
    text_blob_key,
)
from tianzhou_agent_platform.conversations.conversion import convert
from tianzhou_agent_platform.conversations.models import Conversation
from tianzhou_agent_platform.conversations.repository import ConversationRepository
from tianzhou_agent_platform.core.base import utc_now
from tianzhou_agent_platform.core.errors import PlatformError, conflict, not_found

if TYPE_CHECKING:
    from tianzhou_agent_platform.aina.document.service import DocumentService
    from tianzhou_agent_platform.aina.package.files import FileBlobs


# How much of a file's text its preview shows.
PREVIEW_CHARS = 1_500


class AttachmentService:
    def __init__(self, repository: ConversationRepository, blobs: FileBlobs, documents: DocumentService | None) -> None:
        self.repository = repository
        self.blobs = blobs
        self.documents = documents

    async def upload(self, name: str | None, payload: bytes, *, tenant_id: str, user_id: str) -> MessageAttachment:
        """Keep a file the user picked until a message names it; refused unless Superbot can read it."""
        self._documents()
        file_name = clean_name(name)
        converted = await asyncio.to_thread(convert, file_name, payload)
        await self._drop_unsent(tenant_id=tenant_id, user_id=user_id)
        attachment = Attachment(
            id=new_file_ref(),
            tenant_id=tenant_id,
            user_id=user_id,
            name=file_name,
            content_type=converted.content_type,
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
        )
        # The bytes first: a message can name the file only once they are stored.
        await self.blobs.write(blob_key(attachment), payload)
        await self.blobs.write(text_blob_key(attachment), converted.text.encode("utf-8"))
        return (await self.repository.put_chat_attachment(attachment)).view()

    async def save_file(
        self, name: str | None, payload: bytes, *, tenant_id: str, user_id: str, workspace_id: str | None
    ) -> str:
        """Store a file an app made for the user in ``Uploads/`` of their file tree (the workspace's, when given);
        returns its name there. Refused unless Superbot can read it, like an upload."""
        documents = self._documents()
        file_name = clean_name(name)
        converted = await asyncio.to_thread(convert, file_name, payload)
        return await documents.add_upload(
            file_name,
            payload if converted.binary else converted.text.encode("utf-8"),
            converted.text if converted.binary else None,
            **await self._scope(workspace_id, user_id=user_id, tenant_id=tenant_id),
        )

    async def owned(self, file_ref: str, *, tenant_id: str, user_id: str) -> Attachment:
        """The user's own upload, sent or not; not found for anyone else's."""
        attachment = await self.repository.get_chat_attachment(file_ref)
        if attachment is None or (attachment.tenant_id, attachment.user_id) != (tenant_id, user_id):
            raise not_found("Attachment", file_ref)
        return attachment

    async def discard(self, file_ref: str, *, tenant_id: str, user_id: str) -> None:
        """Delete an upload no message has named; a sent file is deleted as its document."""
        attachment = await self.owned(file_ref, tenant_id=tenant_id, user_id=user_id)
        if attachment.conversation_id is not None:
            raise conflict(f"Attachment {file_ref!r} was sent; delete its document {attachment.document!r} instead")
        await self._remove(attachment)

    async def bind(self, conversation: Conversation, file_refs: list[str]) -> list[MessageAttachment]:
        """The attachments a new message names, stored as documents in the conversation's file tree.

        Each must be the owner's own upload, unsent or already sent in this conversation (a retried message).
        """
        refs = list(dict.fromkeys(file_refs))
        found = {item.id: item for item in await self.repository.list_chat_attachments(refs)}
        owner = (conversation.tenant_id, conversation.user_id)
        for ref in refs:
            attachment = found.get(ref)
            if (
                attachment is None
                or (attachment.tenant_id, attachment.user_id) != owner
                or attachment.conversation_id not in {None, conversation.id}
            ):
                raise _not_attachable(ref)
        views = []
        for ref in refs:
            attachment = await self.repository.bind_chat_attachment(
                ref, conversation_id=conversation.id, workspace_id=conversation.workspace_id
            )
            if attachment is None or attachment.conversation_id != conversation.id:
                raise _not_attachable(ref)  # another message named it first
            if attachment.document is None:
                attachment = await self._store_document(attachment)
            views.append(attachment.view())
        return views

    async def texts(self, conversation: Conversation, attachments: list[MessageAttachment]) -> dict[str, str]:
        """The documents the message's files became, as they are now (file_ref -> text); a deleted one is left out."""
        documents = self._documents()
        scope = await self._scope(
            conversation.workspace_id, user_id=conversation.user_id, tenant_id=conversation.tenant_id
        )
        existing = {document.name for document in await documents.list_documents(**scope)}
        return {
            item.file_ref: (await documents.get_document(item.document, **scope)).content
            for item in attachments
            if item.document in existing
        }

    async def original(self, attachment: Attachment) -> bytes:
        """The file itself; not found once the user deleted it from the file tree."""
        if attachment.document is None:
            return await self.blobs.read(blob_key(attachment))
        scope = await self._scope(attachment.workspace_id, user_id=attachment.user_id, tenant_id=attachment.tenant_id)
        return await self._documents().read_file(attachment.document, **scope)

    async def preview(self, attachment: Attachment) -> tuple[str, bool]:
        """The start of the file's text (a PDF's, DOCX's or XLSX's extracted text) and whether there is more."""
        if attachment.document is None:
            text = (await self.blobs.read(text_blob_key(attachment))).decode("utf-8")
        else:
            scope = await self._scope(
                attachment.workspace_id, user_id=attachment.user_id, tenant_id=attachment.tenant_id
            )
            text = (await self._documents().get_document(attachment.document, **scope)).content
        return text[:PREVIEW_CHARS], len(text) > PREVIEW_CHARS

    async def _store_document(self, attachment: Attachment) -> Attachment:
        text = await self.blobs.read(text_blob_key(attachment))
        binary = file_kind(attachment.name) == "binary"
        document = await self._documents().add_upload(
            attachment.name,
            await self.blobs.read(blob_key(attachment)) if binary else text,
            text.decode("utf-8") if binary else None,
            **await self._scope(attachment.workspace_id, user_id=attachment.user_id, tenant_id=attachment.tenant_id),
        )
        stored = await self.repository.put_chat_attachment(attachment.model_copy(update={"document": document}))
        await self._delete_blobs(attachment)
        return stored

    async def _scope(self, workspace_id: str | None, *, user_id: str, tenant_id: str) -> dict[str, Any]:
        """Whose file tree holds the documents: the workspace's, or the user's own."""
        storage_key = None
        if workspace_id is not None:
            workspace = await self.repository.require_workspace_actor(
                workspace_id, user_id=user_id, tenant_id=tenant_id
            )
            storage_key = workspace.storage_key
        return {"user_id": user_id, "tenant_id": tenant_id, "workspace_storage_key": storage_key}

    async def _drop_unsent(self, *, tenant_id: str, user_id: str) -> None:
        """Delete the user's uploads that no message named in time."""
        stale = await self.repository.list_unsent_chat_attachments(
            tenant_id=tenant_id, user_id=user_id, created_before=utc_now() - UNSENT_LIFETIME
        )
        for attachment in stale:
            await self._remove(attachment)

    async def _remove(self, attachment: Attachment) -> None:
        await self._delete_blobs(attachment)
        await self.repository.delete_chat_attachment(attachment.id)

    async def _delete_blobs(self, attachment: Attachment) -> None:
        await self.blobs.delete(blob_key(attachment))
        await self.blobs.delete(text_blob_key(attachment))

    def _documents(self) -> DocumentService:
        if self.documents is None:
            raise PlatformError(
                "DEPENDENCY_FAILED",
                "Attachments need the document NAS storage, which is unavailable",
                status_code=503,
                source="attachments",
            )
        return self.documents


def _not_attachable(ref: str) -> PlatformError:
    return PlatformError(
        "INVALID_REQUEST",
        f"Attachment {ref!r} is not an upload of this conversation's owner that a message can name",
        status_code=422,
        source="attachments",
    )
