import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table

from tianzhou_agent_platform.store import MySqlStore, StorageUnsupportedCapabilityError, StorageValidationError, StoreQuery


class FakeEngine:
    async def dispose(self) -> None:
        return None


def fake_session_factory():
    raise AssertionError("session factory should not be used by validation-only tests")


@pytest.mark.asyncio
async def test_mysql_store_rejects_unconfigured_resource() -> None:
    store = MySqlStore(FakeEngine(), fake_session_factory)

    with pytest.raises(StorageUnsupportedCapabilityError):
        await store.read("missing", 1)


@pytest.mark.asyncio
async def test_mysql_store_rejects_unknown_query_filter() -> None:
    metadata = MetaData()
    table = Table("items", metadata, Column("id", Integer), Column("name", String))
    store = MySqlStore(FakeEngine(), fake_session_factory, resource_tables={"items": table})

    with pytest.raises(StorageValidationError):
        await store.query("items", StoreQuery(filters={"unknown": "value"}))
