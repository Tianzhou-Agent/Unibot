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


async def _run_once(message: str, *, capability: str | None = None) -> AgentRun:
    user_id, tenant_id = eval_actor("builtin")
    async with UnibotClient(eval_base_url(), timeout=120) as client:
        run = await chat_run(
            client,
            message,
            user_id=user_id,
            tenant_id=tenant_id,
            capability=capability,
        )
        await client.delete_conversation(run.response["conversation_id"])
    return run


def test_ordinary_chat_does_not_call_capabilities() -> None:
    marker = f"DIRECT-{uuid4().hex[:8]}"
    message = f"Reply with exactly {marker} and do not call any tool or AINA."
    run = asyncio.run(_run_once(message))

    assert run.response["status"] == "completed"
    assert run.response["content"].strip() == marker
    assert_agent_run(
        run,
        task="Return the requested marker without invoking any Tool or AINA",
        expected_output=marker,
        expected_tools=[],
        criteria="Ignoring surrounding whitespace, the response must contain exactly the requested marker.",
        evaluate_correctness=False,
    )


def test_list_apps_agent_flow() -> None:
    run = asyncio.run(_run_once("列出应用"))

    assert run.response["status"] == "completed"
    assert run.response["iterations"] <= 3
    assert run.response["widgets"] and run.response["widgets"][0]["kind"] == "app_list"
    listed_ids = {item["aina_id"] for item in run.response["widgets"][0]["apps"]}
    assert "unibot-memory" in listed_ids
    assert "unibot-assistant" not in listed_ids
    assert_agent_run(
        run,
        task="List the AINA applications available to the current user",
        expected_output=(
            "The available applications include unibot-memory; do not invent other applications."
        ),
        expected_tools=["list_app"],
        criteria="The answer must accurately summarize the applications returned by the list_app capability.",
    )


def test_open_aina_agent_flow() -> None:
    run = asyncio.run(
        _run_once(
            "打开 unibot-memory 应用",
            capability="builtin:open_aina",
        )
    )

    assert run.response["status"] == "completed"
    widget = run.response["widgets"][0]
    assert widget["kind"] == "navigation"
    assert widget["actions"][0]["kind"] == "open_aina"
    assert widget["actions"][0]["aina_id"] == "unibot-memory"
    assert_agent_run(
        run,
        task="Open the unibot-memory AINA",
        expected_output="Confirm that Unibot Memory is ready to open.",
        expected_tools=["open_aina"],
        criteria="The answer must concern opening unibot-memory and must not claim a different AINA was opened.",
    )


def test_clarification_form_agent_flow() -> None:
    run = asyncio.run(
        _run_once(
            "Ask for project name and deadline in a form; prefill project name as Unibot.",
            capability="builtin:request_clarification",
        )
    )

    assert run.response["status"] == "completed"
    form = run.response["widgets"][0]
    assert form["kind"] == "form"
    assert len(form["fields"]) >= 2
    assert any(field.get("value") == "Unibot" for field in form["fields"])
    assert_agent_run(
        run,
        task="Present a form with project name prefilled and request the missing deadline",
        expected_output=(
            "The interactive form is validated separately; the assistant should direct the user to enter the "
            "deadline and note that project name is prefilled as Unibot."
        ),
        expected_tools=["request_clarification"],
        criteria=(
            "Evaluate only the assistant text because the interactive form is validated separately. The text "
            "must direct the user to that form and must not fabricate a deadline."
        ),
    )


async def _run_multi_turn(marker: str) -> tuple[AgentRun, AgentRun]:
    user_id, tenant_id = eval_actor("context")
    conversation_ids: set[str] = set()
    async with UnibotClient(eval_base_url(), timeout=120) as client:
        try:
            first = await chat_run(
                client,
                f"For this conversation only, the temporary code is {marker}. Acknowledge it without tools.",
                user_id=user_id,
                tenant_id=tenant_id,
            )
            conversation_ids.add(first.response["conversation_id"])
            second = await chat_run(
                client,
                "What temporary code did I give earlier? Reply with the code only and do not use tools.",
                conversation_id=first.response["conversation_id"],
                user_id=user_id,
                tenant_id=tenant_id,
            )
            return first, second
        finally:
            await delete_conversations(client, conversation_ids)


def test_multi_turn_context_agent_flow() -> None:
    marker = f"CTX-{uuid4().hex[:10]}"
    first, second = asyncio.run(_run_multi_turn(marker))

    assert first.response["status"] == "completed"
    assert second.response["status"] == "completed"
    assert marker in second.response["content"]
    assert_agent_run(
        second,
        task=f"Recall the temporary code {marker} from the preceding turn",
        expected_output=marker,
        expected_tools=[],
        criteria="The answer must return the exact code supplied earlier in the same conversation.",
    )
