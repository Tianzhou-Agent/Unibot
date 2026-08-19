from datetime import datetime
from typing import Any, Literal

from pydantic import AnyHttpUrl, Field

from tianzhou_agent_platform.aina.security.models import Authentication
from tianzhou_agent_platform.core.base import StrictModel, utc_now


class McpServerCreate(StrictModel):
    server_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    name: str = Field(min_length=1, max_length=128)
    endpoint: AnyHttpUrl
    authentication: Authentication = Field(default_factory=Authentication)
    timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    side_effect_level: Literal["none", "low", "high"] = "low"
    visibility: Literal["public", "private", "tenant"] = "public"
    status: Literal["published", "disabled"] = "published"


class McpToolDescriptor(StrictModel):
    name: str = Field(min_length=1, max_length=256)
    title: str | None = Field(default=None, max_length=256)
    description: str = Field(default="MCP tool", max_length=4000)
    input_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    output_schema: dict[str, Any] = Field(default_factory=dict)
    annotations: dict[str, Any] = Field(default_factory=dict)


class McpServerRecord(McpServerCreate):
    owner_user_id: str | None = None
    tenant_id: str | None = None
    protocol_version: str | None = None
    server_name: str | None = None
    server_version: str | None = None
    instructions: str | None = None
    tools: list[McpToolDescriptor] = Field(default_factory=list)
    refreshed_at: datetime = Field(default_factory=utc_now)
    created_at: datetime = Field(default_factory=utc_now)
