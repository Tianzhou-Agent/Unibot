"""What the host stores for a registered AINA Protocol 2.0 package (design §6.1, §6.3).

A 2.0 AINA is kept as an ordinary ``AinaRecord`` whose runtime is ``package``: identity, permissions and
capability summaries are projected into the 1.0 record shape so listing, installation and permission grants keep
working, while the runtime holds the validated ``aina.json``, the reviewed tool snapshot and the skill texts.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from tianzhou_agent_platform.aina.package.manifest import AinaPackageManifest, Effect, Visibility
from tianzhou_agent_platform.aina.package.oauth import OAuthServer
from tianzhou_agent_platform.core.base import StrictModel, utc_now

DEFAULT_TOOL_TIMEOUT_SECONDS = 30.0


class PackagedSkill(StrictModel):
    id: str
    entry: str
    # SKILL.md body with any frontmatter removed.
    body: str
    # Supporting files by path relative to the skill folder.
    files: dict[str, str] = Field(default_factory=dict)


class McpToolDescriptor(StrictModel):
    """One tool as an MCP server lists it."""

    name: str
    title: str | None = None
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    output_schema: dict[str, Any] | None = None
    annotations: dict[str, Any] = Field(default_factory=dict)


class ToolSnapshot(McpToolDescriptor):
    """An exposed MCP tool as reviewed at registration: the model is only ever shown this copy."""

    digest: str
    effect: Effect
    visibility: list[Visibility]
    timeout_seconds: float = DEFAULT_TOOL_TIMEOUT_SECONDS
    # ``drifted``/``missing`` tools are disabled for every installation until the publisher ships a new version.
    status: Literal["active", "drifted", "missing"] = "active"

    @property
    def model_visible(self) -> bool:
        return self.status == "active" and "model" in self.visibility


class PackageRuntimeDefinition(StrictModel):
    type: Literal["package"] = "package"
    package: AinaPackageManifest
    archive_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    tools: list[ToolSnapshot]
    skills: list[PackagedSkill] = Field(default_factory=list)
    server_name: str | None = None
    server_version: str | None = None
    tools_checked_at: datetime = Field(default_factory=utc_now)
    # A first-party package shipped with Unibot (design §11): served in process and available to every user.
    bundled: bool = False
    # ``oauth2`` MCP servers: the authorization server and the client Unibot registered there (kept across upgrades).
    oauth: OAuthServer | None = None

    def tool(self, name: str) -> ToolSnapshot | None:
        return next((tool for tool in self.tools if tool.name == name), None)

    def skill(self, skill_id: str) -> PackagedSkill | None:
        return next((skill for skill in self.skills if skill.id == skill_id), None)
