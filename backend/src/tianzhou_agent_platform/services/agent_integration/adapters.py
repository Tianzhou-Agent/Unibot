"""Superbot's services as the agent core calls them (``unibot_agent.ports``), and the conversions between the
core's data and the platform's records."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Protocol

from langchain_core.language_models.chat_models import BaseChatModel

from tianzhou_agent_platform.aina.memory.models import MemoryRecord
from tianzhou_agent_platform.aina.package.tasks import AinaTaskService, model_result_text, result_message_id
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.conversations.models import Conversation
from tianzhou_agent_platform.conversations.models import Message as ArchivedMessage
from tianzhou_agent_platform.conversations.schemas import ApprovalRecord, ChatRequest, ChatResponse
from tianzhou_agent_platform.conversations.service import ConversationService
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.model_providers.errors import map_model_error, rejects_tool_choice
from tianzhou_agent_platform.model_providers.models import (
    current_context_window_tokens,
    current_max_output_tokens,
    current_model_runtime,
)
from unibot_agent.tokens import output_token_reserve
from unibot_agent.types import AgentConfig, ApprovalRequest, Message, ModelHandle, Session, TurnRequest, TurnResult

ModelFactory = Callable[..., BaseChatModel]


def agent_config(settings: AgentSettings) -> AgentConfig:
    return AgentConfig(
        max_agent_iterations=settings.max_agent_iterations,
        context_compression_enabled=settings.context_compression_enabled,
        context_compression_threshold_ratio=settings.context_compression_threshold_ratio,
        context_compression_keep_recent_turns=settings.context_compression_keep_recent_turns,
        context_compression_min_messages=settings.context_compression_min_messages,
    )


def session_of(conversation: Conversation) -> Session:
    return Session(
        id=conversation.id,
        user_id=conversation.user_id,
        tenant_id=conversation.tenant_id,
        workspace_id=conversation.workspace_id,
        message_ids=frozenset(message.id for message in conversation.messages),
        record=conversation,
    )


def turn_request(request: ChatRequest) -> TurnRequest:
    return TurnRequest(
        message=request.message,
        ui_context=request.ui_context,
        capability=request.capability,
        scope_id=request.preferred_aina_id,
    )


def approval_record(approval: ApprovalRequest) -> ApprovalRecord:
    return ApprovalRecord(
        id=approval.id,
        conversation_id=approval.conversation_id,
        user_id=approval.user_id,
        tenant_id=approval.tenant_id,
        trace_id=approval.trace_id,
        tool_calls=approval.tool_calls,
        capability_names=approval.capability_names,
        created_at=approval.created_at,
        runtime_ref=approval.runtime_ref,
        widgets=approval.confirmations,
    )


def approval_request(record: ApprovalRecord) -> ApprovalRequest:
    return ApprovalRequest(
        id=record.id,
        conversation_id=record.conversation_id,
        user_id=record.user_id,
        tenant_id=record.tenant_id,
        trace_id=record.trace_id,
        tool_calls=record.tool_calls,
        capability_names=record.capability_names,
        runtime_ref=record.runtime_ref,
        created_at=record.created_at,
        confirmations=list(record.widgets),
        editable_call_ids=frozenset(
            widget.call_id for widget in record.widgets if widget.purpose == "confirmation" and widget.call_id
        ),
    )


def chat_response(result: TurnResult) -> ChatResponse:
    return ChatResponse(
        conversation_id=result.conversation_id,
        message_id=result.message_id,
        content=result.content,
        status=result.status,
        trace_id=result.trace_id,
        iterations=result.iterations,
        usage=result.usage,
        approval=approval_record(result.approval) if result.approval is not None else None,
        widgets=result.artifacts,
        opened_app_id=result.values.get("opened_app_id"),
    )


class ConversationSessions:
    """The conversation archive and its approvals (``SessionStore`` and ``ApprovalStore``)."""

    def __init__(self, conversations: ConversationService, aina_tasks: AinaTaskService | None = None) -> None:
        self.conversations = conversations
        self.aina_tasks = aina_tasks

    async def history(self, session: Session) -> list[Message]:
        conversation: Conversation = session.record
        messages = [
            Message(
                id=message.id,
                role=message.role,
                content=message.content,
                reasoning=message.reasoning,
                tool_calls=message.tool_calls,
                tool_call_id=message.tool_call_id,
                name=message.name,
                attachments=[item.model_dump() for item in message.attachments],
            )
            for message in conversation.messages
        ]
        if self.aina_tasks is None or not messages:
            return messages
        # Archived background task results, as the model must see them: delimited third-party output. The archive
        # keeps the user-facing report (the AINA server's own text); seeding it verbatim would hand the model that
        # text as an assistant message of its own.
        by_message = {
            result_message_id(task): task for task in await self.aina_tasks.repository.list_aina_tasks(session.id)
        }
        return [
            Message(id=message.id, role="assistant", content=model_result_text(by_message[message.id]))
            if message.id in by_message
            else message
            for message in messages
        ]

    async def append_messages(self, session_id: str, messages: Sequence[Message]) -> None:
        records = []
        for message in messages:
            # The clarification question is shown while its turn waits for the answer; it is not kept.
            widgets = [widget for widget in message.artifacts if widget.id != f"clarification-{session_id}"]
            records.append(
                ArchivedMessage(
                    id=message.id,
                    role=message.role,  # type: ignore[arg-type]
                    content=message.content,
                    reasoning=message.reasoning,
                    tool_calls=message.tool_calls,
                    tool_call_id=message.tool_call_id,
                    name=message.name,
                    widgets=widgets,
                    attachments=message.attachments,  # type: ignore[arg-type]
                    content_type="tool" if message.role == "tool" else "widget" if widgets else "text",
                    trace_id=message.trace_id,
                )
            )
        await self.conversations.append_messages_idempotent(session_id, records)

    async def take_external_messages(self, session_id: str) -> list[Message]:
        """Results of background AINA tasks that ended since the last turn, for the model (design §7.3). They were
        posted to the archive when the task ended."""
        if self.aina_tasks is None:
            return []
        return [
            Message(id=result_message_id(task), role="assistant", content=model_result_text(task))
            for task in await self.aina_tasks.take_undelivered_results(session_id)
        ]

    async def create(self, approval: ApprovalRequest) -> None:
        await self.conversations.create_approval(approval_record(approval))


class MemorySearch(Protocol):
    async def search_memories(
        self,
        query: str,
        *,
        user_id: str,
        tenant_id: str,
        limit: int = 8,
    ) -> list[MemoryRecord]: ...


class Memories:
    """The user's stored memories (``MemoryProvider``)."""

    def __init__(self, repository: MemorySearch) -> None:
        self.repository = repository

    async def recall(self, query: str, *, user_id: str, tenant_id: str, limit: int) -> list[Any]:
        return list(await self.repository.search_memories(query, user_id=user_id, tenant_id=tenant_id, limit=limit))


class ModelRuntimes:
    """The chat model of the current actor (``use_model_runtime``), else the configured one (``ModelProvider``)."""

    def __init__(
        self,
        *,
        settings: AgentSettings,
        model: BaseChatModel | None = None,
        model_factory: ModelFactory | None = None,
    ) -> None:
        self.settings = settings
        # The configured model serves requests unless the actor selected a provider model (``model_factory``).
        self.model = model
        self.model_factory = model_factory
        self._runtime_models: dict[tuple[Any, ...], BaseChatModel] = {}

    def resolve(self, run: Any = None) -> ModelHandle:
        runtime = current_model_runtime()
        base_url = (runtime.base_url if runtime else self.settings.llm_base_url) or ""
        return ModelHandle(
            model=self._model(),
            name=runtime.model if runtime else (self.settings.llm_model or "unknown"),
            endpoint=f"{base_url.rstrip('/')}/chat/completions",
            context_window_tokens=current_context_window_tokens(self.settings.context_window_tokens),
            max_output_tokens=current_max_output_tokens(self.settings.max_output_tokens),
            map_error=map_model_error,
            rejects_tool_choice=rejects_tool_choice,
        )

    def chat_model(self) -> BaseChatModel:
        """The model serving this request; the AINA Host API ``llm`` scope uses it."""
        model = self._model()
        if model is None:
            raise PlatformError(
                code="INVALID_REQUEST",
                message="The LLM provider is not configured",
                status_code=503,
                source="model",
                user_message="The language model is not configured for this service.",
            )
        return model

    def _model(self) -> BaseChatModel | None:
        runtime = current_model_runtime()
        if runtime is not None and self.model_factory is not None:
            key = (
                runtime.provider_id,
                runtime.model_id,
                runtime.model,
                runtime.base_url,
                runtime.api_key,
                runtime.timeout_seconds,
                runtime.context_window_tokens,
                runtime.max_output_tokens,
            )
            model = self._runtime_models.get(key)
            if model is None:
                model = self._runtime_models[key] = self.model_factory(
                    runtime,
                    max_completion_tokens=output_token_reserve(
                        runtime.context_window_tokens, runtime.max_output_tokens
                    ),
                )
            return model
        return self.model
