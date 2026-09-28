"""Native model construction and archive ↔ native message conversion."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

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
    assert type(model).__name__ == "ChatOpenAI"
    # No accidental SDK retries
    assert model.max_retries == 0


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
