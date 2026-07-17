from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import quote

from tianzhou_agent_platform.aina.document.models import (
    DocumentHeading,
    DocumentOutline,
    DocumentRecord,
    DocumentSection,
    DocumentSectionUpdateResult,
    DocumentSummary,
)
from tianzhou_agent_platform.store.errors import StoragePolicyViolationError, StorageValidationError
from tianzhou_agent_platform.store.models import FileMetadata, StoragePath
from tianzhou_agent_platform.store.nas.filesystem import NasStore

MAX_DOCUMENT_BYTES = 1024 * 1024
_WINDOWS_RESERVED_NAMES = {
    "aux",
    "con",
    "nul",
    "prn",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}
_INVALID_FILENAME_CHARS = set('<>:"/\\|?*')
_HEADING_PATTERN = re.compile(r"^[ \t]{0,3}(#{1,6})[ \t]+(.+?)[ \t]*(?:\r?\n)?$")
_FENCE_PATTERN = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")


@dataclass(frozen=True)
class _MarkdownSection:
    heading: str
    level: int
    occurrence: int
    start_index: int
    end_index: int


class DocumentService:
    def __init__(self, nas: NasStore) -> None:
        self._nas = nas

    async def list_documents(self, *, user_id: str, tenant_id: str) -> list[DocumentSummary]:
        prefix = self._actor_prefix(user_id=user_id, tenant_id=tenant_id)
        metadata = await self._nas.list_files(StoragePath(relative_path=prefix))
        items = [
            self._summary(item)
            for item in metadata
            if PurePosixPath(item.path.relative_path).parent.as_posix() == prefix
            and item.path.relative_path.casefold().endswith(".md")
        ]
        return sorted(items, key=lambda item: item.name.casefold())

    async def get_document(self, name: str, *, user_id: str, tenant_id: str) -> DocumentRecord:
        normalized = normalize_document_name(name)
        path = self._path(normalized, user_id=user_id, tenant_id=tenant_id)
        content = await self._nas.read(path)
        try:
            decoded = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise StorageValidationError("Document content is not valid UTF-8") from exc
        metadata = await self._nas.metadata(path)
        summary = self._summary(metadata)
        return DocumentRecord(**summary.model_dump(), content=decoded)

    async def get_outline(self, name: str, *, user_id: str, tenant_id: str) -> DocumentOutline:
        document = await self.get_document(name, user_id=user_id, tenant_id=tenant_id)
        sections = _markdown_sections(document.content)
        return DocumentOutline(
            name=document.name,
            size_bytes=document.size_bytes,
            revision=_document_revision(document.content),
            headings=[
                DocumentHeading(
                    index=index,
                    heading=section.heading,
                    level=section.level,
                    occurrence=section.occurrence,
                    line_start=section.start_index + 1,
                    line_end=section.end_index,
                )
                for index, section in enumerate(sections, start=1)
            ],
        )

    async def get_section(
        self,
        name: str,
        heading: str,
        occurrence: int,
        *,
        user_id: str,
        tenant_id: str,
    ) -> DocumentSection:
        document = await self.get_document(name, user_id=user_id, tenant_id=tenant_id)
        section = _find_section(_markdown_sections(document.content), heading, occurrence)
        lines = document.content.splitlines(keepends=True)
        return DocumentSection(
            name=document.name,
            heading=section.heading,
            level=section.level,
            occurrence=section.occurrence,
            revision=_document_revision(document.content),
            content="".join(lines[section.start_index : section.end_index]),
        )

    async def create_document(
        self,
        name: str,
        content: str,
        *,
        user_id: str,
        tenant_id: str,
    ) -> DocumentRecord:
        normalized = normalize_document_name(name)
        encoded = _encode_content(content)
        await self._nas.write(
            self._path(normalized, user_id=user_id, tenant_id=tenant_id),
            encoded,
            overwrite=False,
        )
        return await self.get_document(normalized, user_id=user_id, tenant_id=tenant_id)

    async def update_document(
        self,
        name: str,
        content: str,
        *,
        user_id: str,
        tenant_id: str,
    ) -> DocumentRecord:
        normalized = normalize_document_name(name)
        path = self._path(normalized, user_id=user_id, tenant_id=tenant_id)
        if not await self._nas.exists(path):
            await self._nas.read(path)
        await self._nas.write(path, _encode_content(content), overwrite=True)
        return await self.get_document(normalized, user_id=user_id, tenant_id=tenant_id)

    async def update_section(
        self,
        name: str,
        heading: str,
        occurrence: int,
        section_content: str,
        expected_revision: str,
        *,
        user_id: str,
        tenant_id: str,
    ) -> DocumentSectionUpdateResult:
        current = await self.get_document(name, user_id=user_id, tenant_id=tenant_id)
        if _document_revision(current.content) != expected_revision:
            raise StorageValidationError(
                "Document revision changed. Call document.read_section again before retrying the update."
            )

        target = _find_section(_markdown_sections(current.content), heading, occurrence)
        replacement, replacement_heading = _validate_section_replacement(
            section_content,
            target_level=target.level,
            newline=_preferred_newline(current.content),
        )
        lines = current.content.splitlines(keepends=True)
        suffix = "".join(lines[target.end_index :])
        if suffix and not replacement.endswith(("\n", "\r")):
            replacement += _preferred_newline(current.content)
        updated_content = "".join(lines[: target.start_index]) + replacement + suffix
        updated = await self.update_document(
            current.name,
            updated_content,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        return DocumentSectionUpdateResult(
            name=updated.name,
            previous_heading=target.heading,
            heading=replacement_heading,
            level=target.level,
            occurrence=target.occurrence,
            revision=_document_revision(updated.content),
            size_bytes=updated.size_bytes,
            modified_at=updated.modified_at,
        )

    async def append_document(
        self,
        name: str,
        content: str,
        *,
        user_id: str,
        tenant_id: str,
    ) -> DocumentRecord:
        current = await self.get_document(name, user_id=user_id, tenant_id=tenant_id)
        return await self.update_document(
            current.name,
            current.content + content,
            user_id=user_id,
            tenant_id=tenant_id,
        )

    async def rename_document(
        self,
        name: str,
        new_name: str,
        *,
        user_id: str,
        tenant_id: str,
    ) -> DocumentRecord:
        current = await self.get_document(name, user_id=user_id, tenant_id=tenant_id)
        normalized_new_name = normalize_document_name(new_name)
        if current.name == normalized_new_name:
            return current
        destination = self._path(normalized_new_name, user_id=user_id, tenant_id=tenant_id)
        await self._nas.write(destination, _encode_content(current.content), overwrite=False)
        await self._nas.delete(self._path(current.name, user_id=user_id, tenant_id=tenant_id))
        return await self.get_document(normalized_new_name, user_id=user_id, tenant_id=tenant_id)

    async def delete_document(self, name: str, *, user_id: str, tenant_id: str) -> bool:
        normalized = normalize_document_name(name)
        result = await self._nas.delete(self._path(normalized, user_id=user_id, tenant_id=tenant_id))
        return result.deleted

    @staticmethod
    def _actor_prefix(*, user_id: str, tenant_id: str) -> str:
        return f"documents/t-{_actor_segment(tenant_id)}/u-{_actor_segment(user_id)}"

    def _path(self, name: str, *, user_id: str, tenant_id: str) -> StoragePath:
        return StoragePath(relative_path=f"{self._actor_prefix(user_id=user_id, tenant_id=tenant_id)}/{name}")

    @staticmethod
    def _summary(metadata: FileMetadata) -> DocumentSummary:
        return DocumentSummary(
            name=PurePosixPath(metadata.path.relative_path).name,
            size_bytes=metadata.size_bytes,
            modified_at=metadata.modified_at,
        )


def normalize_document_name(value: str) -> str:
    name = value.strip()
    if not name:
        raise StorageValidationError("Document name must not be empty")
    if any(character in _INVALID_FILENAME_CHARS or ord(character) < 32 for character in name):
        raise StorageValidationError("Document name contains unsupported characters")
    if name.endswith((".", " ")):
        raise StorageValidationError("Document name must not end with a dot or space")
    if not name.casefold().endswith(".md"):
        if "." in name:
            raise StorageValidationError("Only Markdown (.md) documents are supported")
        name = f"{name}.md"
    stem = name[:-3]
    if not stem or stem.casefold() in _WINDOWS_RESERVED_NAMES:
        raise StorageValidationError("Document name is reserved by the filesystem")
    if len(name) > 160:
        raise StorageValidationError("Document name exceeds 160 characters")
    return name


def _actor_segment(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise StorageValidationError("Document actor identifiers must not be empty")
    return quote(normalized, safe="-_")


def _encode_content(content: str) -> bytes:
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_DOCUMENT_BYTES:
        raise StoragePolicyViolationError("Markdown document exceeds the 1 MiB size limit")
    return encoded


def _document_revision(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _markdown_sections(content: str) -> list[_MarkdownSection]:
    lines = content.splitlines(keepends=True)
    headings: list[tuple[str, int, int]] = []
    fence_character: str | None = None
    fence_length = 0
    for line_index, line in enumerate(lines):
        fence = _FENCE_PATTERN.match(line)
        if fence:
            marker = fence.group(1)
            if fence_character is None:
                fence_character = marker[0]
                fence_length = len(marker)
            elif marker[0] == fence_character and len(marker) >= fence_length:
                fence_character = None
                fence_length = 0
            continue
        if fence_character is not None:
            continue
        match = _HEADING_PATTERN.match(line)
        if not match:
            continue
        heading = re.sub(r"[ \t]+#+[ \t]*$", "", match.group(2)).strip()
        if heading:
            headings.append((heading, len(match.group(1)), line_index))

    occurrences: dict[str, int] = {}
    sections: list[_MarkdownSection] = []
    for position, (heading, level, start_index) in enumerate(headings):
        occurrences[heading] = occurrences.get(heading, 0) + 1
        end_index = len(lines)
        for _, following_level, following_start in headings[position + 1 :]:
            if following_level <= level:
                end_index = following_start
                break
        sections.append(
            _MarkdownSection(
                heading=heading,
                level=level,
                occurrence=occurrences[heading],
                start_index=start_index,
                end_index=end_index,
            )
        )
    return sections


def _find_section(sections: list[_MarkdownSection], heading: str, occurrence: int) -> _MarkdownSection:
    normalized_heading = heading.strip()
    if not normalized_heading:
        raise StorageValidationError("Section heading must not be empty")
    if occurrence < 1:
        raise StorageValidationError("Section occurrence must be greater than zero")
    for section in sections:
        if section.heading == normalized_heading and section.occurrence == occurrence:
            return section
    raise StorageValidationError(
        f"Markdown section {normalized_heading!r} occurrence {occurrence} was not found. Call document.outline first."
    )


def _validate_section_replacement(section_content: str, *, target_level: int, newline: str) -> tuple[str, str]:
    sections = _markdown_sections(section_content)
    if not sections:
        raise StorageValidationError("Section content must include its Markdown heading")
    first = sections[0]
    lines = section_content.splitlines(keepends=True)
    if "".join(lines[: first.start_index]).strip():
        raise StorageValidationError("Section content must start with its Markdown heading")
    if first.level != target_level:
        raise StorageValidationError(f"Replacement section heading must remain at level {target_level}")
    if any(section.level <= target_level for section in sections[1:]):
        raise StorageValidationError("Section content must not include another peer or parent section")
    normalized = section_content.replace("\r\n", "\n").replace("\r", "\n")
    if newline != "\n":
        normalized = normalized.replace("\n", newline)
    return normalized, first.heading


def _preferred_newline(content: str) -> str:
    return "\r\n" if "\r\n" in content else "\n"
