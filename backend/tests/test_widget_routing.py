from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from fastapi.testclient import TestClient

from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.main import create_app
from tests.support.fake_llm import ScriptedLLM, assistant, call_first_tool


def _settings() -> AgentSettings:
    return AgentSettings(
        _env_file=None,
        llm_base_url="https://model.invalid/v1",
        llm_api_key="test-key",
        llm_model="test-model",
    )


def _manifest(*, aina_id: str = "com.example.canvas", linked_tool: str | None = None) -> dict[str, Any]:
    tools = []
    if linked_tool:
        tools.append(
            {
                "id": linked_tool,
                "name": "Linked report tool",
                "description": "Create the report data used by this AINA.",
                "input_schema": {"type": "object"},
            }
        )
    return {
        "protocol_version": "1.0",
        "aina": {
            "id": aina_id,
            "name": "Canvas Report AINA",
            "version": "1.2.0",
            "description": "Creates structured project status reports.",
            "publisher": {"id": "tests", "name": "Tests"},
        },
        "runtime": {
            "type": "remote",
            "endpoint": "https://cap.invalid/aina",
            "streaming": False,
            "async_tasks": False,
        },
        "capabilities": {
            "skills": [
                {
                    "id": "status-report",
                    "name": "Status report",
                    "description": "Turn project notes into a status report.",
                    "instructions": "Use a concise summary followed by risks and next steps.",
                    "input_schema": {"type": "object"},
                }
            ],
            "tools": tools,
            "ui": [],
            "events": [],
        },
        "main_widget": {
            "id": "report-main",
            "kind": "form",
            "title": "Project report",
            "description": "Build a report from project notes.",
            "fields": [
                {
                    "id": "notes",
                    "label": "Project notes",
                    "input_type": "textarea",
                    "placeholder": "Paste notes",
                    "required": True,
                }
            ],
            "actions": [
                {
                    "id": "generate",
                    "label": "Generate report",
                    "kind": "prompt",
                    "prompt": "Create a status report from: {notes}",
                }
            ],
        },
        "permissions": [],
        "authentication": {"type": "none"},
    }


def _remote(
    *,
    widget_output: bool = False,
    invoked: list[dict[str, Any]] | None = None,
) -> Callable[[httpx.Request], Awaitable[httpx.Response]]:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/describe"):
            return httpx.Response(200, json={"protocol_version": "1.0"})
        if request.url.path.endswith("/health"):
            return httpx.Response(200, json={"status": "healthy"})
        if request.url.path.endswith("/invoke"):
            payload = json.loads(request.content)
            if invoked is not None:
                invoked.append(payload)
            outputs: list[dict[str, Any]] = [{"type": "text", "content": "Report created"}]
            if widget_output:
                outputs.append(
                    {
                        "type": "widget",
                        "content": {
                            "id": "report-result",
                            "kind": "markdown",
                            "title": "Generated report",
                            "markdown": "## On track\n\n- Risk: none",
                        },
                    }
                )
            return httpx.Response(
                200,
                json={
                    "request_id": payload["request_id"],
                    "status": "completed",
                    "outputs": outputs,
                    "trace_id": payload["trace"]["trace_id"],
                },
            )
        if request.url.path == "/tool":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404)

    return handler


def test_list_app_builtin_persists_an_interactive_widget() -> None:
    llm = ScriptedLLM(
        [
            call_first_tool(prefix="builtin_list_app_"),
            assistant("## 可用应用\n\n请选择一个应用。"),
        ]
    )
    with TestClient(create_app(settings=_settings(), llm=llm)) as client:
        response = client.post("/chat", json={"message": "请列出应用"})
        conversation = client.get(f"/conversations/{response.json()['conversation_id']}")
        trace = client.get(f"/traces/{response.json()['trace_id']}")

    assert response.status_code == 200
    widget = response.json()["widgets"][0]
    assert widget["kind"] == "app_list"
    assert [item["aina_id"] for item in widget["apps"]] == ["unibot-assistant", "unibot-memory"]
    assert conversation.json()["messages"][-1]["widgets"] == response.json()["widgets"]
    assert any(event["kind"] == "builtin.completed" for event in trace.json()["events"])
    assert llm.calls[0]["tool_choice"]["function"]["name"].startswith("builtin_list_app_")


def test_clarification_builtin_returns_a_host_rendered_prefilled_form() -> None:
    llm = ScriptedLLM(
        [
            call_first_tool(
                prefix="builtin_request_clarification_",
                arguments=json.dumps(
                    {
                        "title": "Clarify the report",
                        "description": "Add the missing scope.",
                        "fields": [
                            {
                                "id": "audience",
                                "label": "Audience",
                                "input_type": "text",
                                "required": True,
                                "value": "Leadership",
                            },
                            {
                                "id": "period",
                                "label": "Reporting period",
                                "input_type": "text",
                                "required": True,
                            },
                        ],
                    }
                ),
            ),
            assistant("Please complete the clarification form."),
        ]
    )
    with TestClient(create_app(settings=_settings(), llm=llm)) as client:
        response = client.post(
            "/chat",
            json={
                "message": "Create a report",
                "capability": "builtin:request_clarification",
            },
        )
        conversation = client.get(f"/conversations/{response.json()['conversation_id']}")

    assert response.status_code == 200
    widget = response.json()["widgets"][0]
    assert widget["kind"] == "form"
    assert widget["fields"][0]["value"] == "Leadership"
    assert widget["actions"][0]["prompt"].startswith("以下是我的补充信息")
    assert conversation.json()["messages"][-1]["widgets"] == [widget]


def test_aina_ui_declaration_loads_the_matching_host_widget_capability() -> None:
    manifest = _manifest()
    manifest["capabilities"]["ui"] = [
        {
            "id": "report-clarification",
            "kind": "form",
            "description": "Collect missing report parameters.",
            "instructions": "Ask only for audience and reporting period.",
        }
    ]
    llm = ScriptedLLM(
        [
            call_first_tool(prefix="aina_", arguments='{"input":"Create a report"}'),
            assistant("The report is ready."),
        ]
    )
    capability_client = httpx.AsyncClient(transport=httpx.MockTransport(_remote()))
    with TestClient(create_app(settings=_settings(), llm=llm, capability_http_client=capability_client)) as client:
        registered = client.post("/ainas", json=manifest)
        client.post("/ainas/com.example.canvas/install", json={})
        response = client.post(
            "/chat",
            json={"message": "Create a report", "capability": "aina:com.example.canvas"},
        )

    assert registered.status_code == 201
    assert response.status_code == 200
    tool_names = [item["function"]["name"] for item in llm.calls[0]["tools"]]
    assert any(name.startswith("builtin_request_clarification_") for name in tool_names)
    assert "Host-rendered AINA UI" in llm.calls[0]["messages"][0]["content"]


def test_open_aina_returns_canvas_and_declared_main_widget() -> None:
    capability_client = httpx.AsyncClient(transport=httpx.MockTransport(_remote()))
    with TestClient(
        create_app(settings=_settings(), llm=ScriptedLLM([]), capability_http_client=capability_client)
    ) as client:
        client.post("/ainas", json=_manifest())
        client.post("/ainas/com.example.canvas/install", json={})
        response = client.post(
            "/ainas/com.example.canvas/open",
            json={"conversation_id": "conv_existing"},
        )

    assert response.status_code == 200
    assert response.json()["route"] == "/canvas/com.example.canvas?conversation=conv_existing"
    assert response.json()["main_widget"]["id"] == "report-main"
    assert response.json()["main_widget"]["actions"][0]["kind"] == "prompt"


def test_open_aina_builtin_returns_navigation_widget_through_agent() -> None:
    llm = ScriptedLLM(
        [
            call_first_tool(
                prefix="builtin_open_aina_",
                arguments='{"aina_id":"unibot-memory"}',
            ),
            assistant("Unibot Memory is ready to open."),
        ]
    )
    with TestClient(create_app(settings=_settings(), llm=llm)) as client:
        response = client.post(
            "/chat",
            json={
                "message": "Open the Unibot Memory application",
                "capability": "builtin:open_aina",
            },
        )
        trace = client.get(f"/traces/{response.json()['trace_id']}")

    assert response.status_code == 200
    widget = response.json()["widgets"][0]
    assert widget["kind"] == "navigation"
    assert widget["actions"][0]["kind"] == "open_aina"
    assert widget["actions"][0]["aina_id"] == "unibot-memory"
    assert len(llm.calls[0]["tools"]) == 1
    assert llm.calls[0]["tools"][0]["function"]["name"].startswith("builtin_open_aina_")
    assert any(
        event["kind"] == "builtin.completed" and event["target_id"] == "open_aina"
        for event in trace.json()["events"]
    )


def test_routing_checks_aina_first_then_loads_only_its_declared_capabilities() -> None:
    invoked: list[dict[str, Any]] = []
    llm = ScriptedLLM(
        [
            call_first_tool(prefix="aina_", arguments='{"input":"Create a project status report"}'),
            assistant("The project report is ready."),
        ]
    )
    capability_client = httpx.AsyncClient(transport=httpx.MockTransport(_remote(invoked=invoked)))
    with TestClient(create_app(settings=_settings(), llm=llm, capability_http_client=capability_client)) as client:
        client.post(
            "/tools",
            json={
                "tool_id": "report.data",
                "name": "Report data",
                "description": "Create report data.",
                "input_schema": {"type": "object"},
                "endpoint": "https://cap.invalid/tool",
            },
        )
        client.post("/ainas", json=_manifest(linked_tool="report.data"))
        client.post("/ainas/com.example.canvas/install", json={})
        response = client.post("/chat", json={"message": "Create a project status report"})
        trace = client.get(f"/traces/{response.json()['trace_id']}")

    assert response.status_code == 200
    assert len(llm.calls[0]["tools"]) == 1
    assert llm.calls[0]["tools"][0]["function"]["name"].startswith("aina_")
    scoped_names = [item["function"]["name"] for item in llm.calls[1]["tools"]]
    assert any(name.startswith("aina_") for name in scoped_names)
    assert any(name.startswith("tool_report_data_") for name in scoped_names)
    assert not any(name.startswith("builtin_") for name in scoped_names)
    assert "Use a concise summary followed by risks and next steps." in llm.calls[1]["messages"][0]["content"]
    assert invoked[0]["input"]["input"] == "Create a project status report"
    assert any(
        event["kind"] == "routing.aina.completed" and event["target_id"] == "com.example.canvas"
        for event in trace.json()["events"]
    )
    discovery = next(
        event for event in trace.json()["events"] if event["kind"] == "capability.discovery"
    )["details"]
    remote_aina = next(
        item for item in discovery["aina_graph"]["available"] if item["id"] == "com.example.canvas"
    )
    assert discovery["aina_graph"]["counts"] == {"builtin_aina": 2, "remote_aina": 1}
    assert discovery["model_scope"]["counts"] == {
        "remote_tool": 1,
        "remote_aina": 1,
        "builtin_capability": 0,
    }
    assert remote_aina["availability"] == "installed"
    assert remote_aina["routing_candidate"] is True
    assert remote_aina["entrypoint"]["owner_aina_id"] == "com.example.canvas"
    linked_tool = next(item for item in remote_aina["capabilities"]["tools"] if item["id"] == "report.data")
    assert linked_tool["model_exposed"] is True
    scoped = next(
        item for item in discovery["model_scope"]["by_aina"] if item["aina_id"] == "com.example.canvas"
    )
    assert {item["id"] for item in scoped["capabilities"]} == {"com.example.canvas", "report.data"}


def test_trace_graph_explains_why_a_remote_aina_is_unavailable() -> None:
    llm = ScriptedLLM([assistant("Ordinary response.")])
    capability_client = httpx.AsyncClient(transport=httpx.MockTransport(_remote()))
    with TestClient(create_app(settings=_settings(), llm=llm, capability_http_client=capability_client)) as client:
        client.post("/ainas", json=_manifest())
        response = client.post("/chat", json={"message": "Hello"})
        trace = client.get(f"/traces/{response.json()['trace_id']}").json()

    discovery = next(event for event in trace["events"] if event["kind"] == "capability.discovery")["details"]
    excluded = next(
        item for item in discovery["aina_graph"]["excluded"] if item["id"] == "com.example.canvas"
    )
    assert excluded == {
        "id": "com.example.canvas",
        "name": "Canvas Report AINA",
        "runtime": "remote",
        "reason": "not_installed",
        "missing_permissions": [],
    }


def test_routing_falls_back_to_system_tools_when_no_aina_matches() -> None:
    llm = ScriptedLLM(
        [
            assistant("NO_AINA_MATCH"),
            call_first_tool(prefix="tool_report_data_"),
            assistant("The system tool completed."),
        ]
    )
    capability_client = httpx.AsyncClient(transport=httpx.MockTransport(_remote()))
    with TestClient(create_app(settings=_settings(), llm=llm, capability_http_client=capability_client)) as client:
        client.post(
            "/tools",
            json={
                "tool_id": "report.data",
                "name": "Report data",
                "description": "Read platform report data.",
                "input_schema": {"type": "object"},
                "endpoint": "https://cap.invalid/tool",
            },
        )
        client.post("/ainas", json=_manifest())
        client.post("/ainas/com.example.canvas/install", json={})
        response = client.post("/chat", json={"message": "Read the platform report data"})

    assert response.status_code == 200
    assert all(item["function"]["name"].startswith("aina_") for item in llm.calls[0]["tools"])
    fallback_names = [item["function"]["name"] for item in llm.calls[1]["tools"]]
    assert any(name.startswith("tool_report_data_") for name in fallback_names)
    assert any(name.startswith("builtin_list_app_") for name in fallback_names)
    assert not any(name.startswith("aina_") for name in fallback_names)
    assert "Unibot Assistant" in llm.calls[1]["messages"][0]["content"]


def test_aina_widget_output_is_returned_and_persisted() -> None:
    llm = ScriptedLLM(
        [
            call_first_tool(prefix="aina_", arguments='{"input":"Create the report"}'),
            assistant("The report widget is ready."),
        ]
    )
    capability_client = httpx.AsyncClient(transport=httpx.MockTransport(_remote(widget_output=True)))
    with TestClient(create_app(settings=_settings(), llm=llm, capability_http_client=capability_client)) as client:
        client.post("/ainas", json=_manifest())
        client.post("/ainas/com.example.canvas/install", json={})
        response = client.post(
            "/chat",
            json={"message": "Create the report", "capability": "aina:com.example.canvas"},
        )
        conversation = client.get(f"/conversations/{response.json()['conversation_id']}")

    assert response.json()["widgets"][0]["id"] == "report-result"
    assert response.json()["widgets"][0]["kind"] == "markdown"
    assert conversation.json()["messages"][-1]["widgets"][0]["markdown"].startswith("## On track")
