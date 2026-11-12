"""What the model reads about the files a user message attached."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

# Kept on the native user message (checkpointed working memory) so later model calls still see the refs.
ATTACHMENTS_KEY = "unibot_attachments"
# How much of the files' text the model reads inside the message that attached them: at most this much of one file,
# and of all of them at most this much or a quarter of the context window (a character counted as a token).
INLINE_FILE_CHARS = 20_000
INLINE_MESSAGE_CHARS = 60_000


class AttachedFile(Protocol):
    @property
    def file_ref(self) -> str: ...

    @property
    def document(self) -> str | None: ...

    def model_dump(self) -> dict[str, Any]:
        """What the message keeps about the file (``attachments_block`` reads it)."""
        ...


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


def inline_contents(attachments: Sequence[AttachedFile], texts: dict[str, str], budget: int) -> dict[str, str]:
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


def _size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size / (1024 * 1024):.1f} MB"
