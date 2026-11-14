"""Files the user attaches to a chat message, and how an AINA tool may read them (Host API ``files/{file_ref}``).

A file is uploaded when the user picks it and waits, unsent, in the blob store of AINA private files. The message
that names it binds it to its conversation and stores it as a document in ``Uploads/`` of the conversation's file
tree, where the user sees and deletes it and the model reads it by path. A tool reads the file as uploaded only when
its own call's arguments carry the ``file_ref``, which the host records in the call's context token (claim
``files``).
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote

from pydantic import Field

from tianzhou_agent_platform.core.base import StrictModel, utc_now
from tianzhou_agent_platform.core.errors import PlatformError
# How the model reads attachments is the agent core's; re-exported for the modules that import it from here.
from unibot_agent.attachments import ATTACHMENTS_KEY as ATTACHMENTS_KEY
from unibot_agent.attachments import attachments_block as attachments_block
from unibot_agent.attachments import inline_budget as inline_budget
from unibot_agent.attachments import inline_contents as inline_contents

MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
MAX_ATTACHMENTS_PER_MESSAGE = 10
MAX_NAME_CHARS = 255
REF_PREFIX = "file_"
DEFAULT_TYPE = "application/octet-stream"
# An upload no message named in this long is deleted.
UNSENT_LIFETIME = timedelta(hours=24)


class MessageAttachment(StrictModel):
    """An attachment as a message shows it."""

    file_ref: str
    name: str
    content_type: str
    size_bytes: int
    # The document the file became, in the conversation's file tree; unset while the file is unsent.
    document: str | None = Field(default=None, exclude_if=lambda value: value is None)


class AttachmentPreview(StrictModel):
    """The start of a file's text, as a chip's hover card shows it."""

    text: str
    truncated: bool


class Attachment(StrictModel):
    id: str  # the file_ref
    tenant_id: str
    user_id: str
    conversation_id: str | None = None  # unset until a message names the file
    workspace_id: str | None = None  # whose file tree holds the document; unset for the user's own
    document: str | None = None
    name: str = Field(max_length=MAX_NAME_CHARS)
    content_type: str = DEFAULT_TYPE
    size_bytes: int = Field(ge=0)
    sha256: str
    created_at: datetime = Field(default_factory=utc_now)

    def view(self) -> MessageAttachment:
        return MessageAttachment(
            file_ref=self.id,
            name=self.name,
            content_type=self.content_type,
            size_bytes=self.size_bytes,
            document=self.document,
        )


def new_file_ref() -> str:
    return f"{REF_PREFIX}{secrets.token_hex(16)}"


def blob_key(attachment: Attachment) -> str:
    """The unsent file as uploaded."""
    return f"chat-attachments/{attachment.id}"


def text_blob_key(attachment: Attachment) -> str:
    """The Markdown the unsent file becomes as a document."""
    return f"chat-attachments/{attachment.id}.md"


def clean_name(name: str | None) -> str:
    """The file name without directories or control characters."""
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    base = "".join(character for character in base if ord(character) >= 32 and ord(character) != 127).strip()
    return base[:MAX_NAME_CHARS] or "file"


def content_disposition(name: str) -> str:
    """Always a download: an attachment is never rendered on Superbot's origin."""
    fallback = "".join(
        character if character.isascii() and character.isalnum() or character in "._-" else "_" for character in name
    )
    return f"attachment; filename=\"{fallback or 'file'}\"; filename*=UTF-8''{quote(name, safe='')}"


def too_large() -> PlatformError:
    return PlatformError(
        "QUOTA_EXCEEDED",
        f"An attachment may have at most {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MiB",
        status_code=413,
        source="attachments",
    )


def passed_refs(arguments: Any) -> set[str]:
    """Every string in a tool call's arguments that looks like a ``file_ref``."""
    if isinstance(arguments, str):
        return {arguments} if arguments.startswith(REF_PREFIX) else set()
    if isinstance(arguments, dict):
        return set().union(*(passed_refs(value) for value in arguments.values()))
    if isinstance(arguments, list):
        return set().union(*(passed_refs(value) for value in arguments))
    return set()
