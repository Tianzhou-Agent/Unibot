from __future__ import annotations

import asyncio
from uuid import uuid4

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


async def _memory_lifecycle(marker: str) -> tuple[AgentRun, AgentRun, AgentRun, dict[str, object]]:
    user_id, tenant_id = eval_actor("memory")
    conversation_ids: set[str] = set()
    memory_id: str | None = None
    async with UnibotClient(eval_base_url(), timeout=120) as client:
        try:
            remembered = await chat_run(
                client,
                f"Use memory to remember this exact durable test token: {marker}.",
                user_id=user_id,
                tenant_id=tenant_id,
                capability="aina:unibot-memory",
            )
            conversation_ids.add(remembered.response["conversation_id"])
            memories = await client.list_memories(user_id=user_id, tenant_id=tenant_id, query=marker)
            matching = [item for item in memories["items"] if marker in item["content"]]
            if not matching:
                raise AssertionError("The memory write did not persist the unique marker")
            memory_id = matching[0]["id"]

            recalled = await chat_run(
                client,
                "What is my durable test token? Include the stored token in your answer.",
                user_id=user_id,
                tenant_id=tenant_id,
                capability="aina:unibot-memory",
            )
            conversation_ids.add(recalled.response["conversation_id"])

            denied_run = await chat_run(
                client,
                f"Forget memory {memory_id} permanently.",
                user_id=user_id,
                tenant_id=tenant_id,
                capability="aina:unibot-memory",
            )
            conversation_ids.add(denied_run.response["conversation_id"])
            if denied_run.response["status"] != "approval_required":
                raise AssertionError("Memory deletion did not stop for approval")
            denied = await client.deny_approval(
                denied_run.response["approval"]["id"],
                user_id=user_id,
                tenant_id=tenant_id,
            )
            after_denial = await client.list_memories(user_id=user_id, tenant_id=tenant_id, query=marker)
            if after_denial["total"] != 1:
                raise AssertionError("Denied memory deletion changed persistent state")

            pending_delete = await chat_run(
                client,
                f"Forget memory {memory_id} permanently.",
                user_id=user_id,
                tenant_id=tenant_id,
                capability="aina:unibot-memory",
            )
            conversation_ids.add(pending_delete.response["conversation_id"])
            deleted_response = await client.confirm_approval(
                pending_delete.response["approval"]["id"],
                user_id=user_id,
                tenant_id=tenant_id,
            )
            deleted_trace = await client.get_trace(deleted_response["trace_id"])
            deleted = AgentRun(
                input=pending_delete.input,
                response=deleted_response,
                trace=deleted_trace,
            )
            after_confirmation = await client.list_memories(user_id=user_id, tenant_id=tenant_id, query=marker)
            if after_confirmation["total"] != 0:
                raise AssertionError("Approved memory deletion did not remove persistent state")
            memory_id = None
            return remembered, recalled, deleted, denied
        finally:
            if memory_id is not None:
                await client.delete_memory(memory_id, user_id=user_id, tenant_id=tenant_id)
            await delete_conversations(client, conversation_ids)


def test_memory_write_recall_and_approval_lifecycle() -> None:
    marker = f"MEM-{uuid4().hex[:12]}"
    remembered, recalled, deleted, denied = asyncio.run(_memory_lifecycle(marker))

    assert remembered.response["status"] == "completed"
    assert marker in recalled.response["content"]
    assert denied["status"] == "denied"
    assert deleted.response["status"] == "completed"
    assert_agent_run(
        remembered,
        task="Persist the exact durable test token in memory",
        expected_output=f"Confirm that {marker} was remembered.",
        expected_tools=["memory.remember"],
        criteria="The answer must confirm the requested durable memory write without changing the token.",
        evaluate_efficiency=False,
    )
    assert_agent_run(
        recalled,
        task="Recall the durable test token from persistent memory",
        expected_output=f"The recalled durable test token is {marker}.",
        expected_tools=["memory.recall"],
        criteria=(
            "The answer must contain the exact previously stored token and must not invent a different value; "
            "brief explanatory text or formatting is allowed."
        ),
    )
    assert_agent_run(
        deleted,
        task="Delete the specified persistent memory after explicit approval",
        expected_output="Confirm that the specified memory was deleted.",
        expected_tools=["memory.forget"],
        criteria="The answer must state that the requested memory deletion completed successfully.",
    )
