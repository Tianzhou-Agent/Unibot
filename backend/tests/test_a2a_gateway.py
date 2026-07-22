from __future__ import annotations

import json

import httpx
import pytest

from tianzhou_agent_platform.aina.gateway import RemoteCapabilityGateway
from tianzhou_agent_platform.aina.protocol.models import AinaInstallation, AinaManifest
from tianzhou_agent_platform.config import AgentSettings


@pytest.mark.asyncio
async def test_a2a_runtime_uses_agent_card_and_send_message() -> None:
    requests: list[httpx.Request] = []
    agent_card = {
        "name": "Report Agent",
        "description": "Creates reports",
        "supportedInterfaces": [
            {
                "url": "https://a2a.invalid",
                "protocolBinding": "JSONRPC",
                "protocolVersion": "1.0",
            }
        ],
        "version": "1.0",
        "capabilities": {"streaming": False},
        "defaultInputModes": ["application/json"],
        "defaultOutputModes": ["application/json"],
        "skills": [
            {
                "id": "report",
                "name": "Report",
                "description": "Creates reports",
                "tags": ["report"],
            }
        ],
    }

    async def remote(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=agent_card)
        payload = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {
                    "task": {
                        "id": "task-1",
                        "contextId": "conversation-1",
                        "status": {"state": "TASK_STATE_COMPLETED"},
                        "artifacts": [
                            {
                                "artifactId": "artifact-1",
                                "parts": [
                                    {
                                        "data": {"answer": 42},
                                        "mediaType": "application/json",
                                    }
                                ],
                            }
                        ],
                    }
                },
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(remote))
    gateway = RemoteCapabilityGateway(AgentSettings(_env_file=None), http_client)
    manifest = AinaManifest.model_validate(
        {
            "protocol_version": "1.0",
            "aina": {
                "id": "com.example.a2a",
                "name": "A2A Report",
                "version": "1.0.0",
                "description": "A remote A2A report agent",
                "publisher": {"id": "tests", "name": "Tests"},
            },
            "runtime": {
                "type": "remote",
                "protocol": "a2a",
                "endpoint": "https://a2a.invalid",
            },
            "authentication": {"type": "none"},
        }
    )
    installation = AinaInstallation(aina_id=manifest.aina.id, installed_version="1.0.0")

    health = await gateway.probe_aina(manifest)
    response, _ = await gateway.invoke_aina(
        manifest,
        installation,
        arguments={"question": "six times seven"},
        call_id="call-1",
        conversation_id="conversation-1",
        trace_id="trace-1",
        available_tools=[],
    )
    await http_client.aclose()

    sent = json.loads(requests[-1].content)
    assert health["protocol"] == "a2a"
    assert requests[0].url.path == "/.well-known/agent-card.json"
    assert sent["method"] == "SendMessage"
    assert sent["params"]["message"]["parts"][0]["data"] == {"question": "six times seven"}
    assert response.status == "completed"
    assert response.outputs[0].content == {"answer": 42.0}
