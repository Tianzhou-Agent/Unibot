import os
from pathlib import Path, PureWindowsPath

import pytest

from tianzhou_agent_platform.store import (
    NasStore,
    StorageBackendUnavailableError,
    StorageNotFoundError,
    StoragePath,
    StoragePolicyViolationError,
    StorageValidationError,
)
from tianzhou_agent_platform.store.nas.filesystem import _relative_to_root


@pytest.mark.parametrize("target,root,expected", [
    (r"\\?\C:\workspace\docs\file.txt", r"C:\workspace", "docs/file.txt"),
    (r"C:\workspace\docs\file.txt", r"\\?\C:\workspace", "docs/file.txt"),
    (r"\\?\UNC\server\share\docs\file.txt", r"\\server\share", "docs/file.txt"),
    (r"\\server\share\docs\file.txt", r"\\?\UNC\server\share", "docs/file.txt"),
    (r"\\?\C:\outside\file.txt", r"C:\workspace", None),
    (r"\\?\C:\workspace-other\file.txt", r"C:\workspace", None),
    (r"\\?\D:\workspace\file.txt", r"C:\workspace", None),
    (r"\\?\UNC\other\share\file.txt", r"\\server\share", None),
])
def test_windows_path_aliases_preserve_the_containment_boundary(target, root, expected):
    relative = _relative_to_root(PureWindowsPath(target), PureWindowsPath(root))
    assert (relative.as_posix() if relative is not None else None) == expected


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "nt", reason="Windows extended-path filesystem behavior")
async def test_nas_handles_extended_resolved_paths_without_allowing_escape(tmp_path, monkeypatch):
    store = NasStore(tmp_path)
    path = StoragePath(relative_path="docs/file.txt")
    await store.write(path, b"preserved")
    original_resolve = type(tmp_path).resolve

    def extended_resolve(self, *args, **kwargs):
        resolved = original_resolve(self, *args, **kwargs)
        if resolved == tmp_path / "escape":
            resolved = tmp_path.parent / "outside"
        value = str(resolved)
        return Path(value if value.startswith("\\\\?\\") else "\\\\?\\" + value)

    monkeypatch.setattr(type(tmp_path), "resolve", extended_resolve)
    assert str(store._resolve(path)).startswith("\\\\?\\")
    assert await store.read(path) == b"preserved"
    assert [item.path.relative_path for item in await store.list_files(StoragePath(relative_path="docs"))] == ["docs/file.txt"]
    with pytest.raises(StoragePolicyViolationError):
        store._resolve(StoragePath(relative_path="escape"))


@pytest.mark.asyncio
async def test_nas_store_write_read_metadata_delete(tmp_path) -> None:
    store = NasStore(tmp_path)
    path = StoragePath(relative_path="docs/item.txt")

    metadata = await store.write(path, b"hello")

    assert metadata.size_bytes == 5
    assert await store.read(path) == b"hello"
    assert await store.exists(path) is True
    assert (await store.delete(path)).deleted is True
    assert await store.exists(path) is False


@pytest.mark.asyncio
async def test_nas_store_lists_files_below_prefix(tmp_path) -> None:
    store = NasStore(tmp_path)
    await store.write(StoragePath(relative_path="documents/a.md"), b"a")
    await store.write(StoragePath(relative_path="documents/nested/b.md"), b"b")
    await store.write(StoragePath(relative_path="other/c.md"), b"c")

    items = await store.list_files(StoragePath(relative_path="documents"))

    assert [item.path.relative_path for item in items] == ["documents/a.md", "documents/nested/b.md"]


@pytest.mark.asyncio
async def test_nas_store_rejects_write_without_overwrite(tmp_path) -> None:
    store = NasStore(tmp_path)
    path = StoragePath(relative_path="docs/item.txt")

    await store.write(path, b"first")

    with pytest.raises(StorageValidationError):
        await store.write(path, b"second", overwrite=False)


@pytest.mark.asyncio
async def test_nas_store_move_does_not_replace_existing_file(tmp_path) -> None:
    store = NasStore(tmp_path)
    source = StoragePath(relative_path="docs/source.txt")
    destination = StoragePath(relative_path="docs/destination.txt")
    await store.write(source, b"source")
    await store.write(destination, b"destination")

    with pytest.raises(StorageValidationError):
        await store.move(source, destination)

    assert await store.read(source) == b"source"
    assert await store.read(destination) == b"destination"


@pytest.mark.asyncio
async def test_nas_store_rejects_oversized_content(tmp_path) -> None:
    store = NasStore(tmp_path, max_file_size_bytes=3)

    with pytest.raises(StoragePolicyViolationError):
        await store.write(StoragePath(relative_path="item.txt"), b"four")


@pytest.mark.asyncio
async def test_nas_store_missing_read_raises_not_found(tmp_path) -> None:
    store = NasStore(tmp_path)

    with pytest.raises(StorageNotFoundError):
        await store.read(StoragePath(relative_path="missing.txt"))


@pytest.mark.asyncio
async def test_nas_store_missing_root_raises_unavailable(tmp_path) -> None:
    missing_root = tmp_path / "missing"
    store = NasStore(missing_root)

    with pytest.raises(StorageBackendUnavailableError):
        await store.exists(StoragePath(relative_path="item.txt"))
