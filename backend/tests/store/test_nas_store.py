import pytest

from tianzhou_agent_platform.store import (
    NasStore,
    StorageBackendUnavailableError,
    StorageNotFoundError,
    StoragePath,
    StoragePolicyViolationError,
    StorageValidationError,
)


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
