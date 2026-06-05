from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from tests.support.storage_test_service import create_app
from tianzhou_agent_platform.store import NasStore, StorePage, StoreQuery


class FakeMySqlStore:
    def __init__(self) -> None:
        self.resource = ""
        self.captured_query: StoreQuery | None = None

    async def query(self, resource: str, query: StoreQuery) -> StorePage:
        self.resource = resource
        self.captured_query = query
        return StorePage(items=[], limit=query.limit, offset=query.offset)


def test_nas_write_accepts_raw_bytes(tmp_path) -> None:
    app = create_app()
    app.state.storage_stores = SimpleNamespace(nas=NasStore(tmp_path))
    client = TestClient(app)
    content = b"\x00\xffhello nas"

    response = client.put(
        "/nas/files/manual/blob.bin?overwrite=true",
        content=content,
        headers={"Content-Type": "application/octet-stream"},
    )

    assert response.status_code == 200
    assert response.json()["size_bytes"] == len(content)
    assert client.get("/nas/files/manual/blob.bin").content == content


def test_nas_write_honors_overwrite_query_parameter(tmp_path) -> None:
    app = create_app()
    app.state.storage_stores = SimpleNamespace(nas=NasStore(tmp_path))
    client = TestClient(app)

    first = client.put(
        "/nas/files/manual/blob.bin",
        content=b"first",
        headers={"Content-Type": "application/octet-stream"},
    )
    second = client.put(
        "/nas/files/manual/blob.bin?overwrite=false",
        content=b"second",
        headers={"Content-Type": "application/octet-stream"},
    )

    assert first.status_code == 200
    assert second.status_code == 400
    assert client.get("/nas/files/manual/blob.bin").content == b"first"


def test_mysql_query_accepts_condition_query_params() -> None:
    app = create_app()
    mysql = FakeMySqlStore()
    app.state.storage_stores = SimpleNamespace(mysql=mysql)
    client = TestClient(app)

    response = client.get(
        "/mysql/items",
        params=[
            ("condition_field", "created_at"),
            ("condition_op", "ge"),
            ("condition_value", "2026-06-01T00:00:00Z"),
            ("condition_field", "created_at"),
            ("condition_op", "lt"),
            ("condition_value", "2026-06-02T00:00:00Z"),
            ("limit", "25"),
            ("offset", "5"),
        ],
    )

    assert response.status_code == 200
    assert mysql.resource == "test_items"
    assert mysql.captured_query is not None
    assert mysql.captured_query.limit == 25
    assert mysql.captured_query.offset == 5
    assert mysql.captured_query.conditions[0].field == "created_at"
    assert mysql.captured_query.conditions[0].op == "ge"
    assert mysql.captured_query.conditions[0].value == datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert mysql.captured_query.conditions[1].field == "created_at"
    assert mysql.captured_query.conditions[1].op == "lt"
    assert mysql.captured_query.conditions[1].value == datetime(2026, 6, 2, tzinfo=timezone.utc)


def test_mysql_query_rejects_unmatched_condition_query_params() -> None:
    app = create_app()
    app.state.storage_stores = SimpleNamespace(mysql=FakeMySqlStore())
    client = TestClient(app)

    response = client.get(
        "/mysql/items",
        params=[
            ("condition_field", "created_at"),
            ("condition_op", "ge"),
        ],
    )

    assert response.status_code == 400
