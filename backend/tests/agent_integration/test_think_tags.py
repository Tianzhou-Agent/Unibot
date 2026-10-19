"""Replies that start with a ``<think>`` block (MiniMax): the block becomes reasoning, and MiniMax gets it back."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGenerationChunk

from tianzhou_agent_platform.model_providers.factory import (
    REASONING_KEY,
    create_native_chat_model,
    split_think,
    split_think_stream,
)


def _minimax() -> object:
    return create_native_chat_model(model="MiniMax-M3", api_key="sk", base_url="https://api.minimax.io/v1")


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("<think>The user said hi.</think>\n\nHi there!", ("The user said hi.", "Hi there!")),
        ("\n<think>\nPlan.\n</think>Done", ("Plan.", "Done")),
        ("<think>Still thinking", ("Still thinking", "")),
        ("Plain answer with <think> later", (None, "Plain answer with <think> later")),
    ],
)
def test_a_leading_think_block_is_split_from_the_reply(content: str, expected: tuple[str | None, str]) -> None:
    assert split_think(content) == expected


async def _chunks(*parts: str | AIMessageChunk) -> AsyncIterator[ChatGenerationChunk]:
    for part in parts:
        message = part if isinstance(part, AIMessageChunk) else AIMessageChunk(content=part)
        yield ChatGenerationChunk(message=message)


def _merge(parts: tuple[str | AIMessageChunk, ...]) -> AIMessageChunk:
    async def collect() -> list[ChatGenerationChunk]:
        return [chunk async for chunk in split_think_stream(_chunks(*parts))]

    merged = AIMessageChunk(content="")
    for chunk in asyncio.run(collect()):
        merged = merged + chunk.message  # type: ignore[assignment]
    return merged


@pytest.mark.parametrize(
    "parts",
    [
        ("<think>The user said hi.</think>\n\nHi there!",),
        ("<thi", "nk>The user ", "said hi.</th", "ink>", "\n\nHi ", "there!"),
        ("\n", "<", "think>The user said hi.<", "/think>Hi there!"),
    ],
)
def test_a_streamed_think_block_becomes_reasoning_even_when_the_tags_are_cut(parts: tuple[str, ...]) -> None:
    merged = _merge(parts)

    assert merged.additional_kwargs[REASONING_KEY] == "The user said hi."
    assert merged.content == "Hi there!"


def test_a_stream_without_a_think_block_is_unchanged_and_tool_calls_pass_through() -> None:
    call = AIMessageChunk(content="", tool_call_chunks=[{"name": "search", "args": "{}", "id": "c1", "index": 0}])
    merged = _merge(("<", "b>bold</b> answer", call))

    assert merged.content == "<b>bold</b> answer"
    assert REASONING_KEY not in merged.additional_kwargs
    assert merged.tool_call_chunks[0]["name"] == "search"


def test_a_complete_minimax_reply_is_split() -> None:
    result = _minimax()._create_chat_result(  # type: ignore[attr-defined]
        {
            "model": "MiniMax-M3",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "<think>Greet back.</think>\n\nHi there!"},
                }
            ],
        }
    )
    message = result.generations[0].message

    assert message.content == "Hi there!" and result.generations[0].text == "Hi there!"
    assert message.additional_kwargs[REASONING_KEY] == "Greet back."


def test_minimax_gets_its_reasoning_back_as_a_think_block() -> None:
    reply = AIMessage(content="Hi there!", additional_kwargs={REASONING_KEY: "Greet back."})
    payload = _minimax()._get_request_payload([HumanMessage(content="hi"), reply])  # type: ignore[attr-defined]

    assert payload["messages"][1]["content"] == "<think>Greet back.</think>\n\nHi there!"
    assert REASONING_KEY not in payload["messages"][1]


def test_minimax_flash_gets_its_reasoning_back_in_the_reasoning_field() -> None:
    model = create_native_chat_model(model="MiniMax-M3.1-Flash", api_key="sk", base_url="https://api.minimax.io/v1")
    reply = AIMessage(content="Hi there!", additional_kwargs={REASONING_KEY: "Greet back."})
    payload = model._get_request_payload([HumanMessage(content="hi"), reply])  # type: ignore[attr-defined]

    assert payload["messages"][1]["content"] == "Hi there!"
    assert payload["messages"][1][REASONING_KEY] == "Greet back."


def test_a_non_streamed_call_may_take_the_whole_call_limit_to_answer() -> None:
    model = create_native_chat_model(
        model="MiniMax-M3",
        api_key="sk",
        base_url="https://api.minimax.io/v1",
        timeout_seconds=60,
        max_completion_tokens=32_768,
    )
    whole = model._get_request_payload([HumanMessage(content="hi")])  # type: ignore[attr-defined]
    streamed = model._get_request_payload([HumanMessage(content="hi")], stream=True)  # type: ignore[attr-defined]

    assert whole["timeout"].read == 32_768 / 40 and whole["timeout"].connect == 60
    assert "timeout" not in streamed  # the provider timeout keeps bounding the gap between chunks
