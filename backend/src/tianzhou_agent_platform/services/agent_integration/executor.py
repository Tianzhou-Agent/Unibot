"""Native tools for Superbot capabilities and the domain operations behind them.

Each capability becomes a ``StructuredTool`` whose coroutine performs exactly one domain call and returns the
JSON result. Failures raise; retries, error envelopes and observation are middleware concerns.
"""

from __future__ import annotations

import json
from typing import Any, cast
from uuid import uuid4

from langchain_core.tools import StructuredTool
from pydantic import ValidationError

from tianzhou_agent_platform.model_providers.models import current_model_runtime
from tianzhou_agent_platform.aina.builtin import invoke_builtin
from tianzhou_agent_platform.aina.document.builtin import CREATE_EDIT_TASK_TOOL_ID
from tianzhou_agent_platform.aina.package.models import ToolSnapshot
from tianzhou_agent_platform.aina.package.canvas_calls import CanvasCallBroker, canvas_result
from tianzhou_agent_platform.aina.package.manifest import CanvasTool
from tianzhou_agent_platform.aina.package.service import (
    AinaPackageService,
    load_skill,
    package_runtime,
    tool_images,
    validate_schema_arguments,
)
from tianzhou_agent_platform.aina.package.tasks import AinaTask, AinaTaskService
from tianzhou_agent_platform.aina.package.widgets import aina_widget_definition
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
    LIST_APP_TOOL_ID,
    OPEN_APP_TOOL_ID,
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
        packages: AinaPackageService | None = None,
        aina_tasks: AinaTaskService | None = None,
        canvas_calls: CanvasCallBroker | None = None,
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
        self.packages = packages
        self.aina_tasks = aina_tasks
        self.canvas_calls = canvas_calls

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
            if capability.kind == "skill":
                return cast(str, result)  # delimited skill text is read by the model as written
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
        if capability.activates_scope:
            return await self.activate_scope(run, capability, call_id, arguments)
        if capability.kind == "mcp":
            if self.packages is None:
                raise PlatformError("DEPENDENCY_FAILED", "AINA packages are not available", status_code=503)
            aina, installation, tool = cast(tuple[AinaRecord, AinaInstallation, ToolSnapshot], capability.value)
            outcome = await self.packages.call_tool(
                aina,
                installation,
                tool.name,
                arguments,
                invocation_id=call_id,
                conversation_id=run.conversation_id,
                workspace_id=run.workspace_id,
            )
            self._keep_images(run, call_id, tool.name, outcome.images)
            if outcome.widget is not None:
                # Design §9.2: the AINA's result widget, shown with the turn's answer and kept with it.
                run.scope.widgets.append(aina_widget_definition(outcome.widget))
            if outcome.task is not None:
                if self.aina_tasks is None:
                    raise PlatformError("DEPENDENCY_FAILED", "AINA tasks are not available", status_code=503)
                task = await self.aina_tasks.register(
                    outcome.task,
                    installation=installation,
                    tool_name=tool.name,
                    invocation_id=call_id,
                    conversation_id=run.conversation_id,
                    workspace_id=run.workspace_id,
                )
                # Design §7.3: the acknowledgement completes the turn; the result is posted when the task ends.
                run.scope.direct_reply = _aina_task_reply(task)
            return outcome.visible
        if capability.kind == "canvas":
            return await self._call_canvas(run, capability, call_id, arguments)
        if capability.kind == "skill":
            aina, _installation = cast(tuple[AinaRecord, AinaInstallation], capability.value)
            return load_skill(aina, arguments["id"], arguments.get("file"))
        if capability.capability_id in {DESCRIBE_AINA_TOOL_ID, OPEN_APP_TOOL_ID, SUGGEST_AINAS_TOOL_ID}:
            run.scope.widgets = [widget for widget in run.scope.widgets if widget.kind != "app_list"]
        if capability.capability_id in {LIST_APP_TOOL_ID, OPEN_APP_TOOL_ID, SUGGEST_AINAS_TOOL_ID}:
            # Keep one app selection row; opening an app replaces the suggestions.
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
        if capability.capability_id == OPEN_APP_TOOL_ID:
            run.opened_app_id = result["aina_id"]
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
        run.active_aina_id = capability.capability_id
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


    async def _call_canvas(
        self,
        run: AgentRun,
        capability: Capability,
        call_id: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Design §9.5: run a tool the AINA's open canvas implements, in the user's most recently active viewer."""
        if self.canvas_calls is None:
            raise PlatformError("DEPENDENCY_FAILED", "Canvas tools are not available", status_code=503)
        aina, _installation, tool = cast(tuple[AinaRecord, AinaInstallation, CanvasTool], capability.value)
        validate_schema_arguments(tool.name, tool.input_schema, arguments)
        canvas = package_runtime(aina).package.canvas
        timeout = min(tool.timeout_seconds or DEFAULT_CANVAS_TIMEOUT_SECONDS, MAX_CANVAS_TIMEOUT_SECONDS)
        payload = await self.canvas_calls.dispatch(
            run.conversation_id,
            {
                "call_id": call_id,
                "aina_id": aina.manifest.aina.id,
                "version": aina.manifest.aina.version,
                "canvas_title": canvas.title if canvas is not None else aina.manifest.aina.name,
                "name": tool.name,
                "effect": tool.effect,
                "arguments": arguments,
                "timeout_ms": int(timeout * 1000),
            },
            timeout=timeout,
        )
        visible = canvas_result(tool.name, payload)
        result = payload.get("result")
        content = result.get("content") if isinstance(result, dict) else None
        self._keep_images(run, call_id, tool.name, tool_images(content if isinstance(content, list) else []))
        return visible

    def _keep_images(self, run: AgentRun, call_id: str, name: str, images: list[dict[str, str]]) -> None:
        """A tool's images, for the model of this run if it reads images; never stored in the conversation."""
        if not images or self.packages is None:
            return
        runtime = current_model_runtime()
        if runtime.accepts_images if runtime is not None else self.packages.settings.llm_accepts_images:
            run.scope.tool_images[call_id] = (name, images)

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


DEFAULT_CANVAS_TIMEOUT_SECONDS = 10.0
MAX_CANVAS_TIMEOUT_SECONDS = 30.0


def _aina_task_reply(task: AinaTask) -> str:
    """Direct completion text after a long-running AINA tool started a background task."""
    return (
        f'"{task.title}" is running in the background. Its result will be posted here when it finishes; '
        "follow its progress under Background tasks."
    )


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
