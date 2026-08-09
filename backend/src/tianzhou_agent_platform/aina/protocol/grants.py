"""Shared AINA protocol helpers: grant checks and invoke request handling."""

from __future__ import annotations

from typing import Any

from tianzhou_agent_platform.aina.protocol.models import (
    AinaInvokeRequest,
    AinaInvokeResponse,
    AinaManifest,
)
from tianzhou_agent_platform.core.errors import PlatformError


def require_grants(manifest: AinaManifest, granted_permissions: list[str]) -> None:
    """Refuse to invoke an AINA that is missing declared grants."""
    missing = set(manifest.permissions) - set(granted_permissions)
    if missing:
        raise PlatformError(
            "PERMISSION_DENIED",
            f"AINA is missing grants: {', '.join(sorted(missing))}",
            status_code=403,
            source="aina",
        )


def build_invoke_request(
    *,
    request_id: str,
    user_id: str,
    tenant_id: str,
    session_id: str,
    conversation_id: str,
    input: dict[str, Any],
    available_tools: list[str],
    granted_permissions: list[str],
    trace_id: str,
) -> AinaInvokeRequest:
    return AinaInvokeRequest(
        request_id=request_id,
        user_id=user_id,
        tenant_id=tenant_id,
        session_id=session_id,
        conversation_id=conversation_id,
        input=input,
        context={"source": "agent"},
        authorization={"permissions": granted_permissions},
        trace={"trace_id": trace_id},
        available_tools=available_tools,
    )


def parse_invoke_response(raw: Any, *, label: str = "AINA") -> AinaInvokeResponse:
    try:
        return AinaInvokeResponse.model_validate(raw)
    except ValueError as exc:
        raise PlatformError(
            "DEPENDENCY_FAILED",
            f"{label} returned a response that does not match Protocol 1.0",
            status_code=502,
            source="aina",
        ) from exc
