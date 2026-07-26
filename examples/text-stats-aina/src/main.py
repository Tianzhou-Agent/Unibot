from __future__ import annotations

from typing import Any


async def invoke(request: dict[str, Any]) -> dict[str, Any]:
    """Count basic text metrics and return an AINA Protocol 1.0 response."""
    text = request.get("input", {}).get("text")
    if not isinstance(text, str):
        return {
            "request_id": request["request_id"],
            "status": "failed",
            "outputs": [{"type": "text", "content": "输入字段 text 必须是字符串。"}],
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "trace_id": request["trace"]["trace_id"],
        }

    stats = {
        "characters": len(text),
        "characters_without_whitespace": sum(not character.isspace() for character in text),
        "words": len(text.split()),
        "lines": len(text.splitlines()) if text else 0,
    }
    summary = (
        f"字符 {stats['characters']} 个，非空字符 {stats['characters_without_whitespace']} 个，"
        f"单词 {stats['words']} 个，行数 {stats['lines']}。"
    )
    return {
        "request_id": request["request_id"],
        "status": "completed",
        "outputs": [
            {"type": "text", "content": summary},
            {"type": "json", "content": stats},
        ],
        "usage": {"input_tokens": 0, "output_tokens": 0},
        "trace_id": request["trace"]["trace_id"],
    }
