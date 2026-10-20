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

MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
MAX_ATTACHMENTS_PER_MESSAGE = 10
MAX_NAME_CHARS = 255
REF_PREFIX = "file_"
DEFAULT_TYPE = "application/octet-stream"
# An upload no message named in this long is deleted.
UNSENT_LIFETIME = timedelta(hours=24)
# Kept on the native user message (checkpointed working memory) so later model calls still see the refs.
ATTACHMENTS_KEY = "unibot_attachments"
# How much of the files' text the model reads inside the message that attached them: at most this much of one file,
# and of all of them at most this much or a quarter of the context window (a character counted as a token).
INLINE_FILE_CHARS = 20_000
INLINE_MESSAGE_CHARS = 60_000


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


def attachments_block(attachments: list[dict[str, Any]], contents: dict[str, str] | None = None) -> str:
    """What the model reads about a message's attachments: their documents, and refs for tools that take a file.

    ``contents`` (file_ref -> text, see ``inline_contents``) are the files' text, given for the turn's own message.
    """
    lines = [
        f"- {item['name']} ({item['content_type']}, {_size(int(item['size_bytes']))})"
        + (f" document: {item['document']}" if item.get("document") else "")
        + f" file_ref: {item['file_ref']}"
        for item in attachments
    ]
    block = (
        "<attachments>\nThe user attached these files. Each was saved as a document in the user's files: read it "
        "with the document tools by its document path. To give a tool that takes a file the file as uploaded, pass "
        "its file_ref as the tool's file argument.\n" + "\n".join(lines) + "\n</attachments>"
    )
    shown = [
        f'<attachment document="{item["document"]}">\n{contents[item["file_ref"]]}\n</attachment>'
        for item in attachments
        if item.get("document") and item["file_ref"] in (contents or {})
    ]
    return "\n\n".join([block, *shown])


def inline_budget(context_window_tokens: int) -> int:
    """How many characters of its files' text a message may carry for a model with this context window."""
    return min(INLINE_MESSAGE_CHARS, context_window_tokens // 4)


def inline_contents(attachments: list[MessageAttachment], texts: dict[str, str], budget: int) -> dict[str, str]:
    """The files' document ``texts`` (file_ref -> text) as the model reads them in the message, clipped to ``budget``.

    A clipped file says which lines were left out and how to read them.
    """
    contents = {}
    for attachment in attachments:
        text = texts.get(attachment.file_ref)
        if text is None or attachment.document is None:
            continue
        shown = _clip(text, min(INLINE_FILE_CHARS, budget), attachment.document)
        budget = max(0, budget - len(shown))
        contents[attachment.file_ref] = shown
    return contents


def _clip(text: str, limit: int, document: str) -> str:
    if len(text) <= limit:
        return text
    lines = text.splitlines(keepends=True)
    kept: list[str] = []
    size = 0
    for line in lines:
        if size + len(line) > limit:
            break
        kept.append(line)
        size += len(line)
    if not kept:
        return (
            f"[Not shown here, as the message has no room left for it: {len(lines)} lines. Read {document} with the "
            "document read tool, a range of lines at a time.]"
        )
    return (
        "".join(kept).rstrip("\n")
        + f"\n[Only lines 1-{len(kept)} of {len(lines)} are shown. Read the rest of {document} with the document "
        f"read tool from offset {len(kept) + 1}.]"
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
