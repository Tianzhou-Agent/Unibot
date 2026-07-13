from typing import Any, cast

from tianzhou_agent_platform.aina.memory.models import MemoryCategory, MemoryCreate
from tianzhou_agent_platform.aina.protocol.models import (
    AinaCapabilities,
    AinaCapability,
    AinaIdentity,
    AinaManifest,
    AinaRecord,
    BuiltinRuntimeDefinition,
    Publisher,
)
from tianzhou_agent_platform.aina.protocol.widgets import WidgetDefinition
from tianzhou_agent_platform.aina.security.models import Authentication
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.core.repository import InMemoryRepository

UNIBOT_MEMORY_ID = "unibot-memory"
REMEMBER_TOOL_ID = "memory.remember"
RECALL_TOOL_ID = "memory.recall"
FORGET_TOOL_ID = "memory.forget"
MEMORY_TOOL_IDS = {REMEMBER_TOOL_ID, RECALL_TOOL_ID, FORGET_TOOL_ID}


def unibot_memory_record() -> AinaRecord:
    return AinaRecord(
        manifest=AinaManifest(
            protocol_version="1.0",
            aina=AinaIdentity(
                id=UNIBOT_MEMORY_ID,
                name="Unibot Memory",
                version="1.0.0",
                description=(
                    "Stores, recalls, and removes durable user facts, preferences, goals, and instructions. "
                    "Use when the user asks Unibot to remember or forget something, or asks what Unibot "
                    "remembers about them."
                ),
                publisher=Publisher(id="unibot", name="Unibot"),
            ),
            runtime=BuiltinRuntimeDefinition(),
            capabilities=AinaCapabilities(
                skills=[
                    AinaCapability(
                        id="memory-management",
                        name="Durable memory management",
                        description="Curate durable cross-conversation memory without storing transient chat.",
                        instructions=(
                            "When the user explicitly asks to remember a durable fact, call memory.remember. "
                            "When they ask what is remembered, call memory.recall. When they explicitly ask to "
                            "forget an item and its id is known, call memory.forget. Never invent a memory write."
                        ),
                    )
                ],
                tools=[
                    AinaCapability(
                        id=REMEMBER_TOOL_ID,
                        name="Remember",
                        description="Store one durable fact, preference, goal, or instruction.",
                    ),
                    AinaCapability(
                        id=RECALL_TOOL_ID,
                        name="Recall",
                        description="Retrieve memories relevant to a query.",
                    ),
                    AinaCapability(
                        id=FORGET_TOOL_ID,
                        name="Forget",
                        description="Delete a memory by its exact memory id.",
                    ),
                ],
            ),
            main_widget=WidgetDefinition(
                id="unibot-memory-main",
                kind="memory",
                title="记忆系统",
                description="管理跨对话保留的事实、偏好、目标和指令。",
                markdown=(
                    "### 持久记忆\n\n记忆会在后续对话中按相关性召回。只保存长期有用的信息，"
                    "不会把完整聊天记录直接当作记忆。"
                ),
            ),
            authentication=Authentication(type="none"),
        ),
        last_health={"status": "healthy", "runtime": "builtin"},
    )


async def invoke_memory_tool(
    repository: InMemoryRepository,
    tool_id: str,
    arguments: dict[str, Any],
    *,
    user_id: str,
    tenant_id: str,
    conversation_id: str,
) -> tuple[dict[str, Any], list[WidgetDefinition]]:
    if tool_id == REMEMBER_TOOL_ID:
        content = str(arguments.get("content") or "").strip()
        category_value = str(arguments.get("category") or "fact")
        if category_value not in {"fact", "preference", "goal", "instruction"}:
            raise PlatformError("INVALID_REQUEST", f"Unsupported memory category: {category_value}")
        category = cast(MemoryCategory, category_value)
        try:
            memory = await repository.create_memory(
                MemoryCreate(
                    content=content,
                    category=category,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    source_conversation_id=conversation_id,
                    metadata={"write_origin": "unibot-memory", "tool": REMEMBER_TOOL_ID},
                )
            )
        except ValueError as exc:
            raise PlatformError("INVALID_REQUEST", str(exc)) from exc
        return {"saved": True, "memory": memory.model_dump(mode="json")}, []
    if tool_id == RECALL_TOOL_ID:
        query = str(arguments.get("query") or "").strip()
        if query:
            memories = await repository.search_memories(
                query,
                user_id=user_id,
                tenant_id=tenant_id,
                limit=8,
            )
            if not memories and any(
                marker in query.casefold()
                for marker in ("记得", "记忆", "知道我", "remember", "memory", "know about me")
            ):
                memories = (await repository.list_memories(user_id=user_id, tenant_id=tenant_id))[:8]
        else:
            memories = (await repository.list_memories(user_id=user_id, tenant_id=tenant_id))[:8]
        return {
            "count": len(memories),
            "memories": [memory.model_dump(mode="json") for memory in memories],
        }, []
    if tool_id == FORGET_TOOL_ID:
        memory_id = str(arguments.get("memory_id") or "").strip()
        if not memory_id:
            raise PlatformError("INVALID_REQUEST", "memory.forget requires memory_id")
        await repository.remove_memory(memory_id, user_id=user_id, tenant_id=tenant_id)
        return {"deleted": True, "memory_id": memory_id}, []
    raise PlatformError("RESOURCE_NOT_FOUND", f"Unknown memory tool {tool_id!r}", status_code=404)
