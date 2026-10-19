"""Files the user attaches to a chat message, and how an AINA tool may read them (Host API ``files/{file_ref}``).

An attachment belongs to one conversation and its owner. The model sees each attachment's ``file_ref``; a tool reads
a file only when its own call's arguments carry that ref, which the host records in the call's context token
(claim ``files``). The bytes live in the same blob store as AINA private files.
"""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Any
from urllib.parse import quote

from pydantic import Field

from tianzhou_agent_platform.core.base import StrictModel, utc_now
from tianzhou_agent_platform.core.errors import PlatformError

MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
MAX_ATTACHMENTS_PER_MESSAGE = 10
MAX_NAME_CHARS = 255
REF_PREFIX = "file_"
DEFAULT_TYPE = "application/octet-stream"
# Kept on the native user message (checkpointed working memory) so later model calls still see the refs.
ATTACHMENTS_KEY = "unibot_attachments"


class MessageAttachment(StrictModel):
    """An attachment as a message shows it."""

    file_ref: str
    name: str
    content_type: str
    size_bytes: int


class Attachment(StrictModel):
    id: str  # the file_ref
    tenant_id: str
    user_id: str
    conversation_id: str
    name: str = Field(max_length=MAX_NAME_CHARS)
    content_type: str = DEFAULT_TYPE
    size_bytes: int = Field(ge=0)
    sha256: str
    created_at: datetime = Field(default_factory=utc_now)

    def view(self) -> MessageAttachment:
        return MessageAttachment(
            file_ref=self.id, name=self.name, content_type=self.content_type, size_bytes=self.size_bytes
        )


def new_file_ref() -> str:
    return f"{REF_PREFIX}{secrets.token_hex(16)}"


def blob_key(attachment: Attachment) -> str:
    return f"chat-attachments/{attachment.conversation_id}/{attachment.id}"


def clean_name(name: str | None) -> str:
    """The file name without directories or control characters."""
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    base = "".join(character for character in base if ord(character) >= 32 and ord(character) != 127).strip()
    return base[:MAX_NAME_CHARS] or "file"


def content_disposition(name: str) -> str:
    """Always a download: an attachment is never rendered on Unibot's origin."""
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


def attachments_block(attachments: list[dict[str, Any]]) -> str:
    """What the model reads about a message's attachments: it passes a ``file_ref`` to a tool that takes a file."""
    lines = [
        f"- {item['name']} ({item['content_type']}, {_size(int(item['size_bytes']))}) file_ref: {item['file_ref']}"
        for item in attachments
    ]
    return (
        "<attachments>\nThe user attached these files. To work on one with a tool, pass its file_ref as the tool's "
        "file argument.\n" + "\n".join(lines) + "\n</attachments>"
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


def _size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size / (1024 * 1024):.1f} MB"
