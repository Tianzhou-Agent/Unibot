"""The native agent runtime on the production storage stack (MySQL archive and checkpoints, Redis, NAS).

Run with ``TZ_STORAGE_E2E=1`` against ``backend/docker-compose.storage.yml``; point the ``TZ_STORAGE_E2E_*``
variables at an isolated database, Redis database and NAS directory.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from tests.support.fake_llm import ScriptedLLM, assistant, call_first_tool
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.main import create_app
from tianzhou_agent_platform.store import StorageSettings

pytestmark = pytest.mark.skipif(
    os.getenv("TZ_STORAGE_E2E") != "1",
    reason="Set TZ_STORAGE_E2E=1 and start backend/docker-compose.storage.yml to run live storage E2E tests",
)


def _storage() -> StorageSettings:
    return StorageSettings(
        mysql_dsn=os.getenv("TZ_STORAGE_E2E_MYSQL_DSN", "mysql+aiomysql://unibot:unibot@127.0.0.1:13306/unibot"),
        redis_dsn=os.getenv("TZ_STORAGE_E2E_REDIS_URL", "redis://127.0.0.1:16379/0"),
        nas_root_path=Path(
            os.getenv("TZ_STORAGE_E2E_NAS_ROOT", str(Path(__file__).parents[3] / "data" / "nas"))
        ),
    )


def _app(llm: ScriptedLLM, remote: httpx.AsyncClient) -> Any:
    settings = AgentSettings(  # type: ignore[call-arg]
        _env_file=None,
        llm_base_url="https://model.invalid/v1",
        llm_api_key=SecretStr("test-key"),
        llm_model="test-model",
        obs_enabled=False,
    )
    return create_app(settings=settings, storage_settings=_storage(), llm=llm, capability_http_client=remote)


async def _checkpoints(app: Any, conversation_id: str) -> list[Any]:
    config = {"configurable": {"thread_id": f"lc-v2:{conversation_id}"}}
    return [item async for item in app.state.agent_checkpointer.alist(config)]


def test_paused_approval_resumes_after_restart_on_production_storage() -> None:
    sent: list[dict[str, Any]] = []

    async def remote_tool(request: httpx.Request) -> httpx.Response:
        sent.append({"idempotency_key": request.headers["Idempotency-Key"]})
        return httpx.Response(200, json={"sent": True})

    remote = httpx.AsyncClient(transport=httpx.MockTransport(remote_tool))
    tool_id = f"e2e.send.{os.getpid()}"

    first_llm = ScriptedLLM([call_first_tool(arguments='{"recipient": "user@example.com"}', call_id="call_send")])
    first = _app(first_llm, remote)
    with TestClient(first) as client:
        registered = client.post(
            "/tools",
            json={
                "tool_id": tool_id,
                "name": "Send message",
                "description": "Send a message to an external recipient.",
                "input_schema": {
                    "type": "object",
                    "properties": {"recipient": {"type": "string"}},
                    "required": ["recipient"],
                },
                "endpoint": "https://tool.invalid/send",
                "side_effect_level": "high",
            },
        )
        assert registered.status_code == 201, registered.text
        pending = client.post("/chat", json={"message": "Send it", "capability": f"tool:{tool_id}"}).json()
        paused_checkpoints = client.portal.call(_checkpoints, first, pending["conversation_id"])

    assert pending["status"] == "approval_required", pending
    assert sent == []
    assert len(paused_checkpoints) == 1

    # A new process over the same storage resumes the checkpointed batch, then continues the conversation.
    second_llm = ScriptedLLM([assistant("Sent."), assistant("Anything else?")])
    second = _app(second_llm, remote)
    conversation_id = pending["conversation_id"]
    with TestClient(second) as client:
        confirmed = client.post(f"/approvals/{pending['approval']['id']}/confirm", json={})
        follow_up = client.post("/chat", json={"message": "Thanks", "conversation_id": conversation_id})
        conversation = client.get(f"/conversations/{conversation_id}").json()
        checkpoints = client.portal.call(_checkpoints, second, conversation_id)
        deleted = client.delete(f"/conversations/{conversation_id}")
        after_delete = client.portal.call(_checkpoints, second, conversation_id)
        client.delete(f"/tools/{tool_id}")

    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "completed"
    assert confirmed.json()["content"] == "Sent."
    assert sent == [{"idempotency_key": "call_send"}]
    assert follow_up.json()["content"] == "Anything else?"
    assert any(message.get("content") == "Sent." for message in second_llm.calls[1]["messages"])
    assert [message["role"] for message in conversation["messages"]] == [
        "user",
        "assistant",
        "tool",
        "assistant",
        "user",
        "assistant",
    ]
    assert len(checkpoints) == 1
    assert deleted.status_code == 204
    assert after_delete == []
