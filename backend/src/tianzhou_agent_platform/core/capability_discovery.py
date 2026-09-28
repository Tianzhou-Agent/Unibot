"""Capability discovery details recorded on every run's ``capability.discovery`` trace event.

Pure functions of the resolved capabilities and repository records (no observability dependency), so the
runtime can describe the AINA graph and the model-facing scope through its run-event publisher.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tianzhou_agent_platform.aina.protocol.models import AinaCapability, AinaInstallation, AinaRecord
from tianzhou_agent_platform.core.conversation import Conversation

if TYPE_CHECKING:
    from tianzhou_agent_platform.core.agent import Capability


async def aina_graph(
    repository: Any,
    conversation: Conversation,
    model_capabilities: dict[str, Capability],
) -> dict[str, Any]:
    records = sorted(
        await repository.list_ainas(),
        key=lambda item: item.manifest.aina.id,
    )
    installations = {
        item.aina_id: item
        for item in await repository.list_installations(
            tenant_id=conversation.tenant_id,
            user_id=conversation.user_id,
        )
    }
    available: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for record in records:
        manifest = record.manifest
        aina_id = manifest.aina.id
        installation = installations.get(aina_id)
        is_available, reason, missing_permissions = _aina_availability(
            record,
            installation,
            conversation,
        )
        if not is_available:
            excluded.append(
                {
                    "id": aina_id,
                    "name": manifest.aina.name,
                    "runtime": manifest.runtime.type,
                    "reason": reason,
                    "missing_permissions": missing_permissions,
                }
            )
            continue
        owned_scope = {
            capability.capability_id: capability
            for capability in model_capabilities.values()
            if capability.owner_aina_id == aina_id
        }
        entrypoint = next(
            (
                capability
                for capability in model_capabilities.values()
                if capability.owner_aina_id == aina_id
                and capability.kind == "aina"
                and capability.capability_id == aina_id
            ),
            None,
        )
        available.append(
            {
                "id": aina_id,
                "name": manifest.aina.name,
                "version": manifest.aina.version,
                "runtime": manifest.runtime.type,
                "availability": reason,
                "routing_candidate": entrypoint is not None,
                "entrypoint": capability_details(entrypoint) if entrypoint else None,
                "capabilities": {
                    "skills": [
                        _manifest_capability_details(item, "skill", owned_scope)
                        for item in manifest.capabilities.skills
                    ],
                    "tools": [
                        _manifest_capability_details(item, "tool", owned_scope)
                        for item in manifest.capabilities.tools
                    ],
                    "ui": [
                        {
                            "id": item.id,
                            "kind": item.kind,
                            "description": item.description,
                        }
                        for item in manifest.capabilities.ui
                    ],
                    "events": manifest.capabilities.events,
                },
                "main_widget": (
                    {"id": manifest.main_widget.id, "kind": manifest.main_widget.kind}
                    if manifest.main_widget
                    else None
                ),
            }
        )
    return {
        "available_count": len(available),
        "counts": {
            "builtin_aina": sum(item["runtime"] == "builtin" for item in available),
            "remote_aina": sum(item["runtime"] == "remote" for item in available),
        },
        "available": available,
        "excluded": excluded,
    }




def capability_details(capability: Capability) -> dict[str, Any]:
    return {
        "id": capability.capability_id,
        "kind": capability.kind,
        "function_name": capability.function_name,
        "display_name": capability.display_name,
        "requires_confirmation": capability.requires_confirmation,
        "owner_aina_id": capability.owner_aina_id,
    }


def model_scope_details(
    capabilities: dict[str, Capability],
    *,
    forced_capability: str | None,
    forced_function: str | None,
) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    standalone: list[dict[str, Any]] = []
    for capability in sorted(
        capabilities.values(),
        key=lambda item: (item.kind, item.capability_id),
    ):
        details = capability_details(capability)
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


def _manifest_capability_details(
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
    missing_permissions = sorted(
        set(manifest.permissions) - set(installation.granted_permissions)
    )
    if missing_permissions:
        return False, "missing_permissions", missing_permissions
    return True, "installed", []
