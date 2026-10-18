"""The Unibot context of one MCP tool call (``_meta["unibot/context"]``, design §6.4)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from unibot_aina.host import HostApi

CONTEXT_META_KEY = "unibot/context"


@dataclass(frozen=True)
class UnibotContext:
    invocation_id: str
    source: str
    user_id: str
    tenant_id: str
    installation_id: str
    granted_permissions: tuple[str, ...]
    config: dict[str, Any]
    api_base: str | None
    token: str | None
    conversation_id: str | None = None
    workspace_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def host(self) -> HostApi:
        """A Host API client authorized by this call's context token."""
        if not self.api_base or not self.token:
            raise RuntimeError("This call carries no Host API access")
        return HostApi(self.api_base, self.token)


def unibot_context(ctx: Any) -> UnibotContext:
    """The context of the current call, from an MCP SDK ``Context`` (or the request ``_meta`` mapping itself)."""
    meta = ctx if isinstance(ctx, dict) else (getattr(getattr(ctx, "request_context", None), "meta", None) or {})
    raw = meta.get(CONTEXT_META_KEY)
    if not isinstance(raw, dict):
        raise ValueError("This tool must be called by Unibot (no unibot/context in the request)")
    return UnibotContext(
        invocation_id=str(raw.get("invocation_id") or ""),
        source=str(raw.get("source") or "agent"),
        user_id=str((raw.get("user") or {}).get("id") or ""),
        tenant_id=str(raw.get("tenant_id") or ""),
        installation_id=str(raw.get("installation_id") or ""),
        granted_permissions=tuple(raw.get("granted_permissions") or ()),
        config=dict(raw.get("config") or {}),
        api_base=(raw.get("host") or {}).get("api_base"),
        token=raw.get("token"),
        conversation_id=raw.get("conversation_id"),
        workspace_id=raw.get("workspace_id"),
        raw=raw,
    )
