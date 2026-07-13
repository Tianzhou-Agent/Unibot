from datetime import datetime
from typing import Any, Literal

from pydantic import Field, field_validator

from tianzhou_agent_platform.core.base import StrictModel, utc_now

MemoryCategory = Literal["fact", "preference", "goal", "instruction"]


class MemoryCreate(StrictModel):
    content: str = Field(min_length=1, max_length=1000)
    category: MemoryCategory = "fact"
    user_id: str = "anonymous"
    tenant_id: str = "default"
    source_conversation_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        content = " ".join(value.split()).strip()
        if not content:
            raise ValueError("Memory content must not be empty")
        lowered = content.casefold()
        blocked_markers = (
            "<memory-context",
            "</memory-context",
            "ignore previous instructions",
            "ignore all previous instructions",
            "system prompt",
        )
        if any(marker in lowered for marker in blocked_markers):
            raise ValueError("Memory content contains an unsafe instruction marker")
        return content


class MemoryUpdate(StrictModel):
    content: str | None = Field(default=None, min_length=1, max_length=1000)
    category: MemoryCategory | None = None
    user_id: str = "anonymous"
    tenant_id: str = "default"

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return MemoryCreate.validate_content(value)


class MemoryRecord(StrictModel):
    id: str
    content: str
    category: MemoryCategory
    user_id: str
    tenant_id: str
    source_conversation_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class MemoryListResponse(StrictModel):
    items: list[MemoryRecord]
    total: int


class MemoryStats(StrictModel):
    total: int = 0
    fact: int = 0
    preference: int = 0
    goal: int = 0
    instruction: int = 0
