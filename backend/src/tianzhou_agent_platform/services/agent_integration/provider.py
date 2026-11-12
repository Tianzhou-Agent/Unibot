"""Superbot's capabilities, as the agent core asks for them (``unibot_agent.ports.CapabilityProvider``).

Routes a turn to its scope (the unified entry, an AINA, or one requested capability), builds the instructions that
go with it and executes the calls. The conversation is read from ``Session.record``, which ``sessions.session_of``
sets.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from tianzhou_agent_platform.aina.package.canvas_calls import canvas_state_block
from tianzhou_agent_platform.conversations.models import Conversation
from tianzhou_agent_platform.conversations.service import ConversationService
from tianzhou_agent_platform.core.capability_discovery import aina_graph, model_scope_details
from tianzhou_agent_platform.services.agent_integration.capabilities import (
    Capability,
    CapabilityCatalog,
    CapabilityRegistry,
    resolve_forced_capability,
    scope_recovery,
    scope_resolved,
)
from tianzhou_agent_platform.services.agent_integration.executor import CapabilityExecutor
from tianzhou_agent_platform.services.agent_integration.prompts import SystemPrompts
from unibot_agent.capabilities import CapabilityScope
from unibot_agent.run import AgentRun
from unibot_agent.types import Session, TurnRequest


class AinaCapabilities:
    def __init__(
        self,
        *,
        registry: CapabilityRegistry,
        conversations: ConversationService,
        catalog: CapabilityCatalog,
        prompts: SystemPrompts,
        executor: CapabilityExecutor,
    ) -> None:
        self.registry = registry
        self.conversations = conversations
        self.catalog = catalog
        self.prompts = prompts
        self.executor = executor

    async def open_scope(
        self, run: AgentRun, session: Session, request: TurnRequest, *, after_failed_turn: bool
    ) -> CapabilityScope:
        conversation: Conversation = session.record
        everything = await self.catalog.available(conversation)
        requested = request.capability
        preferred = request.scope_id
        selected: Capability | None = None
        source = "unified_entry"
        if requested is not None:
            selected = everything[resolve_forced_capability(requested, everything) or ""]
            source = "explicit_capability"
        elif preferred is not None:
            selected = everything[resolve_forced_capability(f"aina:{preferred}", everything) or ""]
            source = "preferred_aina"
        elif after_failed_turn and (selected := unfinished_turn_aina(conversation, everything)) is not None:
            # The failed turn's scope is not checkpointed: without this, the follow-up sees that AINA's calls only
            # as non-executable history and may answer without re-activating it.
            source = "unfinished_turn"
        await scope_resolved(
            run,
            conversation,
            selected=selected,
            source=source,
            requested_capability=requested,
            preferred_aina_id=preferred,
        )
        if selected is None:
            prompt = await self.prompts.build(conversation, memory_context=run.memories)
            return await self._scope(conversation, await self.catalog.entry(conversation), prompt)
        if selected.kind == "aina":
            bound = await self.conversations.bind_aina(conversation.id, selected.capability_id)
            capabilities, aina = await self.catalog.aina_scope(bound, selected)
            prompt = await self.prompts.build(bound, aina, memory_context=run.memories)
            # A remote AINA selected directly is invoked on the first model request.
            remote = aina.manifest.runtime.type == "remote"
            return await self._scope(
                conversation,
                capabilities,
                prompt,
                forced_capability=f"aina:{selected.capability_id}" if remote else None,
                aina_id=selected.capability_id,
            )
        prompt = await self.prompts.build(conversation, memory_context=run.memories)
        return await self._scope(
            conversation, {selected.function_name: selected}, prompt, forced_capability=requested
        )

    async def resume_scope(
        self, run: AgentRun, session: Session, stored_scope_id: Callable[[], Awaitable[str | None]]
    ) -> CapabilityScope:
        """A turn that worked in an AINA continues with that AINA's tools, instructions and canvas state; any other
        offers every capability. The AINA is found from the turn's transcript (which also shows an AINA the turn
        switched to), else from the scope its checkpoints recorded when the run started. Every capability stays
        registered, so a paused call always runs.
        """
        conversation: Conversation = session.record
        everything = await self.catalog.available(conversation)
        selected = unfinished_turn_aina(conversation, everything)
        if selected is None:
            aina_id = await stored_scope_id()
            selected = next(
                (item for item in everything.values() if item.kind == "aina" and item.capability_id == aina_id), None
            )
        await scope_resolved(run, conversation, selected=selected, source="continued_turn")
        if selected is None:
            return await self._scope(conversation, everything, await self.prompts.build(conversation))
        capabilities, aina = await self.catalog.aina_scope(conversation, selected)
        return await self._scope(
            conversation, capabilities, await self.prompts.build(conversation, aina), aina_id=selected.capability_id
        )

    async def execute(self, run: AgentRun, capability: Any, call_id: str, arguments: dict[str, Any]) -> str:
        return await self.executor.execute(run, capability, call_id, arguments)

    async def request_context(self, run: AgentRun) -> str:
        """The task projection, and the active AINA's canvas state (design §9.5)."""
        parts: list[str] = []
        task_service = self.executor.task_service
        if task_service is not None:
            parts.append(
                await task_service.context_projection(run.conversation_id, user_id=run.user_id, tenant_id=run.tenant_id)
            )
        broker = self.executor.canvas_calls
        if broker is not None and run.scope_id is not None:
            state = await broker.model_context(run.conversation_id, run.scope_id)
            if state:
                parts.append(canvas_state_block(run.scope_id, state))
        return "\n\n".join(part for part in parts if part)

    async def _scope(
        self,
        conversation: Conversation,
        capabilities: dict[str, Capability],
        prompt: str,
        *,
        forced_capability: str | None = None,
        aina_id: str | None = None,
    ) -> CapabilityScope:
        recovery = await self.catalog.available(conversation)
        forced_function = resolve_forced_capability(forced_capability, capabilities)
        return CapabilityScope(
            visible=dict(capabilities),
            system_prompt=prompt,
            registered=dict(recovery),
            id=aina_id,
            forced_function=forced_function,
            discovery={
                "aina_graph": await aina_graph(self.registry, conversation, capabilities),
                "model_scope": model_scope_details(
                    capabilities,
                    forced_capability=forced_capability,
                    forced_function=forced_function,
                ),
            },
            recover=lambda name: scope_recovery(name, recovery),
        )


def unfinished_turn_aina(conversation: Conversation, capabilities: dict[str, Capability]) -> Capability | None:
    """Entry of the scope-activating AINA the conversation's last turn worked in, if it is still reachable.

    The turn worked in it when it called the AINA's entry or one of the AINA's own tools: a turn routed to an AINA
    directly (``preferred_aina_id``) never calls the entry.
    """
    last_user = max((index for index, message in enumerate(conversation.messages) if message.role == "user"), default=-1)
    called = {
        str((call.get("function") or {}).get("name") or "")
        for message in conversation.messages[last_user + 1 :]
        if message.role == "assistant"
        for call in message.tool_calls or []
    }
    owners = {capabilities[name].owner_id for name in called if name in capabilities}
    return next(
        (
            capability
            for capability in capabilities.values()
            if capability.kind == "aina"
            and capability.capability_id == conversation.last_aina_id
            and (capability.function_name in called or capability.capability_id in owners)
            and capability.activates_scope
        ),
        None,
    )
