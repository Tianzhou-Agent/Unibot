from datetime import datetime
from typing import Any, Literal

from pydantic import AnyHttpUrl, Field

from tianzhou_agent_platform.aina.security.models import Authentication
from tianzhou_agent_platform.core.base import StrictModel, utc_now


class ToolCreate(StrictModel):
    tool_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=2000)
    version: str = "1.0.0"
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] = Field(default_factory=dict)
    endpoint: AnyHttpUrl
    authentication: Authentication = Field(default_factory=Authentication)
    timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    retries: int = Field(default=1, ge=0, le=3)
    side_effect_level: Literal["none", "low", "high"] = "none"
    permissions: list[str] = Field(default_factory=list)
    visibility: Literal["public", "private", "tenant"] = "public"
    status: Literal["testing", "published", "disabled"] = "published"


class ToolRecord(ToolCreate):
    created_at: datetime = Field(default_factory=utc_now)
    owner_user_id: str | None = None
    owner_tenant_id: str | None = None
