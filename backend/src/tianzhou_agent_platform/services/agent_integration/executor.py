"""Native tools for Unibot capabilities and the domain operations behind them.

Each capability becomes a ``StructuredTool`` whose coroutine performs exactly one domain call and returns the
JSON result. Failures raise; retries, error envelopes and observation are middleware concerns.
"""

from __future__ import annotations

import json
from typing import Any, cast
from uuid import uuid4

from langchain_core.tools import StructuredTool
from pydantic import ValidationError

from tianzhou_agent_platform.aina.builtin import invoke_builtin
from tianzhou_agent_platform.aina.document.builtin import CREATE_EDIT_TASK_TOOL_ID
from tianzhou_agent_platform.aina.protocol.models import AinaInstallation, AinaRecord
from tianzhou_agent_platform.aina.protocol.widgets import WidgetDefinition
from tianzhou_agent_platform.aina.tool.models import ToolRecord
from tianzhou_agent_platform.conversations.service import ConversationService
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.core.capability_discovery import model_scope_details
from tianzhou_agent_platform.services.agent_integration.capabilities import Capability, CapabilityCatalog
from tianzhou_agent_platform.services.agent_integration.capability_tools import current_tool_call_id
from tianzhou_agent_platform.services.agent_integration.prompts import SystemPrompts
from tianzhou_agent_platform.services.agent_integration.run import AgentRun
from tianzhou_agent_platform.services.platform_tools import (
    DESCRIBE_AINA_TOOL_ID,
    OPEN_AINA_TOOL_ID,
    PLATFORM_TOOL_IDS,
    SUGGEST_AINAS_TOOL_ID,
    invoke_platform_tool,
    suggest_ainas_widget,
)


class CapabilityExecutor:
    def __init__(
        self,
        *,
        repository: Any,
        conversations: ConversationService,
        gateway: Any,
        catalog: CapabilityCatalog,
        prompts: SystemPrompts,
        document_service: Any | None = None,
        document_edit_task_service: Any | None = None,
        sandbox_service: Any | None = None,
        task_service: Any | None = None,
    ) -> None:
        # The domain operations (platform tools, built-in AINAs) run against the repository they are written for.
        self.repository = repository
        self.conversations = conversations
        self.gateway = gateway
        self.catalog = catalog
        self.prompts = prompts
        self.document_service = document_service
        self.document_edit_task_service = document_edit_task_service
        self.sandbox_service = sandbox_service
        self.task_service = task_service

    def tools(self, run: AgentRun) -> list[StructuredTool]:
        """One native tool per registered capability; the run scope decides which are advertised."""
        return [self._tool(run, capability) for capability in run.registry.values()]

    def _tool(self, run: AgentRun, capability: Capability) -> StructuredTool:
        name = capability.function_name

        async def invoke(**arguments: Any) -> str:
            call_id = current_tool_call_id.get() or f"call_{uuid4().hex}"
            try:
                # The registry entry can be refined during the run (e.g. an AINA scope assigns tool ownership).
                result = await self.execute(run, run.registry.get(name, capability), call_id, arguments)
            except ValidationError as exc:
                # ToolNode reads a pydantic ValidationError raised by a tool as invalid call arguments. The
                # arguments were already validated against the capability schema, so this is invalid data
                # produced by the domain call.
                raise ValueError(str(exc)) from exc
            return json.dumps(result, ensure_ascii=False, default=str)

        # The capability's own JSON Schema is advertised verbatim and arguments reach ``execute`` exactly as the
        # model sent them; a generated pydantic model would inject None for omitted optional fields.
        return StructuredTool.from_function(
            coroutine=invoke,
            name=name,
            description=capability.description,
            args_schema=capability.input_schema or {"type": "object", "properties": {}},
        )

    async def execute(
        self,
        run: AgentRun,
        capability: Capability,
        call_id: str,
        arguments: dict[str, Any],
    ) -> Any:
        """Perform one capability call; widgets and scope changes are applied to the run."""
        if capability.is_builtin_aina:
            return await self.activate_scope(run, capability, call_id, arguments)
        if capability.capability_id in {DESCRIBE_AINA_TOOL_ID, OPEN_AINA_TOOL_ID, SUGGEST_AINAS_TOOL_ID}:
            run.scope.widgets = [widget for widget in run.scope.widgets if widget.kind != "app_list"]
        if capability.capability_id == SUGGEST_AINAS_TOOL_ID:
            # The model's ranked suggestion replaces the one offered when the turn was routed into an AINA.
            run.scope.widgets = [widget for widget in run.scope.widgets if widget.kind != "aina_chooser"]
        if capability.kind == "tool":
            result, _duration_ms = await self.gateway.invoke_tool(
                cast(ToolRecord, capability.value),
                arguments=arguments,
                call_id=call_id,
                user_id=run.user_id,
                tenant_id=run.tenant_id,
                conversation_id=run.conversation_id,
                workspace_id=run.workspace_id,
                trace_id=run.trace_id,
            )
            return result
        if capability.kind == "aina":
            aina, installation = cast(tuple[AinaRecord, AinaInstallation], capability.value)
            response, _duration_ms = await self.gateway.invoke_aina(
                aina.manifest,
                installation,
                arguments=arguments,
                call_id=call_id,
                conversation_id=run.conversation_id,
                workspace_id=run.workspace_id,
                trace_id=run.trace_id,
                available_tools=[item.capability_id for item in run.registry.values() if item.kind == "tool"],
            )
            for output in response.outputs:
                if output.type != "widget":
                    continue
                try:
                    run.scope.widgets.append(WidgetDefinition.model_validate(output.content))
                except ValueError as exc:
                    raise PlatformError(
                        "DEPENDENCY_FAILED",
                        "AINA returned an invalid widget output",
                        status_code=502,
                        source="aina",
                    ) from exc
            await self.activate_scope(run, capability, call_id, arguments)
            return response.model_dump(mode="json")
        if capability.capability_id in PLATFORM_TOOL_IDS:
            result, widgets = await invoke_platform_tool(
                self.repository,
                cast(str, capability.value),
                arguments,
                user_id=run.user_id,
                tenant_id=run.tenant_id,
                conversation_id=run.conversation_id,
                task_service=self.task_service,
                tool_execution_id=call_id,
            )
        else:
            result, widgets = await invoke_builtin(
                self.repository,
                cast(str, capability.value),
                arguments,
                user_id=run.user_id,
                tenant_id=run.tenant_id,
                conversation_id=run.conversation_id,
                document_service=self.document_service,
                document_edit_task_service=self.document_edit_task_service,
                sandbox_service=self.sandbox_service,
            )
        run.scope.widgets.extend(widgets)
        if capability.capability_id == CREATE_EDIT_TASK_TOOL_ID:
            run.scope.direct_reply = _edit_task_reply(result)
        return result

    async def activate_scope(
        self,
        run: AgentRun,
        capability: Capability,
        call_id: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Switch the following model requests to an AINA's capabilities and instructions."""
        await run.emit({"type": "routing.started", "candidate_count": 1})
        conversation = await self.conversations.bind_aina(run.conversation_id, capability.capability_id)
        scoped, aina = await self.catalog.aina_scope(conversation, capability)
        run.scope.visible = dict(scoped)
        run.scope.system_prompt = await self.prompts.build(conversation, aina, memory_context=run.memory_context)
        run.scope.activated_entries.add(capability.function_name)
        run.registry.update(scoped)
        await run.emit({"type": "routing.completed", "kind": "aina", "id": capability.capability_id})
        await run.push(
            "routing.scope.activated",
            "completed",
            target_type="aina",
            target_id=capability.capability_id,
            details={
                "call_id": call_id,
                "function_name": capability.function_name,
                "arguments": arguments,
                "model_scope": model_scope_details(scoped, forced_capability=None, forced_function=None),
            },
        )
        await run.scope_resolved(conversation, selected=capability, source="model_selection")
        await self._offer_aina_ui(run, capability.capability_id)
        return {
            "activated": True,
            "aina_id": capability.capability_id,
            "available_capability_ids": sorted(item.capability_id for item in scoped.values()),
            "note": "Only this AINA's capabilities were activated for this turn; its application UI was not opened.",
        }


    async def _offer_aina_ui(self, run: AgentRun, aina_id: str) -> None:
        """Suggest the routed AINA's application UI; the host lets the user choose whether to open it."""
        if any(widget.kind == "aina_chooser" for widget in run.scope.widgets):
            return
        try:
            widget = await suggest_ainas_widget(
                self.repository,
                [aina_id],
                "",
                user_id=run.user_id,
                tenant_id=run.tenant_id,
            )
        except PlatformError:
            return
        if widget.apps[0].has_main_widget:
            run.scope.widgets.append(widget)


def _edit_task_reply(result: Any) -> str | None:
    """Direct completion text after a document edit task was created (no further model call)."""
    task = result.get("task") if isinstance(result, dict) else None
    if not isinstance(task, dict):
        return None
    title = str(task.get("title") or "Document edit task")
    sections = task.get("sections")
    section_count = len(sections) if isinstance(sections, list) else 0
    return (
        f'Edit task "{title}" was created. The AI is working on {section_count} section(s) in the background. '
        "When it finishes, the task moves to review; follow progress and drafts in the Tasks mode on the "
        "right. The document itself changes only after you confirm the merge."
    )
