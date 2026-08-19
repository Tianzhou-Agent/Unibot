from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi.testclient import TestClient
from mcp.client import Client
from mcp.server import MCPServer
from pydantic import SecretStr

from tianzhou_agent_platform.aina.mcp.client import McpCapabilityClient
from tianzhou_agent_platform.aina.mcp.models import McpServerCreate
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.main import create_app
from tests.support.fake_llm import ScriptedLLM, assistant, call_first_tool


class InProcessMcpClient(McpCapabilityClient):
    def __init__(self, server: MCPServer) -> None:
        self.server = server

    @asynccontextmanager
    async def _connect(self, server: McpServerCreate) -> AsyncIterator[Client]:
        del server
        async with Client(self.server, raise_exceptions=True) as client:
            yield client


def _server() -> MCPServer:
    server = MCPServer("calculator", version="1.0.0", instructions="Use exact arithmetic.")

    @server.tool(description="Add two integers with the standard MCP calculator")
    def add(a: int, b: int) -> dict[str, int]:
        return {"total": a + b}

    return server


def _settings() -> AgentSettings:
    return AgentSettings(  # type: ignore[call-arg]
        _env_file=None,
        llm_base_url="https://model.invalid/v1",
        llm_api_key=SecretStr("test-key"),
        llm_model="test-model",
    )


async def test_official_mcp_client_discovers_and_calls_in_process_server() -> None:
    client = InProcessMcpClient(_server())
    record = await client.discover(
        McpServerCreate.model_validate(
            {
                "server_id": "calculator",
                "name": "Calculator",
                "endpoint": "http://mcp.invalid/mcp",
            }
        )
    )

    assert record.protocol_version is not None
    assert record.server_name == "calculator"
    assert [tool.name for tool in record.tools] == ["add"]
    assert record.tools[0].input_schema["required"] == ["a", "b"]

    result, duration_ms = await client.call_tool(record, record.tools[0], {"a": 17, "b": 25})

    assert result == {"total": 42}
    assert duration_ms >= 0


def test_mcp_server_registration_exposes_tool_to_agent_loop() -> None:
    llm = ScriptedLLM(
        [
            call_first_tool(
                prefix="mcp_",
                arguments='{"a":17,"b":25}',
                description_contains="standard MCP calculator",
            ),
            assistant("The MCP result is 42."),
        ]
    )
    mcp_client = InProcessMcpClient(_server())
    app = create_app(settings=_settings(), llm=llm, mcp_capability_client=mcp_client)

    with TestClient(app) as client:
        registered = client.post(
            "/mcp-servers",
            json={
                "server_id": "calculator",
                "name": "Calculator",
                "endpoint": "http://mcp.invalid/mcp",
            },
        )
        response = client.post("/chat", json={"message": "Add 17 and 25 using MCP"})
        listed = client.get("/mcp-servers")

    assert registered.status_code == 201
    assert registered.json()["tools"][0]["name"] == "add"
    assert listed.status_code == 200
    assert response.status_code == 200
    assert response.json()["content"] == "The MCP result is 42."
    tool_message = next(message for message in llm.calls[1]["messages"] if message["role"] == "tool")
    assert '"total": 42' in tool_message["content"]


def test_mcp_registry_uses_admin_destination_and_visibility_boundaries() -> None:
    settings = _settings()
    app = create_app(settings=settings, mcp_capability_client=InProcessMcpClient(_server()), enforce_auth=True)
    payload = {
        "server_id": "calculator",
        "name": "Calculator",
        "endpoint": "http://mcp.invalid/mcp",
        "visibility": "private",
    }
    with TestClient(app) as client:
        owner = client.post("/auth/register", json={
            "email": "mcp-owner@example.com", "name": "Owner", "password": "correct-horse-battery",
        }).json()["user"]
        assert client.post("/mcp-servers", json=payload).status_code == 403
        settings.admin_identities = owner["id"]
        assert client.post("/mcp-servers", json=payload).status_code == 403
        settings.capability_allowed_origins = "http://mcp.invalid"
        registered = client.post("/mcp-servers", json=payload)
        assert registered.status_code == 201
        assert registered.json()["owner_user_id"] == owner["id"]
        settings.admin_identities = ""
        assert client.get("/mcp-servers/calculator").status_code == 200
        assert client.post("/mcp-servers/calculator/refresh").status_code == 403
        assert client.delete("/mcp-servers/calculator").status_code == 403
        client.post("/auth/register", json={
            "email": "mcp-other@example.com", "name": "Other", "password": "correct-horse-battery",
        })
        assert client.get("/mcp-servers").json() == []
        assert client.get("/mcp-servers/calculator").status_code == 404


def test_high_risk_mcp_tool_resumes_through_native_approval_checkpoint() -> None:
    llm = ScriptedLLM([
        call_first_tool(prefix="mcp_", arguments='{"a":17,"b":25}', description_contains="standard MCP calculator"),
        assistant("The approved MCP result is 42."),
    ])
    app = create_app(settings=_settings(), llm=llm, mcp_capability_client=InProcessMcpClient(_server()))
    with TestClient(app) as client:
        assert client.post("/mcp-servers", json={
            "server_id": "calculator", "name": "Calculator", "endpoint": "http://mcp.invalid/mcp",
            "side_effect_level": "high",
        }).status_code == 201
        pending = client.post("/chat", json={
            "message": "Add 17 and 25", "capability": "mcp:calculator:add",
        }).json()
        assert pending["status"] == "approval_required"
        assert len(llm.calls) == 1
        confirmed = client.post(f"/approvals/{pending['approval']['id']}/confirm", json={})
        assert confirmed.status_code == 200
        assert confirmed.json()["content"] == "The approved MCP result is 42."
        tool_message = next(message for message in llm.calls[1]["messages"] if message["role"] == "tool")
        assert '"total": 42' in tool_message["content"]
