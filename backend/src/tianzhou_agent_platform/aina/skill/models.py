from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from tianzhou_agent_platform.core.base import StrictModel, utc_now


class SkillCreate(StrictModel):
    skill_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=2000)
    version: str = "1.0.0"
    input_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    output_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    instructions: str = Field(min_length=1)
    tools: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    publisher: str = "platform"
    visibility: Literal["public", "private", "tenant"] = "public"
    status: Literal["draft", "testing", "published", "deprecated", "disabled", "archived"] = "draft"


class SkillRecord(SkillCreate):
    created_at: datetime = Field(default_factory=utc_now)
