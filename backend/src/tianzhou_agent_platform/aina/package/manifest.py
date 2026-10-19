"""Typed view of an AINA Protocol 2.0 manifest (``aina.json``).

``aina.schema.json`` is the published contract and is checked first; these models give the host typed access to a
manifest that already passed it, so they mirror the schema without repeating every bound.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import ConfigDict, Field

from tianzhou_agent_platform.core.base import StrictModel

Effect = Literal["read", "write", "destructive"]
Visibility = Literal["model", "app"]
PermissionScope = Literal[
    "storage",
    "notify",
    "documents.read",
    "documents.write",
    "memory.read",
    "memory.write",
    "llm",
    "sandbox.exec",
]

EFFECT_ORDER: dict[Effect, int] = {"read": 0, "write": 1, "destructive": 2}


class PackagePublisher(StrictModel):
    id: str
    name: str
    url: str | None = None


class PackageLocale(StrictModel):
    name: str | None = None
    description: str | None = None


class PackageIdentity(StrictModel):
    id: str
    version: str
    name: str
    description: str
    publisher: PackagePublisher
    icon: str | None = None
    locales: dict[str, PackageLocale] = Field(default_factory=dict)


class McpAuth(StrictModel):
    type: Literal["none", "bearer", "api_key", "oauth2"]
    secret: str | None = None
    header: str | None = None
    # ``oauth2``: the scopes to ask for; by default those the server's protected resource metadata lists.
    scopes: list[str] | None = None


DEFAULT_SERVER_ID = "default"


class McpServer(StrictModel):
    # Required when ``tools.mcp`` lists several servers; a single server is ``default``.
    id: str = DEFAULT_SERVER_ID
    url: str
    auth: McpAuth


class ExposedTool(StrictModel):
    effect: Effect | None = None
    visibility: list[Visibility] = Field(default_factory=lambda: list[Visibility](["model", "app"]))
    confirm_widget: str | None = None
    result_widget: str | None = None
    long_running: bool = False
    timeout_seconds: float | None = None
    # The id of the server that provides the tool; required when the app has several servers.
    server: str | None = None


class PackageTools(StrictModel):
    mcp: McpServer | list[McpServer]
    expose: dict[str, ExposedTool]

    def servers(self) -> list[McpServer]:
        return self.mcp if isinstance(self.mcp, list) else [self.mcp]


class PackageSkill(StrictModel):
    id: str
    name: str
    description: str
    path: str
    tools: list[str] = Field(default_factory=list)


class CanvasTool(StrictModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str
    title: str | None = None
    description: str
    input_schema: dict[str, Any] = Field(alias="inputSchema")
    output_schema: dict[str, Any] | None = Field(default=None, alias="outputSchema")
    effect: Effect | None = None
    timeout_seconds: float | None = None


class PackageCanvas(StrictModel):
    title: str
    entry: str | None = None
    url: str | None = None
    tools: list[CanvasTool] = Field(default_factory=list)


class PackageWidget(StrictModel):
    id: str
    purpose: Literal["confirmation", "result", "display"]
    entry: str
    height: int | None = None


class PackageConfig(StrictModel):
    # ``schema`` would shadow a BaseModel attribute.
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    json_schema: dict[str, Any] = Field(alias="schema")

    def secret_fields(self) -> set[str]:
        properties = self.json_schema.get("properties")
        if not isinstance(properties, dict):
            return set()
        return {name for name, field in properties.items() if isinstance(field, dict) and field.get("x-secret") is True}


class PackagePermission(StrictModel):
    scope: PermissionScope
    reason: str | None = None
    optional: bool = False


class AinaPackageManifest(StrictModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_url: str | None = Field(default=None, alias="$schema")
    protocol_version: Literal["2.0"]
    aina: PackageIdentity
    tools: PackageTools | None = None
    skills: list[PackageSkill] = Field(default_factory=list)
    canvas: PackageCanvas | None = None
    widgets: list[PackageWidget] = Field(default_factory=list)
    config: PackageConfig | None = None
    permissions: list[PackagePermission] = Field(default_factory=list)

    def required_scopes(self) -> set[str]:
        return {item.scope for item in self.permissions if not item.optional}

    def secret_config_fields(self) -> set[str]:
        return self.config.secret_fields() if self.config is not None else set()

    def exposed(self) -> dict[str, ExposedTool]:
        """The exposed MCP tools by name (none for an app of only skills or a canvas)."""
        return self.tools.expose if self.tools is not None else {}

    def servers(self) -> list[McpServer]:
        return self.tools.servers() if self.tools is not None else []

    def server_for(self, tool_name: str) -> McpServer:
        """The server that provides an exposed tool (validation guarantees there is exactly one)."""
        servers = self.servers()
        wanted = self.exposed()[tool_name].server
        return next(server for server in servers if wanted is None or server.id == wanted)

    def oauth_server(self) -> McpServer | None:
        """The one server that uses OAuth, if any (validation allows at most one)."""
        return next((server for server in self.servers() if server.auth.type == "oauth2"), None)
