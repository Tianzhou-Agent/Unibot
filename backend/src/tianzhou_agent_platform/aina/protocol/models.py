from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import AnyHttpUrl, Field, computed_field, field_validator, model_validator

from tianzhou_agent_platform.aina.package.models import PackageRuntimeDefinition
from tianzhou_agent_platform.aina.package.oauth import OAUTH_SECRET
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
    protocol: Literal["aina", "a2a"] = "aina"
    streaming: bool = False
    async_tasks: bool = False


class BuiltinRuntimeDefinition(StrictModel):
    type: Literal["builtin"] = "builtin"


class ManagedRuntimeDefinition(StrictModel):
    """Source runtime declaration for a packaged AINA that has not been deployed yet."""

    type: Literal["managed"] = "managed"
    language: Literal["python", "node"]
    entrypoint: str
    dependency_file: str | None = None

    @field_validator("entrypoint")
    @classmethod
    def validate_entrypoint(cls, value: str) -> str:
        path, separator, handler = value.partition(":")
        if not separator or not handler.isidentifier():
            raise ValueError("Managed runtime entrypoint must use relative/path.ext:handler")
        _validate_relative_project_path(path, label="Managed runtime entrypoint")
        return value

    @field_validator("dependency_file")
    @classmethod
    def validate_dependency_file(cls, value: str | None) -> str | None:
        if value is not None:
            _validate_relative_project_path(value, label="Managed runtime dependency_file")
        return value

    @model_validator(mode="after")
    def validate_language_entrypoint(self) -> "ManagedRuntimeDefinition":
        path = self.entrypoint.partition(":")[0]
        if self.language == "python" and not path.endswith(".py"):
            raise ValueError("Python managed runtimes require a .py entrypoint")
        if self.language == "node" and not path.endswith((".js", ".mjs", ".cjs")):
            raise ValueError("Node managed runtimes require a .js, .mjs, or .cjs entrypoint")
        return self


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
    runtime: RemoteRuntimeDefinition | BuiltinRuntimeDefinition | ManagedRuntimeDefinition | PackageRuntimeDefinition
    capabilities: AinaCapabilities = Field(default_factory=AinaCapabilities)
    main_widget: WidgetDefinition | None = None
    permissions: list[str] = Field(default_factory=list)
    authentication: Authentication = Field(default_factory=Authentication)
    health_check: AnyHttpUrl | None = None

    @model_validator(mode="after")
    def validate_protocol_version(self) -> "AinaManifest":
        # A 2.0 package is registered from its aina.json; this record is only its host-side projection.
        expected = "2.0" if self.runtime.type == "package" else "1.0"
        if self.protocol_version != expected:
            raise ValueError(f"A {self.runtime.type} AINA runtime requires AINA Protocol {expected}")
        return self


def _validate_relative_project_path(value: str, *, label: str) -> None:
    if not value or "\\" in value or "\x00" in value:
        raise ValueError(f"{label} must be a non-empty POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"{label} must stay inside the AINA project")


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
    # ``consent_required``: an upgrade widened what the AINA can do; it is paused until the user approves again.
    # ``authorization_required``: its OAuth MCP server needs the user to connect their account (again).
    status: Literal["active", "disabled", "consent_required", "authorization_required"] = "active"
    installed_at: datetime = Field(default_factory=utc_now)
    # What the upgrade that paused the installation changed, shown when the user is asked again.
    consent_changes: list[str] = Field(default_factory=list)
    # Encrypted ``x-secret`` configuration values of an AINA 2.0 package; stored, never serialized to clients.
    secrets: dict[str, str] = Field(default_factory=dict, exclude=True)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def configured_secrets(self) -> list[str]:
        """Names of the secret settings that have a value (their values are never returned)."""
        return sorted(name for name in self.secrets if name != OAUTH_SECRET)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def oauth_connected(self) -> bool:
        """Whether the user connected their account of an OAuth MCP server."""
        return OAUTH_SECRET in self.secrets


class PermissionUpdate(StrictModel):
    user_id: str = "anonymous"
    tenant_id: str = "default"
    granted_permissions: list[str]


class OpenAinaRequest(StrictModel):
    user_id: str = "anonymous"
    tenant_id: str = "default"
    conversation_id: str | None = None
    workspace_id: str | None = None


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
