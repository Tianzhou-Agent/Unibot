from types import SimpleNamespace

from fastapi.testclient import TestClient

from tianzhou_agent_platform.store import NasStore
from tianzhou_agent_platform.store.test_service import create_app


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
