from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Message(StrictModel):
    id: str
    role: Literal["user", "assistant", "system", "tool"]
    content: str = ""
    content_type: str = "text"
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    name: str | None = None
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
    title: str = "New conversation"
    config: dict[str, Any] = Field(default_factory=dict)
    enabled_ainas: list[str] = Field(default_factory=list)


class ConversationUpdate(StrictModel):
    title: str | None = None
    config: dict[str, Any] | None = None
    enabled_ainas: list[str] | None = None


class Conversation(StrictModel):
    id: str
    user_id: str
    tenant_id: str
    title: str
    status: Literal["active", "archived", "deleted"] = "active"
    config: dict[str, Any] = Field(default_factory=dict)
    enabled_ainas: list[str] = Field(default_factory=list)
    messages: list[Message] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Authentication(StrictModel):
    type: Literal["none", "bearer", "api_key", "oauth2"] = "none"
    header_name: str = "Authorization"
    credential: SecretStr | None = Field(default=None, exclude=True)


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


class Publisher(StrictModel):
    id: str
    name: str


class AinaIdentity(StrictModel):
    id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9._-]+$")
    name: str
    version: str
    description: str
    publisher: Publisher


class RemoteRuntimeDefinition(StrictModel):
    type: Literal["remote"] = "remote"
    endpoint: AnyHttpUrl
    streaming: bool = False
    async_tasks: bool = False


class AinaCapability(StrictModel):
    id: str
    name: str
    description: str
    input_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})


class AinaCapabilities(StrictModel):
    skills: list[AinaCapability] = Field(default_factory=list)
    tools: list[AinaCapability] = Field(default_factory=list)
    ui: list[dict[str, Any]] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)


class AinaManifest(StrictModel):
    protocol_version: str
    aina: AinaIdentity
    runtime: RemoteRuntimeDefinition
    capabilities: AinaCapabilities = Field(default_factory=AinaCapabilities)
    permissions: list[str] = Field(default_factory=list)
    authentication: Authentication = Field(default_factory=Authentication)
    health_check: AnyHttpUrl | None = None

    @field_validator("protocol_version")
    @classmethod
    def validate_protocol_version(cls, value: str) -> str:
        if value != "1.0":
            raise ValueError("Only AINA Protocol 1.0 is supported")
        return value


class AinaRecord(StrictModel):
    manifest: AinaManifest
    status: Literal["registered", "disabled"] = "registered"
    registered_at: datetime = Field(default_factory=utc_now)
    last_health: dict[str, Any] = Field(default_factory=dict)


class InstallationRequest(StrictModel):
    user_id: str = "anonymous"
    tenant_id: str = "default"
    granted_permissions: list[str] = Field(default_factory=list)
    configuration: dict[str, Any] = Field(default_factory=dict)


class AinaInstallation(InstallationRequest):
    aina_id: str
    installed_version: str
    status: Literal["active", "disabled"] = "active"
    installed_at: datetime = Field(default_factory=utc_now)


class PermissionUpdate(StrictModel):
    user_id: str = "anonymous"
    tenant_id: str = "default"
    granted_permissions: list[str]


class ChatRequest(StrictModel):
    message: str = Field(min_length=1)
    conversation_id: str | None = None
    user_id: str = "anonymous"
    tenant_id: str = "default"
    capability: str | None = Field(
        default=None,
        description="Optionally force the first call to tool:<id> or aina:<id>.",
    )


class ApprovalAction(StrictModel):
    user_id: str = "anonymous"
    tenant_id: str = "default"


class ApprovalRecord(StrictModel):
    id: str
    conversation_id: str
    user_id: str
    tenant_id: str
    trace_id: str
    tool_calls: list[dict[str, Any]]
    capability_names: list[str]
    status: Literal["pending", "approved", "denied", "executed"] = "pending"
    created_at: datetime = Field(default_factory=utc_now)
    resolved_at: datetime | None = None


class Usage(StrictModel):
    input_tokens: int = 0
    output_tokens: int = 0


class ChatResponse(StrictModel):
    conversation_id: str
    message_id: str | None = None
    content: str
    status: Literal["completed", "approval_required", "failed"]
    trace_id: str
    iterations: int
    usage: Usage = Field(default_factory=Usage)
    approval: ApprovalRecord | None = None


class TraceEvent(StrictModel):
    timestamp: datetime = Field(default_factory=utc_now)
    kind: str
    status: str
    target_type: str | None = None
    target_id: str | None = None
    duration_ms: float | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class TraceRecord(StrictModel):
    trace_id: str
    conversation_id: str | None = None
    user_id: str
    tenant_id: str
    status: Literal["running", "completed", "approval_required", "failed"] = "running"
    events: list[TraceEvent] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None


class StandardError(StrictModel):
    code: str
    message: str
    retryable: bool
    source: str
    user_message: str
    trace_id: str


class ErrorEnvelope(StrictModel):
    error: StandardError


class AinaInvokeRequest(StrictModel):
    request_id: str
    user_id: str
    tenant_id: str
    session_id: str
    conversation_id: str
    input: dict[str, Any]
    context: dict[str, Any] = Field(default_factory=dict)
    authorization: dict[str, Any] = Field(default_factory=dict)
    locale: str = "en"
    timezone: str = "UTC"
    trace: dict[str, Any]
    available_tools: list[str] = Field(default_factory=list)


class AinaOutput(StrictModel):
    type: str
    content: Any


class AinaInvokeResponse(StrictModel):
    request_id: str
    status: Literal["completed", "failed", "input_required", "approval_required"]
    outputs: list[AinaOutput] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    trace_id: str

    @model_validator(mode="after")
    def require_output_for_completed(self) -> "AinaInvokeResponse":
        if self.status == "completed" and not self.outputs:
            raise ValueError("A completed AINA response must include an output")
        return self
