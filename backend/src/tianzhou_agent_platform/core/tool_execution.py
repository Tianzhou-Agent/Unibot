"""Pure logic for tool-call execution: approval collection, argument decoding,
call deduplication and output shaping.

Kept free of framework/agent state so each step is unit-testable; the agent
runtime wires these helpers into the LangGraph tool node.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, cast

from tianzhou_agent_platform.aina.builtin import (
    UNIBOT_CODE_RUNNER_ID,
    UNIBOT_DOCUMENTS_ID,
    UNIBOT_MEMORY_ID,
)
from tianzhou_agent_platform.aina.protocol.models import (
    AinaCapability,
    AinaInstallation,
    AinaRecord,
)
from tianzhou_agent_platform.aina.tool.models import ToolRecord
from tianzhou_agent_platform.core.capability import Capability
from tianzhou_agent_platform.core.conversation import Conversation
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.core.schema import validate_value

#: Hard cap on the tool result content fed back into the model context.
MAX_TOOL_OUTPUT_CHARS = 50_000

ToolCall = dict[str, Any]


def decode_arguments(arguments_text: Any) -> dict[str, Any]:
    """Decode the raw ``arguments`` field of a tool call into a dict."""
    arguments = json.loads(arguments_text) if isinstance(arguments_text, str) else arguments_text
    if not isinstance(arguments, dict):
        raise ValueError("arguments must decode to an object")
    return arguments


def call_signature(name: str, arguments: dict[str, Any]) -> str:
    """Stable signature identifying a (tool, arguments) pair for deduplication."""
    normalized_arguments = json.dumps(arguments, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{name}:{normalized_arguments}".encode()).hexdigest()


def validate_call_arguments(capability: Capability, arguments: dict[str, Any]) -> None:
    validate_value(
        arguments,
        capability.input_schema,
        label=f"Capability {capability.capability_id} arguments",
    )


def collect_approval_required(
    tool_calls: list[ToolCall],
    capabilities: dict[str, Capability],
    approved: set[str],
) -> tuple[list[ToolCall], list[str]]:
    """Return the calls needing confirmation and their display names.

    A call needs confirmation when its capability requires it and the call id
    was not pre-approved. Invalid arguments are skipped: the call will be
    rejected with a normal error by the executor instead.
    """
    risky_calls: list[ToolCall] = []
    risky_names: list[str] = []
    for call in tool_calls:
        function = call.get("function") or {}
        name = str(function.get("name") or "")
        capability = capabilities.get(name)
        if capability is None or not capability.requires_confirmation:
            continue
        if call.get("id") in approved:
            continue
        try:
            arguments = decode_arguments(function.get("arguments") or "{}")
            validate_call_arguments(capability, arguments)
        except (TypeError, ValueError, json.JSONDecodeError, PlatformError):
            continue
        risky_calls.append(call)
        risky_names.append(capability.display_name)
    return risky_calls, risky_names


def truncate_tool_output(content: str) -> str:
    """Trim oversized tool results before they re-enter the model context."""
    if len(content) > MAX_TOOL_OUTPUT_CHARS:
        return f"{content[:MAX_TOOL_OUTPUT_CHARS]}\n[tool output truncated]"
    return content


def tool_output_message(name: str, call_id: str, content: str) -> dict[str, Any]:
    return {
        "role": "tool",
        "name": name,
        "tool_call_id": call_id,
        "content": content,
    }


#: Agent runtime helpers shared by the tool node and capability assembly.

def _capability_scope_recovery(
    requested_name: str,
    capabilities: dict[str, Capability],
) -> dict[str, Any] | None:
    target = capabilities.get(requested_name)
    if target is None:
        matches = [
            capability
            for capability in capabilities.values()
            if capability.capability_id == requested_name
        ]
        if len(matches) == 1:
            target = matches[0]
    if target is None:
        matches = [
            capability
            for capability in capabilities.values()
            if requested_name.startswith(f"{capability.function_name.rsplit('_', 1)[0]}_")
        ]
        if len(matches) == 1:
            target = matches[0]
    if target is None or target.owner_aina_id is None:
        return None
    entry = next(
        (
            capability
            for capability in capabilities.values()
            if capability.kind == "aina"
            and capability.capability_id == target.owner_aina_id
            and _is_routable_aina(capability)
        ),
        None,
    )
    if entry is None:
        return None
    return {
        "capability": target,
        "owner_aina_id": target.owner_aina_id,
        "entry_function_name": entry.function_name,
    }

def _is_routable_aina(capability: Capability) -> bool:
    if capability.kind != "aina":
        return False
    aina, _installation = cast(tuple[AinaRecord, AinaInstallation], capability.value)
    if aina.manifest.runtime.type in {"remote", "managed"}:
        return True
    return aina.manifest.aina.id in {
        UNIBOT_MEMORY_ID,
        UNIBOT_DOCUMENTS_ID,
        UNIBOT_CODE_RUNNER_ID,
    }

def _tool_call_trace_details(
    call: dict[str, Any],
    capabilities: dict[str, Capability],
) -> dict[str, Any]:
    function = call.get("function") or {}
    function_name = str(function.get("name") or "")
    capability = capabilities.get(function_name)
    arguments_text = function.get("arguments") or "{}"

    return {
        "call_id": str(call.get("id") or ""),
        "function_name": function_name,
        "capability_id": capability.capability_id if capability else None,
        "kind": capability.kind if capability else None,
        "arguments": _tool_arguments_trace_data(arguments_text),
    }

def _tool_arguments_trace_data(arguments: Any) -> Any:
    try:
        parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
    except (TypeError, ValueError, json.JSONDecodeError):
        parsed = arguments
    return parsed

def _capability_version(capability: Capability | None) -> str | None:
    if capability is None:
        return None
    if capability.kind == "tool":
        return cast(ToolRecord, capability.value).version
    if capability.kind == "aina":
        aina, installation = cast(tuple[AinaRecord, AinaInstallation], capability.value)
        return installation.installed_version or aina.manifest.aina.version
    return None

def _function_name(kind: str, capability_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", capability_id).strip("_") or "capability"
    digest = hashlib.sha1(f"{kind}:{capability_id}".encode()).hexdigest()[:8]
    prefix = f"{kind}_"
    available = 64 - len(prefix) - len(digest) - 1
    return f"{prefix}{safe[:available]}_{digest}"


def _model_message_trace_details(
    message: dict[str, Any],
    capabilities: dict[str, Capability],
) -> dict[str, Any]:
    details: dict[str, Any] = {}
    for key, value in message.items():
        if key == "tool_calls" and isinstance(value, list):
            details[key] = [
                _tool_call_trace_details(call, capabilities)
                for call in value
                if isinstance(call, dict)
            ]
            continue
        if key == "content" and isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        details[key] = value
    return details

def _aina_entry_description(aina: AinaRecord, *, executable: bool) -> str:
    description = aina.manifest.aina.description.rstrip(".。")
    if executable:
        return (
            f"{description}. Invoke this AINA when it should perform the user's task. Pass the complete "
            "request, preserving every important constraint instead of narrowing it to one planned subtask."
        )
    return (
        f"{description}. Activate this built-in capability scope when the user wants work performed in this "
        "domain, including listing, searching, reading, creating, or editing its data. This entrypoint takes no "
        "arguments and does not execute the work itself. Do not use open_aina for data work."
    )

def _manifest_capability_trace_details(
    capability: AinaCapability,
    kind: str,
    owned_scope: dict[str, Capability],
) -> dict[str, Any]:
    runtime_capability = owned_scope.get(capability.id)
    return {
        "id": capability.id,
        "kind": kind,
        "name": capability.name,
        "description": capability.description,
        "model_exposed": runtime_capability is not None,
        "function_name": runtime_capability.function_name if runtime_capability else None,
    }

def _capability_trace_details(capability: Capability) -> dict[str, Any]:
    return {
        "id": capability.capability_id,
        "kind": capability.kind,
        "function_name": capability.function_name,
        "display_name": capability.display_name,
        "requires_confirmation": capability.requires_confirmation,
        "owner_aina_id": capability.owner_aina_id,
    }

def _model_scope_trace_details(
    capabilities: dict[str, Capability],
    *,
    forced_capability: str | None,
    forced_function: str | None,
) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    standalone: list[dict[str, Any]] = []
    for capability in sorted(capabilities.values(), key=lambda item: (item.kind, item.capability_id)):
        details = _capability_trace_details(capability)
        if capability.owner_aina_id is None:
            standalone.append(details)
        else:
            grouped.setdefault(capability.owner_aina_id, []).append(details)
    return {
        "counts": {
            "remote_tool": sum(item.kind == "tool" for item in capabilities.values()),
            "remote_aina": sum(item.kind == "aina" for item in capabilities.values()),
            "builtin_capability": sum(item.kind == "builtin" for item in capabilities.values()),
        },
        "forced": forced_capability,
        "forced_function": forced_function,
        "by_aina": [
            {"aina_id": aina_id, "capabilities": grouped[aina_id]}
            for aina_id in sorted(grouped)
        ],
        "standalone": standalone,
    }

def _aina_availability(
    record: AinaRecord,
    installation: AinaInstallation | None,
    conversation: Conversation,
) -> tuple[bool, str, list[str]]:
    manifest = record.manifest
    if record.status != "registered":
        return False, "disabled", []
    if manifest.runtime.type == "builtin":
        return True, "builtin", []
    if installation is None:
        return False, "not_installed", []
    if installation.status != "active":
        return False, "installation_disabled", []
    if conversation.enabled_ainas and manifest.aina.id not in conversation.enabled_ainas:
        return False, "disabled_for_conversation", []
    missing_permissions = sorted(set(manifest.permissions) - set(installation.granted_permissions))
    if missing_permissions:
        return False, "missing_permissions", missing_permissions
    return True, "installed", []
