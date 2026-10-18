"""Plugin-private files of an AINA installation (Host API ``storage/files``, scope ``storage``, design §8).

Metadata (path, size, type, checksum) lives in the repository, so listing and quotas need no file system walk; the
bytes live in a blob store (``store/aina_file_blobs.py``): the NAS when storage is configured, memory otherwise. Files are private to one
installation and are removed with it unless the user keeps the AINA's data.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Protocol

from pydantic import Field

from tianzhou_agent_platform.core.base import StrictModel, utc_now
from tianzhou_agent_platform.core.errors import PlatformError

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024
MAX_FILES = 1000
MAX_PATH_CHARS = 512
DEFAULT_TYPE = "application/octet-stream"


class AinaFile(StrictModel):
    id: str  # sha256 of (installation_key, path)
    installation_key: str
    aina_id: str
    path: str
    size_bytes: int = Field(ge=0)
    content_type: str = DEFAULT_TYPE
    sha256: str
    updated_at: datetime = Field(default_factory=utc_now)


class FileView(StrictModel):
    """A file as the Host API describes it."""

    path: str
    size_bytes: int
    content_type: str
    sha256: str
    updated_at: datetime


class FileList(StrictModel):
    items: list[FileView]


def file_view(file: AinaFile) -> FileView:
    return FileView(
        path=file.path,
        size_bytes=file.size_bytes,
        content_type=file.content_type,
        sha256=file.sha256,
        updated_at=file.updated_at,
    )


def file_record_id(installation_key: str, path: str) -> str:
    return hashlib.sha256(f"{installation_key}\x1f{path}".encode()).hexdigest()


def blob_key(file: AinaFile) -> str:
    return f"aina-files/{file.installation_key}/{file.id}"


def check_path(path: str) -> str:
    """A relative, slash-separated path without empty, ``.`` or ``..`` segments or control characters."""
    parts = path.split("/")
    if (
        not path
        or len(path) > MAX_PATH_CHARS
        or path.startswith("/")
        or "\\" in path
        or any(part in {"", ".", ".."} for part in parts)
        or any(ord(character) < 32 or ord(character) == 127 for character in path)
    ):
        raise PlatformError(
            "INVALID_REQUEST",
            f"File paths are relative, use '/' between non-empty segments without '.' or '..', and have at most "
            f"{MAX_PATH_CHARS} characters",
            status_code=422,
            source="aina_host",
        )
    return path


def check_quota(*, used_bytes: int, used_files: int, replaced: AinaFile | None, size: int) -> None:
    if size > MAX_FILE_BYTES:
        raise _too_large(f"A file may have at most {MAX_FILE_BYTES // (1024 * 1024)} MiB")
    if replaced is None and used_files >= MAX_FILES:
        raise _too_large(f"An installation may keep at most {MAX_FILES} files")
    if used_bytes - (replaced.size_bytes if replaced else 0) + size > MAX_TOTAL_BYTES:
        raise _too_large(f"An installation may keep at most {MAX_TOTAL_BYTES // (1024 * 1024)} MiB of files")


def _too_large(message: str) -> PlatformError:
    return PlatformError("QUOTA_EXCEEDED", message, status_code=413, source="aina_host")


class FileBlobs(Protocol):
    async def write(self, key: str, payload: bytes) -> None: ...

    async def read(self, key: str) -> bytes: ...

    async def delete(self, key: str) -> None: ...
