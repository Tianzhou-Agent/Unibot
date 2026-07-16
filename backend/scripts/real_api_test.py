from __future__ import annotations

import os
import socket
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator, cast
from uuid import uuid4

import httpx
import uvicorn
from fastapi import FastAPI


def create_demo_runtime() -> FastAPI:
    demo = FastAPI()

    @demo.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @demo.post("/tool/add")
    async def add(payload: dict[str, Any]) -> dict[str, int]:
        arguments = payload["arguments"]
        return {"result": int(arguments["a"]) + int(arguments["b"])}

    @demo.get("/aina/describe")
    async def describe() -> dict[str, Any]:
        return {"protocol_version": "1.0", "capabilities": {"skills": ["multiply"]}}

    @demo.get("/aina/health")
    async def aina_health() -> dict[str, str]:
        return {"status": "healthy", "version": "1.0.0"}

    @demo.post("/aina/invoke")
    async def invoke(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "request_id": payload["request_id"],
            "status": "completed",
            "outputs": [
                {"type": "text", "content": "The deterministic multiplication result is 42."},
                {
                    "type": "widget",
                    "content": {
                        "id": "smoke-result",
                        "kind": "markdown",
                        "title": "Multiplication result",
                        "markdown": "## Result\n\nThe product is **42**.",
                    },
                },
            ],
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "trace_id": payload["trace"]["trace_id"],
        }

    return demo


@contextmanager
def demo_server() -> Iterator[str]:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_demo_runtime(), host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{url}/health", timeout=0.5).status_code == 200:
                break
        except httpx.RequestError:
            time.sleep(0.05)
    else:
        server.should_exit = True
        thread.join(timeout=5)
        raise RuntimeError("The local demo capability server did not start")
    try:
        yield url
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def assert_ok(response: httpx.Response) -> dict[str, Any]:
    if response.is_error:
        raise AssertionError(f"HTTP {response.status_code}: {response.text}")
    return cast(dict[str, Any], response.json())


def assert_trace_has(client: httpx.Client, trace_id: str, event_kind: str) -> None:
    trace = assert_ok(client.get(f"/traces/{trace_id}"))
    if not any(event["kind"] == event_kind for event in trace["events"]):
        raise AssertionError(f"Trace {trace_id} does not contain {event_kind!r}")


def main() -> None:
    service_url = os.getenv("UNIBOT_API_URL", "http://127.0.0.1:8000")
    suffix = uuid4().hex[:10]
    with demo_server() as demo_url, httpx.Client(base_url=service_url, timeout=120) as client:
        assert_ok(client.get("/health"))

        direct = assert_ok(
            client.post(
                "/chat",
                json={"message": "Reply with exactly UNIBOT_REAL_API_OK and no other text."},
            )
        )
        if "UNIBOT_REAL_API_OK" not in direct["content"]:
            raise AssertionError(f"Unexpected direct model response: {direct['content']!r}")

        memory_token = f"UNIBOT_MEMORY_{suffix}"
        remembered = assert_ok(
            client.post(
                "/chat",
                json={
                    "message": (
                        f"Use the memory app to remember this exact fact: my private test token is {memory_token}."
                    ),
                    "capability": "aina:unibot-memory",
                },
            )
        )
        assert_trace_has(client, remembered["trace_id"], "builtin.completed")
        stored_memories = assert_ok(client.get("/memories", params={"q": memory_token}))
        if not any(memory_token in item["content"] for item in stored_memories["items"]):
            raise AssertionError("The real model did not persist the requested memory")

        profession_marker = f"SOFTWARE_ENGINEER_{suffix}"
        profession = assert_ok(
            client.post(
                "/chat",
                json={
                    "message": f"My profession is software engineer. Durable marker: {profession_marker}.",
                    "conversation_id": remembered["conversation_id"],
                },
            )
        )
        assert_trace_has(client, profession["trace_id"], "builtin.completed")
        stored_profession = assert_ok(client.get("/memories", params={"q": profession_marker}))
        if not any(profession_marker in item["content"] for item in stored_profession["items"]):
            raise AssertionError("The follow-up durable fact could not reuse the memory capability")

        recalled = assert_ok(
            client.post(
                "/chat",
                json={
                    "message": "What is my private test token? Return the token exactly.",
                    "capability": "aina:unibot-memory",
                },
            )
        )
        if memory_token not in recalled["content"]:
            raise AssertionError(f"Memory AINA did not recall the stored token: {recalled['content']!r}")

        clarification = assert_ok(
            client.post(
                "/chat",
                json={
                    "message": (
                        "The request is ambiguous. Ask me for project name and deadline using the host "
                        "clarification form. Prefill the project name as Unibot."
                    ),
                    "capability": "builtin:request_clarification",
                },
            )
        )
        forms = [widget for widget in clarification["widgets"] if widget["kind"] == "form"]
        if not forms or len(forms[0]["fields"]) < 2:
            raise AssertionError("The real model did not produce a host-rendered clarification form")
        if not any(field.get("value") == "Unibot" for field in forms[0]["fields"]):
            raise AssertionError("The clarification form did not preserve the model-provided prefilled value")

        tool_id = f"smoke.add.{suffix}"
        assert_ok(
            client.post(
                "/tools",
                json={
                    "tool_id": tool_id,
                    "name": "Smoke test addition",
                    "description": "Add two integers using the deterministic smoke-test service.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                        "required": ["a", "b"],
                        "additionalProperties": False,
                    },
                    "output_schema": {
                        "type": "object",
                        "properties": {"result": {"type": "integer"}},
                        "required": ["result"],
                    },
                    "endpoint": f"{demo_url}/tool/add",
                },
            )
        )
        tool_chat = assert_ok(
            client.post(
                "/chat",
                json={
                    "message": "Use the selected capability to add 17 and 25, then report the numeric result.",
                    "capability": f"tool:{tool_id}",
                },
            )
        )
        assert_trace_has(client, tool_chat["trace_id"], "tool.completed")
        if "42" not in tool_chat["content"]:
            raise AssertionError(f"Tool loop did not report 42: {tool_chat['content']!r}")

        follow_up = assert_ok(
            client.post(
                "/chat",
                json={
                    "message": "What numeric result did the tool return? Reply only with digits.",
                    "conversation_id": tool_chat["conversation_id"],
                },
            )
        )
        if "42" not in follow_up["content"]:
            raise AssertionError(f"Multi-turn context was not retained: {follow_up['content']!r}")

        aina_id = f"com.example.smoke.{suffix}"
        assert_ok(
            client.post(
                "/ainas",
                json={
                    "protocol_version": "1.0",
                    "aina": {
                        "id": aina_id,
                        "name": "Smoke Arithmetic AINA",
                        "version": "1.0.0",
                        "description": "Returns a deterministic multiplication result for smoke testing.",
                        "publisher": {"id": "smoke-tests", "name": "Smoke Tests"},
                    },
                    "runtime": {
                        "type": "remote",
                        "endpoint": f"{demo_url}/aina",
                        "streaming": False,
                        "async_tasks": False,
                    },
                    "capabilities": {
                        "skills": [
                            {
                                "id": "multiply",
                                "name": "Multiply",
                                "description": "Multiply two numbers.",
                                "input_schema": {"type": "object"},
                            }
                        ],
                        "tools": [],
                        "ui": [],
                        "events": [],
                    },
                    "main_widget": {
                        "id": "smoke-arithmetic-main",
                        "kind": "form",
                        "title": "Multiply integers",
                        "description": "Enter two integers and send the generated request to this AINA.",
                        "fields": [
                            {
                                "id": "left",
                                "label": "Left integer",
                                "input_type": "number",
                                "placeholder": "6",
                                "required": True,
                            },
                            {
                                "id": "right",
                                "label": "Right integer",
                                "input_type": "number",
                                "placeholder": "7",
                                "required": True,
                            },
                        ],
                        "actions": [
                            {
                                "id": "multiply",
                                "label": "Multiply",
                                "kind": "prompt",
                                "prompt": "Multiply {left} by {right}.",
                            }
                        ],
                    },
                    "permissions": [],
                    "authentication": {"type": "none"},
                },
            )
        )
        assert_ok(client.post(f"/ainas/{aina_id}/install", json={}))
        aina_chat = assert_ok(
            client.post(
                "/chat",
                json={
                    "message": (
                        "Use the installed Smoke Arithmetic AINA, whose description matches deterministic "
                        "multiplication, to multiply 6 by 7 and report its result."
                    ),
                },
            )
        )
        assert_trace_has(client, aina_chat["trace_id"], "aina.completed")
        if "42" not in aina_chat["content"]:
            raise AssertionError(f"AINA loop did not report 42: {aina_chat['content']!r}")
        if not aina_chat["widgets"] or aina_chat["widgets"][0]["id"] != "smoke-result":
            raise AssertionError("AINA widget output was not returned by /chat")

        app_list = assert_ok(client.post("/chat", json={"message": "列出我可以使用的 AINA 应用"}))
        listed_ids = {
            app["aina_id"]
            for widget in app_list["widgets"]
            if widget["kind"] == "app_list"
            for app in widget["apps"]
        }
        if aina_id not in listed_ids or "unibot-assistant" not in listed_ids:
            raise AssertionError(f"list_app widget did not include expected AINAs: {sorted(listed_ids)}")

        canvas = assert_ok(
            client.post(
                f"/ainas/{aina_id}/open",
                json={"conversation_id": aina_chat["conversation_id"]},
            )
        )
        if canvas["main_widget"]["id"] != "smoke-arithmetic-main":
            raise AssertionError("open_aina did not return the declared main_widget")
        if not canvas["route"].startswith(f"/canvas/{aina_id}"):
            raise AssertionError(f"open_aina returned an unexpected route: {canvas['route']!r}")

        with client.stream("POST", "/chat/stream", json={"message": "Reply with the word streamed."}) as stream:
            stream.raise_for_status()
            stream_body = "".join(stream.iter_text())
        if "event: message.delta" not in stream_body or "event: message.completed" not in stream_body:
            raise AssertionError("The SSE endpoint did not emit both delta and completion events")

        print(
            "Real API smoke test passed: direct chat, Memory AINA remember/recall/follow-up persistence, "
            "host clarification form, "
            "multi-turn context, remote Tool loop, "
            "automatic AINA routing, remote Widget output, list_app, open_aina Canvas, trace lookup, "
            "and SSE streaming."
        )


if __name__ == "__main__":
    main()
