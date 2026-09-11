from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
from fastapi.testclient import TestClient
from pydantic import SecretStr

from tests.support.fake_llm import ScriptedLLM, assistant, call_first_tool
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.core.context_compression import (
    COMPRESSION_CONFIG_KEY,
    SUMMARY_PREFIX,
    active_history,
    plan_compression,
    serialized_state,
    summary_request,
)
from tianzhou_agent_platform.core.conversation import Conversation, ConversationCreate, Message
from tianzhou_agent_platform.core.repository import InMemoryRepository
from tianzhou_agent_platform.main import create_app


def _settings() -> AgentSettings:
    return AgentSettings(  # type: ignore[call-arg]
        _env_file=None,
        llm_base_url="https://model.invalid/v1",
        llm_api_key=SecretStr("test-key"),
        llm_model="test-model",
        context_window_tokens=4_096,
        context_compression_threshold_ratio=0.5,
        context_compression_keep_recent_turns=1,
        context_compression_min_messages=4,
    )


def _seed_long_conversation(repository: InMemoryRepository, *, content_size: int = 400) -> str:
    async def seed() -> str:
        conversation = await repository.create_conversation(ConversationCreate())
        old_context = "old-context-" + ("x" * content_size)
        messages: list[dict[str, Any]] = []
        for turn in range(3):
            messages.extend(
                [
                    {"role": "user", "content": f"{old_context}-question-{turn}"},
                    {"role": "assistant", "content": f"{old_context}-answer-{turn}"},
                ]
            )
        await repository.append_provider_messages(conversation.id, messages, trace_id="trace_seed")
        return conversation.id

    return asyncio.run(seed())


def test_long_context_is_summarized_without_deleting_conversation_messages() -> None:
    repository = InMemoryRepository()
    conversation_id = _seed_long_conversation(repository)
    llm = ScriptedLLM(
        [
            assistant("## Goal\nPreserve the old requirements.\n## Next steps\nAnswer the latest question.", input_tokens=120, output_tokens=20),
            assistant("final answer", input_tokens=40, output_tokens=5),
        ]
    )

    with TestClient(create_app(settings=_settings(), repository=repository, llm=llm)) as client:
        response = client.post(
            "/chat",
            json={"message": "latest question", "conversation_id": conversation_id},
        )
        conversation = client.get(f"/conversations/{conversation_id}").json()
        trace = client.get(f"/traces/{response.json()['trace_id']}").json()

    assert response.status_code == 200
    assert response.json()["usage"] == {
        "input_tokens": 160,
        "output_tokens": 25,
        "estimated": False,
    }
    assert len(llm.calls) == 2
    assert llm.calls[0]["context_type"] == "compression"
    assert llm.calls[0]["tools"] == []
    assert "old-context" in json.dumps(llm.calls[0]["messages"])

    model_messages = llm.calls[1]["messages"]
    assert any(str(message.get("content", "")).startswith(SUMMARY_PREFIX) for message in model_messages)
    assert any(message.get("content") == "latest question" for message in model_messages)
    assert "old-context" not in json.dumps(model_messages)

    assert len(conversation["messages"]) == 8
    assert conversation["messages"][0]["content"].startswith("old-context")
    compression_state = conversation["config"][COMPRESSION_CONFIG_KEY]
    assert compression_state["count"] == 1
    assert compression_state["through_message_id"] == conversation["messages"][5]["id"]

    compacted = next(event for event in trace["events"] if event["kind"] == "context.compacted")
    assert compacted["details"]["before_tokens"] > compacted["details"]["after_tokens"]
    assert compacted["details"]["summarized_message_count"] == 6
    compression_span = next(span for span in trace["spans"] if span["name"] == "context.compress")
    assert compression_span["kind"] == "internal"
    assert compression_span["status"] == "completed"


def test_selected_model_context_window_controls_compression_threshold() -> None:
    repository = InMemoryRepository()
    conversation_id = _seed_long_conversation(repository)
    llm = ScriptedLLM(
        [
            assistant("## Goal\nPreserve the old requirements.", input_tokens=120, output_tokens=20),
            assistant("final answer", input_tokens=40, output_tokens=5),
        ]
    )
    settings = _settings().model_copy(update={"context_window_tokens": 128_000})

    with TestClient(create_app(settings=settings, repository=repository, llm=llm)) as client:
        provider = client.post(
            "/model-settings/providers",
            json={
                "provider_type": "openai",
                "name": "Small context provider",
                "base_url": "https://model.invalid/v1",
                "api_key": "test-key",
                "models": [
                    {
                        "name": "Small context model",
                        "model": "small-context",
                        "context_window_tokens": 4_096,
                    }
                ],
            },
        ).json()
        client.post(
            f"/model-settings/providers/{provider['id']}/models/{provider['models'][0]['id']}/default",
            json={},
        )
        response = client.post(
            "/chat",
            json={"message": "latest question", "conversation_id": conversation_id},
        )
        trace = client.get(f"/traces/{response.json()['trace_id']}").json()

    assert response.status_code == 200
    assert len(llm.calls) == 2
    compression_span = next(span for span in trace["spans"] if span["name"] == "context.compress")
    assert compression_span["attributes"]["context_window_tokens"] == 4_096


def test_compression_failure_preserves_full_context_and_continues() -> None:
    repository = InMemoryRepository()
    conversation_id = _seed_long_conversation(repository)

    def fail_summary(**_: Any) -> Any:
        raise RuntimeError("summary model unavailable")

    llm = ScriptedLLM([fail_summary, assistant("fallback answer")])
    with TestClient(create_app(settings=_settings(), repository=repository, llm=llm)) as client:
        response = client.post(
            "/chat",
            json={"message": "latest question", "conversation_id": conversation_id},
        )
        conversation = client.get(f"/conversations/{conversation_id}").json()
        trace = client.get(f"/traces/{response.json()['trace_id']}").json()

    assert response.status_code == 200
    assert response.json()["content"] == "fallback answer"
    assert "old-context" in json.dumps(llm.calls[1]["messages"])
    assert COMPRESSION_CONFIG_KEY not in conversation["config"]
    assert any(event["kind"] == "context.compression.failed" for event in trace["events"])
    assert not any(event["kind"] == "context.compacted" for event in trace["events"])


def test_recompression_updates_previous_summary_without_splitting_tool_groups() -> None:
    conversation = Conversation(
        id="conv_recompress",
        user_id="anonymous",
        tenant_id="default",
        title="Long conversation",
        config={
            COMPRESSION_CONFIG_KEY: {
                "version": 1,
                "summary": "Previous summary",
                "through_message_id": "msg_old_assistant",
                "count": 1,
            }
        },
        messages=[
            Message(id="msg_old_user", role="user", content="old question"),
            Message(id="msg_old_assistant", role="assistant", content="old answer"),
            Message(id="msg_tool_user", role="user", content="run the tool"),
            Message(
                id="msg_tool_call",
                role="assistant",
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "demo", "arguments": "{}"},
                    }
                ],
            ),
            Message(id="msg_tool_result", role="tool", content="result", tool_call_id="call_1", name="demo"),
            Message(id="msg_latest", role="user", content="latest question"),
        ],
    )

    history = active_history(conversation)
    plan = plan_compression(history, keep_recent_turns=1, min_messages=4)

    assert plan is not None
    assert [message.id for message in plan.messages_to_summarize] == [
        "msg_tool_user",
        "msg_tool_call",
        "msg_tool_result",
    ]
    assert [message.id for message in plan.retained_messages] == ["msg_latest"]
    assert "Previous summary" in summary_request(plan)[1]["content"]
    assert serialized_state(plan, "Updated summary") == {
        "version": 1,
        "summary": "Updated summary",
        "through_message_id": "msg_tool_result",
        "count": 2,
    }


def test_large_tool_result_stops_before_next_model_call_and_preserves_full_result() -> None:
    result = "result-marker:" + "x" * 60_000 + ":end-marker"
    llm = ScriptedLLM([call_first_tool()])
    remote = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"result": result})))
    with TestClient(create_app(settings=_settings(), llm=llm, capability_http_client=remote)) as client:
        assert client.post("/tools", json={
            "tool_id": "large-result", "name": "Large result", "description": "Return a large result",
            "input_schema": {"type": "object"}, "endpoint": "https://tool.invalid/invoke",
        }).status_code == 201
        response = client.post("/chat", json={"message": "Read it", "capability": "tool:large-result"}).json()
        conversation = client.get(f"/conversations/{response['conversation_id']}").json()
    assert response["status"] == "failed"
    assert "context budget" in response["content"]
    assert len(llm.calls) == 1
    saved_result = next(message for message in conversation["messages"] if message["role"] == "tool")
    assert json.loads(saved_result["content"])["result"] == result


def test_oversized_transcript_never_reaches_summary_or_answer_model():
    repository = InMemoryRepository()
    conversation_id = _seed_long_conversation(repository, content_size=20_000)
    llm = ScriptedLLM([])
    with TestClient(create_app(settings=_settings(), repository=repository, llm=llm)) as client:
        response = client.post("/chat", json={"message": "Continue", "conversation_id": conversation_id}).json()
        conversation = client.get(f"/conversations/{conversation_id}").json()
    assert response["status"] == "failed"
    assert not llm.calls
    assert "x" * 20_000 in conversation["messages"][0]["content"]
    assert COMPRESSION_CONFIG_KEY not in conversation["config"]
