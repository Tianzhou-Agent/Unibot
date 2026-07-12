from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from typing import Any, Iterable
from uuid import uuid4

from tianzhou_agent_platform.core.errors import PlatformError, conflict, not_found
from tianzhou_agent_platform.core.models import (
    AinaInstallation,
    AinaRecord,
    ApprovalRecord,
    Conversation,
    ConversationCreate,
    ConversationUpdate,
    Message,
    MemoryCreate,
    MemoryRecord,
    MemoryStats,
    MemoryUpdate,
    SkillRecord,
    ToolRecord,
    TraceEvent,
    TraceRecord,
)


class InMemoryRepository:
    """Concurrency-safe MVP repository.

    All runtime code depends on this small method surface rather than on the
    dictionaries directly, so a durable SQL-backed implementation can replace
    it without changing the agent or API contracts.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._conversations: dict[str, Conversation] = {}
        self._tools: dict[str, ToolRecord] = {}
        self._skills: dict[str, SkillRecord] = {}
        self._ainas: dict[str, AinaRecord] = {}
        self._installations: dict[tuple[str, str, str], AinaInstallation] = {}
        self._traces: dict[str, TraceRecord] = {}
        self._approvals: dict[str, ApprovalRecord] = {}
        self._memories: dict[str, MemoryRecord] = {}

    @staticmethod
    def _copy[T](value: T) -> T:
        if hasattr(value, "model_copy"):
            return value.model_copy(deep=True)  # type: ignore[no-any-return, union-attr]
        return value

    async def create_conversation(self, data: ConversationCreate) -> Conversation:
        conversation = Conversation(id=f"conv_{uuid4().hex}", **data.model_dump())
        async with self._lock:
            self._conversations[conversation.id] = conversation
        return self._copy(conversation)

    async def get_conversation(self, conversation_id: str, *, include_deleted: bool = False) -> Conversation:
        async with self._lock:
            conversation = self._conversations.get(conversation_id)
            if conversation is None or (conversation.status == "deleted" and not include_deleted):
                raise not_found("Conversation", conversation_id)
            return self._copy(conversation)

    async def require_conversation_actor(
        self,
        conversation_id: str,
        *,
        user_id: str,
        tenant_id: str,
        include_deleted: bool = False,
    ) -> Conversation:
        conversation = await self.get_conversation(conversation_id, include_deleted=include_deleted)
        if conversation.user_id != user_id or conversation.tenant_id != tenant_id:
            raise PlatformError(
                code="PERMISSION_DENIED",
                message="Conversation ownership does not match the caller",
                status_code=403,
            )
        return conversation

    async def list_conversations(
        self,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
        category: str | None = None,
    ) -> list[Conversation]:
        async with self._lock:
            values = [
                self._copy(item)
                for item in self._conversations.values()
                if item.status != "deleted"
                and (user_id is None or item.user_id == user_id)
                and (tenant_id is None or item.tenant_id == tenant_id)
                and (category is None or item.category == category)
            ]
        return sorted(values, key=lambda item: item.updated_at, reverse=True)

    async def update_conversation(self, conversation_id: str, data: ConversationUpdate) -> Conversation:
        changes = data.model_dump(exclude_none=True)
        async with self._lock:
            conversation = self._conversations.get(conversation_id)
            if conversation is None or conversation.status == "deleted":
                raise not_found("Conversation", conversation_id)
            updated = conversation.model_copy(update={**changes, "updated_at": datetime.now(UTC)}, deep=True)
            self._conversations[conversation_id] = updated
            return self._copy(updated)

    async def set_conversation_status(self, conversation_id: str, status: str) -> Conversation:
        async with self._lock:
            conversation = self._conversations.get(conversation_id)
            if conversation is None:
                raise not_found("Conversation", conversation_id)
            updated = conversation.model_copy(update={"status": status, "updated_at": datetime.now(UTC)}, deep=True)
            self._conversations[conversation_id] = updated
            return self._copy(updated)

    async def start_conversation_run(self, conversation_id: str, trace_id: str) -> Conversation:
        async with self._lock:
            conversation = self._conversations.get(conversation_id)
            if conversation is None or conversation.status == "deleted":
                raise not_found("Conversation", conversation_id)
            if conversation.run_status == "running":
                raise conflict("This conversation already has a running request")
            updated = conversation.model_copy(
                update={
                    "run_status": "running",
                    "active_trace_id": trace_id,
                    "run_error": None,
                    "run_started_at": datetime.now(UTC),
                    "updated_at": datetime.now(UTC),
                },
                deep=True,
            )
            self._conversations[conversation_id] = updated
            return self._copy(updated)

    async def finish_conversation_run(
        self,
        conversation_id: str,
        *,
        status: str = "idle",
        error: str | None = None,
    ) -> Conversation:
        async with self._lock:
            conversation = self._conversations.get(conversation_id)
            if conversation is None:
                raise not_found("Conversation", conversation_id)
            updated = conversation.model_copy(
                update={
                    "run_status": status,
                    "active_trace_id": None,
                    "run_error": error,
                    "run_started_at": None,
                    "updated_at": datetime.now(UTC),
                },
                deep=True,
            )
            self._conversations[conversation_id] = updated
            return self._copy(updated)

    async def create_memory(self, data: MemoryCreate) -> MemoryRecord:
        normalized = data.content.casefold()
        async with self._lock:
            for memory in self._memories.values():
                if (
                    memory.user_id == data.user_id
                    and memory.tenant_id == data.tenant_id
                    and memory.content.casefold() == normalized
                ):
                    return self._copy(memory)
            actor_count = sum(
                item.user_id == data.user_id and item.tenant_id == data.tenant_id
                for item in self._memories.values()
            )
            if actor_count >= 500:
                raise conflict("Memory limit reached; remove or consolidate an existing memory")
            memory = MemoryRecord(id=f"mem_{uuid4().hex}", **data.model_dump())
            self._memories[memory.id] = memory
            return self._copy(memory)

    async def get_memory(self, memory_id: str, *, user_id: str, tenant_id: str) -> MemoryRecord:
        async with self._lock:
            memory = self._memories.get(memory_id)
            if memory is None:
                raise not_found("Memory", memory_id)
            if memory.user_id != user_id or memory.tenant_id != tenant_id:
                raise PlatformError("PERMISSION_DENIED", "Memory ownership does not match the caller", status_code=403)
            return self._copy(memory)

    async def list_memories(
        self,
        *,
        user_id: str,
        tenant_id: str,
        query: str | None = None,
        category: str | None = None,
    ) -> list[MemoryRecord]:
        normalized_query = (query or "").strip().casefold()
        async with self._lock:
            values = [
                self._copy(item)
                for item in self._memories.values()
                if item.user_id == user_id
                and item.tenant_id == tenant_id
                and (category is None or item.category == category)
                and (not normalized_query or normalized_query in item.content.casefold())
            ]
        return sorted(values, key=lambda item: item.updated_at, reverse=True)

    async def search_memories(
        self,
        query: str,
        *,
        user_id: str,
        tenant_id: str,
        limit: int = 8,
    ) -> list[MemoryRecord]:
        candidates = await self.list_memories(user_id=user_id, tenant_id=tenant_id)
        query_terms = _memory_terms(query)
        normalized_query = query.casefold().strip()
        scored: list[tuple[float, MemoryRecord]] = []
        for memory in candidates:
            normalized_content = memory.content.casefold()
            content_terms = _memory_terms(memory.content)
            overlap = len(query_terms & content_terms)
            score = overlap / max(len(query_terms), 1)
            if normalized_query and (
                normalized_query in normalized_content or normalized_content in normalized_query
            ):
                score += 2.0
            if score > 0:
                scored.append((score, memory))
        scored.sort(key=lambda item: (item[0], item[1].updated_at), reverse=True)
        return [memory for _score, memory in scored[:limit]]

    async def update_memory(self, memory_id: str, data: MemoryUpdate) -> MemoryRecord:
        async with self._lock:
            memory = self._memories.get(memory_id)
            if memory is None:
                raise not_found("Memory", memory_id)
            if memory.user_id != data.user_id or memory.tenant_id != data.tenant_id:
                raise PlatformError("PERMISSION_DENIED", "Memory ownership does not match the caller", status_code=403)
            changes = data.model_dump(exclude_none=True, exclude={"user_id", "tenant_id"})
            updated = memory.model_copy(update={**changes, "updated_at": datetime.now(UTC)}, deep=True)
            self._memories[memory_id] = updated
            return self._copy(updated)

    async def remove_memory(self, memory_id: str, *, user_id: str, tenant_id: str) -> None:
        async with self._lock:
            memory = self._memories.get(memory_id)
            if memory is None:
                raise not_found("Memory", memory_id)
            if memory.user_id != user_id or memory.tenant_id != tenant_id:
                raise PlatformError("PERMISSION_DENIED", "Memory ownership does not match the caller", status_code=403)
            del self._memories[memory_id]

    async def memory_stats(self, *, user_id: str, tenant_id: str) -> MemoryStats:
        memories = await self.list_memories(user_id=user_id, tenant_id=tenant_id)
        counts = {category: 0 for category in ("fact", "preference", "goal", "instruction")}
        for memory in memories:
            counts[memory.category] += 1
        return MemoryStats(total=len(memories), **counts)

    async def append_provider_messages(
        self,
        conversation_id: str,
        messages: Iterable[dict[str, Any]],
        *,
        trace_id: str,
    ) -> list[Message]:
        appended: list[Message] = []
        async with self._lock:
            conversation = self._conversations.get(conversation_id)
            if conversation is None or conversation.status != "active":
                raise not_found("Conversation", conversation_id)
            for raw in messages:
                message = Message(
                    id=f"msg_{uuid4().hex}",
                    role=raw["role"],
                    content=raw.get("content") or "",
                    tool_calls=raw.get("tool_calls"),
                    tool_call_id=raw.get("tool_call_id"),
                    name=raw.get("name"),
                    widgets=raw.get("widgets") or [],
                    content_type=(
                        "tool" if raw["role"] == "tool" else "widget" if raw.get("widgets") else "text"
                    ),
                    trace_id=trace_id,
                )
                conversation.messages.append(message)
                appended.append(self._copy(message))
            conversation.updated_at = datetime.now(UTC)
        return appended

    async def close_dangling_tool_calls(self, conversation_id: str, *, trace_id: str) -> None:
        conversation = await self.get_conversation(conversation_id)
        if not conversation.messages:
            return
        last = conversation.messages[-1]
        if last.role != "assistant" or not last.tool_calls:
            return
        closing = [
            {
                "role": "tool",
                "name": call.get("function", {}).get("name", "unknown"),
                "tool_call_id": call.get("id"),
                "content": "Cancelled because the user started a new turn before granting approval.",
            }
            for call in last.tool_calls
        ]
        await self.append_provider_messages(conversation_id, closing, trace_id=trace_id)
        await self.cancel_pending_approvals(conversation_id)

    async def register_tool(self, tool: ToolRecord) -> ToolRecord:
        async with self._lock:
            if tool.tool_id in self._tools:
                raise conflict(f"Tool {tool.tool_id!r} is already registered")
            self._tools[tool.tool_id] = tool
        return self._copy(tool)

    async def get_tool(self, tool_id: str) -> ToolRecord:
        async with self._lock:
            tool = self._tools.get(tool_id)
            if tool is None:
                raise not_found("Tool", tool_id)
            return self._copy(tool)

    async def list_tools(self) -> list[ToolRecord]:
        async with self._lock:
            return [self._copy(item) for item in self._tools.values()]

    async def remove_tool(self, tool_id: str) -> None:
        async with self._lock:
            if self._tools.pop(tool_id, None) is None:
                raise not_found("Tool", tool_id)

    async def register_skill(self, skill: SkillRecord) -> SkillRecord:
        async with self._lock:
            if skill.skill_id in self._skills:
                raise conflict(f"Skill {skill.skill_id!r} is already registered")
            self._skills[skill.skill_id] = skill
        return self._copy(skill)

    async def get_skill(self, skill_id: str) -> SkillRecord:
        async with self._lock:
            skill = self._skills.get(skill_id)
            if skill is None:
                raise not_found("Skill", skill_id)
            return self._copy(skill)

    async def list_skills(self) -> list[SkillRecord]:
        async with self._lock:
            return [self._copy(item) for item in self._skills.values()]

    async def remove_skill(self, skill_id: str) -> None:
        async with self._lock:
            if self._skills.pop(skill_id, None) is None:
                raise not_found("Skill", skill_id)

    async def register_aina(self, aina: AinaRecord) -> AinaRecord:
        aina_id = aina.manifest.aina.id
        async with self._lock:
            if aina_id in self._ainas:
                raise conflict(f"AINA {aina_id!r} is already registered")
            self._ainas[aina_id] = aina
        return self._copy(aina)

    async def get_aina(self, aina_id: str) -> AinaRecord:
        async with self._lock:
            aina = self._ainas.get(aina_id)
            if aina is None:
                raise not_found("AINA", aina_id)
            return self._copy(aina)

    async def list_ainas(self) -> list[AinaRecord]:
        async with self._lock:
            return [self._copy(item) for item in self._ainas.values()]

    async def remove_aina(self, aina_id: str) -> None:
        async with self._lock:
            if self._ainas.pop(aina_id, None) is None:
                raise not_found("AINA", aina_id)
            for key in [key for key in self._installations if key[2] == aina_id]:
                del self._installations[key]

    async def put_installation(self, installation: AinaInstallation) -> AinaInstallation:
        key = (installation.tenant_id, installation.user_id, installation.aina_id)
        async with self._lock:
            self._installations[key] = installation
        return self._copy(installation)

    async def get_installation(self, *, tenant_id: str, user_id: str, aina_id: str) -> AinaInstallation:
        async with self._lock:
            installation = self._installations.get((tenant_id, user_id, aina_id))
            if installation is None:
                raise not_found("AINA installation", aina_id)
            return self._copy(installation)

    async def list_installations(
        self,
        *,
        tenant_id: str | None = None,
        user_id: str | None = None,
    ) -> list[AinaInstallation]:
        async with self._lock:
            return [
                self._copy(item)
                for item in self._installations.values()
                if (tenant_id is None or item.tenant_id == tenant_id) and (user_id is None or item.user_id == user_id)
            ]

    async def remove_installation(self, *, tenant_id: str, user_id: str, aina_id: str) -> None:
        async with self._lock:
            if self._installations.pop((tenant_id, user_id, aina_id), None) is None:
                raise not_found("AINA installation", aina_id)

    async def create_trace(self, trace: TraceRecord) -> TraceRecord:
        async with self._lock:
            self._traces[trace.trace_id] = trace
        return self._copy(trace)

    async def add_trace_event(self, trace_id: str, event: TraceEvent) -> None:
        async with self._lock:
            trace = self._traces.get(trace_id)
            if trace is None:
                raise not_found("Trace", trace_id)
            trace.events.append(event)

    async def finish_trace(self, trace_id: str, status: str) -> TraceRecord:
        async with self._lock:
            trace = self._traces.get(trace_id)
            if trace is None:
                raise not_found("Trace", trace_id)
            trace.status = status  # type: ignore[assignment]
            trace.completed_at = datetime.now(UTC)
            return self._copy(trace)

    async def get_trace(self, trace_id: str) -> TraceRecord:
        async with self._lock:
            trace = self._traces.get(trace_id)
            if trace is None:
                raise not_found("Trace", trace_id)
            return self._copy(trace)

    async def list_traces(
        self,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> list[TraceRecord]:
        async with self._lock:
            traces = [
                self._copy(item)
                for item in self._traces.values()
                if (user_id is None or item.user_id == user_id) and (tenant_id is None or item.tenant_id == tenant_id)
            ]
        return sorted(traces, key=lambda item: item.created_at, reverse=True)

    async def create_approval(self, approval: ApprovalRecord) -> ApprovalRecord:
        async with self._lock:
            self._approvals[approval.id] = approval
        return self._copy(approval)

    async def get_approval(self, approval_id: str) -> ApprovalRecord:
        async with self._lock:
            approval = self._approvals.get(approval_id)
            if approval is None:
                raise not_found("Approval", approval_id)
            return self._copy(approval)

    async def list_approvals(
        self,
        *,
        conversation_id: str | None = None,
        user_id: str | None = None,
        tenant_id: str | None = None,
        status: str | None = None,
    ) -> list[ApprovalRecord]:
        async with self._lock:
            approvals = [
                self._copy(item)
                for item in self._approvals.values()
                if (conversation_id is None or item.conversation_id == conversation_id)
                and (user_id is None or item.user_id == user_id)
                and (tenant_id is None or item.tenant_id == tenant_id)
                and (status is None or item.status == status)
            ]
        return sorted(approvals, key=lambda item: item.created_at, reverse=True)

    async def set_approval_status(self, approval_id: str, status: str) -> ApprovalRecord:
        async with self._lock:
            approval = self._approvals.get(approval_id)
            if approval is None:
                raise not_found("Approval", approval_id)
            approval.status = status  # type: ignore[assignment]
            approval.resolved_at = datetime.now(UTC)
            return self._copy(approval)

    async def cancel_pending_approvals(self, conversation_id: str) -> None:
        async with self._lock:
            for approval in self._approvals.values():
                if approval.conversation_id == conversation_id and approval.status == "pending":
                    approval.status = "denied"
                    approval.resolved_at = datetime.now(UTC)


def _memory_terms(value: str) -> set[str]:
    normalized = value.casefold()
    words = set(re.findall(r"[a-z0-9_]+", normalized))
    cjk = re.findall(r"[\u3400-\u9fff]", normalized)
    words.update(cjk)
    words.update("".join(cjk[index : index + 2]) for index in range(len(cjk) - 1))
    return words
