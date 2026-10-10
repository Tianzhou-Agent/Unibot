"""Native model construction and archive ↔ native message conversion."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import httpx
import openai
import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_openai import ChatOpenAI

from tianzhou_agent_platform.conversations.models import Message
from tianzhou_agent_platform.model_providers.factory import (
    _openai_base_url,
    create_model_from_settings,
    create_native_chat_model,
)
from tianzhou_agent_platform.model_providers.models import ModelRuntimeConfig, use_model_runtime
from tianzhou_agent_platform.services.agent_integration.history import (
    archive_to_native,
    dangling_tool_closures,
    native_to_wire,
    project_scope_history,
)
from tianzhou_agent_platform.services.agent_integration.runner import thread_id_for


def test_native_model_factory_rejects_unconfigured_provider() -> None:
    from tianzhou_agent_platform.core.errors import PlatformError

    with pytest.raises(PlatformError):
        create_native_chat_model(model="", api_key="", base_url="")


def test_native_model_factory_builds_chat_openai() -> None:
    model = create_native_chat_model(
        model="gpt-test",
        api_key="sk-test",
        base_url="https://api.example.com/v1",
        timeout_seconds=30.0,
        max_retries=0,
    )
    assert isinstance(model, ChatOpenAI)
    # No accidental SDK retries
    assert model.max_retries == 0


def _reasoning_model() -> ChatOpenAI:
    return create_native_chat_model(model="mimo-test", api_key="sk", base_url="https://api.example.com/v1")


def test_native_model_keeps_streamed_reasoning_content() -> None:
    model = _reasoning_model()
    chunks = [
        {"choices": [{"index": 0, "delta": {"role": "assistant", "reasoning_content": "Think "}}]},
        {"choices": [{"index": 0, "delta": {"reasoning_content": "hard."}}]},
        {"choices": [{"index": 0, "delta": {"content": "Answer"}, "finish_reason": "stop"}]},
    ]
    generations = [model._convert_chunk_to_generation_chunk(chunk, AIMessageChunk, {}) for chunk in chunks]
    merged = generations[0].message + generations[1].message + generations[2].message

    assert merged.additional_kwargs["reasoning_content"] == "Think hard."
    assert merged.content == "Answer"


def test_native_model_keeps_reasoning_content_of_a_complete_response() -> None:
    result = _reasoning_model()._create_chat_result(
        {
            "model": "mimo-test",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "Answer", "reasoning_content": "Think."},
                }
            ],
        }
    )

    assert result.generations[0].message.additional_kwargs["reasoning_content"] == "Think."


def test_reasoning_content_is_never_sent_back_to_the_provider() -> None:
    message = AIMessage(content="Answer", additional_kwargs={"reasoning_content": "Think."})
    payload = _reasoning_model()._get_request_payload([HumanMessage(content="hi"), message])

    assert "reasoning_content" not in payload["messages"][1]


def test_native_model_from_settings() -> None:
    model = create_model_from_settings(
        model="gpt-test",
        api_key="sk-test",
        base_url="https://api.example.com/v1",
    )
    assert model is not None


def test_native_factory_preserves_provider_quirks() -> None:
    model = create_native_chat_model(
        model="gpt-test",
        api_key="sk",
        base_url="https://api.example.com/v1/chat/completions",
        max_retries=0,
    )
    assert model.max_retries == 0
    assert "chat/completions" not in str(model.openai_api_base)
    assert _openai_base_url("https://api.example.com/v1") == "https://api.example.com/v1"


def test_thread_id_is_conversation_scoped() -> None:
    assert thread_id_for("c1") == "lc-v2:c1"


def test_archive_to_native_roundtrip_preserves_ids_and_tool_pairs() -> None:
    wire_call = {"id": "c1", "type": "function", "function": {"name": "echo_tool", "arguments": '{"text": "hi"}'}}
    user = Message(id="m1", role="user", content="hello")
    ai = Message(id="m2", role="assistant", content="", tool_calls=[wire_call])
    tool = Message(id="m3", role="tool", content="echo:hi", tool_call_id="c1", name="echo_tool")

    n_user, n_ai, n_tool = (archive_to_native(item) for item in (user, ai, tool))

    assert isinstance(n_user, HumanMessage) and n_user.id == "m1"
    assert isinstance(n_ai, AIMessage) and n_ai.id == "m2"
    assert n_ai.tool_calls[0]["id"] == "c1" and n_ai.tool_calls[0]["args"] == {"text": "hi"}
    assert isinstance(n_tool, ToolMessage) and n_tool.tool_call_id == "c1" and n_tool.id == "m3"
    assert native_to_wire(n_ai) == {"role": "assistant", "content": "", "tool_calls": [wire_call]}
    assert native_to_wire(n_tool) == tool.provider_message()


def test_unanswered_calls_are_closed_before_new_input() -> None:
    messages = [
        HumanMessage(content="send it"),
        AIMessage(content="", tool_calls=[{"id": "c1", "name": "send", "args": {}}, {"id": "c2", "name": "log", "args": {}}]),
        ToolMessage(content="logged", tool_call_id="c2", name="log"),
    ]

    [closure] = dangling_tool_closures(messages, "cancelled")

    assert (closure.tool_call_id, closure.name, closure.content) == ("c1", "send", "cancelled")
    assert dangling_tool_closures([*messages, closure], "cancelled") == []


def test_out_of_scope_history_becomes_non_executable_context() -> None:
    messages = [
        AIMessage(content="", tool_calls=[{"id": "c1", "name": "hidden", "args": {}}]),
        ToolMessage(content="secret result", tool_call_id="c1", name="hidden"),
        AIMessage(content="", tool_calls=[{"id": "c2", "name": "visible", "args": {}}]),
        ToolMessage(content="ok", tool_call_id="c2", name="visible"),
    ]

    projected = project_scope_history(messages, {"visible"})

    assert not projected[0].tool_calls and "non-executable" in projected[0].content
    assert isinstance(projected[1], AIMessage) and "<historical-capability-result>" in projected[1].content
    assert projected[2].tool_calls[0]["name"] == "visible"
    assert isinstance(projected[3], ToolMessage)


def test_model_runtime_context_is_actor_scoped() -> None:
    config = ModelRuntimeConfig(
        provider_id="p1",
        provider_name="Test",
        base_url="https://api.example.com/v1",
        api_key="sk-x",
        model_id="m1",
        model_name="GPT",
        model="gpt-test",
        context_window_tokens=32_000,
        timeout_seconds=20.0,
    )
    with use_model_runtime(config):
        from tianzhou_agent_platform.model_providers.models import (
            current_context_window_tokens,
            current_model_runtime,
        )

        assert current_model_runtime() is config
        assert current_context_window_tokens(128_000) == 32_000
    from tianzhou_agent_platform.model_providers.models import current_model_runtime

    assert current_model_runtime() is None


async def test_a_stream_that_keeps_trickling_fails_at_the_call_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    async def trickle(self: Any, *args: Any, **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        while True:  # a keep-alive every few milliseconds: no read timeout ever fires
            yield ChatGenerationChunk(message=AIMessageChunk(content=""))
            await asyncio.sleep(0.01)

    monkeypatch.setattr(ChatOpenAI, "_astream", trickle)
    model = _reasoning_model()
    model.call_deadline_seconds = 0.1

    with pytest.raises(openai.APITimeoutError):
        async for _chunk in model._astream([HumanMessage(content="hi")]):
            pass


async def test_a_finished_stream_is_passed_through_within_the_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    async def answer(self: Any, *args: Any, **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        for text in ("Hel", "lo"):
            yield ChatGenerationChunk(message=AIMessageChunk(content=text))

    monkeypatch.setattr(ChatOpenAI, "_astream", answer)

    chunks = [chunk.message.content async for chunk in _reasoning_model()._astream([HumanMessage(content="hi")])]

    assert chunks == ["Hel", "lo"]


async def test_a_hanging_complete_call_fails_at_the_call_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    async def hang(self: Any, *args: Any, **kwargs: Any) -> ChatResult:
        await asyncio.sleep(10)
        raise AssertionError("unreachable")

    monkeypatch.setattr(ChatOpenAI, "_agenerate", hang)
    model = _reasoning_model()
    model.call_deadline_seconds = 0.05

    with pytest.raises(openai.APITimeoutError):
        await model._agenerate([HumanMessage(content="hi")])


def _timeout() -> openai.APITimeoutError:
    return openai.APITimeoutError(request=httpx.Request("POST", "https://api.example.com/v1/chat/completions"))


async def test_a_call_that_times_out_before_any_chunk_is_sent_again(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[int] = []

    async def stall_once(self: Any, *args: Any, **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        attempts.append(1)
        if len(attempts) == 1:
            raise _timeout()  # e.g. langchain-openai's "no streaming chunk received" timeout
        yield ChatGenerationChunk(message=AIMessageChunk(content="Answer"))

    monkeypatch.setattr(ChatOpenAI, "_astream", stall_once)

    chunks = [chunk.message.content async for chunk in _reasoning_model()._astream([HumanMessage(content="hi")])]

    assert chunks == ["Answer"] and len(attempts) == 2


async def test_a_call_that_stalls_after_streaming_is_not_sent_again(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[int] = []

    async def stall_midway(self: Any, *args: Any, **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        attempts.append(1)
        yield ChatGenerationChunk(message=AIMessageChunk(content="Part"))
        raise _timeout()

    monkeypatch.setattr(ChatOpenAI, "_astream", stall_midway)

    with pytest.raises(openai.APITimeoutError):
        async for _chunk in _reasoning_model()._astream([HumanMessage(content="hi")]):
            pass
    assert len(attempts) == 1


async def test_silent_timeouts_are_retried_a_bounded_number_of_times(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[int] = []

    async def always_silent(self: Any, *args: Any, **kwargs: Any) -> ChatResult:
        attempts.append(1)
        raise _timeout()

    monkeypatch.setattr(ChatOpenAI, "_agenerate", always_silent)

    with pytest.raises(openai.APITimeoutError):
        await _reasoning_model()._agenerate([HumanMessage(content="hi")])
    assert len(attempts) == 2
