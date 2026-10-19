"""Serving the UI files of AINA 2.0 packages: canvas, widgets and assets (design §9.1).

Packaged UI always runs sandboxed. The canvas pane frames it with ``sandbox`` but never ``allow-same-origin``, and
every file is also served with a ``Content-Security-Policy: sandbox`` header, so a file opened directly in a tab
runs with an opaque origin too and can never act as Unibot. ``connect-src 'none'`` stops it from calling any
network endpoint: all data goes through the UI bridge.
"""

from __future__ import annotations

import mimetypes
from collections import OrderedDict
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from tianzhou_agent_platform.aina.package.models import PackageRuntimeDefinition
from tianzhou_agent_platform.aina.project import read_project_archive
from tianzhou_agent_platform.aina.project_service import AinaProjectArtifactStore
from tianzhou_agent_platform.core.errors import PlatformError, not_found
from tianzhou_agent_platform.store.models import StoragePath

_CACHED_PACKAGES = 16
# Drafts from the Add app page, kept in memory for the canvas preview only (newest first out of mind).
_CACHED_DRAFTS = 32
_TEXT_TYPES = {".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css"}


def archive_path(aina_id: str, archive_sha256: str) -> StoragePath:
    return StoragePath(relative_path=f"aina-packages/{aina_id}/{archive_sha256}.aina.zip")


class PackageUiStore:
    """Keeps registered package archives and serves their UI files; extracted archives are cached per digest."""

    def __init__(self, artifacts: AinaProjectArtifactStore) -> None:
        self._artifacts = artifacts
        self._cache: OrderedDict[str, dict[str, bytes]] = OrderedDict()
        self._drafts: OrderedDict[str, tuple[dict[str, bytes], set[str]]] = OrderedDict()

    def save_draft(self, archive_sha256: str, files: dict[str, bytes], skill_paths: list[str]) -> None:
        """A checked but unregistered package, so its canvas can be previewed; kept in this node's memory."""
        self._drafts[archive_sha256] = (files, {f"{path.rstrip('/')}/" for path in skill_paths})
        self._drafts.move_to_end(archive_sha256)
        while len(self._drafts) > _CACHED_DRAFTS:
            self._drafts.popitem(last=False)

    def draft_file(self, archive_sha256: str, path: str) -> tuple[bytes, str]:
        files, skill_folders = self._drafts.get(archive_sha256) or ({}, set())
        content = files.get(path) if _is_ui_file(path, skill_folders) else None
        if content is None:
            raise not_found("AINA UI file", path)
        return content, _media_type(path)

    async def save(self, aina_id: str, archive_sha256: str, payload: bytes) -> None:
        await self._artifacts.write(archive_path(aina_id, archive_sha256), payload)

    async def file(self, aina_id: str, runtime: PackageRuntimeDefinition, path: str) -> tuple[bytes, str]:
        """A UI file of the package and its media type; skills and the manifest are never served."""
        if not is_ui_path(runtime, path):
            raise not_found("AINA UI file", path)
        files = await self._files(aina_id, runtime.archive_sha256)
        content = files.get(path)
        if content is None:
            raise not_found("AINA UI file", path)
        return content, _media_type(path)

    async def _files(self, aina_id: str, archive_sha256: str) -> dict[str, bytes]:
        files = self._cache.get(archive_sha256)
        if files is None:
            try:
                payload = await self._artifacts.read(archive_path(aina_id, archive_sha256))
            except Exception as exc:  # noqa: BLE001 - a missing archive only means the UI cannot be shown
                raise PlatformError(
                    "RESOURCE_NOT_FOUND",
                    "The AINA package files are not available; register the package again",
                    status_code=404,
                    source="aina_package",
                ) from exc
            files, _size = read_project_archive(payload)
            self._cache[archive_sha256] = files
            while len(self._cache) > _CACHED_PACKAGES:
                self._cache.popitem(last=False)
        self._cache.move_to_end(archive_sha256)
        return files


def is_ui_path(runtime: PackageRuntimeDefinition, path: str) -> bool:
    return _is_ui_file(path, {f"{skill.path.rstrip('/')}/" for skill in runtime.package.skills})


def _is_ui_file(path: str, skill_folders: set[str]) -> bool:
    parts = PurePosixPath(path).parts
    if not parts or path != PurePosixPath(path).as_posix() or any(part in {"", ".", ".."} for part in parts):
        return False
    if path == "aina.json":
        return False
    return not any(path.startswith(folder) for folder in skill_folders)


def _media_type(path: str) -> str:
    suffix = PurePosixPath(path).suffix.lower()
    media = _TEXT_TYPES.get(suffix) or mimetypes.guess_type(path)[0] or "application/octet-stream"
    return f"{media}; charset=utf-8" if media.startswith("text/") else media


def ui_headers(*, frame_ancestors: list[str]) -> dict[str, str]:
    """Headers of every packaged UI file (design §9.1)."""
    ancestors = " ".join(dict.fromkeys(origin for origin in frame_ancestors if origin)) or "'none'"
    policy = "; ".join(
        [
            "sandbox allow-scripts allow-forms allow-downloads",
            "default-src 'self'",
            "script-src 'self' 'unsafe-inline'",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data: blob:",
            "font-src 'self' data:",
            "media-src 'self' data: blob:",
            "connect-src 'none'",
            "form-action 'none'",
            "base-uri 'none'",
            f"frame-ancestors {ancestors}",
        ]
    )
    return {
        "Content-Security-Policy": policy,
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        # The sandboxed document has an opaque origin, so even its own module scripts are cross-origin requests.
        "Access-Control-Allow-Origin": "*",
        "Cache-Control": "public, max-age=3600",
    }


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else ""
