"""Plugin-private files (Host API ``storage/files``, scope ``storage``): bytes, isolation, limits and cleanup."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from tests.test_aina_host_api import AINA_ID, _call
from tests.test_aina_host_services import _headers, _install, host  # noqa: F401 - the fixture
from tianzhou_agent_platform.aina.package import files
from tianzhou_agent_platform.aina.protocol.models import AinaInstallation
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.store.memory_repository import installation_record_id
from unibot_aina import HostApi, set_default_transport

API = "/aina-host/v1/storage/files"


def test_files_are_stored_listed_replaced_and_deleted(host: Any) -> None:  # noqa: F811
    client, app, _llm = host
    alice = _headers(app, _install(client, ["storage"]))

    put = client.put(
        f"{API}/receipts/2026-10.pdf", content=b"%PDF-1.7 a", headers={**alice, "Content-Type": "application/pdf"}
    )
    client.put(f"{API}/notes.txt", content=b"hello", headers={**alice, "Content-Type": "text/plain; charset=utf-8"})
    got = client.get(f"{API}/receipts/2026-10.pdf", headers=alice)
    replaced = client.put(
        f"{API}/receipts/2026-10.pdf", content=b"%PDF-1.7 bb", headers={**alice, "Content-Type": "application/pdf"}
    )
    listed = client.get(API, headers=alice).json()["items"]
    receipts = client.get(API, params={"prefix": "receipts/"}, headers=alice).json()["items"]
    deleted = client.delete(f"{API}/notes.txt", headers=alice)
    gone = client.get(f"{API}/notes.txt", headers=alice)

    assert put.status_code == 200 and put.json()["size_bytes"] == 10 and put.json()["content_type"] == "application/pdf"
    assert got.content == b"%PDF-1.7 a" and got.headers["content-type"] == "application/pdf"
    assert got.headers["etag"] == f'"{put.json()["sha256"]}"'
    assert replaced.json()["size_bytes"] == 11
    assert [item["path"] for item in listed] == ["notes.txt", "receipts/2026-10.pdf"]
    assert listed[0]["content_type"] == "text/plain"
    assert [item["path"] for item in receipts] == ["receipts/2026-10.pdf"]
    assert deleted.status_code == 204 and gone.status_code == 404


def test_files_belong_to_one_installation_and_need_the_storage_scope(host: Any) -> None:  # noqa: F811
    client, app, _llm = host
    alice = _headers(app, _install(client, ["storage"]))
    bob = _headers(app, _install(client, ["storage"], user_id="bob"))
    nothing = _headers(app, _install(client, [], user_id="carol"))
    client.put(f"{API}/secret.txt", content=b"alice only", headers=alice)

    assert client.get(f"{API}/secret.txt", headers=bob).status_code == 404
    assert client.get(API, headers=bob).json()["items"] == []
    assert client.delete(f"{API}/secret.txt", headers=bob).status_code == 404
    assert client.put(f"{API}/x.txt", content=b"x", headers=nothing).status_code == 403
    assert client.get(f"{API}/secret.txt", headers=alice).content == b"alice only"


@pytest.mark.parametrize("path", ["", "/abs", "a//b", "a/./b", "a/../b", "back\\slash", "tab\tname", "x" * 513])
def test_unsafe_paths_are_refused(path: str) -> None:
    with pytest.raises(PlatformError, match="relative"):
        files.check_path(path)


def test_size_count_and_total_limits(host: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    client, app, _llm = host
    monkeypatch.setattr(files, "MAX_FILE_BYTES", 10)
    monkeypatch.setattr(files, "MAX_FILES", 2)
    monkeypatch.setattr(files, "MAX_TOTAL_BYTES", 15)
    alice = _headers(app, _install(client, ["storage"]))

    too_big = client.put(f"{API}/big", content=b"x" * 11, headers=alice)
    first = client.put(f"{API}/a", content=b"x" * 8, headers=alice)
    over_total = client.put(f"{API}/b", content=b"x" * 8, headers=alice)
    second = client.put(f"{API}/b", content=b"x" * 7, headers=alice)
    too_many = client.put(f"{API}/c", content=b"x", headers=alice)
    replace = client.put(f"{API}/a", content=b"x" * 8, headers=alice)

    assert too_big.status_code == 413 and first.status_code == 200
    assert over_total.status_code == 413 and second.status_code == 200
    assert too_many.status_code == 413 and "1000" not in too_many.json()["error"]["message"]
    assert replace.status_code == 200  # replacing a file does not count as a new one


def test_files_go_with_the_installation_unless_the_user_keeps_its_data(host: Any) -> None:  # noqa: F811
    client, app, _llm = host
    repo = app.state.repository
    # Uninstall acts for the request's actor, so the files are that user's.
    user = (client.get("/auth/me").json() or {}).get("user") or {}
    tenant_id, me = str(user.get("tenant_id") or "default"), str(user.get("id") or "anonymous")

    def install(user_id: str) -> dict[str, str]:
        installation = AinaInstallation(
            user_id=user_id,
            tenant_id=tenant_id,
            aina_id=AINA_ID,
            installed_version="1.2.0",
            granted_permissions=["storage"],
        )
        _call(client, repo.put_installation, installation)
        return _headers(app, installation)

    mine, theirs = install(me), install("bob")
    client.put(f"{API}/a.txt", content=b"a", headers=mine)
    client.put(f"{API}/b.txt", content=b"b", headers=theirs)
    params = {"user_id": me, "tenant_id": tenant_id}

    kept = client.delete(f"/ainas/{AINA_ID}/install", params={**params, "keep_data": "true"})
    my_key = installation_record_id(tenant_id, me, AINA_ID)
    after_keep = _call(client, repo.aina_file_usage, my_key)
    install(me)
    client.delete(f"/ainas/{AINA_ID}/install", params=params)
    after_uninstall = _call(client, repo.aina_file_usage, my_key)
    bob_before_delete = _call(client, repo.aina_file_usage, installation_record_id(tenant_id, "bob", AINA_ID))
    client.delete(f"/ainas/{AINA_ID}")
    bob_after_delete = _call(client, repo.aina_file_usage, installation_record_id(tenant_id, "bob", AINA_ID))

    assert kept.status_code == 204, kept.text
    assert after_keep == (1, 1)
    assert after_uninstall == (0, 0) and bob_before_delete == (1, 1)
    assert bob_after_delete == (0, 0)
    assert app.state.aina_file_blobs._blobs == {}  # noqa: SLF001 - the bytes went too


def test_the_sdk_reads_and_writes_files(host: Any) -> None:  # noqa: F811
    client, app, _llm = host
    token = _headers(app, _install(client, ["storage"]))["Authorization"].removeprefix("Bearer ")
    api = HostApi("http://testserver/aina-host/v1", token)
    set_default_transport(httpx.ASGITransport(app=app), api_base="http://testserver/aina-host/v1")
    try:
        written = _call(client, api.put_file, "img/logo.png", b"\x89PNG", "image/png")
        read = _call(client, api.get_file, "img/logo.png")
        missing = _call(client, api.get_file, "nope")
        listed = _call(client, api.list_files)
        _call(client, api.delete_file, "img/logo.png")
        after = _call(client, api.get_file, "img/logo.png")
    finally:
        set_default_transport(None)

    assert written["content_type"] == "image/png" and read == b"\x89PNG"
    assert missing is None and after is None
    assert [item["path"] for item in listed] == ["img/logo.png"]
