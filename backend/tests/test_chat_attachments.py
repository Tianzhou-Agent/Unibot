"""Files attached to chat messages, and AINA tools reading them with Host API ``files/{file_ref}`` (design §8)."""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from mcp.server.mcpserver import Context

from tests.support.fake_llm import ScriptedLLM, assistant, call_first_tool, tool_calling
from tests.test_aina_package import (
    ENTRY,
    MCP_URL,
    _call,
    _example_files,
    _example_server,
    _register,
    _set,
    _settings,
    _with_manifest,
)
from tianzhou_agent_platform.aina.document.service import DocumentService
from tianzhou_agent_platform.api import conversations as conversation_routes
from tianzhou_agent_platform.conversations import attachments as attachments_module
from tianzhou_agent_platform.conversations.attachments import ATTACHMENTS_KEY, MessageAttachment, passed_refs
from tianzhou_agent_platform.conversations.models import Message
from tianzhou_agent_platform.aina.package.mcp_client import McpToolClient
from tianzhou_agent_platform.core.base import utc_now
from tianzhou_agent_platform.main import create_app
from tianzhou_agent_platform.services.agent_integration.history import archive_to_native
from tianzhou_agent_platform.store.errors import StorageNotFoundError
from tianzhou_agent_platform.store.nas.filesystem import NasStore
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
def chat(tmp_path: Path) -> Any:
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
    app = create_app(
        settings=_settings(),
        llm=llm,
        mcp_client=McpToolClient(servers={MCP_URL: server.mcp}),
        document_service=DocumentService(NasStore(tmp_path)),
    )
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


def _upload(client: TestClient, name: str = "taxi.txt", data: bytes = b"Taxi 42 EUR", **params: str) -> Any:
    return client.post("/attachments", files={"file": (name, data, "text/plain")}, params=params)


def _send(client: TestClient, conversation_id: str, refs: list[str]) -> list[dict[str, Any]]:
    """Bind uploads to the conversation as a sent message does, without running a turn."""
    state = client.app.state  # type: ignore[attr-defined]
    conversation = _call(client, state.conversation_service.get, conversation_id)
    return [item.model_dump(mode="json") for item in _call(client, state.chat_attachments.bind, conversation, refs)]


def _documents(client: TestClient, **params: str) -> list[str]:
    return [item["name"] for item in client.get("/documents/tree", params=params).json()["documents"]]


def test_a_tool_reads_the_file_the_model_passed_it(chat: Any) -> None:
    client, seen = chat
    conversation_id = client.post("/conversations", json={"title": "Receipts"}).json()["id"]
    uploaded = _upload(client, name="../taxi receipt.txt").json()

    reply = client.post(
        "/chat",
        json={
            "message": "What is this receipt?",
            "conversation_id": conversation_id,
            "attachments": [uploaded["file_ref"]],
        },
    ).json()
    messages = client.get(f"/conversations/{conversation_id}").json()["messages"]
    download = client.get(f"/attachments/{uploaded['file_ref']}")

    assert reply["status"] == "completed", reply
    assert uploaded["name"] == "taxi receipt.txt" and uploaded["size_bytes"] == 11 and "document" not in uploaded
    assert f"document: Uploads/taxi receipt.txt file_ref: {uploaded['file_ref']}" in seen[0]
    assert '<attachment document="Uploads/taxi receipt.txt">\nTaxi 42 EUR\n</attachment>' in seen[0]
    tool_result = next(message for message in messages if message["role"] == "tool" and "taxi" in message["content"])
    assert "taxi receipt.txt text/plain Taxi 42 EUR" in tool_result["content"]
    user = next(message for message in messages if message["role"] == "user")
    assert user["content"] == "What is this receipt?"  # the refs reach the model, not the shown message
    assert user["attachments"] == [{**uploaded, "document": "Uploads/taxi receipt.txt"}]
    assert download.content == b"Taxi 42 EUR" and download.headers["content-disposition"].startswith("attachment;")
    assert client.get("/documents/Uploads/taxi receipt.txt").json()["content"] == "Taxi 42 EUR"


def test_a_message_names_only_its_owners_unsent_files_or_its_own(chat: Any) -> None:
    client, _seen = chat
    mine = client.post("/conversations", json={"title": "Mine"}).json()["id"]
    other = client.post("/conversations", json={"title": "Other"}).json()["id"]
    elsewhere = _upload(client).json()["file_ref"]
    _send(client, other, [elsewhere])
    someone_elses = _upload(client, user_id="someone-else").json()["file_ref"]

    refused = [
        client.post("/chat", json={"message": "Read it", "conversation_id": mine, "attachments": [ref]})
        for ref in (elsewhere, someone_elses, "file_" + "0" * 32)
    ]
    missing = client.get(f"/attachments/{someone_elses}")

    assert [response.status_code for response in refused] == [422, 422, 422]
    assert "is not an upload of this conversation's owner" in refused[0].text
    assert missing.status_code == 404


def test_a_retried_message_reuses_the_document_its_files_became(chat: Any) -> None:
    client, _seen = chat
    conversation_id = client.post("/conversations", json={"title": "Retry"}).json()["id"]
    ref = _upload(client, name="notes.md", data=b"# Notes").json()["file_ref"]

    first, again = _send(client, conversation_id, [ref, ref]), _send(client, conversation_id, [ref])

    assert [item["document"] for item in first + again] == ["Uploads/notes.md", "Uploads/notes.md"]
    assert _documents(client) == ["Uploads/notes.md"]


def test_files_unibot_cannot_read_are_refused_on_upload(chat: Any) -> None:
    client, _seen = chat

    image = _upload(client, name="photo.png", data=b"\x89PNG\r\n")
    broken = _upload(client, name="scan.pdf", data=b"%PDF-1.4 no pages")

    assert image.status_code == 415 and broken.status_code == 422


def test_an_unsent_upload_can_be_discarded_but_a_sent_one_is_deleted_as_its_document(chat: Any) -> None:
    client, _seen = chat
    conversation_id = client.post("/conversations", json={"title": "Discard"}).json()["id"]
    unsent = _upload(client).json()["file_ref"]
    sent = _upload(client).json()["file_ref"]
    _send(client, conversation_id, [sent])

    discarded = client.delete(f"/attachments/{unsent}")
    refused = client.delete(f"/attachments/{sent}")
    named_after = client.post(
        "/chat", json={"message": "Read it", "conversation_id": conversation_id, "attachments": [unsent]}
    )

    assert discarded.status_code == 204 and named_after.status_code == 422
    assert refused.status_code == 409 and "Uploads/taxi.txt" in refused.text


def test_deleting_the_document_deletes_the_file(chat: Any) -> None:
    client, _seen = chat
    conversation_id = client.post("/conversations", json={"title": "Delete"}).json()["id"]
    ref = _upload(client).json()["file_ref"]
    _send(client, conversation_id, [ref])

    state = client.app.state  # type: ignore[attr-defined]
    conversation = _call(client, state.conversation_service.get, conversation_id)
    views = _call(client, state.chat_attachments.bind, conversation, [ref])
    before = client.get(f"/attachments/{ref}")
    texts_before = _call(client, state.chat_attachments.texts, conversation, views)
    deleted = client.delete("/documents/Uploads/taxi.txt")
    after = client.get(f"/attachments/{ref}")
    texts_after = _call(client, state.chat_attachments.texts, conversation, views)

    assert before.content == b"Taxi 42 EUR" and deleted.status_code == 204 and after.status_code == 404
    assert texts_before == {ref: "Taxi 42 EUR"} and texts_after == {}
    assert client.get("/documents/tree").json() == {"folders": [{"path": "Uploads", "name": "Uploads"}], "documents": []}


def test_uploads_no_message_named_within_a_day_are_deleted(chat: Any) -> None:
    client, _seen = chat
    state = client.app.state  # type: ignore[attr-defined]
    stale = _upload(client).json()["file_ref"]
    attachment = _call(client, state.repository.get_chat_attachment, stale)
    aged = attachment.model_copy(update={"created_at": utc_now() - timedelta(days=2)})
    _call(client, state.repository.put_chat_attachment, aged)
    fresh = _upload(client).json()["file_ref"]

    assert _call(client, state.repository.get_chat_attachment, stale) is None
    assert _call(client, state.repository.get_chat_attachment, fresh) is not None
    with pytest.raises(StorageNotFoundError):
        _call(client, state.aina_file_blobs.read, f"chat-attachments/{stale}")


def test_a_workspace_conversation_stores_its_files_in_the_workspace(chat: Any) -> None:
    client, _seen = chat
    workspace = client.post("/workspaces", json={"name": "Team"}).json()
    conversation = client.post("/conversations", json={"title": "Team chat", "workspace_id": workspace["id"]}).json()
    ref = _upload(client).json()["file_ref"]
    _send(client, conversation["id"], [ref])

    assert _documents(client, workspace_id=workspace["id"]) == ["Uploads/taxi.txt"]
    assert _documents(client) == []
    assert client.get(f"/attachments/{ref}").content == b"Taxi 42 EUR"


def test_uploads_need_the_document_storage() -> None:
    with TestClient(create_app(settings=_settings(), llm=ScriptedLLM([]))) as client:
        assert _upload(client).status_code == 503


def test_attachments_have_a_size_limit(chat: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _seen = chat
    monkeypatch.setattr(conversation_routes, "MAX_ATTACHMENT_BYTES", 4)

    assert _upload(client, data=b"12345").status_code == 413
    assert _upload(client, data=b"1234").status_code == 201


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
    ref = _upload(client).json()["file_ref"]
    unpassed = _upload(client, name="secret.txt").json()["file_ref"]
    _send(client, conversation_id, [ref, unpassed])

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


def test_only_the_message_that_attached_a_file_shows_its_text(tmp_path: Path) -> None:
    seen: list[str] = []

    def answer(*, messages: list[dict[str, Any]], **_: Any) -> Any:
        seen.append("\n".join(str(message.get("content")) for message in messages))
        return assistant("Noted.")

    app = create_app(
        settings=_settings(), llm=ScriptedLLM([answer, answer]), document_service=DocumentService(NasStore(tmp_path))
    )
    with TestClient(app) as client:
        conversation_id = client.post("/conversations", json={"title": "Notes"}).json()["id"]
        ref = _upload(client, name="notes.md", data=b"# Plan\n\nShip on Friday").json()["file_ref"]
        for message, refs in (("Read my notes", [ref]), ("And now?", [])):
            reply = client.post(
                "/chat", json={"message": message, "conversation_id": conversation_id, "attachments": refs}
            ).json()
            assert reply["status"] == "completed", reply

    shown = '<attachment document="Uploads/notes.md">\n# Plan\n\nShip on Friday\n</attachment>'
    assert shown in seen[0]
    assert f"document: Uploads/notes.md file_ref: {ref}" in seen[1] and "Ship on Friday" not in seen[1]


def _attachment(ref: str, document: str) -> MessageAttachment:
    return MessageAttachment(file_ref=ref, name=document, content_type="text/plain", size_bytes=1, document=document)


def test_a_messages_files_text_is_clipped_to_its_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(attachments_module, "INLINE_FILE_CHARS", 12)
    files = [_attachment("file_a", "a.md"), _attachment("file_b", "b.md"), _attachment("file_c", "c.md")]
    texts = {"file_a": "one\ntwo\nthree\nfour\n", "file_b": "short", "file_c": "x" * 30}

    contents = attachments_module.inline_contents(files, texts, budget=200)
    starved = attachments_module.inline_contents(files, texts, budget=3)

    assert contents["file_a"] == (
        "one\ntwo\n[Only lines 1-2 of 4 are shown. Read the rest of a.md with the document read tool from offset 3.]"
    )
    assert contents["file_b"] == "short"
    assert contents["file_c"].startswith("[Not shown here") and "c.md" in contents["file_c"]
    assert starved["file_a"].startswith("[Not shown here")  # nothing of it fits in what the message may carry
    assert attachments_module.inline_budget(32_000) == 8_000
    assert attachments_module.inline_budget(1_000_000) == attachments_module.INLINE_MESSAGE_CHARS


def test_a_pdf_is_stored_as_uploaded_and_read_through_its_text(chat: Any) -> None:
    from tests.test_attachment_conversion import pdf

    client, _seen = chat
    conversation_id = client.post("/conversations", json={"title": "Report"}).json()["id"]
    payload = pdf("Revenue grew", "Costs fell")
    ref = client.post("/attachments", files={"file": ("q3.pdf", payload, "application/pdf")}).json()["file_ref"]
    unsent_preview = client.get(f"/attachments/{ref}/preview").json()
    (sent,) = _send(client, conversation_id, [ref])

    document = client.get("/documents/Uploads/q3.pdf").json()
    raw = client.get("/documents/Uploads/q3.pdf/file")
    preview = client.get(f"/attachments/{ref}/preview").json()
    someone_else = client.get(f"/attachments/{ref}/preview", params={"user_id": "someone-else"})

    assert sent["document"] == "Uploads/q3.pdf" and _documents(client) == ["Uploads/q3.pdf"]
    assert document["content"] == "## Page 1\n\nRevenue grew\n\n## Page 2\n\nCosts fell\n"
    assert raw.content == payload and raw.headers["content-type"] == "application/pdf"
    assert raw.headers["content-disposition"].startswith("attachment;")
    assert client.get(f"/attachments/{ref}").content == payload
    assert unsent_preview == preview == {"text": document["content"], "truncated": False}
    assert someone_else.status_code == 404


def test_a_preview_shows_the_start_of_a_long_file(chat: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from tianzhou_agent_platform.conversations import attachment_service

    client, _seen = chat
    monkeypatch.setattr(attachment_service, "PREVIEW_CHARS", 5)
    ref = _upload(client, name="long.txt", data=b"0123456789").json()["file_ref"]

    assert client.get(f"/attachments/{ref}/preview").json() == {"text": "01234", "truncated": True}
