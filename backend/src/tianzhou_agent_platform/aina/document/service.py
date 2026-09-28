from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import quote

from tianzhou_agent_platform.aina.document.models import (
    DocumentFolder,
    DocumentHeading,
    DocumentOutline,
    DocumentRecord,
    DocumentSearchResult,
    DocumentSection,
    DocumentSectionUpdateResult,
    DocumentSectionsUpdateResult,
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

    async def list_documents(
        self,
        *,
        user_id: str,
        tenant_id: str,
        workspace_storage_key: str | None = None,
    ) -> list[DocumentSummary]:
        prefix = self._actor_prefix(
            user_id=user_id,
            tenant_id=tenant_id,
            workspace_storage_key=workspace_storage_key,
        )
        metadata = await self._nas.list_files(StoragePath(relative_path=prefix))
        items = [
            self._summary(item, prefix)
            for item in metadata
            if item.path.relative_path.casefold().endswith(".md")
        ]
        return sorted(items, key=lambda item: item.name.casefold())

    async def search_documents(
        self,
        query: str,
        *,
        user_id: str,
        tenant_id: str,
        workspace_storage_key: str | None = None,
        limit: int = 20,
    ) -> list[DocumentSearchResult]:
        normalized_query = query.strip()
        if not normalized_query:
            raise StorageValidationError("Document search query cannot be empty")
        pattern = re.compile(re.escape(normalized_query), re.IGNORECASE)
        matches: list[DocumentSearchResult] = []
        for summary in await self.list_documents(
            user_id=user_id,
            tenant_id=tenant_id,
            workspace_storage_key=workspace_storage_key,
        ):
            document = await self.get_document(
                summary.name,
                user_id=user_id,
                tenant_id=tenant_id,
                workspace_storage_key=workspace_storage_key,
            )
            name_match = pattern.search(summary.name) is not None
            content_match = pattern.search(document.content)
            if not name_match and content_match is None:
                continue
            matched_in = [
                source
                for source, matched in (("name", name_match), ("content", content_match is not None))
                if matched
            ]
            matches.append(
                DocumentSearchResult(
                    **summary.model_dump(),
                    matched_in=matched_in,
                    excerpt=_search_excerpt(document.content, content_match) if content_match else None,
                )
            )
        matches.sort(key=lambda item: ("name" not in item.matched_in, item.name.casefold()))
        return matches[:limit]

    async def list_folders(
        self,
        *,
        user_id: str,
        tenant_id: str,
        workspace_storage_key: str | None = None,
    ) -> list[DocumentFolder]:
        prefix = self._actor_prefix(
            user_id=user_id,
            tenant_id=tenant_id,
            workspace_storage_key=workspace_storage_key,
        )
        directories = await self._nas.list_directories(StoragePath(relative_path=prefix))
        return [
            DocumentFolder(
                path=directory.relative_path.removeprefix(f"{prefix}/"),
                name=PurePosixPath(directory.relative_path).name,
            )
            for directory in directories
            if directory.relative_path != prefix
        ]

    async def create_folder(
        self,
        path: str,
        *,
        user_id: str,
        tenant_id: str,
        workspace_storage_key: str | None = None,
    ) -> DocumentFolder:
        normalized = normalize_folder_path(path)
        await self._nas.create_directory(
            self._path(
                normalized,
                user_id=user_id,
                tenant_id=tenant_id,
                workspace_storage_key=workspace_storage_key,
            )
        )
        return DocumentFolder(path=normalized, name=PurePosixPath(normalized).name)

    async def rename_folder(
        self,
        path: str,
        new_path: str,
        *,
        user_id: str,
        tenant_id: str,
        workspace_storage_key: str | None = None,
    ) -> DocumentFolder:
        normalized = normalize_folder_path(path)
        normalized_new_path = normalize_folder_path(new_path)
        if normalized_new_path == normalized:
            return DocumentFolder(path=normalized, name=PurePosixPath(normalized).name)
        if normalized_new_path.startswith(f"{normalized}/"):
            raise StorageValidationError("A folder cannot be moved inside itself")
        await self._nas.move(
            self._path(
                normalized,
                user_id=user_id,
                tenant_id=tenant_id,
                workspace_storage_key=workspace_storage_key,
            ),
            self._path(
                normalized_new_path,
                user_id=user_id,
                tenant_id=tenant_id,
                workspace_storage_key=workspace_storage_key,
            ),
        )
        return DocumentFolder(path=normalized_new_path, name=PurePosixPath(normalized_new_path).name)

    async def delete_folder(
        self,
        path: str,
        *,
        user_id: str,
        tenant_id: str,
        workspace_storage_key: str | None = None,
    ) -> bool:
        normalized = normalize_folder_path(path)
        result = await self._nas.delete_directory(
            self._path(
                normalized,
                user_id=user_id,
                tenant_id=tenant_id,
                workspace_storage_key=workspace_storage_key,
            )
        )
        return result.deleted

    async def get_document(
        self,
        name: str,
        *,
        user_id: str,
        tenant_id: str,
        workspace_storage_key: str | None = None,
    ) -> DocumentRecord:
        normalized = normalize_document_name(name)
        path = self._path(
            normalized,
            user_id=user_id,
            tenant_id=tenant_id,
            workspace_storage_key=workspace_storage_key,
        )
        content = await self._nas.read(path)
        try:
            decoded = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise StorageValidationError("Document content is not valid UTF-8") from exc
        metadata = await self._nas.metadata(path)
        summary = self._summary(
            metadata,
            self._actor_prefix(
                user_id=user_id,
                tenant_id=tenant_id,
                workspace_storage_key=workspace_storage_key,
            ),
        )
        return DocumentRecord(**summary.model_dump(), content=decoded)

    async def get_outline(
        self,
        name: str,
        *,
        user_id: str,
        tenant_id: str,
        workspace_storage_key: str | None = None,
    ) -> DocumentOutline:
        document = await self.get_document(
            name,
            user_id=user_id,
            tenant_id=tenant_id,
            workspace_storage_key=workspace_storage_key,
        )
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
        workspace_storage_key: str | None = None,
    ) -> DocumentSection:
        document = await self.get_document(
            name,
            user_id=user_id,
            tenant_id=tenant_id,
            workspace_storage_key=workspace_storage_key,
        )
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
        workspace_storage_key: str | None = None,
    ) -> DocumentRecord:
        normalized = normalize_document_name(name)
        encoded = _encode_content(content)
        await self._nas.write(
            self._path(
                normalized,
                user_id=user_id,
                tenant_id=tenant_id,
                workspace_storage_key=workspace_storage_key,
            ),
            encoded,
            overwrite=False,
        )
        return await self.get_document(
            normalized,
            user_id=user_id,
            tenant_id=tenant_id,
            workspace_storage_key=workspace_storage_key,
        )

    async def update_document(
        self,
        name: str,
        content: str,
        *,
        user_id: str,
        tenant_id: str,
        workspace_storage_key: str | None = None,
    ) -> DocumentRecord:
        normalized = normalize_document_name(name)
        path = self._path(
            normalized,
            user_id=user_id,
            tenant_id=tenant_id,
            workspace_storage_key=workspace_storage_key,
        )
        if not await self._nas.exists(path):
            await self._nas.read(path)
        await self._nas.write(path, _encode_content(content), overwrite=True)
        return await self.get_document(
            normalized,
            user_id=user_id,
            tenant_id=tenant_id,
            workspace_storage_key=workspace_storage_key,
        )

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
        workspace_storage_key: str | None = None,
    ) -> DocumentSectionUpdateResult:
        current = await self.get_document(
            name,
            user_id=user_id,
            tenant_id=tenant_id,
            workspace_storage_key=workspace_storage_key,
        )
        if _document_revision(current.content) != expected_revision:
            raise StorageValidationError(
                "Document revision changed. Call document.read_section again before retrying the update."
            )

        sections = _markdown_sections(current.content)
        target = _find_section(sections, heading, occurrence)
        if _covers_whole_document(target, sections):
            raise StorageValidationError(
                "The document root cannot be updated. Select one of its child sections."
            )
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
            workspace_storage_key=workspace_storage_key,
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

    async def merge_sections(
        self,
        name: str,
        replacements: list[tuple[str, int, str]],
        expected_revision: str,
        *,
        user_id: str,
        tenant_id: str,
        workspace_storage_key: str | None = None,
    ) -> DocumentRecord:
        if not replacements:
            raise StorageValidationError("At least one document section is required")
        current = await self.get_document(
            name,
            user_id=user_id,
            tenant_id=tenant_id,
            workspace_storage_key=workspace_storage_key,
        )
        if _document_revision(current.content) != expected_revision:
            raise StorageValidationError("Document revision changed. Review the latest document before merging.")

        sections = _markdown_sections(current.content)
        resolved: list[tuple[_MarkdownSection, str]] = []
        seen: set[tuple[str, int]] = set()
        newline = _preferred_newline(current.content)
        for heading, occurrence, section_content in replacements:
            key = (heading.strip(), occurrence)
            if key in seen:
                raise StorageValidationError("A document section cannot be merged more than once")
            seen.add(key)
            target = _find_section(sections, heading, occurrence)
            if _covers_whole_document(target, sections):
                raise StorageValidationError(
                    "The document root cannot be merged. Select one of its child sections."
                )
            replacement, _ = _validate_section_replacement(
                section_content,
                target_level=target.level,
                newline=newline,
            )
            resolved.append((target, replacement))

        ordered = sorted(resolved, key=lambda item: item[0].start_index)
        for (previous, _), (following, _) in zip(ordered, ordered[1:]):
            if following.start_index < previous.end_index:
                raise StorageValidationError("Selected document sections must not overlap")

        lines = current.content.splitlines(keepends=True)
        offsets = [0]
        for line in lines:
            offsets.append(offsets[-1] + len(line))
        updated_content = current.content
        for target, replacement in sorted(resolved, key=lambda item: item[0].start_index, reverse=True):
            start = offsets[target.start_index]
            end = offsets[target.end_index]
            if end < len(current.content) and not replacement.endswith(("\n", "\r")):
                replacement += newline
            updated_content = updated_content[:start] + replacement + updated_content[end:]

        return await self.update_document(
            current.name,
            updated_content,
            user_id=user_id,
            tenant_id=tenant_id,
            workspace_storage_key=workspace_storage_key,
        )

    async def update_sections(
        self,
        name: str,
        content: str,
        expected_revision: str,
        *,
        user_id: str,
        tenant_id: str,
        workspace_storage_key: str | None = None,
    ) -> DocumentSectionsUpdateResult:
        current = await self.get_document(
            name,
            user_id=user_id,
            tenant_id=tenant_id,
            workspace_storage_key=workspace_storage_key,
        )
        if _document_revision(current.content) != expected_revision:
            raise StorageValidationError(
                "Document revision changed. Review the latest document before saving."
            )
        updated_sections = _changed_direct_section_headings(current.content, content)
        updated = await self.update_document(
            current.name,
            content,
            user_id=user_id,
            tenant_id=tenant_id,
            workspace_storage_key=workspace_storage_key,
        )
        return DocumentSectionsUpdateResult(
            name=updated.name,
            revision=_document_revision(updated.content),
            updated_sections=updated_sections,
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
        workspace_storage_key: str | None = None,
    ) -> DocumentRecord:
        current = await self.get_document(
            name,
            user_id=user_id,
            tenant_id=tenant_id,
            workspace_storage_key=workspace_storage_key,
        )
        return await self.update_document(
            current.name,
            current.content + content,
            user_id=user_id,
            tenant_id=tenant_id,
            workspace_storage_key=workspace_storage_key,
        )

    async def rename_document(
        self,
        name: str,
        new_name: str,
        *,
        user_id: str,
        tenant_id: str,
        workspace_storage_key: str | None = None,
    ) -> DocumentRecord:
        current = await self.get_document(
            name,
            user_id=user_id,
            tenant_id=tenant_id,
            workspace_storage_key=workspace_storage_key,
        )
        normalized_new_name = normalize_document_name(new_name)
        if current.name == normalized_new_name:
            return current
        await self._nas.move(
            self._path(
                current.name,
                user_id=user_id,
                tenant_id=tenant_id,
                workspace_storage_key=workspace_storage_key,
            ),
            self._path(
                normalized_new_name,
                user_id=user_id,
                tenant_id=tenant_id,
                workspace_storage_key=workspace_storage_key,
            ),
        )
        return await self.get_document(
            normalized_new_name,
            user_id=user_id,
            tenant_id=tenant_id,
            workspace_storage_key=workspace_storage_key,
        )

    async def delete_document(
        self,
        name: str,
        *,
        user_id: str,
        tenant_id: str,
        workspace_storage_key: str | None = None,
    ) -> bool:
        normalized = normalize_document_name(name)
        result = await self._nas.delete(
            self._path(
                normalized,
                user_id=user_id,
                tenant_id=tenant_id,
                workspace_storage_key=workspace_storage_key,
            )
        )
        return result.deleted

    @staticmethod
    def _actor_prefix(
        *,
        user_id: str,
        tenant_id: str,
        workspace_storage_key: str | None = None,
    ) -> str:
        if workspace_storage_key is not None:
            return f"workspaces/{_workspace_segment(workspace_storage_key)}/files"
        return f"documents/t-{_actor_segment(tenant_id)}/u-{_actor_segment(user_id)}"

    def _path(
        self,
        name: str,
        *,
        user_id: str,
        tenant_id: str,
        workspace_storage_key: str | None = None,
    ) -> StoragePath:
        prefix = self._actor_prefix(
            user_id=user_id,
            tenant_id=tenant_id,
            workspace_storage_key=workspace_storage_key,
        )
        return StoragePath(relative_path=f"{prefix}/{name}")

    @staticmethod
    def _summary(metadata: FileMetadata, actor_prefix: str) -> DocumentSummary:
        return DocumentSummary(
            name=metadata.path.relative_path.removeprefix(f"{actor_prefix}/"),
            size_bytes=metadata.size_bytes,
            modified_at=metadata.modified_at,
        )


def normalize_document_name(value: str) -> str:
    path = _normalize_relative_path(value, label="Document path")
    parts = list(PurePosixPath(path).parts)
    name = parts[-1]
    if not name.casefold().endswith(".md"):
        if "." in name:
            raise StorageValidationError("Only Markdown (.md) documents are supported")
        name = f"{name}.md"
    _validate_path_segment(name, label="Document name")
    stem = name[:-3]
    if not stem or stem.casefold() in _WINDOWS_RESERVED_NAMES:
        raise StorageValidationError("Document name is reserved by the filesystem")
    parts[-1] = name
    normalized = "/".join(parts)
    if len(normalized) > 512:
        raise StorageValidationError("Document path exceeds 512 characters")
    return normalized


def normalize_folder_path(value: str) -> str:
    path = _normalize_relative_path(value, label="Folder path")
    if len(path) > 512:
        raise StorageValidationError("Folder path exceeds 512 characters")
    return path


def _normalize_relative_path(value: str, *, label: str) -> str:
    path = value.replace("\\", "/").strip()
    if path.startswith("/"):
        raise StorageValidationError(f"{label} must be relative")
    path = path.rstrip("/")
    if not path:
        raise StorageValidationError(f"{label} must not be empty")
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise StorageValidationError(f"{label} contains an invalid segment")
    for part in parts:
        _validate_path_segment(part, label=label)
    return "/".join(parts)


def _validate_path_segment(name: str, *, label: str) -> None:
    if not name:
        raise StorageValidationError("Document name must not be empty")
    if any(character in _INVALID_FILENAME_CHARS or ord(character) < 32 for character in name):
        raise StorageValidationError(f"{label} contains unsupported characters")
    if name.endswith((".", " ")):
        raise StorageValidationError(f"{label} must not end with a dot or space")
    if name.casefold() in _WINDOWS_RESERVED_NAMES:
        raise StorageValidationError(f"{label} is reserved by the filesystem")
    if len(name) > 160:
        raise StorageValidationError(f"{label} segment exceeds 160 characters")


def _actor_segment(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise StorageValidationError("Document actor identifiers must not be empty")
    return quote(normalized, safe="-_")


def _workspace_segment(value: str) -> str:
    normalized = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", normalized):
        raise StorageValidationError("Workspace storage key is invalid")
    return normalized


def _encode_content(content: str) -> bytes:
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_DOCUMENT_BYTES:
        raise StoragePolicyViolationError("Markdown document exceeds the 1 MiB size limit")
    return encoded


def _search_excerpt(content: str, match: re.Match[str], *, context_chars: int = 100) -> str:
    start = max(0, match.start() - context_chars)
    end = min(len(content), match.end() + context_chars)
    excerpt = content[start:end].strip()
    return f"{'…' if start else ''}{excerpt}{'…' if end < len(content) else ''}"


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


def _covers_whole_document(target: _MarkdownSection, sections: list[_MarkdownSection]) -> bool:
    return len(sections) > 1 and all(
        target.start_index <= section.start_index and target.end_index >= section.end_index
        for section in sections
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


def _changed_direct_section_headings(original: str, updated: str) -> list[str]:
    original_blocks = _direct_section_blocks(original)
    updated_blocks = _direct_section_blocks(updated)
    if not updated_blocks:
        raise StorageValidationError("A Markdown document must contain at least one heading")
    if len(original_blocks) != len(updated_blocks):
        return [section.heading for section, _ in updated_blocks]
    return [
        updated_section.heading
        for (original_section, original_content), (updated_section, updated_content) in zip(
            original_blocks,
            updated_blocks,
        )
        if original_section.level != updated_section.level or original_content != updated_content
    ]


def _direct_section_blocks(content: str) -> list[tuple[_MarkdownSection, str]]:
    lines = content.splitlines(keepends=True)
    sections = _markdown_sections(content)
    blocks: list[tuple[_MarkdownSection, str]] = []
    for index, section in enumerate(sections):
        start_index = 0 if index == 0 else section.start_index
        end_index = (
            sections[index + 1].start_index if index + 1 < len(sections) else len(lines)
        )
        blocks.append((section, "".join(lines[start_index:end_index])))
    return blocks


def _preferred_newline(content: str) -> str:
    return "\r\n" if "\r\n" in content else "\n"
