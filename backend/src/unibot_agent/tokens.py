"""Token estimates and budgets shared by the agent integration and observability.

The runtime middleware receives these as injected callables, so neither observability nor the runtime depends on
the other.
"""

from __future__ import annotations

import json
import math
from typing import Any


# Vision models bill a picture by its pixels, not its encoded size: a 1,280-px slide render costs about 1,100-1,300
# tokens. An image part is counted as this much instead of its base64 text, which would read as tens of thousands.
IMAGE_TOKENS = 1_600
_IMAGE_PARTS = frozenset({"image_url", "image"})


def estimate_request_tokens(messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> int:
    """Conservative tokenizer-free estimate for multilingual JSON chat payloads.

    Counts every non-ASCII character as a token, so CJK text is not underestimated the way a characters/4 rule
    (``count_tokens_approximately``) would underestimate it. Each image counts as ``IMAGE_TOKENS``.
    """
    total = sum(_estimate_message_tokens(message) + 4 for message in messages)
    if tools:
        total += _estimate_value_tokens(tools)
    return total


def _estimate_message_tokens(message: dict[str, Any]) -> int:
    content = message.get("content")
    if not isinstance(content, list):
        return _estimate_value_tokens(message)
    images = sum(isinstance(part, dict) and part.get("type") in _IMAGE_PARTS for part in content)
    text = [part for part in content if not (isinstance(part, dict) and part.get("type") in _IMAGE_PARTS)]
    return _estimate_value_tokens({**message, "content": text}) + images * IMAGE_TOKENS


def output_token_reserve(context_window_tokens: int, max_output_tokens: int | None = None) -> int:
    """Answer space kept free in every request: the model's own setting, else a quarter of the window, at most
    4096 tokens."""
    if max_output_tokens is not None:
        return min(max_output_tokens, context_window_tokens // 2)
    return min(4_096, context_window_tokens // 4)


def request_input_budget(context_window_tokens: int, max_output_tokens: int | None = None) -> int:
    return context_window_tokens - output_token_reserve(context_window_tokens, max_output_tokens)


def _estimate_value_tokens(value: Any) -> int:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    ascii_count = sum(character.isascii() for character in text)
    return math.ceil(ascii_count / 4) + (len(text) - ascii_count)
