import pytest
from pydantic import ValidationError

from tianzhou_agent_platform.store.models import StoragePath, StoreQuery


def test_storage_path_normalizes_separators() -> None:
    path = StoragePath(relative_path="scope\\item.txt")

    assert path.relative_path == "scope/item.txt"


@pytest.mark.parametrize("relative_path", ["", "/tmp/item.txt", "C:\\tmp\\item.txt", "../item.txt", "scope/../item.txt"])
def test_storage_path_rejects_unsafe_values(relative_path: str) -> None:
    with pytest.raises(ValidationError):
        StoragePath(relative_path=relative_path)


def test_store_query_limits_page_size() -> None:
    with pytest.raises(ValidationError):
        StoreQuery(limit=1001)


def test_store_query_rejects_empty_contains_filter() -> None:
    with pytest.raises(ValidationError):
        StoreQuery(contains_filters={"name": ""})
