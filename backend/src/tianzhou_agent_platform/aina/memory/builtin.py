from typing import Any, cast

from tianzhou_agent_platform.aina.memory.models import MemoryCategory, MemoryCreate, MemoryUpdate
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
from tianzhou_agent_platform.core.errors import PlatformError, unknown_tool_error
from tianzhou_agent_platform.core.repository import InMemoryRepository

UNIBOT_MEMORY_ID = "unibot-memory"
REMEMBER_TOOL_ID = "memory.remember"
RECALL_TOOL_ID = "memory.recall"
UPDATE_TOOL_ID = "memory.update"
FORGET_TOOL_ID = "memory.forget"
MEMORY_TOOL_IDS = {REMEMBER_TOOL_ID, RECALL_TOOL_ID, UPDATE_TOOL_ID, FORGET_TOOL_ID}


def unibot_memory_record() -> AinaRecord:
    return AinaRecord(
        manifest=AinaManifest(
            protocol_version="1.0",
            aina=AinaIdentity(
                id=UNIBOT_MEMORY_ID,
                name="记忆管理",
                version="1.0.0",
                description="保存、召回和删除用户的长期事实、偏好、目标与指令。",
                publisher=Publisher(id="unibot", name="Unibot"),
            ),
            runtime=BuiltinRuntimeDefinition(),
            capabilities=AinaCapabilities(
                skills=[
                    AinaCapability(
                        id="memory-management",
                        name="持久记忆管理",
                        description="管理跨对话保留的长期记忆，不保存临时聊天内容。",
                        instructions=(
                            "When the user explicitly asks to remember a durable fact, call memory.remember. "
                            "When they correct or refine an existing memory and its id is known, call memory.update. "
                            "When they ask what is remembered, call memory.recall. When they explicitly ask to "
                            "forget an item and its id is known, call memory.forget. Never invent a memory write."
                        ),
                    )
                ],
                tools=[
                    AinaCapability(
                        id=REMEMBER_TOOL_ID,
                        name="记住信息",
                        description=(
                            "保存一条用户明确要求记住的长期事实、偏好、目标或指令；"
                            "不要保存瞬时聊天内容。"
                        ),
                        input_schema={
                            "type": "object",
                            "properties": {
                                "content": {
                                    "type": "string",
                                    "description": "需要长期保存的简洁陈述。",
                                },
                                "category": {
                                    "type": "string",
                                    "enum": ["fact", "preference", "goal", "instruction"],
                                    "description": "记忆分类。",
                                },
                            },
                            "required": ["content", "category"],
                            "additionalProperties": False,
                        },
                    ),
                    AinaCapability(
                        id=RECALL_TOOL_ID,
                        name="召回记忆",
                        description="检索与查询相关的记忆。",
                        input_schema={
                            "type": "object",
                            "properties": {
                                "query": {
                                    "type": "string",
                                    "description": "用于匹配长期记忆的查询；留空时返回最近记忆。",
                                }
                            },
                            "additionalProperties": False,
                        },
                    ),
                    AinaCapability(
                        id=UPDATE_TOOL_ID,
                        name="更新记忆",
                        description="根据准确的记忆 ID 原地更新内容或分类，不创建重复记忆。",
                        input_schema={
                            "type": "object",
                            "properties": {
                                "memory_id": {
                                    "type": "string",
                                    "description": "需要更新的准确记忆 ID。",
                                },
                                "content": {
                                    "type": "string",
                                    "description": "更新后的完整记忆内容。",
                                },
                                "category": {
                                    "type": "string",
                                    "enum": ["fact", "preference", "goal", "instruction"],
                                    "description": "可选的新分类。",
                                },
                            },
                            "required": ["memory_id", "content"],
                            "additionalProperties": False,
                        },
                    ),
                    AinaCapability(
                        id=FORGET_TOOL_ID,
                        name="删除记忆",
                        description="在用户明确要求遗忘后，根据准确的记忆 ID 永久删除一条记忆。",
                        input_schema={
                            "type": "object",
                            "properties": {
                                "memory_id": {
                                    "type": "string",
                                    "description": "需要永久删除的准确记忆 ID。",
                                }
                            },
                            "required": ["memory_id"],
                            "additionalProperties": False,
                        },
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
    if tool_id == UPDATE_TOOL_ID:
        memory_id = str(arguments.get("memory_id") or "").strip()
        content = str(arguments.get("content") or "").strip()
        if not memory_id:
            raise PlatformError("INVALID_REQUEST", "memory.update requires memory_id")
        update_category_raw = arguments.get("category")
        if update_category_raw is not None and update_category_raw not in {
            "fact",
            "preference",
            "goal",
            "instruction",
        }:
            raise PlatformError("INVALID_REQUEST", f"Unsupported memory category: {update_category_raw}")
        update_category = cast(MemoryCategory | None, update_category_raw)
        try:
            memory = await repository.update_memory(
                memory_id,
                MemoryUpdate(
                    content=content,
                    category=update_category,
                    user_id=user_id,
                    tenant_id=tenant_id,
                ),
            )
        except ValueError as exc:
            raise PlatformError("INVALID_REQUEST", str(exc)) from exc
        return {"updated": True, "memory": memory.model_dump(mode="json")}, []
    if tool_id == FORGET_TOOL_ID:
        memory_id = str(arguments.get("memory_id") or "").strip()
        if not memory_id:
            raise PlatformError("INVALID_REQUEST", "memory.forget requires memory_id")
        await repository.remove_memory(memory_id, user_id=user_id, tenant_id=tenant_id)
        return {"deleted": True, "memory_id": memory_id}, []
    raise unknown_tool_error(tool_id, kind="memory")
