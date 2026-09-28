from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
from fastapi.testclient import TestClient
from pydantic import SecretStr

from tests.support.fake_llm import ScriptedLLM, assistant, call_first_tool
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.conversations.models import ConversationCreate
from tianzhou_agent_platform.store.memory_repository import InMemoryRepository
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


SUMMARY_MARKER = "Here is a summary of the conversation to date"


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
        compression_calls = client.get(
            "/llm-calls", params={"user_id": "anonymous", "tenant_id": "default"}
        ).json()

    assert response.status_code == 200
    assert response.json()["usage"] == {
        "input_tokens": 160,
        "output_tokens": 25,
        "estimated": False,
    }
    assert len(llm.calls) == 2
    assert llm.calls[0]["tools"] == []
    assert "old-context" in json.dumps(llm.calls[0]["messages"])
    assert [call["context_type"] for call in compression_calls].count("compression") == 1

    model_messages = llm.calls[1]["messages"]
    assert any(SUMMARY_MARKER in str(message.get("content", "")) for message in model_messages)
    assert any(message.get("content") == "latest question" for message in model_messages)
    assert "old-context" not in json.dumps(model_messages)

    # The archive keeps every original message; only the working memory was compacted.
    assert len(conversation["messages"]) == 8
    assert conversation["messages"][0]["content"].startswith("old-context")
    assert not any(SUMMARY_MARKER in message["content"] for message in conversation["messages"])
    assert conversation["config"] == {}

    compacted = next(event for event in trace["events"] if event["kind"] == "context.compacted")
    assert compacted["details"]["before_tokens"] > compacted["details"]["after_tokens"]
    assert compacted["details"]["summarized_message_count"] == 6
    compression_span = next(span for span in trace["spans"] if span["name"] == "context.compress")
    assert compression_span["kind"] == "internal"
    assert compression_span["status"] == "completed"


def test_summary_stays_in_working_memory_for_the_next_turn() -> None:
    repository = InMemoryRepository()
    conversation_id = _seed_long_conversation(repository)
    llm = ScriptedLLM(
        [
            assistant("Earlier turns discussed old requirements."),
            assistant("first answer"),
            assistant("second answer"),
        ]
    )
    # Seven messages on the first turn trigger compaction; the compacted working memory stays below the minimum.
    settings = _settings().model_copy(update={"context_compression_min_messages": 5})

    with TestClient(create_app(settings=settings, repository=repository, llm=llm)) as client:
        client.post("/chat", json={"message": "latest question", "conversation_id": conversation_id})
        second = client.post("/chat", json={"message": "follow up", "conversation_id": conversation_id})
        conversation = client.get(f"/conversations/{conversation_id}").json()

    assert second.json()["content"] == "second answer"
    assert len(llm.calls) == 3
    follow_up_request = json.dumps(llm.calls[2]["messages"], ensure_ascii=False)
    # The next turn continues from the checkpointed working memory, not from the full archive.
    assert SUMMARY_MARKER in follow_up_request
    assert "first answer" in follow_up_request
    assert "old-context" not in follow_up_request
    assert [message["content"] for message in conversation["messages"][-4:]] == [
        "latest question",
        "first answer",
        "follow up",
        "second answer",
    ]


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

    llm = ScriptedLLM([assistant(""), assistant("fallback answer")])
    with TestClient(create_app(settings=_settings(), repository=repository, llm=llm)) as client:
        response = client.post(
            "/chat",
            json={"message": "latest question", "conversation_id": conversation_id},
        )
        trace = client.get(f"/traces/{response.json()['trace_id']}").json()

    assert response.status_code == 200
    assert response.json()["content"] == "fallback answer"
    assert "old-context" in json.dumps(llm.calls[1]["messages"])
    failed = next(event for event in trace["events"] if event["kind"] == "context.compression.failed")
    assert failed["details"]["error"]["message"] == "The context compression model returned an empty summary"
    assert not any(event["kind"] == "context.compacted" for event in trace["events"])
    compression_span = next(span for span in trace["spans"] if span["name"] == "context.compress")
    assert compression_span["status"] == "failed"


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
    assert conversation["config"] == {}
