"""Notifications from AINAs (Host API ``notify``): the user's inbox, its limits and its links."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from tests.test_aina_host_api import AINA_ID, _call
from tests.test_aina_host_services import _headers, _install, host  # noqa: F401 - the fixture
from tianzhou_agent_platform.aina.package.notifications import KEEP_PER_USER, MAX_PER_HOUR, AinaNotification
from tianzhou_agent_platform.conversations.models import ConversationCreate
from unibot_aina import HostApi, set_default_transport

API = "/aina-host/v1"


def _actor(client: Any) -> tuple[str, str]:
    """The (tenant, user) the shell routes act for in this app (the dev user when sign-in is bypassed)."""
    user = (client.get("/auth/me").json() or {}).get("user") or {}
    return str(user.get("tenant_id") or "default"), str(user.get("id") or "anonymous")


def _inbox(client: Any, user_id: str = "alice") -> dict[str, Any]:
    """The inbox as ``user_id`` sees it (the routes act for the request's actor, so read it through the repository)."""
    repo = client.app.state.repository
    items = _call(client, repo.list_aina_notifications, tenant_id="t1", user_id=user_id, limit=100)
    unread = _call(client, repo.count_aina_notifications, tenant_id="t1", user_id=user_id, unread=True)
    return {"items": items, "unread": unread}


def test_a_notification_lands_in_the_users_inbox_and_opens_the_app_or_the_conversation(host: Any) -> None:  # noqa: F811
    client, app, _llm = host
    repo = app.state.repository
    conversation = _call(client, repo.create_conversation, ConversationCreate(user_id="alice", tenant_id="t1"))
    installation = _install(client, ["notify"])
    in_conversation = _headers(app, installation, conversation_id=conversation.id)
    outside = _headers(app, installation)

    app_link = client.post(f"{API}/notifications", json={"title": "Report ready", "body": "September"}, headers=outside)
    conversation_link = client.post(
        f"{API}/notifications", json={"title": "Done", "opens": "conversation"}, headers=in_conversation
    )
    no_conversation = client.post(
        f"{API}/notifications", json={"title": "Done", "opens": "conversation"}, headers=outside
    )
    denied = client.post(
        f"{API}/notifications", json={"title": "x"}, headers=_headers(app, _install(client, [], user_id="bob"))
    )
    too_long = client.post(f"{API}/notifications", json={"title": "x" * 121}, headers=outside)
    inbox = _inbox(client)

    assert app_link.status_code == 201 and set(app_link.json()) == {"id", "created_at"}
    assert conversation_link.status_code == 201
    assert no_conversation.status_code == 422
    assert denied.status_code == 403
    assert too_long.status_code == 422
    assert inbox["unread"] == 2
    newest, oldest = inbox["items"]
    assert (newest.opens, newest.conversation_id) == ("conversation", conversation.id)
    assert (oldest.title, oldest.body, oldest.opens, oldest.conversation_id) == (
        "Report ready",
        "September",
        "app",
        None,
    )


def test_the_shell_lists_and_marks_its_users_notifications(host: Any) -> None:  # noqa: F811
    client, app, _llm = host
    repo = app.state.repository
    tenant_id, user_id = _actor(client)
    for title in ("One", "Two"):
        _call(
            client,
            repo.create_aina_notification,
            AinaNotification(id=f"ntf_{title}", tenant_id=tenant_id, user_id=user_id, aina_id=AINA_ID, title=title),
        )
    _call(
        client,
        repo.create_aina_notification,
        AinaNotification(id="ntf_theirs", tenant_id="t1", user_id="bob", aina_id=AINA_ID, title="Theirs"),
    )

    listed = client.get("/notifications").json()
    one = client.post("/notifications/ntf_One/read")
    again = client.post("/notifications/ntf_One/read")
    theirs = client.post("/notifications/ntf_theirs/read")
    after_one = client.get("/notifications").json()
    client.post("/notifications/read-all")
    after_all = client.get("/notifications").json()

    assert [item["title"] for item in listed["items"]] == ["Two", "One"] and listed["unread"] == 2
    assert listed["items"][0]["aina_name"] == "Expenses"
    assert one.status_code == 204 and again.status_code == 204
    assert theirs.status_code == 404  # another user's notification is not found
    assert after_one["unread"] == 1
    assert after_all["unread"] == 0 and all(item["read"] for item in after_all["items"])
    assert _inbox(client, "bob")["unread"] == 1


def test_an_aina_may_send_a_limited_number_and_the_inbox_keeps_the_newest(host: Any) -> None:  # noqa: F811
    client, app, _llm = host
    headers = _headers(app, _install(client, ["notify"]))
    statuses = [
        client.post(f"{API}/notifications", json={"title": f"n{index}"}, headers=headers).status_code
        for index in range(MAX_PER_HOUR + 1)
    ]
    repo = app.state.repository
    for index in range(KEEP_PER_USER + 5):
        _call(
            client,
            repo.create_aina_notification,
            AinaNotification(id=f"ntf_{index}", tenant_id="t2", user_id="carol", aina_id=AINA_ID, title=f"n{index}"),
        )

    assert statuses[:MAX_PER_HOUR] == [201] * MAX_PER_HOUR and statuses[-1] == 429
    assert _call(client, repo.count_aina_notifications, tenant_id="t2", user_id="carol") == KEEP_PER_USER


def test_uninstalling_or_deleting_the_aina_removes_its_notifications(host: Any) -> None:  # noqa: F811
    client, app, _llm = host
    repo = app.state.repository
    tenant_id, me = _actor(client)
    for user_id in (me, "dave"):
        _call(
            client,
            repo.create_aina_notification,
            AinaNotification(id=f"ntf_{user_id}", tenant_id=tenant_id, user_id=user_id, aina_id=AINA_ID, title="x"),
        )

    client.post(f"/ainas/{AINA_ID}/install", json={"user_id": me, "tenant_id": tenant_id})
    uninstalled = client.delete(f"/ainas/{AINA_ID}/install", params={"user_id": me, "tenant_id": tenant_id})
    after_uninstall = _call(client, repo.count_aina_notifications, tenant_id=tenant_id, user_id="dave")
    mine = client.get("/notifications").json()["items"]
    client.delete(f"/ainas/{AINA_ID}")

    assert uninstalled.status_code == 204
    assert mine == [] and after_uninstall == 1  # only the uninstalling user's
    assert _call(client, repo.count_aina_notifications, tenant_id=tenant_id, user_id="dave") == 0


@pytest.mark.parametrize("opens", ["app"])
def test_the_sdk_sends_notifications(host: Any, opens: str) -> None:  # noqa: F811
    client, app, _llm = host
    token = _headers(app, _install(client, ["notify"]))["Authorization"].removeprefix("Bearer ")
    set_default_transport(httpx.ASGITransport(app=app), api_base=f"http://testserver{API}")
    try:
        receipt = _call(client, HostApi(f"http://testserver{API}", token).notify, "From the SDK", opens=opens)
    finally:
        set_default_transport(None)

    assert receipt["id"].startswith("ntf_")
    assert _inbox(client)["items"][0].title == "From the SDK"
