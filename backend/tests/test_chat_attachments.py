"""Files attached to chat messages, and AINA tools reading them with Host API ``files/{file_ref}`` (design §8)."""

from __future__ import annotations

import re
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from mcp.server.mcpserver import Context

from tests.support.fake_llm import ScriptedLLM, assistant, call_first_tool, tool_calling
from tests.test_aina_package import (
    ENTRY,
    MCP_URL,
    _example_files,
    _example_server,
    _register,
    _set,
    _settings,
    _with_manifest,
)
from tianzhou_agent_platform.api import conversations as conversation_routes
from tianzhou_agent_platform.conversations.attachments import ATTACHMENTS_KEY, MessageAttachment, passed_refs
from tianzhou_agent_platform.conversations.models import Message
from tianzhou_agent_platform.aina.package.mcp_client import McpToolClient
from tianzhou_agent_platform.main import create_app
from tianzhou_agent_platform.services.agent_integration.history import archive_to_native
from unibot_aina import HostApi, set_default_transport

AINA_ID = "com.example.expenses"
TOOL = "mcp_com_example_expenses_read_receipt"
REF = re.compile(r"file_ref: (file_[0-9a-f]{32})")


def _receipt_server() -> Any:
    server = _example_server()

    @server.mcp.tool()
    async def read_receipt(file: str, ctx: Context) -> str:
        """Read a receipt the user attached."""
        context = server._unibot(ctx)  # noqa: SLF001 - the example's own helper
        passed = await HostApi(context["host"]["api_base"], context["token"]).passed_file(file)
        if passed is None:
            return "no such file"
        return f"{passed.name} {passed.content_type} {passed.data.decode()}"

    return server


@pytest.fixture
def chat() -> Any:
    server = _receipt_server()
    seen: list[str] = []

    def call_with_the_ref(*, messages: list[dict[str, Any]], tools: list[dict[str, Any]], **_: Any) -> Any:
        text = "\n".join(str(message.get("content")) for message in messages)
        seen.append(text)
        name = next(item["function"]["name"] for item in tools if item["function"]["name"].startswith(TOOL))
        return tool_calling(name, {"file": REF.findall(text)[-1]}, call_id="call_read")

    def answer(*, messages: list[dict[str, Any]], **_: Any) -> Any:
        seen.append("\n".join(str(message.get("content")) for message in messages))
        return assistant("It is a taxi receipt.")

    llm = ScriptedLLM([call_first_tool(prefix=ENTRY, call_id="call_activate"), call_with_the_ref, answer])
    app = create_app(settings=_settings(), llm=llm, mcp_client=McpToolClient(servers={MCP_URL: server.mcp}))
    set_default_transport(httpx.ASGITransport(app=app), api_base="http://testserver/aina-host/v1")
    files = _with_manifest(
        _example_files(), _set("tools.expose.read_receipt", {"effect": "read", "visibility": ["model"]})
    )
    try:
        with TestClient(app) as client:
            assert _register(client, files).status_code == 201
            client.post(f"/ainas/{AINA_ID}/install", json={"granted_permissions": ["storage"]})
            yield client, seen
    finally:
        set_default_transport(None)


def _upload(client: TestClient, conversation_id: str, name: str = "taxi.txt", data: bytes = b"Taxi 42 EUR") -> Any:
    return client.post(f"/conversations/{conversation_id}/attachments", files={"file": (name, data, "text/plain")})


def test_a_tool_reads_the_file_the_model_passed_it(chat: Any) -> None:
    client, seen = chat
    conversation_id = client.post("/conversations", json={"title": "Receipts"}).json()["id"]
    uploaded = _upload(client, conversation_id, name="../taxi receipt.txt").json()

    reply = client.post(
        "/chat",
        json={
            "message": "What is this receipt?",
            "conversation_id": conversation_id,
            "attachments": [uploaded["file_ref"]],
        },
    ).json()
    messages = client.get(f"/conversations/{conversation_id}").json()["messages"]
    download = client.get(f"/conversations/{conversation_id}/attachments/{uploaded['file_ref']}")

    assert reply["status"] == "completed", reply
    assert uploaded["name"] == "taxi receipt.txt" and uploaded["size_bytes"] == 11
    assert f"file_ref: {uploaded['file_ref']}" in seen[0]
    tool_result = next(message for message in messages if message["role"] == "tool" and "taxi" in message["content"])
    assert "taxi receipt.txt text/plain Taxi 42 EUR" in tool_result["content"]
    user = next(message for message in messages if message["role"] == "user")
    assert user["content"] == "What is this receipt?"  # the refs reach the model, not the shown message
    assert user["attachments"] == [uploaded]
    assert download.content == b"Taxi 42 EUR" and download.headers["content-disposition"].startswith("attachment;")


def test_a_message_names_only_files_of_its_own_conversation(chat: Any) -> None:
    client, _seen = chat
    mine = client.post("/conversations", json={"title": "Mine"}).json()["id"]
    other = client.post("/conversations", json={"title": "Other"}).json()["id"]
    elsewhere = _upload(client, other).json()["file_ref"]

    refused = client.post("/chat", json={"message": "Read it", "conversation_id": mine, "attachments": [elsewhere]})
    missing = client.get(f"/conversations/{mine}/attachments/{elsewhere}")

    assert refused.status_code == 422 and "was not uploaded to this conversation" in refused.text
    assert missing.status_code == 404


def test_attachments_have_a_size_limit(chat: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _seen = chat
    monkeypatch.setattr(conversation_routes, "MAX_ATTACHMENT_BYTES", 4)
    conversation_id = client.post("/conversations", json={"title": "Big"}).json()["id"]

    assert _upload(client, conversation_id, data=b"12345").status_code == 413
    assert _upload(client, conversation_id, data=b"1234").status_code == 201


def _host_get(client: TestClient, ref: str, *, files: list[str], conversation_id: str) -> httpx.Response:
    from tests.test_aina_host_services import _headers  # the installed test actor, token minted directly

    app = client.app
    installation = _call_installation(client)
    headers = _headers(app, installation, conversation_id=conversation_id)  # type: ignore[arg-type]
    service = app.state.aina_packages  # type: ignore[attr-defined]
    token = service.tokens.verify(headers["Authorization"].removeprefix("Bearer "))
    claims = {
        key: value for key, value in token.claims.items() if key not in {"iss", "aud", "iat", "exp", "jti", "ref"}
    }
    minted = service.tokens.mint(reference=token.reference, claims={**claims, "files": files})
    return client.get(f"/aina-host/v1/files/{ref}", headers={"Authorization": f"Bearer {minted}"})


def _call_installation(client: TestClient) -> Any:
    from tests.test_aina_package import _call

    (installation,) = _call(client, client.app.state.repository.list_installations)  # type: ignore[attr-defined]
    return installation


def test_the_host_api_serves_only_files_passed_to_the_call_in_its_conversation(chat: Any) -> None:
    client, _seen = chat
    conversation_id = client.post("/conversations", json={"title": "Receipts"}).json()["id"]
    other = client.post("/conversations", json={"title": "Other"}).json()["id"]
    ref = _upload(client, conversation_id).json()["file_ref"]
    unpassed = _upload(client, conversation_id, name="secret.txt").json()["file_ref"]

    passed = _host_get(client, ref, files=[ref], conversation_id=conversation_id)
    not_passed = _host_get(client, unpassed, files=[ref], conversation_id=conversation_id)
    wrong_conversation = _host_get(client, ref, files=[ref], conversation_id=other)

    assert passed.status_code == 200 and passed.content == b"Taxi 42 EUR"
    assert passed.headers["content-type"].startswith("text/plain")
    assert not_passed.status_code == 404 and wrong_conversation.status_code == 404


def test_refs_are_found_anywhere_in_the_arguments() -> None:
    arguments = {"file": "file_a", "pages": [{"source": "file_b"}, "notes"], "count": 2, "label": "a file_c"}

    assert passed_refs(arguments) == {"file_a", "file_b"}


def test_working_memory_rebuilt_from_the_archive_keeps_the_attachments() -> None:
    attachment = MessageAttachment(file_ref="file_" + "0" * 32, name="a.txt", content_type="text/plain", size_bytes=1)
    message = Message(id="msg_1", role="user", content="Read it", attachments=[attachment])

    native = archive_to_native(message)

    assert native.additional_kwargs[ATTACHMENTS_KEY] == [attachment.model_dump()]
    assert "attachments" not in Message(id="msg_2", role="user", content="hi").model_dump(mode="json")
