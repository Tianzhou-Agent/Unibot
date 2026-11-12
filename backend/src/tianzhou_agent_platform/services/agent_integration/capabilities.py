"""Superbot capabilities the agent can call: discovery, AINA scopes and forced-capability resolution.

A capability is a remote tool, an AINA entrypoint or a host built-in. The catalog resolves which ones an actor may
use in a conversation; the executor binds them to native tools.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from typing import Any, Protocol, cast

from tianzhou_agent_platform.aina.builtin import (
    FORGET_TOOL_ID,
    RECALL_TOOL_ID,
    REMEMBER_TOOL_ID,
    UNIBOT_MEMORY_ID,
    UNIBOT_SCHEDULER_ID,
    UPDATE_TOOL_ID,
)
from tianzhou_agent_platform.aina.document.builtin import (
    ABANDON_EDIT_SECTION_TOOL_ID,
    DELETE_DOCUMENT_TOOL_ID,
    MERGE_EDIT_SECTION_TOOL_ID,
    UNIBOT_DOCUMENTS_ID,
    document_tool_capabilities,
)
from tianzhou_agent_platform.aina.package.bundled import with_bundled
from tianzhou_agent_platform.aina.package.models import PackageRuntimeDefinition, ToolSnapshot
from tianzhou_agent_platform.aina.package.service import package_runtime, validate_arguments
from tianzhou_agent_platform.aina.package.widgets import confirmation_widget
from tianzhou_agent_platform.aina.protocol.models import AinaInstallation, AinaRecord
from tianzhou_agent_platform.aina.security.access import capability_visible
from tianzhou_agent_platform.aina.skill.models import SkillRecord
from tianzhou_agent_platform.aina.tool.models import ToolRecord
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.conversations.models import Conversation
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.services.platform_tools import (
    DESCRIBE_AINA_TOOL_ID,
    LIST_APP_TOOL_ID,
    OPEN_APP_TOOL_ID,
    REQUEST_CLARIFICATION_TOOL_ID,
    SUGGEST_AINAS_TOOL_ID,
)
from tianzhou_agent_platform.tasks.operation import TASK_TOOL_IDS, task_tool_specs
from unibot_agent.capabilities import Capability as AgentCapability
from unibot_agent.capabilities import Recovery
from unibot_agent.run import AgentRun

_HIGH_RISK_MARKERS = (
    "send",
    "email",
    "publish",
    "delete",
    "payment",
    "pay.",
    "order",
    "irreversible",
    "sensitive.third_party",
)


class CapabilityRegistry(Protocol):
    """Read access to registered tools, skills, AINAs and installations (the catalog's only persistence need)."""

    async def list_tools(self) -> list[ToolRecord]: ...

    async def list_skills(self) -> list[SkillRecord]: ...

    async def list_ainas(self) -> list[AinaRecord]: ...

    async def get_aina(self, aina_id: str) -> AinaRecord: ...

    async def list_installations(
        self, *, tenant_id: str | None = None, user_id: str | None = None
    ) -> list[AinaInstallation]: ...


class Capability(AgentCapability):
    """A remote tool, an AINA entrypoint or a host built-in, as the agent core calls it.

    ``kind`` is ``tool``, ``aina``, ``builtin``, ``mcp`` (a tool exposed by an AINA 2.0 package), ``canvas`` (a tool
    its open canvas implements, design §9.5) or ``skill`` (its ``load_skill`` host tool). ``value`` is the record
    behind it: a ``ToolRecord``, ``(AinaRecord, AinaInstallation)``, the same with the package's ``ToolSnapshot`` or
    ``CanvasTool``, or a built-in's tool id. ``owner_id`` is the AINA that owns it.
    """

    __slots__ = ()

    @property
    def scope_entry(self) -> bool:
        return self.kind == "aina"

    @property
    def activates_scope(self) -> bool:
        """A built-in or package AINA entrypoint only loads that AINA's capabilities into the model scope."""
        return self.kind == "aina" and cast(
            tuple[AinaRecord, AinaInstallation], self.value
        )[0].manifest.runtime.type in {"builtin", "package"}

    @property
    def retry_safe(self) -> bool:
        """Only side-effect-free remote tools may be retried (automatically or by the model)."""
        if self.kind == "mcp":
            return cast(tuple[AinaRecord, AinaInstallation, ToolSnapshot], self.value)[2].effect == "read"
        return self.kind == "tool" and cast(ToolRecord, self.value).side_effect_level == "none"

    @property
    def retries(self) -> int:
        # A read tool of an AINA package gets one automatic retry; remote tools declare their own budget.
        if not self.retry_safe:
            return 0
        return cast(ToolRecord, self.value).retries if self.kind == "tool" else 1

    @property
    def repeatable(self) -> bool:
        return self.kind == "canvas"

    @property
    def version(self) -> str | None:
        return capability_version(self)

    @property
    def editable(self) -> bool:
        """Its AINA declares a confirmation widget, which may propose changed arguments (design §9.2)."""
        if self.kind != "mcp":
            return False
        aina, _installation, tool = cast(tuple[AinaRecord, AinaInstallation, ToolSnapshot], self.value)
        exposed = package_runtime(aina).package.exposed().get(tool.name)
        return exposed is not None and exposed.confirm_widget is not None

    def confirmation(self, call_id: str, arguments: dict[str, Any]) -> Any | None:
        if self.kind != "mcp":
            return None
        aina, _installation, tool = cast(tuple[AinaRecord, AinaInstallation, ToolSnapshot], self.value)
        return confirmation_widget(aina, tool.name, call_id=call_id, arguments=arguments)

    def check_edit(self, arguments: dict[str, Any]) -> None:
        if self.kind != "mcp":
            raise PlatformError("INVALID_REQUEST", "This tool call cannot be edited", status_code=422)
        aina, _installation, tool = cast(tuple[AinaRecord, AinaInstallation, ToolSnapshot], self.value)
        snapshot = package_runtime(aina).tool(tool.name)
        if snapshot is not None:
            validate_arguments(snapshot, arguments)


class CapabilityCatalog:
    """Resolves the capabilities an actor may use in a conversation."""

    def __init__(
        self,
        *,
        settings: AgentSettings,
        registry: CapabilityRegistry,
        document_service: Any | None = None,
        auth_enforced: bool = False,
    ) -> None:
        self.settings = settings
        self.registry = registry
        self.document_service = document_service
        self.auth_enforced = auth_enforced

    async def available(self, conversation: Conversation) -> dict[str, Capability]:
        """Every capability reachable in the conversation, including built-ins kept for historical calls."""
        entries = await self._aina_entries(conversation)
        packaged: dict[str, Capability] = {}
        for capability in entries.values():
            aina, installation = cast(tuple[AinaRecord, AinaInstallation], capability.value)
            if aina.manifest.runtime.type == "package":
                packaged.update(package_capabilities(aina, installation))
        return {**await self._fallback(conversation), **entries, **packaged}

    async def entry(self, conversation: Conversation) -> dict[str, Capability]:
        """Expose direct host tools and only conversational AINA entrypoints on the first model turn."""
        aina_capabilities = await self._aina_entries(conversation)
        return {
            **await self.system(conversation),
            **{
                function_name: capability
                for function_name, capability in aina_capabilities.items()
                if capability.kind == "aina" and is_routable_aina(capability)
            },
        }

    async def aina_scope(
        self,
        conversation: Conversation,
        selected: Capability,
    ) -> tuple[dict[str, Capability], AinaRecord]:
        if selected.kind != "aina":
            raise PlatformError("INVALID_REQUEST", "The selected capability is not an AINA")
        aina, installation = cast(tuple[AinaRecord, AinaInstallation], selected.value)
        switch_capabilities = {
            function_name: capability
            for function_name, capability in (await self._aina_entries(conversation)).items()
            if capability.kind == "aina"
            and capability.capability_id != aina.manifest.aina.id
            and is_routable_aina(capability)
        }
        system_capabilities = await self.system(conversation)
        task_capabilities = {
            function_name: capability
            for function_name, capability in system_capabilities.items()
            if capability.capability_id in TASK_TOOL_IDS
        }
        aina_id = aina.manifest.aina.id
        if aina.manifest.runtime.type == "package":
            scoped = package_capabilities(aina, installation)
            return {**scoped, **task_capabilities, **switch_capabilities}, aina
        if aina_id == UNIBOT_MEMORY_ID:
            return {**self._memory(), **task_capabilities, **switch_capabilities}, aina
        if aina_id == UNIBOT_DOCUMENTS_ID:
            return {
                **self._documents(),
                **self._memory(),
                **task_capabilities,
                **switch_capabilities,
            }, aina
        if aina_id == UNIBOT_SCHEDULER_ID:
            return {**task_capabilities, **switch_capabilities}, aina
        declared_tool_ids = {item.id for item in aina.manifest.capabilities.tools}
        capabilities = {selected.function_name: selected, **task_capabilities, **switch_capabilities}
        if declared_tool_ids:
            for function_name, capability in system_capabilities.items():
                if capability.kind == "tool" and capability.capability_id in declared_tool_ids:
                    capabilities[function_name] = replace(capability, owner_id=aina_id)
        if any(item.kind == "form" for item in aina.manifest.capabilities.ui):
            for function_name, capability in system_capabilities.items():
                if capability.capability_id == REQUEST_CLARIFICATION_TOOL_ID:
                    capabilities[function_name] = capability
        return capabilities, aina

    async def system(self, conversation: Conversation) -> dict[str, Capability]:
        is_admin = self.settings.is_platform_admin(user_id=conversation.user_id)
        capabilities: dict[str, Capability] = {}
        for tool in await self.registry.list_tools():
            if tool.status != "published" or not capability_visible(
                tool,
                user_id=conversation.user_id,
                tenant_id=conversation.tenant_id,
                auth_enforced=self.auth_enforced,
                is_admin=is_admin,
            ):
                continue
            name = function_name("tool", tool.tool_id)
            capabilities[name] = Capability(
                kind="tool",
                capability_id=tool.tool_id,
                function_name=name,
                display_name=tool.name,
                description=tool.description,
                input_schema=tool.input_schema,
                requires_confirmation=tool.side_effect_level == "high",
                value=tool,
            )
        for capability_id, display_name, description, input_schema in _PLATFORM_BUILTINS:
            name = function_name("builtin", capability_id)
            capabilities[name] = Capability(
                kind="builtin",
                capability_id=capability_id,
                function_name=name,
                display_name=display_name,
                description=description,
                input_schema=input_schema,
                requires_confirmation=False,
                value=capability_id,
            )
        for spec in task_tool_specs():
            name = function_name("builtin", spec["id"])
            capabilities[name] = Capability(
                kind="builtin",
                capability_id=spec["id"],
                function_name=name,
                display_name=spec["display_name"],
                description=spec["description"],
                input_schema=spec["input_schema"],
                requires_confirmation=False,
                value=spec["id"],
            )
        return capabilities

    async def _aina_entries(self, conversation: Conversation) -> dict[str, Capability]:
        capabilities: dict[str, Capability] = {}
        installations = await with_bundled(
            self.registry,
            await self.registry.list_installations(tenant_id=conversation.tenant_id, user_id=conversation.user_id),
            tenant_id=conversation.tenant_id,
            user_id=conversation.user_id,
        )
        for installation in installations:
            if installation.status != "active":
                continue
            if conversation.enabled_ainas and installation.aina_id not in conversation.enabled_ainas:
                continue
            try:
                aina = await self.registry.get_aina(installation.aina_id)
            except PlatformError:
                continue
            if aina.status != "registered" or aina.manifest.runtime.type not in {"remote", "managed", "package"}:
                continue
            name = function_name("aina", installation.aina_id)
            if isinstance(aina.manifest.runtime, PackageRuntimeDefinition):
                if aina.manifest.runtime.package.required_scopes() - set(installation.granted_permissions):
                    continue
                capabilities[name] = Capability(
                    kind="aina",
                    capability_id=installation.aina_id,
                    function_name=name,
                    display_name=aina.manifest.aina.name,
                    description=aina_entry_description(aina, executable=False),
                    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                    requires_confirmation=False,
                    value=(aina, installation),
                    owner_id=installation.aina_id,
                )
                continue
            if set(aina.manifest.permissions) - set(installation.granted_permissions):
                continue
            capabilities[name] = Capability(
                kind="aina",
                capability_id=installation.aina_id,
                function_name=name,
                display_name=aina.manifest.aina.name,
                description=aina_entry_description(aina, executable=True),
                input_schema={
                    "type": "object",
                    "properties": {
                        "input": {
                            "type": "string",
                            "description": (
                                "The user's complete request for the remote AINA. Preserve all constraints and "
                                "important details; do not replace it with only one planned subtask."
                            ),
                        }
                    },
                    "required": ["input"],
                    "additionalProperties": True,
                },
                requires_confirmation=permissions_are_high_risk(aina.manifest.permissions),
                value=(aina, installation),
                owner_id=installation.aina_id,
            )
        for aina_id in (
            UNIBOT_MEMORY_ID,
            UNIBOT_SCHEDULER_ID,
            UNIBOT_DOCUMENTS_ID,
        ):
            capabilities.update(await self._builtin_aina(conversation, aina_id))
        return capabilities

    async def _fallback(self, conversation: Conversation) -> dict[str, Capability]:
        """Keep stable built-ins resolvable when their calls remain in conversation history."""
        return {
            **await self.system(conversation),
            **self._memory(),
            **self._documents(),
        }

    async def _builtin_aina(self, conversation: Conversation, aina_id: str) -> dict[str, Capability]:
        if aina_id == UNIBOT_DOCUMENTS_ID and self.document_service is None:
            return {}
        try:
            aina = await self.registry.get_aina(aina_id)
        except PlatformError:
            return {}
        if aina.status != "registered" or aina.manifest.runtime.type != "builtin":
            return {}
        installation = AinaInstallation(
            user_id=conversation.user_id,
            tenant_id=conversation.tenant_id,
            aina_id=aina_id,
            installed_version=aina.manifest.aina.version,
        )
        name = function_name("aina", aina_id)
        return {
            name: Capability(
                kind="aina",
                capability_id=aina_id,
                function_name=name,
                display_name=aina.manifest.aina.name,
                description=aina_entry_description(aina, executable=False),
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                requires_confirmation=False,
                value=(aina, installation),
                owner_id=aina_id,
            )
        }

    @staticmethod
    def _memory() -> dict[str, Capability]:
        category = {"type": "string", "enum": ["fact", "preference", "goal", "instruction"]}
        specs: list[tuple[str, str, str, dict[str, Any], bool]] = [
            (
                REMEMBER_TOOL_ID,
                "Remember durable information",
                "Store one durable fact the user explicitly wants remembered. Do not store transient chat.",
                {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "A concise declarative memory."},
                        "category": category,
                    },
                    "required": ["content", "category"],
                    "additionalProperties": False,
                },
                False,
            ),
            (
                RECALL_TOOL_ID,
                "Recall memory",
                "Retrieve durable memories relevant to a question; use an empty query to list recent memory.",
                {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "additionalProperties": False,
                },
                False,
            ),
            (
                UPDATE_TOOL_ID,
                "Update memory",
                (
                    "Replace an existing memory in place when the user corrects or refines it and its exact id "
                    "is available. Prefer this over forget followed by remember."
                ),
                {
                    "type": "object",
                    "properties": {
                        "memory_id": {"type": "string"},
                        "content": {"type": "string", "description": "The complete updated memory."},
                        "category": category,
                    },
                    "required": ["memory_id", "content"],
                    "additionalProperties": False,
                },
                False,
            ),
            (
                FORGET_TOOL_ID,
                "Forget memory",
                "Permanently delete one memory by exact memory_id after the user asks to forget it.",
                {
                    "type": "object",
                    "properties": {"memory_id": {"type": "string"}},
                    "required": ["memory_id"],
                    "additionalProperties": False,
                },
                True,
            ),
        ]
        capabilities: dict[str, Capability] = {}
        for capability_id, display_name, description, input_schema, requires_confirmation in specs:
            name = function_name("builtin", capability_id)
            capabilities[name] = Capability(
                kind="builtin",
                capability_id=capability_id,
                function_name=name,
                display_name=display_name,
                description=description,
                input_schema=input_schema,
                requires_confirmation=requires_confirmation,
                value=capability_id,
                owner_id=UNIBOT_MEMORY_ID,
            )
        return capabilities

    def _documents(self) -> dict[str, Capability]:
        if self.document_service is None:
            return {}
        capabilities: dict[str, Capability] = {}
        for tool in document_tool_capabilities():
            name = function_name("builtin", tool.id)
            capabilities[name] = Capability(
                kind="builtin",
                capability_id=tool.id,
                function_name=name,
                display_name=tool.name,
                description=tool.description,
                input_schema=tool.input_schema,
                requires_confirmation=tool.id
                in {DELETE_DOCUMENT_TOOL_ID, MERGE_EDIT_SECTION_TOOL_ID, ABANDON_EDIT_SECTION_TOOL_ID},
                value=tool.id,
                owner_id=UNIBOT_DOCUMENTS_ID,
            )
        return capabilities


_PLATFORM_BUILTINS: tuple[tuple[str, str, str, dict[str, Any]], ...] = (
    (
        LIST_APP_TOOL_ID,
        "List applications",
        (
            "Show all available applications as Suggested apps chips only when the user wants to discover or "
            "choose among applications. Do not use it to resolve a named AINA before describe_aina."
        ),
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    (
        DESCRIBE_AINA_TOOL_ID,
        "Describe AINA",
        (
            "Read the declared skills, tools, UI, and metadata of one AINA without opening it or navigating "
            "to its canvas. Use this for questions about what an AINA is or can do."
        ),
        {
            "type": "object",
            "properties": {
                "aina_id": {
                    "type": "string",
                    "description": "The exact AINA identifier or display name to inspect without opening it.",
                }
            },
            "required": ["aina_id"],
            "additionalProperties": False,
        },
    ),
    (
        OPEN_APP_TOOL_ID,
        "Open application",
        (
            "Navigate to a selected AINA's canvas and load its main widget only when the user explicitly asks "
            "to open, enter, or switch to the application UI. Never use this to list, search, inspect, read, "
            "create, or edit the application's data; activate the matching AINA capability scope instead."
        ),
        {
            "type": "object",
            "properties": {
                "aina_id": {
                    "type": "string",
                    "description": "The exact AINA identifier whose application UI the user explicitly asked to open.",
                }
            },
            "required": ["aina_id"],
            "additionalProperties": False,
        },
    ),
    (
        SUGGEST_AINAS_TOOL_ID,
        "Suggest AINAs",
        (
            "Offer a ranked shortlist of AINAs whose UI fits the user's goal; the user picks which to open. "
            "Never use it when the user asks to open a named app: call open_app."
        ),
        {
            "type": "object",
            "properties": {
                "aina_ids": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 5,
                    "items": {"type": "string"},
                    "description": "AINA ids, best match first.",
                },
                "reason": {
                    "type": "string",
                    "description": "Why they fit, in one sentence in the user's language.",
                },
            },
            "required": ["aina_ids", "reason"],
            "additionalProperties": False,
        },
    ),
    (
        REQUEST_CLARIFICATION_TOOL_ID,
        "Request clarification",
        (
            "Show a host-rendered form only when essential information is missing. Include known values as "
            "prefilled field values, and ask only for information needed to continue."
        ),
        {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string"},
                "submit_label": {"type": "string"},
                "fields": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 6,
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "label": {"type": "string"},
                            "input_type": {"type": "string", "enum": ["text", "number", "textarea"]},
                            "placeholder": {"type": "string"},
                            "required": {"type": "boolean"},
                            "value": {"type": "string"},
                        },
                        "required": ["id", "label"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["title", "fields"],
            "additionalProperties": False,
        },
    ),
)


def package_capabilities(aina: AinaRecord, installation: AinaInstallation) -> dict[str, Capability]:
    """The model-visible scope of an AINA 2.0 package: its active exposed tools and ``load_skill``.

    Design §6.1: the reviewed snapshot is what the model sees, and the tool effect decides approvals. ``write``
    asks only for the first completed call in a conversation; ``destructive`` asks for every call.
    """
    runtime = cast(PackageRuntimeDefinition, aina.manifest.runtime)
    aina_id = aina.manifest.aina.id
    capabilities: dict[str, Capability] = {}
    for tool in runtime.tools:
        if not tool.model_visible:
            continue
        name = function_name("mcp", f"{aina_id}.{tool.name}")
        capabilities[name] = Capability(
            kind="mcp",
            capability_id=f"{aina_id}/{tool.name}",
            function_name=name,
            display_name=tool.title or tool.name,
            description=tool.description,
            input_schema=tool.input_schema,
            requires_confirmation=tool.effect != "read",
            value=(aina, installation, tool),
            owner_id=aina_id,
            confirm_once=tool.effect == "write",
        )
    canvas = runtime.package.canvas
    for canvas_tool in canvas.tools if canvas is not None else []:
        effect = canvas_tool.effect or "write"
        name = function_name("canvas", f"{aina_id}.{canvas_tool.name}")
        capabilities[name] = Capability(
            kind="canvas",
            capability_id=f"{aina_id}/canvas/{canvas_tool.name}",
            function_name=name,
            display_name=canvas_tool.title or canvas_tool.name,
            description=canvas_tool.description,
            input_schema=canvas_tool.input_schema,
            requires_confirmation=effect != "read",
            value=(aina, installation, canvas_tool),
            owner_id=aina_id,
            confirm_once=effect == "write",
        )
    if runtime.skills:
        name = load_skill_function_name(aina_id)
        capabilities[name] = Capability(
            kind="skill",
            capability_id=f"{aina_id}/load_skill",
            function_name=name,
            display_name="Load skill",
            description=(
                f"Load one {aina.manifest.aina.name} skill: a workflow written by the AINA publisher for a task. "
                "Call it when a skill in the index matches the user's task, before using the AINA's tools; pass "
                "file to read a supporting file the skill names."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "enum": [skill.id for skill in runtime.skills]},
                    "file": {"type": "string", "description": "A supporting file path listed by the skill."},
                },
                "required": ["id"],
                "additionalProperties": False,
            },
            requires_confirmation=False,
            value=(aina, installation),
            owner_id=aina_id,
        )
    return capabilities


def load_skill_function_name(aina_id: str) -> str:
    return function_name("load_skill", aina_id)


def resolve_forced_capability(requested: str | None, capabilities: dict[str, Capability]) -> str | None:
    """Function name of a ``tool:<id>`` / ``aina:<id>`` / ``builtin:<id>`` request."""
    if requested is None:
        return None
    kind: str | None = None
    capability_id = requested
    if ":" in requested:
        kind, capability_id = requested.split(":", 1)
        if kind not in {"tool", "aina", "builtin"}:
            raise PlatformError(
                "INVALID_REQUEST",
                "Capability must use tool:<id>, aina:<id>, or builtin:<id>",
            )
    matches = [
        item
        for item in capabilities.values()
        if item.capability_id == capability_id and (kind is None or item.kind == kind)
    ]
    if len(matches) != 1:
        raise PlatformError(
            "PERMISSION_DENIED",
            "The requested capability is not installed, enabled, or fully authorized",
            status_code=403,
        )
    return matches[0].function_name


def scope_recovery(requested_name: str, capabilities: dict[str, Capability]) -> Recovery | None:
    """The AINA entrypoint that would bring an out-of-scope capability back into scope, if any."""
    target = capabilities.get(requested_name)
    if target is None:
        matches = [item for item in capabilities.values() if item.capability_id == requested_name]
        if len(matches) == 1:
            target = matches[0]
    if target is None:
        matches = [
            item
            for item in capabilities.values()
            if requested_name.startswith(f"{item.function_name.rsplit('_', 1)[0]}_")
        ]
        if len(matches) == 1:
            target = matches[0]
    if target is None or target.owner_id is None:
        return None
    entry = next(
        (
            item
            for item in capabilities.values()
            if item.kind == "aina" and item.capability_id == target.owner_id and is_routable_aina(item)
        ),
        None,
    )
    if entry is None:
        return None
    return Recovery(
        capability=target,
        message=(
            f"Capability {requested_name!r} belongs to AINA {target.owner_id!r}, which is not "
            "active in the current scope."
        ),
        instruction=(
            f"Activate AINA {target.owner_id} by calling "
            f"{entry.function_name} with an empty object, then retry with the advertised tool. "
            "Do not claim that the capability completed before its tool succeeds."
        ),
        details={"owner_aina_id": target.owner_id, "entry_function_name": entry.function_name},
    )


async def scope_resolved(
    run: AgentRun,
    conversation: Conversation,
    *,
    selected: Capability | None,
    source: str,
    requested_capability: str | None = None,
    preferred_aina_id: str | None = None,
) -> None:
    """Record on the run's trace which scope its turn was routed to, and why."""
    await run.push(
        "routing.scope.resolved",
        "completed",
        target_type=selected.kind if selected is not None else "system",
        target_id=selected.capability_id if selected is not None else None,
        details={
            "source": source,
            "requested_capability": requested_capability,
            "preferred_aina_id": preferred_aina_id,
            "active_aina_ids": conversation.active_aina_ids,
            "primary_aina_id": conversation.primary_aina_id,
            "last_aina_id": conversation.last_aina_id,
        },
    )


def capability_version(capability: Capability) -> str | None:
    if capability.kind == "tool":
        return cast(ToolRecord, capability.value).version
    if capability.kind in {"aina", "mcp", "canvas", "skill"}:
        aina = cast(tuple[AinaRecord, ...], capability.value)[0]
        return aina.manifest.aina.version
    return None


def is_routable_aina(capability: Capability) -> bool:
    if capability.kind != "aina":
        return False
    aina, _installation = cast(tuple[AinaRecord, AinaInstallation], capability.value)
    runtime = aina.manifest.runtime
    if isinstance(runtime, PackageRuntimeDefinition):
        # A package only its UI drives (app tools, no skills or canvas tools) gives the agent nothing to route to.
        canvas = runtime.package.canvas
        return any(tool.model_visible for tool in runtime.tools) or bool(runtime.skills) or bool(canvas and canvas.tools)
    if runtime.type in {"remote", "managed"}:
        return True
    return aina.manifest.aina.id in {UNIBOT_MEMORY_ID, UNIBOT_DOCUMENTS_ID}


def aina_entry_description(aina: AinaRecord, *, executable: bool) -> str:
    description = aina.manifest.aina.description.rstrip(".。")
    if executable:
        return (
            f"{description}. Invoke this AINA when it should perform the user's task. Pass the complete "
            "request, preserving every important constraint instead of narrowing it to one planned subtask."
        )
    return (
        f"{description}. Activate this capability scope when the user wants work performed in this "
        "domain, including listing, searching, reading, creating, or editing its data. This entrypoint takes no "
        "arguments, does not execute the work itself and does not open the application UI: when the user asks to "
        "open, enter, launch or show this app, call open_app instead. Do not use open_app for data work."
    )


def function_name(kind: str, capability_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", capability_id).strip("_") or "capability"
    digest = hashlib.sha1(f"{kind}:{capability_id}".encode()).hexdigest()[:8]
    prefix = f"{kind}_"
    available = 64 - len(prefix) - len(digest) - 1
    return f"{prefix}{safe[:available]}_{digest}"


def permissions_are_high_risk(permissions: list[str]) -> bool:
    return any(marker in permission.lower() for permission in permissions for marker in _HIGH_RISK_MARKERS)
