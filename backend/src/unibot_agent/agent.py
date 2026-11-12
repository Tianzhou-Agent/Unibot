"""Runs one agent turn or approval continuation on the native ``create_agent`` loop.

Working memory is the checkpointed agent state of the conversation's thread (``lc-v2:<conversation_id>``): a turn
submits only its new input. A conversation without a checkpoint is seeded from its archive. Approvals pause with a
native interrupt and continue with ``Command(resume=...)``. A turn the user stops ends at its last checkpointed step
and continues with ``None`` input (see ``resume_turn``). After every invocation the thread keeps only its latest
checkpoint.

Middleware, first = outermost (``before_model`` hooks run in this order, ``after_model`` hooks in reverse):

======================================  ==========  =========================================================
Middleware                              Owner       Purpose
======================================  ==========  =========================================================
``TerminalDenialMiddleware``            runtime     closes a denied batch and ends the run
``TranscriptHookMiddleware``            runtime     archives new messages before summarization replaces them
``TurnSummarizationMiddleware``         runtime     built-in summarization, retaining complete turns
``CapabilityScopeMiddleware``           core        advertised tools, system prompt, UI context, forced tool
``RequestBudgetGuard``                  runtime     refuses requests over the model's input budget
``ForcedToolChoiceMiddleware``          runtime     provider compatibility for a forced tool call
``ProviderErrorMiddleware``             runtime     provider exceptions → platform errors
``ModelCallRecorder``                   core        model spans, events and ``/llm-calls`` records
``InvalidToolCallMiddleware``           runtime     answers calls with unparseable arguments
``OrderedBatchMiddleware``              runtime     batch order, same-batch and run-scoped deduplication
``ModelCallBudgetMiddleware``           runtime     built-in model-call limit ending with the product reply
``HumanInTheLoopMiddleware``            LangChain   pauses batches with risky calls
``OutputGuardMiddleware``               runtime     empty/truncated responses; truncated calls never run
``ToolErrorMiddleware``                 LangChain   failures → model-visible error envelopes
``CapabilityCallMiddleware``            core        scope/argument checks, tool span and events per call
``ToolRetryMiddleware`` groups          LangChain   automatic retries of side-effect-free remote tools
======================================  ==========  =========================================================
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal
from uuid import uuid4

from langchain.agents.middleware import ToolErrorMiddleware
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command, Interrupt
from pydantic import ValidationError

from unibot_agent.approvals import (
    CANCELLED_REPLY,
    DENIED_TOOL_RESULT,
    approval_request,
    build_approval_middleware,
    resume_decisions,
    validated_edits,
    with_confirmations,
)
from unibot_agent.attachments import ATTACHMENTS_KEY, AttachedFile, inline_budget, inline_contents
from unibot_agent.capabilities import Capability, CapabilityScope
from unibot_agent.capability_tools import current_tool_call_id, format_error_envelope
from unibot_agent.context import build_summarization
from unibot_agent.errors import AgentError
from unibot_agent.history import (
    TranscriptArchiver,
    archive_to_native,
    dangling_tool_closures,
    native_to_wire,
    project_scope_history,
)
from unibot_agent.model_calls import (
    ModelCallRecorder,
    StandaloneCallRecorder,
    is_summary_call,
)
from unibot_agent.observation import (
    CapabilityCallMiddleware,
    CapabilityFailures,
)
from unibot_agent.ports import (
    ApprovalStore,
    CapabilityProvider,
    MemoryProvider,
    ModelCallLog,
    ModelProvider,
    NoRunEvents,
    RunEvents,
    SessionStore,
)
from unibot_agent.retries import tool_retry_middleware
from unibot_agent.run import AgentRun, EventSink, artifact_refs
from unibot_agent.runtime import build_agent
from unibot_agent.runtime.middleware.approval_policy import TerminalDenialMiddleware
from unibot_agent.runtime.middleware.model_policy import (
    InvalidToolCallMiddleware,
    ModelCallBudgetMiddleware,
    OutputGuardMiddleware,
    RequestBudgetGuard,
)
from unibot_agent.runtime.middleware.provider_policy import (
    ForcedToolChoiceMiddleware,
    ProviderErrorMiddleware,
)
from unibot_agent.runtime.middleware.summarization import is_summary_message
from unibot_agent.runtime.middleware.tool_policy import OrderedBatchMiddleware
from unibot_agent.runtime.middleware.transcript import TranscriptHookMiddleware
from unibot_agent.scope import CapabilityScopeMiddleware, RunScope
from unibot_agent.stop import TurnStop
from unibot_agent.streaming import StreamRelay
from unibot_agent.tokens import estimate_request_tokens, request_input_budget
from unibot_agent.types import (
    AgentConfig,
    ApprovalRequest,
    Session,
    TurnRequest,
    TurnResult,
    Usage,
)

logger = logging.getLogger(__name__)

THREAD_PREFIX = "lc-v2:"
# Checkpoint metadata key of the scope a run started in (the literal is kept for the checkpoints already stored).
SCOPE_KEY = "aina_id"
ABANDONED_CALL_RESULT = "Cancelled because the user started a new turn before granting approval."
COMPLETED_REPLY = "The requested operation was completed."
EMPTY_REPLY = OutputGuardMiddleware.EMPTY_REPLY
STOPPED_REPLY = "The response was stopped by the user."
STOPPED_TOOL_RESULT = "Stopped by the user before this call completed; it may have partially run."
# Closes the request of a turn's last allowed model call, which is sent without tools.
WRAP_UP_INSTRUCTION = (
    "[Platform notice, not from the user] You have reached the limit of {limit} model calls for this request, so "
    "tools are no longer available. "
    "Reply to the user now, in text only and in the language of the conversation: say that you paused at the step "
    "limit, summarize what you completed, list what remains, and tell them to reply \"continue\" to carry on. "
    "Do not claim that unfinished work is done."
)
FORCED_CALL_MISSING_REPLY = (
    "The model answered without using the selected capability, so no result was produced. Please retry, or "
    "choose a model that supports forced tool calls."
)


def thread_id_for(conversation_id: str) -> str:
    return f"{THREAD_PREFIX}{conversation_id}"


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
    """A paused batch ready to be approved or denied (see ``Agent.open_approval``)."""

    def __init__(
        self,
        runner: Agent,
        session: Session,
        approval: ApprovalRequest,
        *,
        approve: bool,
        invocation: _Invocation,
        interrupt: Interrupt | None,
        known_ids: set[str | None],
        edits: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._runner = runner
        self._session = session
        self._approval = approval
        self._approve = approve
        self._invocation = invocation
        self._interrupt = interrupt
        self._known_ids = known_ids
        self._edits = edits

    async def run(self, stop: TurnStop | None = None) -> TurnResult:
        if self._interrupt is None:
            return await self._runner._close_denied_without_interrupt(self._session, self._approval)
        return await self._runner._invoke(
            self._invocation,
            Command(
                resume=resume_decisions(self._interrupt, approve=self._approve, approval=self._approval, edits=self._edits)
            ),
            known_ids=self._known_ids,
            resumed=True,
            stop=stop,
        )


class Agent:
    """Runs agent turns against the services of its host (``unibot_agent.ports``)."""

    def __init__(
        self,
        *,
        sessions: SessionStore,
        models: ModelProvider,
        capabilities: CapabilityProvider,
        config: AgentConfig | None = None,
        checkpointer: Any | None = None,
        approvals: ApprovalStore | None = None,
        memory: MemoryProvider | None = None,
        events: RunEvents | None = None,
        model_call_log: ModelCallLog | None = None,
    ) -> None:
        self.config = config or AgentConfig()
        self.sessions = sessions
        self.models = models
        self.capabilities = capabilities
        # Working memory; without a saver it ends with the process.
        self.checkpointer = checkpointer if checkpointer is not None else InMemorySaver()
        self.approvals = approvals
        self.memory = memory
        self.events = events or NoRunEvents()
        self.model_call_log = model_call_log

    async def run_turn(
        self,
        session: Session,
        request: TurnRequest,
        *,
        trace_id: str,
        event_sink: EventSink | None = None,
        stop: TurnStop | None = None,
        attachments: Sequence[AttachedFile] | None = None,
        attachment_texts: dict[str, str] | None = None,
        after_failed_turn: bool = False,
    ) -> TurnResult:
        """Run one user turn. The caller holds the conversation's run lease.

        ``attachment_texts`` are the documents of the message's ``attachments`` (file_ref -> text), which the model
        reads in the message, as much as fits. After a failed turn, the capability provider may re-enter the scope
        that turn activated.
        """
        config = self._config(session, trace_id)
        checkpointed = await self._working_messages(config)
        # A conversation without working memory (never run natively, or its checkpoint was lost) is seeded from
        # its archive; the archive IDs are kept so seeded messages are never archived twice.
        seed = [] if checkpointed else [archive_to_native(message) for message in await self.sessions.history(session)]
        closures = dangling_tool_closures(checkpointed or seed, ABANDONED_CALL_RESULT)
        # What others added to the archive since the last turn reaches working memory here, under the same message
        # id, so it is never archived twice.
        present = {message.id for message in checkpointed or seed}
        external = [
            archive_to_native(message)
            for message in await self.sessions.take_external_messages(session.id)
            if message.id not in present
        ]
        user_message = HumanMessage(
            content=request.message,
            id=f"msg_{uuid4().hex}",
            additional_kwargs=(
                {ATTACHMENTS_KEY: [item.model_dump() for item in attachments]} if attachments else {}
            ),
        )
        archiver = self._archiver(session, trace_id)
        await archiver.archive([*closures, user_message])
        await self._record_user_request(trace_id, session, request, message_id=str(user_message.id))

        run = self._new_run(session, trace_id, event_sink)
        run.scope.ui_context = request.ui_context
        run.scope.ui_context_message_id = user_message.id
        run.scope.attachment_contents = inline_contents(
            attachments or [],
            attachment_texts or {},
            inline_budget(self.models.resolve(run).context_window_tokens),
        )
        if self.memory is not None:
            run.memories = await self.memory.recall(
                request.message,
                user_id=session.user_id,
                tenant_id=session.tenant_id,
                limit=8,
            )
        scope = await self.capabilities.open_scope(run, session, request, after_failed_turn=after_failed_turn)
        invocation = await self._prepare(session, run, archiver, scope)
        known_ids = {message.id for message in (checkpointed or seed)}
        return await self._invoke(
            invocation,
            {"messages": [*seed, *closures, *external, user_message]},
            known_ids=known_ids | {message.id for message in [*closures, *external]} | {user_message.id},
            stop=stop,
        )

    async def resume_turn(
        self,
        session: Session,
        *,
        trace_id: str,
        event_sink: EventSink | None = None,
        stop: TurnStop | None = None,
    ) -> TurnResult:
        """Continue a stopped turn from its last checkpointed step. The caller holds the conversation's run lease.

        Like an approval continuation, the continued run is in the scope the turn worked in
        (``CapabilityProvider.resume_scope``). Raises CONFLICT when the checkpoint has nothing left to run.
        """
        run = self._new_run(session, trace_id, event_sink)
        archiver = self._archiver(session, trace_id)
        invocation = await self._prepare(session, run, archiver, await self._resumed_scope(run, session))
        state = await invocation.agent.aget_state(invocation.config)
        # ``state.next`` leaves out steps that already finished: a stop can land after a step saved its writes but
        # before its checkpoint, and resuming applies those writes and carries on. ``state.tasks`` has every step.
        if not state.tasks or state.interrupts:
            raise AgentError(
                "CONFLICT",
                "The stopped turn can no longer be resumed",
                status_code=409,
                user_message="该回复已无法继续，请重新发送请求。",
            )
        return await self._invoke(
            invocation,
            None,
            known_ids={message.id for message in state.values.get("messages") or []},
            stop=stop,
        )

    async def open_approval(
        self,
        session: Session,
        approval: ApprovalRequest,
        *,
        approve: bool,
        edited_arguments: dict[str, dict[str, Any]] | None = None,
        event_sink: EventSink | None = None,
    ) -> ApprovalContinuation:
        """Load the batch an approval paused, before the approval is resolved.

        Raises CONFLICT when an approved batch can no longer be resumed: without its checkpointed interrupt the
        calls are never re-run, since they may already have had effects. A denied batch without its interrupt is
        still closed (in the archive only).
        """
        run = self._new_run(session, approval.trace_id, event_sink)
        archiver = self._archiver(session, approval.trace_id)
        invocation = await self._prepare(
            session, run, archiver, await self._resumed_scope(run, session), denied=not approve
        )
        state = await invocation.agent.aget_state(invocation.config)
        interrupt = next(
            (item for item in state.interrupts if item.id == (approval.runtime_ref or {}).get("interrupt_id")),
            None,
        )
        if interrupt is None and approve:
            raise AgentError(
                "CONFLICT",
                "The approved operation can no longer be resumed",
                status_code=409,
                user_message="该审批已失效，请重新发起请求。",
            )
        # Arguments the user changed when approving are validated before the approval is resolved.
        edits = validated_edits(approval, edited_arguments, run.registry) if approve and edited_arguments else None
        return ApprovalContinuation(
            self,
            session,
            approval,
            approve=approve,
            invocation=invocation,
            interrupt=interrupt,
            known_ids={message.id for message in state.values.get("messages") or []},
            edits=edits,
        )

    async def delete_working_memory(self, conversation_id: str) -> None:
        await self.checkpointer.adelete_thread(thread_id_for(conversation_id))

    async def _close_denied_without_interrupt(
        self,
        session: Session,
        approval: ApprovalRequest,
    ) -> TurnResult:
        """Close a denied batch whose interrupt is gone; the next turn rebuilds working memory from the archive."""
        archiver = self._archiver(session, approval.trace_id)
        history = [archive_to_native(message) for message in await self.sessions.history(session)]
        await archiver.archive(
            [
                *dangling_tool_closures(history, DENIED_TOOL_RESULT),
                AIMessage(content=CANCELLED_REPLY, id=f"msg_{uuid4().hex}"),
            ]
        )
        await self.delete_working_memory(session.id)
        return TurnResult(
            conversation_id=session.id,
            message_id=archiver.appended[-1].id,
            content=CANCELLED_REPLY,
            status="completed",
            trace_id=approval.trace_id,
            iterations=0,
        )

    async def _resumed_scope(self, run: AgentRun, session: Session) -> CapabilityScope:
        """The scope of a turn continued after a stop or an approval, as its provider restores it."""

        async def stored_scope_id() -> str | None:
            checkpoint = await self.checkpointer.aget_tuple(self._config(session, ""))
            scope_id = checkpoint.metadata.get(SCOPE_KEY) if checkpoint is not None else None
            return scope_id if isinstance(scope_id, str) else None

        return await self.capabilities.resume_scope(run, session, stored_scope_id)

    def _archiver(self, session: Session, trace_id: str) -> TranscriptArchiver:
        return TranscriptArchiver(
            self.sessions, conversation_id=session.id, trace_id=trace_id, archived_ids=session.message_ids
        )

    def _new_run(self, session: Session, trace_id: str, event_sink: EventSink | None) -> AgentRun:
        return AgentRun(
            trace_id=trace_id,
            conversation_id=session.id,
            user_id=session.user_id,
            tenant_id=session.tenant_id,
            workspace_id=session.workspace_id,
            events=self.events,
            scope=RunScope(visible={}, system_prompt=""),
            registry={},
            event_sink=event_sink,
        )

    def _tools(self, run: AgentRun) -> list[StructuredTool]:
        """One native tool per registered capability; the run scope decides which are advertised."""
        return [self._tool(run, capability) for capability in run.registry.values()]

    def _tool(self, run: AgentRun, capability: Capability) -> StructuredTool:
        name = capability.function_name

        async def invoke(**arguments: Any) -> str:
            call_id = current_tool_call_id.get() or f"call_{uuid4().hex}"
            try:
                # The registry entry can be refined during the run (e.g. a scope assigns tool ownership).
                return await self.capabilities.execute(run, run.registry.get(name, capability), call_id, arguments)
            except ValidationError as exc:
                # ToolNode reads a pydantic ValidationError raised by a tool as invalid call arguments. The
                # arguments were already validated against the capability schema, so this is invalid data
                # produced by the call.
                raise ValueError(str(exc)) from exc

        # The capability's own JSON Schema is advertised verbatim and arguments reach ``execute`` exactly as the
        # model sent them; a generated pydantic model would inject None for omitted optional fields.
        return StructuredTool.from_function(
            coroutine=invoke,
            name=name,
            description=capability.description,
            args_schema=capability.input_schema or {"type": "object", "properties": {}},
        )

    async def _prepare(
        self,
        session: Session,
        run: AgentRun,
        archiver: TranscriptArchiver,
        scope: CapabilityScope,
        *,
        denied: bool = False,
    ) -> _Invocation:
        capabilities = scope.visible
        system_prompt = scope.system_prompt
        await run.push("capability.discovery", "completed", details=scope.discovery)
        # Every reachable capability is registered as a tool; the scope decides which ones the next model
        # request advertises, so activating another scope switches tools and system prompt mid-run.
        run.registry = {**scope.registered, **capabilities}
        run.scope_id = scope.id
        failures = CapabilityFailures(run, scope.recover)
        run_scope = run.scope
        run_scope.visible = dict(capabilities)
        run_scope.system_prompt = system_prompt
        run_scope.forced_function = scope.forced_function
        run_scope.project_messages = project_scope_history
        run_scope.on_unknown_tool = failures.unknown_tool
        run_scope.on_tool_error = failures.unrecorded
        # Per-request context, refreshed before every model call (state that changes during the run).
        run_scope.prompt_suffix = lambda: self.capabilities.request_context(run)

        handle = self.models.resolve(run)
        model = handle.model
        if model is None:
            raise AgentError(
                code="INVALID_REQUEST",
                message="The LLM provider is not configured",
                status_code=503,
                source="model",
                user_message="The language model is not configured for this service.",
            )
        model_name, endpoint = handle.name, handle.endpoint
        window = handle.context_window_tokens
        model_calls = ModelCallRecorder(
            events=self.events,
            trace_id=run.trace_id,
            conversation_id=run.conversation_id,
            model=model_name,
            endpoint=endpoint,
            to_wire=native_to_wire,
            visible_capabilities=lambda: run_scope.visible,
            call_sink=self.model_call_log,
            streaming=run.event_sink is not None,
        )
        summary_calls = StandaloneCallRecorder(
            call_sink=self.model_call_log,
            trace_id=run.trace_id,
            context_type="compression",
            context_id=run.conversation_id,
            model=model_name,
            endpoint=endpoint,
            to_wire=native_to_wire,
            select=is_summary_call,
        )
        budget_guard = RequestBudgetGuard(
            input_budget_tokens=request_input_budget(window, handle.max_output_tokens),
            count_tokens=estimate_request_tokens,
        )
        output_guard = OutputGuardMiddleware()
        forced_choice = ForcedToolChoiceMiddleware(handle.rejects_tool_choice, reply=FORCED_CALL_MISSING_REPLY)
        call_budget = ModelCallBudgetMiddleware(
            run_limit=self.config.max_agent_iterations,
            reply=(
                f"I stopped after {self.config.max_agent_iterations} model iterations because the capability loop "
                "did not produce a final answer."
            ),
            wrap_up=WRAP_UP_INSTRUCTION.format(limit=self.config.max_agent_iterations),
        )
        middleware: list[Any] = [
            TerminalDenialMiddleware(denied=denied, tool_result=DENIED_TOOL_RESULT, reply=CANCELLED_REPLY),
            TranscriptHookMiddleware(archiver.archive),
        ]
        if self.config.context_compression_enabled:
            middleware.append(
                build_summarization(
                    model,
                    settings=self.config,
                    run=run,
                    context_window_tokens=window,
                    input_budget_tokens=budget_guard.input_budget_tokens,
                    overhead_tokens=estimate_request_tokens(
                        [{"role": "system", "content": system_prompt}],
                        [item.llm_definition() for item in capabilities.values()],
                    ),
                    call_recorder=summary_calls,
                    model_name=model_name,
                )
            )
        middleware += [
            CapabilityScopeMiddleware(run_scope),
            # Outside the guard and the recorder, so both see the last call's request as sent (without tools).
            call_budget,
            budget_guard,
            forced_choice,
            ProviderErrorMiddleware(handle.map_error),
            model_calls,
            InvalidToolCallMiddleware(failures.invalid_arguments),
            OrderedBatchMiddleware(repeatable=lambda name: getattr(run.registry.get(name), "repeatable", False)),
            build_approval_middleware(run.registry),
            output_guard,
            ToolErrorMiddleware(aon_error=_format_tool_error),
            CapabilityCallMiddleware(run, failures),
            *tool_retry_middleware(run.registry),
        ]
        agent = build_agent(
            model=model,
            tools=self._tools(run),
            system_prompt=system_prompt,
            middleware=middleware,
            checkpointer=self.checkpointer,
        )
        config = self._config(session, run.trace_id)
        # Kept in the run's checkpoints, so a continuation finds the scope of a turn that called no tool yet.
        config["metadata"][SCOPE_KEY] = run.scope_id
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
        stop: TurnStop | None = None,
    ) -> TurnResult:
        run = invocation.run
        agent, config = invocation.agent, invocation.config
        relay = StreamRelay(run.event_sink) if run.event_sink is not None else None
        if relay is None:
            execution = agent.ainvoke(agent_input, config=config)
        else:
            execution = relay.relay(agent.astream(agent_input, config=config, stream_mode="messages"))
        stopped = not await (stop or TurnStop()).run(execution)
        if stopped:
            await self._settle_stopped(invocation, agent_input)
        state = await agent.aget_state(config)
        messages: list[BaseMessage] = list(state.values.get("messages") or [])
        produced = [message for message in messages if message.id not in known_ids and not is_summary_message(message)]

        status: Literal["completed", "approval_required", "failed", "stopped"] = "completed"
        approval: ApprovalRequest | None = None
        extra: list[AIMessage] = []
        if stopped:
            status = "stopped"
            content = STOPPED_REPLY
        elif state.interrupts:
            status = "approval_required"
            approval = approval_request(
                interrupt=state.interrupts[0],
                messages=messages,
                conversation_id=run.conversation_id,
                user_id=run.user_id,
                tenant_id=run.tenant_id,
                trace_id=run.trace_id,
                thread_id=thread_id_for(run.conversation_id),
            )
            approval = with_confirmations(approval, state.interrupts[0], run.registry)
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

        # The run's artifacts are attached to its final assistant text message.
        artifacts = list(run.artifacts)
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
            artifacts={str(final_text.id): artifacts} if artifacts and final_text is not None else None,
        )
        if approval is not None:
            if self.approvals is None:
                raise AgentError("DEPENDENCY_FAILED", "No approval store is configured", status_code=503)
            await self.approvals.create(approval)
            await run.push(
                "approval.required",
                "pending",
                details={"approval_id": approval.id, "capabilities": approval.capability_names},
            )
            await run.emit(
                {"type": "approval.required", "approval_id": approval.id, "capabilities": approval.capability_names}
            )
        elif relay is not None and not stopped:
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
        response = TurnResult(
            conversation_id=run.conversation_id,
            message_id=last_assistant.id if last_assistant else None,
            content=content,
            status=status,
            trace_id=run.trace_id,
            iterations=max(1, invocation.model_calls.iterations),
            usage=usage,
            approval=approval,
            artifacts=run.artifacts,
            values=run.values,
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
                "widgets": artifact_refs(response.artifacts),
            },
        )
        return response

    async def _settle_stopped(self, invocation: _Invocation, agent_input: Any) -> None:
        """Leave the checkpoint of a stopped turn ready to resume and consistent for a new turn.

        A stop before the agent recorded the turn's input records it as pending. A stop during a tool step commits
        the results of the calls that finished (LangGraph holds them only as pending writes, which new input discards,
        or not at all when the stop cancelled the step before saving them) and closes the calls that never returned:
        they may already have had effects, so they are never re-run. A stop during a model call needs nothing:
        resuming runs that step again.
        """
        agent, config = invocation.agent, invocation.config
        state = await agent.aget_state(config)
        messages: list[BaseMessage] = list(state.values.get("messages") or [])
        present = {message.id for message in messages}
        if isinstance(agent_input, dict) and any(item.id not in present for item in agent_input["messages"]):
            await agent.aupdate_state(config, agent_input, as_node="__start__")
            return
        last_ai = next((message for message in reversed(messages) if isinstance(message, AIMessage)), None)
        if last_ai is None:
            return
        # A call that returned before the stop cancelled its step keeps its real result.
        recorded = invocation.run.finished_tool_results
        closures = [
            recorded.get(closure.tool_call_id, closure)
            for closure in dangling_tool_closures(messages, STOPPED_TOOL_RESULT)
        ]
        call_ids = {call.get("id") for call in [*last_ai.tool_calls, *last_ai.invalid_tool_calls]}
        finished = [
            message for message in messages if isinstance(message, ToolMessage) and message.tool_call_id in call_ids
        ]
        # Results of a tool step that finished but was not checkpointed are only pending writes, even when no call
        # is left open, so they are committed too.
        committed = {message.id for message in await self._working_messages(config)}
        if not closures and all(message.id in committed for message in finished):
            return
        await agent.aupdate_state(config, {"messages": [*finished, *closures]}, as_node="tools")

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

    def _config(self, session: Session, trace_id: str) -> RunnableConfig:
        return {
            "configurable": {"thread_id": thread_id_for(session.id)},
            "metadata": {
                "conversation_id": session.id,
                "trace_id": trace_id,
                "user_id": session.user_id,
                "tenant_id": session.tenant_id,
                "workspace_id": session.workspace_id,
            },
        }

    async def _record_user_request(
        self,
        trace_id: str,
        session: Session,
        request: TurnRequest,
        *,
        message_id: str,
    ) -> None:
        content = request.message
        await self.events.push(
            trace_id,
            kind="user.request",
            status="completed",
            conversation_id=session.id,
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


def _text(message: BaseMessage) -> str:
    return message.content if isinstance(message.content, str) else str(message.content or "")
