"""Scope/authorization policy bound outside the runtime.

Re-resolve permissions on approval resume. Scope activation affects the
following model request; sibling calls remain governed by the original batch
scope. One-AINA-activation rule is enforced here (business policy).
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from langchain_core.tools import BaseTool

from tianzhou_agent_platform.core.agent_runtime.middleware.tool_policy import current_batch


def filter_advertised_tools(
    tools: Sequence[BaseTool],
    *,
    allowed_names: set[str] | None = None,
    hidden_names: set[str] | None = None,
) -> list[BaseTool]:
    """Deterministic scope filtering of tools advertised to the model.

    Registration alone does not grant execution permission; hidden tools and
    skill instructions must not leak into the prompt.
    """
    allowed = allowed_names
    hidden = hidden_names or set()
    result: list[BaseTool] = []
    for tool in tools:
        name = tool.name
        if name in hidden:
            continue
        if allowed is not None and name not in allowed:
            continue
        result.append(tool)
    return result


def ensure_execution_scope(
    name: str,
    *,
    allowed_names: set[str] | None = None,
    hidden_names: set[str] | None = None,
) -> None:
    """Execution-time scope check (defense in depth beyond advertisement)."""
    from tianzhou_agent_platform.core.errors import PlatformError

    hidden = hidden_names or set()
    if name in hidden or (allowed_names is not None and name not in allowed_names):
        raise PlatformError(
            code="PERMISSION_DENIED",
            message=f"Tool {name!r} is not permitted for this invocation",
            status_code=403,
            source="auth",
            user_message="没有执行该工具的权限。",
        )


def claim_scope_activation(name: str) -> bool:
    """One-AINA-activation rule: only one scope activation per batch.

    Returns True if this call may activate scope. Sibling calls remain under
    the original batch scope.
    """
    coord = current_batch()
    if coord is None:
        return True
    ledger = coord.ledger
    if ledger.activated_scope:
        return False
    ledger.activated_scope = True
    return True


def revalidate_permissions_on_resume(
    pending_names: Iterable[str],
    *,
    allowed_names: set[str] | None = None,
    hidden_names: set[str] | None = None,
) -> list[str]:
    """Re-resolve permissions when approving a paused batch.

    Returns names that are still permitted. Callers must not execute the rest.
    """
    permitted: list[str] = []
    hidden = hidden_names or set()
    for name in pending_names:
        if name in hidden:
            continue
        if allowed_names is not None and name not in allowed_names:
            continue
        permitted.append(name)
    return permitted
