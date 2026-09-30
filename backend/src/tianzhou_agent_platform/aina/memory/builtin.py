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
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.store.memory_repository import InMemoryRepository

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
                name="Memory",
                version="1.0.0",
                description="Save, recall and delete the user's durable facts, preferences, goals and instructions.",
                publisher=Publisher(id="unibot", name="Unibot"),
            ),
            runtime=BuiltinRuntimeDefinition(),
            capabilities=AinaCapabilities(
                skills=[
                    AinaCapability(
                        id="memory-management",
                        name="Durable memory management",
                        description="Manage long-term memory kept across conversations; transient chat is not saved.",
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
                        name="Remember",
                        description="Save one durable fact, preference, goal or instruction.",
                        input_schema={
                            "type": "object",
                            "properties": {
                                "content": {
                                    "type": "string",
                                    "description": "A concise statement to keep long term.",
                                },
                                "category": {
                                    "type": "string",
                                    "enum": ["fact", "preference", "goal", "instruction"],
                                    "description": "Memory category.",
                                },
                            },
                            "required": ["content", "category"],
                            "additionalProperties": False,
                        },
                    ),
                    AinaCapability(
                        id=RECALL_TOOL_ID,
                        name="Recall memory",
                        description="Retrieve memories relevant to a query.",
                        input_schema={
                            "type": "object",
                            "properties": {
                                "query": {
                                    "type": "string",
                                    "description": "Query matched against long-term memory; leave empty to return recent memories.",
                                }
                            },
                            "additionalProperties": False,
                        },
                    ),
                    AinaCapability(
                        id=UPDATE_TOOL_ID,
                        name="Update memory",
                        description="Update the content or category of a memory in place by its exact id, without creating a duplicate.",
                        input_schema={
                            "type": "object",
                            "properties": {
                                "memory_id": {
                                    "type": "string",
                                    "description": "Exact id of the memory to update.",
                                },
                                "content": {
                                    "type": "string",
                                    "description": "The complete updated memory content.",
                                },
                                "category": {
                                    "type": "string",
                                    "enum": ["fact", "preference", "goal", "instruction"],
                                    "description": "Optional new category.",
                                },
                            },
                            "required": ["memory_id", "content"],
                            "additionalProperties": False,
                        },
                    ),
                    AinaCapability(
                        id=FORGET_TOOL_ID,
                        name="Forget memory",
                        description="Delete one memory by its exact id.",
                        input_schema={
                            "type": "object",
                            "properties": {
                                "memory_id": {
                                    "type": "string",
                                    "description": "Exact id of the memory to delete permanently.",
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
                title="Memory",
                description="Manage facts, preferences, goals and instructions kept across conversations.",
                markdown=(
                    "### Durable memory\n\nMemories are recalled by relevance in later conversations. Only information "
                    "that stays useful is saved; chat transcripts are never stored as memory as a whole."
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
    raise PlatformError("RESOURCE_NOT_FOUND", f"Unknown memory tool {tool_id!r}", status_code=404)
