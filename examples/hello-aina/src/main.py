from __future__ import annotations

from typing import Any


async def invoke(request: dict[str, Any]) -> dict[str, Any]:
    """Generate a short greeting through AINA Protocol 1.0."""
    user_input = request.get("input", {})
    name = user_input.get("name") or user_input.get("input")
    language = user_input.get("language", "zh")

    if not isinstance(name, str) or not name.strip():
        return _response(request, "failed", "请输入有效的姓名。")
    if language not in {"zh", "en"}:
        return _response(request, "failed", "language 只能是 zh 或 en。")

    normalized_name = name.strip()
    greeting = (
        f"你好，{normalized_name}！欢迎使用 Unibot。"
        if language == "zh"
        else f"Hello, {normalized_name}! Welcome to Unibot."
    )
    return _response(request, "completed", greeting)


def _response(request: dict[str, Any], status: str, content: str) -> dict[str, Any]:
    return {
        "request_id": request["request_id"],
        "status": status,
        "outputs": [{"type": "text", "content": content}],
        "usage": {"input_tokens": 0, "output_tokens": 0},
        "trace_id": request["trace"]["trace_id"],
    }
