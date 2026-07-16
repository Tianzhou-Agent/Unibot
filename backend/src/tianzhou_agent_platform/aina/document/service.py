from __future__ import annotations

from pathlib import PurePosixPath
from urllib.parse import quote

from tianzhou_agent_platform.aina.document.models import DocumentRecord, DocumentSummary
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
