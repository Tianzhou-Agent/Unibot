from datetime import datetime
from typing import Any, Literal

from pydantic import AnyHttpUrl, Field, field_validator, model_validator

from tianzhou_agent_platform.aina.protocol.widgets import WidgetDefinition
from tianzhou_agent_platform.aina.security.models import Authentication
from tianzhou_agent_platform.core.base import StrictModel, Usage, utc_now


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


class BuiltinRuntimeDefinition(StrictModel):
    type: Literal["builtin"] = "builtin"


class AinaCapability(StrictModel):
    id: str
    name: str
    description: str
    input_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    instructions: str | None = None


class AinaUiCapability(StrictModel):
    id: str = Field(min_length=1, max_length=128)
    kind: Literal[
        "app_list",
        "form",
        "markdown",
        "panel",
        "navigation",
        "memory",
        "document",
        "document_outline",
    ]
    description: str = Field(min_length=1, max_length=2000)
    instructions: str | None = None


class AinaCapabilities(StrictModel):
    skills: list[AinaCapability] = Field(default_factory=list)
    tools: list[AinaCapability] = Field(default_factory=list)
    ui: list[AinaUiCapability] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)


class AinaManifest(StrictModel):
    protocol_version: str
    aina: AinaIdentity
    runtime: RemoteRuntimeDefinition | BuiltinRuntimeDefinition
    capabilities: AinaCapabilities = Field(default_factory=AinaCapabilities)
    main_widget: WidgetDefinition | None = None
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


class OpenAinaRequest(StrictModel):
    user_id: str = "anonymous"
    tenant_id: str = "default"
    conversation_id: str | None = None


class AinaCanvasResponse(StrictModel):
    aina_id: str
    name: str
    description: str
    version: str
    conversation_id: str | None = None
    route: str
    main_widget: WidgetDefinition


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
