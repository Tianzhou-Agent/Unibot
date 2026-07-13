"""Compatibility facade and dispatcher for platform-owned AINAs."""

from typing import Any

from tianzhou_agent_platform.aina.memory.builtin import (
    FORGET_TOOL_ID,
    MEMORY_TOOL_IDS,
    RECALL_TOOL_ID,
    REMEMBER_TOOL_ID,
    UNIBOT_MEMORY_ID,
    invoke_memory_tool,
    unibot_memory_record,
)
from tianzhou_agent_platform.aina.protocol.widgets import WidgetDefinition
from tianzhou_agent_platform.aina.unibot.builtin import (
    LIST_APP_TOOL_ID,
    OPEN_AINA_TOOL_ID,
    REQUEST_CLARIFICATION_TOOL_ID,
    UNIBOT_ASSISTANT_ID,
    UNIBOT_TOOL_IDS,
    invoke_unibot_tool,
    list_app_widget,
    open_aina,
    unibot_assistant_record,
)
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.core.repository import InMemoryRepository

BUILTIN_AINA_IDS = {UNIBOT_ASSISTANT_ID, UNIBOT_MEMORY_ID}


async def ensure_unibot_assistant(repository: InMemoryRepository) -> None:
    for aina_id, factory in (
        (UNIBOT_ASSISTANT_ID, unibot_assistant_record),
        (UNIBOT_MEMORY_ID, unibot_memory_record),
    ):
        try:
            await repository.get_aina(aina_id)
        except PlatformError as exc:
            if exc.code != "RESOURCE_NOT_FOUND":
                raise
            await repository.register_aina(factory())


async def invoke_builtin(
    repository: InMemoryRepository,
    tool_id: str,
    arguments: dict[str, Any],
    *,
    user_id: str,
    tenant_id: str,
    conversation_id: str,
) -> tuple[dict[str, Any], list[WidgetDefinition]]:
    invoke = invoke_memory_tool if tool_id in MEMORY_TOOL_IDS else invoke_unibot_tool
    if tool_id not in MEMORY_TOOL_IDS | UNIBOT_TOOL_IDS:
        raise PlatformError("RESOURCE_NOT_FOUND", f"Unknown built-in tool {tool_id!r}", status_code=404)
    return await invoke(
        repository,
        tool_id,
        arguments,
        user_id=user_id,
        tenant_id=tenant_id,
        conversation_id=conversation_id,
    )


__all__ = [
    "BUILTIN_AINA_IDS",
    "FORGET_TOOL_ID",
    "LIST_APP_TOOL_ID",
    "OPEN_AINA_TOOL_ID",
    "RECALL_TOOL_ID",
    "REMEMBER_TOOL_ID",
    "REQUEST_CLARIFICATION_TOOL_ID",
    "UNIBOT_ASSISTANT_ID",
    "UNIBOT_MEMORY_ID",
    "ensure_unibot_assistant",
    "invoke_builtin",
    "list_app_widget",
    "open_aina",
    "unibot_assistant_record",
    "unibot_memory_record",
]
