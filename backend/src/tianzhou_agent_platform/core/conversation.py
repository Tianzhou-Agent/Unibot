from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from tianzhou_agent_platform.aina.protocol.widgets import WidgetDefinition
from tianzhou_agent_platform.core.base import StrictModel, utc_now


class Message(StrictModel):
    id: str
    role: Literal["user", "assistant", "system", "tool"]
    content: str = ""
    content_type: str = "text"
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    name: str | None = None
    widgets: list[WidgetDefinition] = Field(default_factory=list)
    trace_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)

    def provider_message(self) -> dict[str, Any]:
        message: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            message["tool_calls"] = self.tool_calls
        if self.tool_call_id:
            message["tool_call_id"] = self.tool_call_id
        if self.name:
            message["name"] = self.name
        return message


class ConversationCreate(StrictModel):
    user_id: str = "anonymous"
    tenant_id: str = "default"
    title: str = "新对话"
    category: str = Field(default="general", min_length=1, max_length=40)
    config: dict[str, Any] = Field(default_factory=dict)
    enabled_ainas: list[str] = Field(default_factory=list)
    active_aina_ids: list[str] = Field(default_factory=list)
    primary_aina_id: str | None = None
    last_aina_id: str | None = None


class ConversationUpdate(StrictModel):
    title: str | None = None
    category: str | None = Field(default=None, min_length=1, max_length=40)
    config: dict[str, Any] | None = None
    enabled_ainas: list[str] | None = None
    active_aina_ids: list[str] | None = None
    primary_aina_id: str | None = None
    last_aina_id: str | None = None


class Conversation(StrictModel):
    id: str
    user_id: str
    tenant_id: str
    title: str
    category: str = "general"
    status: Literal["active", "archived", "deleted"] = "active"
    run_status: Literal["idle", "running", "approval_required", "failed"] = "idle"
    active_trace_id: str | None = None
    run_error: str | None = None
    run_started_at: datetime | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    enabled_ainas: list[str] = Field(default_factory=list)
    active_aina_ids: list[str] = Field(default_factory=list)
    primary_aina_id: str | None = None
    last_aina_id: str | None = None
    messages: list[Message] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
