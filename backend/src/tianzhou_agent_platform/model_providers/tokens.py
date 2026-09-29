"""Token estimates and budgets shared by the agent integration and observability.

The runtime middleware receives these as injected callables, so neither observability nor the runtime depends on
the other.
"""

from __future__ import annotations

import json
import math
from typing import Any


def estimate_request_tokens(messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> int:
    """Conservative tokenizer-free estimate for multilingual JSON chat payloads.

    Counts every non-ASCII character as a token, so CJK text is not underestimated the way a characters/4 rule
    (``count_tokens_approximately``) would underestimate it.
    """
    total = sum(_estimate_value_tokens(message) + 4 for message in messages)
    if tools:
        total += _estimate_value_tokens(tools)
    return total


def output_token_reserve(context_window_tokens: int) -> int:
    """Answer space kept free in every request: a quarter of the window, at most 4096 tokens."""
    return min(4_096, context_window_tokens // 4)


def request_input_budget(context_window_tokens: int) -> int:
    return context_window_tokens - output_token_reserve(context_window_tokens)


def _estimate_value_tokens(value: Any) -> int:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    ascii_count = sum(character.isascii() for character in text)
    return math.ceil(ascii_count / 4) + (len(text) - ascii_count)
