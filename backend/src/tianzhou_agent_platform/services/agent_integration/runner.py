"""Runs one agent turn or approval continuation on the native ``create_agent`` loop.

Working memory is the checkpointed agent state of the conversation's thread (``lc-v2:<conversation_id>``): a turn
submits only its new input. A conversation without a checkpoint is seeded from its archive. Approvals pause with a
native interrupt and continue with ``Command(resume=...)``. After every invocation the thread keeps only its latest
checkpoint.

Middleware, first = outermost (``before_model`` hooks run in this order, ``after_model`` hooks in reverse):

======================================  ==========  =========================================================
Middleware                              Owner       Purpose
======================================  ==========  =========================================================
``TerminalDenialMiddleware``            runtime     closes a denied batch and ends the run
``TranscriptHookMiddleware``            runtime     archives new messages before summarization replaces them
``TurnSummarizationMiddleware``         runtime     built-in summarization, retaining complete turns
``CapabilityScopeMiddleware``           Unibot      advertised tools, system prompt, UI context, forced tool
``RequestBudgetGuard``                  runtime     refuses requests over the model's input budget
``ForcedToolChoiceMiddleware``          runtime     provider compatibility for a forced tool call
``ProviderErrorMiddleware``             runtime     provider exceptions → platform errors
``ModelCallRecorder``                   Unibot      model spans, events and ``/llm-calls`` records
``InvalidToolCallMiddleware``           runtime     answers calls with unparseable arguments
``OrderedBatchMiddleware``              runtime     batch order, same-batch and run-scoped deduplication
``ModelCallBudgetMiddleware``           runtime     built-in model-call limit ending with the product reply
``HumanInTheLoopMiddleware``            LangChain   pauses batches with risky calls
``OutputGuardMiddleware``               runtime     empty/truncated responses; truncated calls never run
``ToolErrorMiddleware``                 LangChain   failures → model-visible error envelopes
``CapabilityCallMiddleware``            Unibot      scope/argument checks, tool span and events per call
``ToolRetryMiddleware`` groups          LangChain   automatic retries of side-effect-free remote tools
======================================  ==========  =========================================================
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol
from uuid import uuid4

from langchain.agents.middleware import ToolErrorMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command, Interrupt

from tianzhou_agent_platform.aina.memory.models import MemoryRecord
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.conversations.models import Conversation
from tianzhou_agent_platform.conversations.schemas import ApprovalRecord, ChatRequest, ChatResponse
from tianzhou_agent_platform.conversations.service import ConversationService
from tianzhou_agent_platform.core.agent_runtime import build_agent
from tianzhou_agent_platform.core.agent_runtime.middleware.approval_policy import TerminalDenialMiddleware
from tianzhou_agent_platform.core.agent_runtime.middleware.model_policy import (
    InvalidToolCallMiddleware,
    ModelCallBudgetMiddleware,
    OutputGuardMiddleware,
    RequestBudgetGuard,
)
from tianzhou_agent_platform.core.agent_runtime.middleware.provider_policy import (
    ForcedToolChoiceMiddleware,
    ProviderErrorMiddleware,
)
from tianzhou_agent_platform.core.agent_runtime.middleware.summarization import is_summary_message
from tianzhou_agent_platform.core.agent_runtime.middleware.tool_policy import OrderedBatchMiddleware
from tianzhou_agent_platform.core.agent_runtime.middleware.transcript import TranscriptHookMiddleware
from tianzhou_agent_platform.core.base import Usage
from tianzhou_agent_platform.core.capability_discovery import aina_graph, model_scope_details
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.model_providers.errors import map_model_error, rejects_tool_choice
from tianzhou_agent_platform.model_providers.models import current_context_window_tokens, current_model_runtime
from tianzhou_agent_platform.model_providers.tokens import (
    estimate_request_tokens,
    output_token_reserve,
    request_input_budget,
)
from tianzhou_agent_platform.services.agent_integration.approvals import (
    CANCELLED_REPLY,
    DENIED_TOOL_RESULT,
    approval_record,
    build_approval_middleware,
    resume_decisions,
)
from tianzhou_agent_platform.services.agent_integration.capabilities import (
    Capability,
    CapabilityCatalog,
    CapabilityRegistry,
    resolve_forced_capability,
)
from tianzhou_agent_platform.services.agent_integration.capability_tools import format_error_envelope
from tianzhou_agent_platform.services.agent_integration.context import build_summarization
from tianzhou_agent_platform.services.agent_integration.executor import CapabilityExecutor
from tianzhou_agent_platform.services.agent_integration.history import (
    TranscriptArchiver,
    archive_to_native,
    dangling_tool_closures,
    native_to_wire,
    project_scope_history,
)
from tianzhou_agent_platform.services.agent_integration.model_calls import (
    ModelCallRecorder,
    StandaloneCallRecorder,
    is_summary_call,
)
from tianzhou_agent_platform.services.agent_integration.observation import (
    CapabilityCallMiddleware,
    CapabilityFailures,
)
from tianzhou_agent_platform.services.agent_integration.prompts import SystemPrompts
from tianzhou_agent_platform.services.agent_integration.retries import tool_retry_middleware
from tianzhou_agent_platform.services.agent_integration.run import AgentRun, EventSink
from tianzhou_agent_platform.services.agent_integration.scope import CapabilityScopeMiddleware, RunScope
from tianzhou_agent_platform.services.agent_integration.streaming import StreamRelay

logger = logging.getLogger(__name__)

THREAD_PREFIX = "lc-v2:"
ABANDONED_CALL_RESULT = "Cancelled because the user started a new turn before granting approval."
COMPLETED_REPLY = "The requested operation was completed."
EMPTY_REPLY = OutputGuardMiddleware.EMPTY_REPLY
FORCED_CALL_MISSING_REPLY = (
    "The model answered without using the selected capability, so no result was produced. Please retry, or "
    "choose a model that supports forced tool calls."
)


def thread_id_for(conversation_id: str) -> str:
    return f"{THREAD_PREFIX}{conversation_id}"


class MemorySearch(Protocol):
    async def search_memories(
        self, query: str, *, user_id: str, tenant_id: str, limit: int = 8
    ) -> list[MemoryRecord]: ...


ModelFactory = Callable[..., BaseChatModel]


async def _format_tool_error(exc: Exception, request: Any) -> str:
    return format_error_envelope(exc)


@dataclass
class _Invocation:
    """One compiled agent bound to a run, with the middleware whose results shape the response."""

    agent: Any
    config: RunnableConfig
    run: AgentRun
    archiver: TranscriptArchiver
    model_calls: ModelCallRecorder
    summary_calls: StandaloneCallRecorder
    budget_guard: RequestBudgetGuard
    output_guard: OutputGuardMiddleware
    call_budget: ModelCallBudgetMiddleware
    forced_choice: ForcedToolChoiceMiddleware


class ApprovalContinuation:
    """A paused batch ready to be approved or denied (see ``AgentRunner.open_approval``)."""

    def __init__(
        self,
        runner: AgentRunner,
        conversation: Conversation,
        approval: ApprovalRecord,
        *,
        approve: bool,
        invocation: _Invocation,
        interrupt: Interrupt | None,
        known_ids: set[str | None],
    ) -> None:
        self._runner = runner
        self._conversation = conversation
        self._approval = approval
        self._approve = approve
        self._invocation = invocation
        self._interrupt = interrupt
        self._known_ids = known_ids

    async def run(self) -> ChatResponse:
        if self._interrupt is None:
            return await self._runner._close_denied_without_interrupt(self._conversation, self._approval)
        return await self._runner._invoke(
            self._invocation,
            Command(resume=resume_decisions(self._interrupt, approve=self._approve)),
            known_ids=self._known_ids,
            resumed=True,
        )


class AgentRunner:
    def __init__(
        self,
        *,
        settings: AgentSettings,
        conversations: ConversationService,
        registry: CapabilityRegistry,
        memories: MemorySearch,
        catalog: CapabilityCatalog,
        prompts: SystemPrompts,
        executor: CapabilityExecutor,
        checkpointer: Any,
        events: Any,
        llm_call_sink: Callable[[Any], Awaitable[None]] | None = None,
        model: BaseChatModel | None = None,
        model_factory: ModelFactory | None = None,
    ) -> None:
        self.settings = settings
        self.conversations = conversations
        self.registry = registry
        self.memories = memories
        self.catalog = catalog
        self.prompts = prompts
        self.executor = executor
        self.checkpointer = checkpointer
        self.events = events
        self.llm_call_sink = llm_call_sink
        # The configured model serves requests unless the actor selected a provider model (``model_factory``).
        self.model = model
        self.model_factory = model_factory
        self._runtime_models: dict[tuple[Any, ...], BaseChatModel] = {}

    async def run_turn(
        self,
        conversation: Conversation,
        request: ChatRequest,
        *,
        trace_id: str,
        event_sink: EventSink | None = None,
    ) -> ChatResponse:
        """Run one user turn. The caller holds the conversation's run lease."""
        config = self._config(conversation, trace_id)
        checkpointed = await self._working_messages(config)
        # A conversation without working memory (never run natively, or its checkpoint was lost) is seeded from
        # its archive; the archive IDs are kept so seeded messages are never archived twice.
        seed = [] if checkpointed else [archive_to_native(message) for message in conversation.messages]
        closures = dangling_tool_closures(checkpointed or seed, ABANDONED_CALL_RESULT)
        user_message = HumanMessage(content=request.message, id=f"msg_{uuid4().hex}")
        archiver = TranscriptArchiver(
            self.conversations,
            conversation_id=conversation.id,
            trace_id=trace_id,
            archived_ids=[message.id for message in conversation.messages],
        )
        await archiver.archive([*closures, user_message])
        await self._record_user_request(trace_id, conversation, request, message_id=str(user_message.id))

        run = self._new_run(conversation, trace_id, event_sink)
        run.scope.ui_context = request.ui_context
        run.scope.ui_context_message_id = user_message.id
        run.memory_context = await self.memories.search_memories(
            request.message,
            user_id=conversation.user_id,
            tenant_id=conversation.tenant_id,
            limit=8,
        )
        capabilities, system_prompt, forced_capability = await self._plan(conversation, request, run)
        invocation = await self._prepare(
            conversation,
            run,
            archiver,
            capabilities=capabilities,
            system_prompt=system_prompt,
            forced_capability=forced_capability,
        )
        known_ids = {message.id for message in (checkpointed or seed)}
        return await self._invoke(
            invocation,
            {"messages": [*seed, *closures, user_message]},
            known_ids=known_ids | {message.id for message in closures} | {user_message.id},
        )

    async def open_approval(
        self,
        conversation: Conversation,
        approval: ApprovalRecord,
        *,
        approve: bool,
    ) -> ApprovalContinuation:
        """Load the batch an approval paused, before the approval is resolved.

        Raises CONFLICT when an approved batch can no longer be resumed: without its checkpointed interrupt the
        calls are never re-run, since they may already have had effects. A denied batch without its interrupt is
        still closed (in the archive only).
        """
        run = self._new_run(conversation, approval.trace_id, None)
        archiver = TranscriptArchiver(
            self.conversations,
            conversation_id=conversation.id,
            trace_id=approval.trace_id,
            archived_ids=[message.id for message in conversation.messages],
        )
        invocation = await self._prepare(
            conversation,
            run,
            archiver,
            capabilities=await self.catalog.available(conversation),
            system_prompt=await self.prompts.build(conversation),
            forced_capability=None,
            denied=not approve,
        )
        state = await invocation.agent.aget_state(invocation.config)
        interrupt = next(
            (item for item in state.interrupts if item.id == (approval.runtime_ref or {}).get("interrupt_id")),
            None,
        )
        if interrupt is None and approve:
            raise PlatformError(
                "CONFLICT",
                "The approved operation can no longer be resumed",
                status_code=409,
                user_message="该审批已失效，请重新发起请求。",
            )
        return ApprovalContinuation(
            self,
            conversation,
            approval,
            approve=approve,
            invocation=invocation,
            interrupt=interrupt,
            known_ids={message.id for message in state.values.get("messages") or []},
        )

    async def delete_working_memory(self, conversation_id: str) -> None:
        await self.checkpointer.adelete_thread(thread_id_for(conversation_id))

    async def _close_denied_without_interrupt(
        self,
        conversation: Conversation,
        approval: ApprovalRecord,
    ) -> ChatResponse:
        """Close a denied batch whose interrupt is gone; the next turn rebuilds working memory from the archive."""
        archiver = TranscriptArchiver(
            self.conversations,
            conversation_id=conversation.id,
            trace_id=approval.trace_id,
            archived_ids=[message.id for message in conversation.messages],
        )
        history = [archive_to_native(message) for message in conversation.messages]
        await archiver.archive(
            [
                *dangling_tool_closures(history, DENIED_TOOL_RESULT),
                AIMessage(content=CANCELLED_REPLY, id=f"msg_{uuid4().hex}"),
            ]
        )
        await self.delete_working_memory(conversation.id)
        return ChatResponse(
            conversation_id=conversation.id,
            message_id=archiver.appended[-1].id,
            content=CANCELLED_REPLY,
            status="completed",
            trace_id=approval.trace_id,
            iterations=0,
        )

    async def _plan(
        self,
        conversation: Conversation,
        request: ChatRequest,
        run: AgentRun,
    ) -> tuple[dict[str, Capability], str, str | None]:
        """(visible capabilities, system prompt, forced capability) of a turn from its routing request."""
        everything = await self.catalog.available(conversation)
        requested = request.capability
        preferred = request.preferred_aina_id
        selected: Capability | None = None
        if requested is not None:
            selected = everything[resolve_forced_capability(requested, everything) or ""]
        elif preferred is not None:
            selected = everything[resolve_forced_capability(f"aina:{preferred}", everything) or ""]
        source = (
            "explicit_capability"
            if requested is not None
            else "preferred_aina"
            if preferred is not None
            else "unified_entry"
        )
        await run.scope_resolved(
            conversation,
            selected=selected,
            source=source,
            requested_capability=requested,
            preferred_aina_id=preferred,
        )
        if selected is None:
            prompt = await self.prompts.build(conversation, memory_context=run.memory_context)
            return await self.catalog.entry(conversation), prompt, None
        if selected.kind == "aina":
            conversation = await self.conversations.bind_aina(conversation.id, selected.capability_id)
            capabilities, aina = await self.catalog.aina_scope(conversation, selected)
            prompt = await self.prompts.build(conversation, aina, memory_context=run.memory_context)
            # A remote AINA selected directly is invoked on the first model request.
            remote = aina.manifest.runtime.type == "remote"
            return capabilities, prompt, f"aina:{selected.capability_id}" if remote else None
        prompt = await self.prompts.build(conversation, memory_context=run.memory_context)
        return {selected.function_name: selected}, prompt, requested

    def _new_run(self, conversation: Conversation, trace_id: str, event_sink: EventSink | None) -> AgentRun:
        return AgentRun(
            trace_id=trace_id,
            conversation_id=conversation.id,
            user_id=conversation.user_id,
            tenant_id=conversation.tenant_id,
            workspace_id=conversation.workspace_id,
            events=self.events,
            scope=RunScope(visible={}, system_prompt=""),
            registry={},
            event_sink=event_sink,
        )

    async def _prepare(
        self,
        conversation: Conversation,
        run: AgentRun,
        archiver: TranscriptArchiver,
        *,
        capabilities: dict[str, Capability],
        system_prompt: str,
        forced_capability: str | None,
        denied: bool = False,
    ) -> _Invocation:
        recovery = await self.catalog.available(conversation)
        forced_function = resolve_forced_capability(forced_capability, capabilities)
        await run.push(
            "capability.discovery",
            "completed",
            details={
                "aina_graph": await aina_graph(self.registry, conversation, capabilities),
                "model_scope": model_scope_details(
                    capabilities,
                    forced_capability=forced_capability,
                    forced_function=forced_function,
                ),
            },
        )
        # Every reachable capability is registered as a tool; the scope decides which ones the next model
        # request advertises, so activating an AINA scope switches tools and system prompt mid-run.
        run.registry = {**recovery, **capabilities}
        failures = CapabilityFailures(run, recovery)
        scope = run.scope
        scope.visible = dict(capabilities)
        scope.system_prompt = system_prompt
        scope.forced_function = forced_function
        scope.project_messages = project_scope_history
        scope.on_unknown_tool = failures.unknown_tool
        scope.on_tool_error = failures.unrecorded
        scope.prompt_suffix = self._task_projection(run)

        model = self._request_model()
        model_name, endpoint = self._model_target()
        window = current_context_window_tokens(self.settings.context_window_tokens)
        model_calls = ModelCallRecorder(
            events=self.events,
            trace_id=run.trace_id,
            conversation_id=run.conversation_id,
            model=model_name,
            endpoint=endpoint,
            to_wire=native_to_wire,
            visible_capabilities=lambda: scope.visible,
            call_sink=self.llm_call_sink,
            streaming=run.event_sink is not None,
        )
        summary_calls = StandaloneCallRecorder(
            call_sink=self.llm_call_sink,
            trace_id=run.trace_id,
            context_type="compression",
            context_id=run.conversation_id,
            model=model_name,
            endpoint=endpoint,
            to_wire=native_to_wire,
            select=is_summary_call,
        )
        budget_guard = RequestBudgetGuard(
            input_budget_tokens=request_input_budget(window),
            count_tokens=estimate_request_tokens,
        )
        output_guard = OutputGuardMiddleware()
        forced_choice = ForcedToolChoiceMiddleware(rejects_tool_choice, reply=FORCED_CALL_MISSING_REPLY)
        call_budget = ModelCallBudgetMiddleware(
            run_limit=self.settings.max_agent_iterations,
            reply=(
                f"I stopped after {self.settings.max_agent_iterations} model iterations because the capability loop "
                "did not produce a final answer."
            ),
        )
        middleware: list[Any] = [
            TerminalDenialMiddleware(denied=denied, tool_result=DENIED_TOOL_RESULT, reply=CANCELLED_REPLY),
            TranscriptHookMiddleware(archiver.archive),
        ]
        if self.settings.context_compression_enabled:
            middleware.append(
                build_summarization(
                    model,
                    settings=self.settings,
                    run=run,
                    context_window_tokens=window,
                    overhead_tokens=estimate_request_tokens(
                        [{"role": "system", "content": system_prompt}],
                        [item.llm_definition() for item in capabilities.values()],
                    ),
                    call_recorder=summary_calls,
                    model_name=model_name,
                )
            )
        middleware += [
            CapabilityScopeMiddleware(scope),
            budget_guard,
            forced_choice,
            ProviderErrorMiddleware(map_model_error),
            model_calls,
            InvalidToolCallMiddleware(failures.invalid_arguments),
            OrderedBatchMiddleware(),
            call_budget,
            build_approval_middleware(run.registry),
            output_guard,
            ToolErrorMiddleware(aon_error=_format_tool_error),
            CapabilityCallMiddleware(run, failures),
            *tool_retry_middleware(run.registry),
        ]
        agent = build_agent(
            model=model,
            tools=self.executor.tools(run),
            system_prompt=system_prompt,
            middleware=middleware,
            checkpointer=self.checkpointer,
        )
        config = self._config(conversation, run.trace_id)
        config["callbacks"] = [summary_calls]
        return _Invocation(
            agent=agent,
            config=config,
            run=run,
            archiver=archiver,
            model_calls=model_calls,
            summary_calls=summary_calls,
            budget_guard=budget_guard,
            output_guard=output_guard,
            call_budget=call_budget,
            forced_choice=forced_choice,
        )

    async def _invoke(
        self,
        invocation: _Invocation,
        agent_input: Any,
        *,
        known_ids: set[str | None],
        resumed: bool = False,
    ) -> ChatResponse:
        run = invocation.run
        agent, config = invocation.agent, invocation.config
        relay = StreamRelay(run.event_sink) if run.event_sink is not None else None
        if relay is None:
            await agent.ainvoke(agent_input, config=config)
        else:
            await relay.relay(agent.astream(agent_input, config=config, stream_mode="messages"))
        state = await agent.aget_state(config)
        messages: list[BaseMessage] = list(state.values.get("messages") or [])
        produced = [message for message in messages if message.id not in known_ids and not is_summary_message(message)]

        status: Literal["completed", "approval_required", "failed"] = "completed"
        approval: ApprovalRecord | None = None
        extra: list[AIMessage] = []
        if state.interrupts:
            status = "approval_required"
            approval = approval_record(
                interrupt=state.interrupts[0],
                messages=messages,
                conversation_id=run.conversation_id,
                user_id=run.user_id,
                tenant_id=run.tenant_id,
                trace_id=run.trace_id,
                thread_id=thread_id_for(run.conversation_id),
            )
            content = f"Approval is required before running: {', '.join(approval.capability_names)}."
        elif invocation.call_budget.exceeded is not None:
            status = "failed"
            content = invocation.call_budget.exceeded
        elif invocation.budget_guard.exceeded is not None:
            status = "failed"
            content = invocation.budget_guard.exceeded
        elif invocation.output_guard.failure is not None:
            status = "failed"
            content = invocation.output_guard.failure
        elif invocation.forced_choice.failure is not None:
            status = "failed"
            content = invocation.forced_choice.failure
        else:
            final = next(
                (message for message in reversed(produced) if isinstance(message, AIMessage) and not message.tool_calls),
                None,
            )
            content = _text(final) if final is not None else ""
            if not content:
                # A continuation may end on tool results alone; a turn must end with an answer.
                content = COMPLETED_REPLY if resumed else EMPTY_REPLY
                status = "completed" if resumed else "failed"
                extra.append(AIMessage(content=content, id=f"msg_{uuid4().hex}"))

        # Persistent widgets are attached to the final assistant text message of the run.
        widgets = [widget for widget in run.scope.widgets if widget.id != f"clarification-{run.conversation_id}"]
        final_text = next(
            (
                message
                for message in reversed([*produced, *extra])
                if isinstance(message, AIMessage) and not message.tool_calls
            ),
            None,
        )
        await invocation.archiver.archive(
            [*produced, *extra],
            widgets={str(final_text.id): widgets} if widgets and final_text is not None else None,
        )
        if approval is not None:
            await self.conversations.create_approval(approval)
            await run.push(
                "approval.required",
                "pending",
                details={"approval_id": approval.id, "capabilities": approval.capability_names},
            )
            await run.emit(
                {"type": "approval.required", "approval_id": approval.id, "capabilities": approval.capability_names}
            )
        elif relay is not None:
            await relay.finish(content)
        await self._prune_history(run.conversation_id)

        last_assistant = next(
            (record for record in reversed(invocation.archiver.appended) if record.role == "assistant"),
            None,
        )
        usage = Usage(
            input_tokens=invocation.model_calls.usage.input_tokens + invocation.summary_calls.usage.input_tokens,
            output_tokens=invocation.model_calls.usage.output_tokens + invocation.summary_calls.usage.output_tokens,
            estimated=invocation.model_calls.usage.estimated,
        )
        response = ChatResponse(
            conversation_id=run.conversation_id,
            message_id=last_assistant.id if last_assistant else None,
            content=content,
            status=status,
            trace_id=run.trace_id,
            iterations=max(1, invocation.model_calls.iterations),
            usage=usage,
            approval=approval,
            widgets=run.scope.widgets,
        )
        await run.push(
            "final.response",
            status,
            details={
                "iterations": response.iterations,
                "message_id": response.message_id,
                "content": response.content,
                "content_length": len(response.content),
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "usage_estimated": response.usage.estimated,
                "widgets": [{"id": widget.id, "kind": widget.kind} for widget in response.widgets],
            },
        )
        return response

    async def _prune_history(self, conversation_id: str) -> None:
        """Keep only the thread's latest checkpoint (with its pending writes, e.g. a paused approval)."""
        try:
            await self.checkpointer.aprune([thread_id_for(conversation_id)], strategy="keep_latest")
        except NotImplementedError:
            # The in-memory development saver cannot prune; its history ends with the process.
            return
        except Exception:
            # Housekeeping only: the run already completed and older checkpoints are pruned after the next run.
            logger.warning("Could not prune agent checkpoints", exc_info=True, extra={"conversation_id": conversation_id})

    async def _working_messages(self, config: RunnableConfig) -> list[BaseMessage]:
        checkpoint = await self.checkpointer.aget_tuple(config)
        if checkpoint is None:
            return []
        return list(checkpoint.checkpoint.get("channel_values", {}).get("messages") or [])

    def _config(self, conversation: Conversation, trace_id: str) -> RunnableConfig:
        return {
            "configurable": {"thread_id": thread_id_for(conversation.id)},
            "metadata": {
                "conversation_id": conversation.id,
                "trace_id": trace_id,
                "user_id": conversation.user_id,
                "tenant_id": conversation.tenant_id,
                "workspace_id": conversation.workspace_id,
            },
        }

    def _task_projection(self, run: AgentRun) -> Callable[[], Awaitable[str]]:
        async def projection() -> str:
            task_service = self.executor.task_service
            if task_service is None:
                return ""
            return await task_service.context_projection(
                run.conversation_id, user_id=run.user_id, tenant_id=run.tenant_id
            )

        return projection

    async def _record_user_request(
        self,
        trace_id: str,
        conversation: Conversation,
        request: ChatRequest,
        *,
        message_id: str,
    ) -> None:
        content = request.message
        await self.events.push(
            trace_id,
            kind="user.request",
            status="completed",
            conversation_id=conversation.id,
            details={
                "message_id": message_id,
                "content": re.sub(
                    r"(password|api_key|token|secret)=[^\s&]+",
                    r"\1=[REDACTED]",
                    content,
                    flags=re.IGNORECASE,
                ),
                "content_length": len(content),
                "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "requested_capability": request.capability,
            },
        )

    def _request_model(self) -> BaseChatModel:
        """Chat model serving this request: the actor's selected provider model, else the configured model."""
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
            )
            model = self._runtime_models.get(key)
            if model is None:
                model = self._runtime_models[key] = self.model_factory(
                    runtime,
                    max_completion_tokens=output_token_reserve(runtime.context_window_tokens),
                )
            return model
        if self.model is None:
            raise PlatformError(
                code="INVALID_REQUEST",
                message="The LLM provider is not configured",
                status_code=503,
                source="model",
                user_message="The language model is not configured for this service.",
            )
        return self.model

    def _model_target(self) -> tuple[str, str]:
        """(model name, chat completions endpoint) of the model serving this request."""
        runtime = current_model_runtime()
        model_name = runtime.model if runtime else (self.settings.llm_model or "unknown")
        base_url = (runtime.base_url if runtime else self.settings.llm_base_url) or ""
        return model_name, f"{base_url.rstrip('/')}/chat/completions"


def _text(message: BaseMessage) -> str:
    return message.content if isinstance(message.content, str) else str(message.content or "")
