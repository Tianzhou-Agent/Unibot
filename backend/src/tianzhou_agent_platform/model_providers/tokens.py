"""Shared native counting/estimate helpers.

Used by runtime budget guards and observability. Injected as callables so
observability does not import runtime internals.
"""

from __future__ import annotations

from typing import Any


def estimate_message_tokens(messages: Any) -> int:
    """Fallback estimate when providers cannot supply a reliable count."""
    total = 0
    for message in messages or []:
        if isinstance(message, str):
            total += max(1, len(message) // 4)
            continue
        content = getattr(message, "content", None)
        if isinstance(content, str):
            total += max(1, len(content) // 4)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, str):
                    total += max(1, len(block) // 4)
                elif isinstance(block, dict):
                    total += max(1, len(str(block.get("text", ""))) // 4)
        total += 4  # role/name overhead
    return total


def estimate_tools_tokens(tools: Any) -> int:
    """Rough token cost of advertising tool schemas."""
    import json

    if not tools:
        return 0
    try:
        payload = json.dumps(tools, default=str)
    except TypeError:
        payload = str(tools)
    return max(1, len(payload) // 4)
