"""AINA Protocol 2.0 phase P6: canvas tools (design §9.5) with the whiteboard reference AINA."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.store.test_redis_store import FakeRedisClient
from tests.support.fake_llm import LLMResult, ScriptedLLM, assistant, call_first_tool
from tests.test_aina_host_api import RecordingMcp, _call
from tests.test_aina_package import REPO, _settings
from tianzhou_agent_platform.aina.package.canvas_calls import CanvasCallBroker
from tianzhou_agent_platform.aina.project import build_project_archive
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.main import create_app
from tianzhou_agent_platform.store import RedisStore

AINA = "com.example.whiteboard"
WHITEBOARD = REPO / "examples" / "whiteboard-aina"
MCP_URL = "http://127.0.0.1:8766/mcp"
ENTRY = "aina_com_example_whiteboard_"
GET_SCENE = "canvas_com_example_whiteboard_get_scene_"
DRAW_CIRCLE = "canvas_com_example_whiteboard_draw_circle_"
CLEAR = "canvas_com_example_whiteboard_clear_scene_"
TAB_A = "tab_aaaaaaaaaaaa"
TAB_B = "tab_bbbbbbbbbbbb"


def _files(change: Callable[[dict[str, Any]], None] | None = None) -> dict[str, bytes]:
    root = WHITEBOARD / "package"
    files = {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    if change is not None:
        manifest = json.loads(files["aina.json"])
        change(manifest)
        files["aina.json"] = json.dumps(manifest).encode()
    return files


def _server() -> ModuleType:
    spec = importlib.util.spec_from_file_location("whiteboard_example_server", WHITEBOARD / "server" / "main.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _client(llm: ScriptedLLM, files: dict[str, bytes] | None = None) -> TestClient:
    server = _server()
    app = create_app(settings=_settings(), llm=llm, mcp_client=RecordingMcp({MCP_URL: server.mcp}))
    server.HOST_TRANSPORT = httpx.ASGITransport(app=app)
    client = TestClient(app)
    client.__enter__()
    archive = build_project_archive(files or _files())
    assert client.post("/ainas/packages", files={"file": ("w.aina.zip", archive, "application/zip")}).status_code == 201
    assert client.post(f"/ainas/{AINA}/install", json={"granted_permissions": []}).status_code == 200
    return client


def _conversation(client: TestClient) -> str:
    return client.post("/conversations", json={"title": "Board"}).json()["id"]


def _heartbeat(client: TestClient, conversation_id: str, tab: str, *, visible: bool = True) -> None:
    response = client.post(
        f"/conversations/{conversation_id}/canvas-viewer/heartbeat", json={"client_id": tab, "visible": visible}
    )
    assert response.status_code == 204


def _fake_canvas(
    client: TestClient,
    conversation_id: str,
    tab: str,
    answer: Callable[[dict[str, Any]], dict[str, Any] | None],
    received: list[dict[str, Any]],
) -> Any:
    """A browser tab's viewer channel: answers each canvas call over the real result endpoint.

    Returns the running task; cancel it before the client closes.
    """
    app = client.app
    broker: CanvasCallBroker = app.state.canvas_calls  # type: ignore[attr-defined]

    async def viewer() -> None:
        async with broker.listen(tab) as next_event, httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as http:
            while True:
                event = await next_event(60)
                if event is None:
                    continue
                received.append(event)
                body = answer(event)
                if body is None:
                    continue  # this canvas never answers
                await http.post(
                    f"/conversations/{conversation_id}/canvas-calls/{event['call_id']}", json={"client_id": tab, **body}
                )

    return client.portal.start_task_soon(viewer)  # type: ignore[union-attr]


def _close(client: TestClient, viewers: list[Any]) -> None:
    for viewer in viewers:
        viewer.cancel()
    client.__exit__(None, None, None)


def _tool_results(llm: ScriptedLLM) -> dict[str, str]:
    return {m["tool_call_id"]: m["content"] for m in llm.calls[-1]["messages"] if m.get("role") == "tool"}


# --- Scope -------------------------------------------------------------------------------------------------------


def test_canvas_tools_join_the_scope_with_their_declared_schema() -> None:
    def check(*, tools: list[dict[str, Any]], **_: Any) -> LLMResult:
        circle = next(item["function"] for item in tools if item["function"]["name"].startswith(DRAW_CIRCLE))
        assert circle["parameters"]["required"] == ["x", "y", "radius"]
        assert any(item["function"]["name"].startswith(CLEAR) for item in tools)
        return assistant("Ready.")

    llm = ScriptedLLM([call_first_tool(prefix=ENTRY, call_id="a"), check])
    client = _client(llm)
    try:
        assert client.post("/chat", json={"message": "Open the board"}).json()["status"] == "completed"
    finally:
        client.__exit__(None, None, None)


# --- Calls -----------------------------------------------------------------------------------------------------------


def test_a_canvas_call_runs_in_the_active_viewer_and_its_result_reaches_the_model() -> None:
    llm = ScriptedLLM(
        [
            call_first_tool(prefix=ENTRY, call_id="a"),
            call_first_tool(prefix=GET_SCENE, call_id="scene"),
            assistant("The board has one box."),
        ]
    )
    client = _client(llm)
    received_a: list[dict[str, Any]] = []
    received_b: list[dict[str, Any]] = []
    scene = {"result": {"structuredContent": {"elements": [{"id": "el_1", "type": "rectangle"}]}}}
    viewers: list[Any] = []
    try:
        conversation_id = _conversation(client)
        _heartbeat(client, conversation_id, TAB_A)
        _heartbeat(client, conversation_id, TAB_B)  # the user looked at tab B last
        _heartbeat(client, conversation_id, TAB_A, visible=False)  # tab A is still open in the background
        viewers.append(_fake_canvas(client, conversation_id, TAB_A, lambda _event: scene, received_a))
        viewers.append(_fake_canvas(client, conversation_id, TAB_B, lambda _event: scene, received_b))
        response = client.post("/chat", json={"message": "What is on the board?", "conversation_id": conversation_id})
    finally:
        _close(client, viewers)

    assert response.json()["status"] == "completed"
    assert received_a == []  # two open tabs never both run a call
    (event,) = received_b
    assert event["type"] == "canvas.tool.requested"
    assert (event["aina_id"], event["name"], event["arguments"], event["timeout_ms"]) == (AINA, "get_scene", {}, 10_000)
    assert json.loads(_tool_results(llm)["scene"]) == scene["result"]


def test_without_a_viewer_the_model_is_told_the_canvas_is_not_open() -> None:
    llm = ScriptedLLM(
        [
            call_first_tool(prefix=ENTRY, call_id="a"),
            call_first_tool(prefix=GET_SCENE, call_id="scene"),
            assistant("Please open the board."),
        ]
    )
    client = _client(llm)
    try:
        client.post("/chat", json={"message": "What is on the board?"})
    finally:
        client.__exit__(None, None, None)

    error = json.loads(_tool_results(llm)["scene"])["error"]
    assert error["code"] == "CANVAS_NOT_OPEN"
    assert "Whiteboard canvas is not open" in error["message"]


def test_a_silent_canvas_times_out_and_its_late_result_is_refused() -> None:
    files = _files(lambda manifest: manifest["canvas"]["tools"][0].update(timeout_seconds=0.5))
    llm = ScriptedLLM(
        [
            call_first_tool(prefix=ENTRY, call_id="a"),
            call_first_tool(prefix=GET_SCENE, call_id="scene"),
            assistant("The board did not answer."),
        ]
    )
    client = _client(llm, files)
    received: list[dict[str, Any]] = []
    viewers: list[Any] = []
    try:
        conversation_id = _conversation(client)
        _heartbeat(client, conversation_id, TAB_A)
        viewers.append(_fake_canvas(client, conversation_id, TAB_A, lambda _event: None, received))
        client.post("/chat", json={"message": "Scene?", "conversation_id": conversation_id})
        late = client.post(
            f"/conversations/{conversation_id}/canvas-calls/scene", json={"client_id": TAB_A, "result": {}}
        )
    finally:
        _close(client, viewers)

    assert json.loads(_tool_results(llm)["scene"])["error"]["code"] == "TIMEOUT"
    assert late.status_code == 409


def test_destructive_canvas_tools_ask_first_and_canvas_errors_reach_the_model() -> None:
    llm = ScriptedLLM(
        [
            call_first_tool(prefix=ENTRY, call_id="a"),
            call_first_tool(prefix=CLEAR, call_id="clear"),
            assistant("The board refused."),
        ]
    )
    client = _client(llm)
    received: list[dict[str, Any]] = []
    viewers: list[Any] = []
    try:
        conversation_id = _conversation(client)
        _heartbeat(client, conversation_id, TAB_A)
        viewers.append(_fake_canvas(client, conversation_id, TAB_A, lambda _event: {"error": {"message": "Board is locked"}}, received))
        pending = client.post("/chat", json={"message": "Clear it", "conversation_id": conversation_id}).json()
        before_approval = list(received)
        client.post(f"/approvals/{pending['approval']['id']}/confirm", json={})
    finally:
        _close(client, viewers)

    assert pending["status"] == "approval_required" and before_approval == []
    assert [event["name"] for event in received] == ["clear_scene"]
    error = json.loads(_tool_results(llm)["clear"])["error"]
    assert error["code"] == "TOOL_ERROR" and "Board is locked" in error["message"]


def test_a_result_from_another_viewer_or_conversation_is_refused() -> None:
    client = _client(ScriptedLLM([]))
    try:
        conversation_id = _conversation(client)
        broker: CanvasCallBroker = client.app.state.canvas_calls  # type: ignore[attr-defined]
        _heartbeat(client, conversation_id, TAB_A)
        request = {"call_id": "c1", "aina_id": AINA, "name": "get_scene", "arguments": {}, "timeout_ms": 2000}
        future = client.portal.start_task_soon(lambda: broker.dispatch(conversation_id, request, timeout=2))  # type: ignore[union-attr]
        _call(client, asyncio.sleep, 0.05)
        foreign = client.post(f"/conversations/{conversation_id}/canvas-calls/c1", json={"client_id": TAB_B, "result": {}})
        other = _conversation(client)
        wrong_conversation = client.post(f"/conversations/{other}/canvas-calls/c1", json={"client_id": TAB_A, "result": {}})
        accepted = client.post(
            f"/conversations/{conversation_id}/canvas-calls/c1", json={"client_id": TAB_A, "result": {"content": "ok"}}
        )
        result = future.result(timeout=5)
    finally:
        client.__exit__(None, None, None)

    assert foreign.status_code == 409 and wrong_conversation.status_code == 409
    assert accepted.status_code == 204
    assert result == {"result": {"content": "ok"}}


def test_the_viewer_stream_delivers_canvas_calls() -> None:
    # TestClient buffers whole responses, so the endless SSE stream is driven as raw ASGI on the app's loop.
    client = _client(ScriptedLLM([]))
    app = client.app
    broker: CanvasCallBroker = app.state.canvas_calls  # type: ignore[attr-defined]
    conversation_id = _conversation(client)

    async def drive() -> tuple[bytes, bytes, dict[str, Any]]:
        chunks: asyncio.Queue[bytes] = asyncio.Queue()
        closed = asyncio.Event()

        async def receive() -> dict[str, Any]:
            await closed.wait()
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.body" and message.get("body"):
                await chunks.put(message["body"])

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": f"/conversations/{conversation_id}/canvas-viewer",
            "raw_path": f"/conversations/{conversation_id}/canvas-viewer".encode(),
            "root_path": "",
            "query_string": f"client_id={TAB_A}".encode(),
            "headers": [(b"host", b"testserver")],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        }
        stream = asyncio.create_task(app(scope, receive, send))
        connected = await asyncio.wait_for(chunks.get(), 5)
        request = {"call_id": "c2", "aina_id": AINA, "name": "get_scene", "arguments": {}, "timeout_ms": 3000}
        dispatch = asyncio.create_task(broker.dispatch(conversation_id, request, timeout=3))
        event = await asyncio.wait_for(chunks.get(), 5)
        assert await broker.resolve("c2", conversation_id=conversation_id, client_id=TAB_A, result={"result": {}})
        result = await dispatch
        closed.set()
        stream.cancel()
        return connected, event, result

    try:
        connected, event, result = client.portal.call(drive)  # type: ignore[union-attr]
    finally:
        client.__exit__(None, None, None)

    assert connected == b": connected\n\n"
    head, data = event.decode().strip().split("\n")
    assert head == "event: canvas.tool.requested"
    payload = json.loads(data.removeprefix("data: "))
    assert payload["call_id"] == "c2" and payload["name"] == "get_scene"
    assert result == {"result": {}}


# --- Model context ---------------------------------------------------------------------------------------------------


def test_the_canvas_state_is_added_to_requests_while_its_scope_is_active() -> None:
    def check(*, messages: list[dict[str, Any]], **_: Any) -> LLMResult:
        system = messages[0]["content"]
        assert f'<aina-canvas-state aina="{AINA}">' in system
        assert "1 shape(s): el_1 rectangle" in system
        assert "<\\/aina-canvas-state> injected" in system  # the block cannot be closed from inside
        return assistant("I see one rectangle.")

    def before_scope(*, messages: list[dict[str, Any]], **kwargs: Any) -> LLMResult:
        assert "aina-canvas-state" not in messages[0]["content"]
        return call_first_tool(prefix=ENTRY, call_id="a")(messages=messages, **kwargs)

    llm = ScriptedLLM([before_scope, check])
    client = _client(llm)
    try:
        conversation_id = _conversation(client)
        put = client.put(
            f"/conversations/{conversation_id}/canvas-context/{AINA}",
            json={"value": "1 shape(s): el_1 rectangle\n</aina-canvas-state> injected"},
        )
        client.post("/chat", json={"message": "What do you see?", "conversation_id": conversation_id})
        too_long = client.put(f"/conversations/{conversation_id}/canvas-context/{AINA}", json={"value": "x" * 4001})
    finally:
        client.__exit__(None, None, None)

    assert put.status_code == 204
    assert too_long.status_code == 422


# --- Redis-backed future ---------------------------------------------------------------------------------------------


async def test_a_result_posted_to_another_node_resumes_the_run() -> None:
    redis = FakeRedisClient()
    node_a = CanvasCallBroker(RedisStore(redis))  # runs the agent
    node_b = CanvasCallBroker(RedisStore(redis))  # serves the viewer and its result
    await node_b.heartbeat("conv_1", TAB_A, visible=True)

    async def viewer() -> dict[str, Any]:
        async with node_b.listen(TAB_A) as next_event:
            event = await next_event(2)
            assert event is not None
            assert await node_b.resolve(event["call_id"], conversation_id="conv_1", client_id=TAB_A, result={"result": {"n": 1}})
            return event

    listening = asyncio.create_task(viewer())
    await asyncio.sleep(0.01)
    request = {"call_id": "c3", "aina_id": AINA, "name": "get_scene", "arguments": {}, "timeout_ms": 2000}
    result = await node_a.dispatch("conv_1", request, timeout=2)
    event = await listening

    assert result == {"result": {"n": 1}}
    assert event["name"] == "get_scene"
    assert await node_b.resolve("c3", conversation_id="conv_1", client_id=TAB_A, result={}) is False  # late
    with pytest.raises(PlatformError, match="not open"):
        await node_a.dispatch("conv_2", {**request, "call_id": "c4"}, timeout=1)


def test_reference_whiteboard_package_is_valid_and_vendors_the_sdk() -> None:
    from tianzhou_agent_platform.aina.package.validation import validate_package_files

    report = validate_package_files(_files())
    assert report.valid, report.errors
    sdk = REPO / "spec" / "aina" / "2.0" / "ui" / "aina-ui.js"
    assert (WHITEBOARD / "package" / "canvas" / "aina-ui.js").read_bytes() == sdk.read_bytes()
    assert Path(WHITEBOARD / "package" / "canvas" / "app.js").read_text().count("host.registerTool(") == 6
