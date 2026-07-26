from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

from scripts.real_api_test import demo_server
from tests.evals.support import (
    REAL_EVAL_MARK,
    AgentRun,
    assert_agent_run,
    chat_run,
    delete_conversations,
    eval_actor,
    eval_base_url,
)
from tianzhou_agent_platform.sdk import UnibotClient

pytestmark = REAL_EVAL_MARK


async def _run_remote_tool(runtime_url: str, tool_id: str) -> AgentRun:
    user_id, tenant_id = eval_actor("tool")
    conversation_ids: set[str] = set()
    async with UnibotClient(eval_base_url(), timeout=120) as client:
        try:
            await client.register_tool(
                {
                    "tool_id": tool_id,
                    "name": "DeepEval addition",
                    "description": "Add two integers using the deterministic DeepEval runtime.",
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
                    "endpoint": f"{runtime_url}/tool/add",
                }
            )
            run = await chat_run(
                client,
                (
                    "Use the selected tool to add 17 and 25, then explain the operation and numeric result "
                    "in one concise sentence."
                ),
                user_id=user_id,
                tenant_id=tenant_id,
                capability=f"tool:{tool_id}",
            )
            conversation_ids.add(run.response["conversation_id"])
            return run
        finally:
            await delete_conversations(client, conversation_ids)
            await client.delete_tool(tool_id)


def test_remote_tool_agent_flow() -> None:
    tool_id = f"eval.add.{uuid4().hex[:12]}"
    with demo_server() as runtime_url:
        run = asyncio.run(_run_remote_tool(runtime_url, tool_id))

    assert run.response["status"] == "completed"
    assert "42" in run.response["content"]
    assert_agent_run(
        run,
        task="Use the selected remote tool and compose a sentence explaining that 17 plus 25 equals 42",
        expected_output="A concise sentence explaining that adding 17 and 25 produces 42.",
        expected_tools=[tool_id],
        criteria="The answer must report 42 as the result and must not report a different sum.",
    )


def _manifest(aina_id: str, runtime_url: str) -> dict[str, Any]:
    return {
        "protocol_version": "1.0",
        "aina": {
            "id": aina_id,
            "name": "DeepEval Arithmetic AINA",
            "version": "1.0.0",
            "description": "Returns a deterministic multiplication result for DeepEval routing tests.",
            "publisher": {"id": "deepeval", "name": "DeepEval"},
        },
        "runtime": {
            "type": "remote",
            "endpoint": f"{runtime_url}/aina",
            "streaming": False,
            "async_tasks": False,
        },
        "capabilities": {
            "skills": [
                {
                    "id": "multiply",
                    "name": "Multiply",
                    "description": "Multiply two integers and return the deterministic product.",
                    "input_schema": {"type": "object"},
                }
            ],
            "tools": [],
            "ui": [],
            "events": [],
        },
        "main_widget": {
            "id": "deepeval-arithmetic-main",
            "kind": "form",
            "title": "Multiply integers",
            "description": "Enter two integers.",
            "fields": [],
            "actions": [],
            "apps": [],
        },
        "permissions": [],
        "authentication": {"type": "none"},
    }


async def _run_remote_aina(runtime_url: str, aina_id: str) -> AgentRun:
    user_id, tenant_id = eval_actor("aina")
    conversation_ids: set[str] = set()
    async with UnibotClient(eval_base_url(), timeout=120) as client:
        try:
            await client.register_aina(_manifest(aina_id, runtime_url))
            await client.install_aina(
                aina_id,
                {"user_id": user_id, "tenant_id": tenant_id},
            )
            run = await chat_run(
                client,
                "Use DeepEval Arithmetic AINA to multiply 6 by 7 and report its deterministic result.",
                user_id=user_id,
                tenant_id=tenant_id,
            )
            conversation_ids.add(run.response["conversation_id"])
            return run
        finally:
            await delete_conversations(client, conversation_ids)
            await client.uninstall_aina(aina_id, user_id=user_id, tenant_id=tenant_id)
            await client.delete_aina(aina_id)


def test_remote_aina_routing_agent_flow() -> None:
    aina_id = f"com.deepeval.arithmetic.{uuid4().hex[:12]}"
    with demo_server() as runtime_url:
        run = asyncio.run(_run_remote_aina(runtime_url, aina_id))

    assert run.response["status"] == "completed"
    assert "42" in run.response["content"]
    assert run.response["widgets"] and run.response["widgets"][0]["id"] == "smoke-result"
    assert any(
        event["kind"] == "routing.scope.activated" and event["target_id"] == aina_id
        for event in run.trace["events"]
    )
    assert_agent_run(
        run,
        task="Route multiplication to the matching remote AINA and report its result",
        expected_output="The deterministic multiplication result is 42.",
        expected_tools=[aina_id],
        criteria="The answer must report the remote AINA result 42 without substituting a different result.",
    )
