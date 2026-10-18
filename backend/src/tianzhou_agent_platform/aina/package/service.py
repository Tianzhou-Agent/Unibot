"""Plugin Host for AINA Protocol 2.0 packages: registration, tool snapshot and drift check, tool calls and skills.

Design §6.1: tools are discovered once, when a package version is registered. The host stores a snapshot of every
exposed tool and only ever shows the model that snapshot; a later ``tools/list`` that disagrees with it disables
the tool until the publisher ships a new version.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any, Protocol, cast

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import SchemaError  # type: ignore[import-untyped]

from tianzhou_agent_platform.aina.package.configuration import mcp_auth_headers, prepare_configuration
from tianzhou_agent_platform.aina.package.context import CONTEXT_META_KEY, Source, unibot_context
from tianzhou_agent_platform.aina.package.manifest import EFFECT_ORDER, AinaPackageManifest, Effect
from tianzhou_agent_platform.aina.package.mcp_client import McpServerInfo, McpToolClient
from tianzhou_agent_platform.aina.package.secrets import SecretBox, aina_key_material
from tianzhou_agent_platform.aina.package.tasks import TaskStart, parse_task_start
from tianzhou_agent_platform.aina.package.tokens import ContextTokenSigner, HostReference
from tianzhou_agent_platform.aina.package.models import (
    DEFAULT_TOOL_TIMEOUT_SECONDS,
    McpToolDescriptor,
    PackageRuntimeDefinition,
    ToolSnapshot,
)
from tianzhou_agent_platform.aina.package.validation import (
    check_mcp_tool_description,
    require_valid_package,
    validate_package_archive,
)
from tianzhou_agent_platform.aina.protocol.models import (
    AinaCapabilities,
    AinaCapability,
    AinaIdentity,
    AinaInstallation,
    AinaManifest,
    AinaRecord,
    InstallationRequest,
    Publisher,
)
from tianzhou_agent_platform.aina.security.destinations import require_approved_destination
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.core.base import utc_now
from tianzhou_agent_platform.core.errors import PlatformError, conflict

_MAX_ERROR_TEXT_CHARS = 2_000
# Design §10: low-risk scopes are shown at install and granted without asking.
AUTO_GRANTED_SCOPES = frozenset({"storage", "notify"})


class PackageRegistry(Protocol):
    async def get_aina(self, aina_id: str) -> AinaRecord: ...

    async def upsert_aina(self, aina: AinaRecord) -> AinaRecord: ...

    async def get_installation(self, *, tenant_id: str, user_id: str, aina_id: str) -> AinaInstallation: ...

    async def put_installation(self, installation: AinaInstallation) -> AinaInstallation: ...


class AinaPackageService:
    def __init__(
        self,
        *,
        repository: PackageRegistry,
        settings: AgentSettings,
        mcp: McpToolClient | None = None,
        enforce_destination_policy: bool = False,
    ) -> None:
        self.repository = repository
        self.settings = settings
        self.mcp = mcp or McpToolClient()
        self.enforce_destination_policy = enforce_destination_policy
        self.box = SecretBox.from_settings(settings)
        self.tokens = ContextTokenSigner.from_settings(settings)

    async def register(self, payload: bytes, *, credential: str | None = None) -> AinaRecord:
        """Validate a package, snapshot its exposed MCP tools and register (or upgrade) the AINA.

        ``credential`` authenticates the registration-time ``tools/list`` of a ``bearer``/``api_key`` server; it
        is used once and never stored.
        """
        report = validate_package_archive(payload)
        manifest = require_valid_package(report)
        if manifest.tools.mcp.auth.type == "oauth2":
            raise PlatformError(
                "INVALID_REQUEST",
                "MCP OAuth 2.0 authorization is not supported yet; use auth type none, bearer or api_key",
                status_code=422,
                source="aina_package",
            )
        self._require_destination(manifest.tools.mcp.url, self.settings.capability_allowed_origins)
        if manifest.canvas is not None and manifest.canvas.url is not None:
            self._require_destination(manifest.canvas.url, self.settings.aina_canvas_allowed_origins)
        existing = await self._existing(manifest.aina.id)
        if existing is not None:
            runtime = existing.manifest.runtime
            if not isinstance(runtime, PackageRuntimeDefinition):
                raise conflict(f"AINA {manifest.aina.id!r} is already registered with another runtime")
            if existing.manifest.aina.version == manifest.aina.version:
                if runtime.archive_sha256 == report.archive_sha256:
                    return existing
                raise conflict(
                    f"AINA {manifest.aina.id!r} version {manifest.aina.version!r} was already registered with "
                    "different content; publish a new version"
                )
        info = await self._list_tools(manifest, self._discovery_headers(manifest, credential=credential))
        snapshots, errors, warnings = snapshot_tools(manifest, info.tools)
        if errors:
            raise PlatformError(
                "INVALID_REQUEST",
                "Invalid AINA package: " + "; ".join(errors),
                status_code=422,
                source="aina_package",
                debug={"errors": errors},
            )
        runtime = PackageRuntimeDefinition(
            package=manifest,
            archive_sha256=cast(str, report.archive_sha256),
            tools=snapshots,
            skills=report.skills,
            server_name=info.name,
            server_version=info.version,
        )
        record = AinaRecord(
            manifest=project_manifest(manifest, runtime),
            last_health={
                "status": "healthy",
                "protocol": "mcp",
                "checked_at": runtime.tools_checked_at.isoformat(),
                "warnings": [*report.warnings, *warnings],
            },
        )
        return await self.repository.upsert_aina(record)

    async def install(self, aina_id: str, request: InstallationRequest) -> AinaInstallation:
        """Install a package for one user: configuration, sealed secrets, scopes and a fresh tool check."""
        record = await self.repository.get_aina(aina_id)
        manifest = package_runtime(record).package
        declared = {item.scope for item in manifest.permissions}
        undeclared = set(request.granted_permissions) - declared
        if undeclared:
            raise PlatformError(
                "INVALID_REQUEST",
                f"Cannot grant undeclared permissions: {', '.join(sorted(undeclared))}",
                status_code=422,
            )
        previous = await self._installation(aina_id, tenant_id=request.tenant_id, user_id=request.user_id)
        public, sealed = prepare_configuration(
            manifest,
            request.configuration,
            box=self.box,
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            previous=previous,
        )
        installation = AinaInstallation(
            user_id=request.user_id,
            tenant_id=request.tenant_id,
            granted_permissions=sorted(set(request.granted_permissions) | (declared & AUTO_GRANTED_SCOPES)),
            configuration=public,
            secrets=sealed,
            aina_id=aina_id,
            installed_version=record.manifest.aina.version,
        )
        # Design §6.1: the tool snapshot is re-checked before every new installation, as this user.
        await self.refresh_tools(aina_id, installation=installation)
        return await self.repository.put_installation(installation)

    async def refresh_tools(
        self,
        aina_id: str,
        *,
        installation: AinaInstallation | None = None,
        credential: str | None = None,
    ) -> AinaRecord:
        """Re-run ``tools/list`` and disable every exposed tool that disappeared or changed since review."""
        record = await self.repository.get_aina(aina_id)
        runtime = package_runtime(record)
        headers = (
            mcp_auth_headers(runtime.package.tools.mcp.auth, installation, box=self.box)
            if installation is not None
            else self._discovery_headers(runtime.package, credential=credential)
        )
        info = await self._list_tools(runtime.package, headers)
        listed = {tool.name: tool for tool in info.tools}
        tools: list[ToolSnapshot] = []
        for snapshot in runtime.tools:
            status = snapshot.status
            if status == "active":
                current = listed.get(snapshot.name)
                if current is None:
                    status = "missing"
                elif tool_digest(current) != snapshot.digest:
                    status = "drifted"
            tools.append(snapshot.model_copy(update={"status": status}))
        checked_at = utc_now()
        updated_runtime = runtime.model_copy(update={"tools": tools, "tools_checked_at": checked_at})
        disabled = sorted(tool.name for tool in tools if tool.status != "active")
        updated = record.model_copy(
            update={
                "manifest": project_manifest(runtime.package, updated_runtime),
                "last_health": {
                    **record.last_health,
                    "status": "degraded" if disabled else "healthy",
                    "checked_at": checked_at.isoformat(),
                    "disabled_tools": disabled,
                },
            }
        )
        return await self.repository.upsert_aina(updated)

    async def call_tool(
        self,
        record: AinaRecord,
        installation: AinaInstallation,
        name: str,
        arguments: dict[str, Any],
        *,
        invocation_id: str,
        conversation_id: str | None,
        workspace_id: str | None,
        source: Source = "agent",
    ) -> tuple[dict[str, Any], TaskStart | None]:
        """Call one exposed MCP tool: what the model sees (design §7.1), and the background task a
        ``long_running`` tool started (design §7.3), if any."""
        runtime = package_runtime(record)
        snapshot = runtime.tool(name)
        if snapshot is None or snapshot.status != "active":
            raise PlatformError(
                "PERMISSION_DENIED",
                f"Tool {name!r} of AINA {record.manifest.aina.id!r} is disabled because it changed after review",
                status_code=403,
                source="aina_package",
            )
        validate_arguments(snapshot, arguments)
        manifest = runtime.package
        self._require_destination(manifest.tools.mcp.url, self.settings.capability_allowed_origins)
        headers = mcp_auth_headers(manifest.tools.mcp.auth, installation, box=self.box)
        context = unibot_context(
            secret=aina_key_material(self.settings),
            manifest=manifest,
            installation=installation,
            invocation_id=invocation_id,
            source=source,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
        )
        context["host"] = {"api_base": self.settings.aina_host_api_base}
        context["token"] = self.mint_token(installation, context)
        result = await self.mcp.call_tool(
            manifest.tools.mcp.url,
            name,
            arguments,
            meta={"progressToken": invocation_id, CONTEXT_META_KEY: context},
            headers=headers,
            timeout=snapshot.timeout_seconds,
        )
        text = content_text(result.content)
        if result.is_error:
            raise PlatformError(
                "TOOL_ERROR",
                f"Tool {name!r} reported an error: {text[:_MAX_ERROR_TEXT_CHARS] or 'no details'}",
                status_code=502,
                source="mcp",
            )
        visible: dict[str, Any] = {}
        if result.structured_content is not None:
            visible["structuredContent"] = result.structured_content
        if text and not _same_json(text, result.structured_content):
            visible["content"] = text
        exposed = manifest.tools.expose.get(name)
        task = parse_task_start(result.meta) if exposed is not None and exposed.long_running else None
        return visible or {"content": ""}, task

    def mint_token(self, installation: AinaInstallation, context: dict[str, Any]) -> str:
        """Context token carrying the same pairwise identity as ``context`` (design §8)."""
        claims = {
            "sub": context["user"]["id"],
            "tenant": context["tenant_id"],
            "installation_id": context["installation_id"],
            "scopes": sorted(installation.granted_permissions),
            "invocation_id": context["invocation_id"],
        }
        for claim, key in (("conversation", "conversation_id"), ("workspace", "workspace_id")):
            if key in context:
                claims[claim] = context[key]
        reference = HostReference(
            tenant_id=installation.tenant_id,
            user_id=installation.user_id,
            aina_id=installation.aina_id,
        )
        return self.tokens.mint(reference=reference, claims=claims)

    async def _installation(self, aina_id: str, *, tenant_id: str, user_id: str) -> AinaInstallation | None:
        try:
            return await self.repository.get_installation(tenant_id=tenant_id, user_id=user_id, aina_id=aina_id)
        except PlatformError as exc:
            if exc.code == "RESOURCE_NOT_FOUND":
                return None
            raise

    def _discovery_headers(self, manifest: AinaPackageManifest, *, credential: str | None) -> dict[str, str]:
        if manifest.tools.mcp.auth.type == "none" or credential is None:
            return {}
        return mcp_auth_headers(manifest.tools.mcp.auth, None, box=self.box, credential=credential)

    async def _existing(self, aina_id: str) -> AinaRecord | None:
        try:
            return await self.repository.get_aina(aina_id)
        except PlatformError as exc:
            if exc.code == "RESOURCE_NOT_FOUND":
                return None
            raise

    async def _list_tools(self, manifest: AinaPackageManifest, headers: dict[str, str]) -> McpServerInfo:
        self._require_destination(manifest.tools.mcp.url, self.settings.capability_allowed_origins)
        return await self.mcp.list_tools(
            manifest.tools.mcp.url,
            headers=headers,
            timeout=self.settings.capability_timeout_seconds,
        )

    def _require_destination(self, url: str, allowed_origins: str) -> None:
        if self.enforce_destination_policy:
            require_approved_destination(url, allowed_origins)


def package_runtime(record: AinaRecord) -> PackageRuntimeDefinition:
    runtime = record.manifest.runtime
    if not isinstance(runtime, PackageRuntimeDefinition):
        raise PlatformError(
            "INVALID_REQUEST",
            f"AINA {record.manifest.aina.id!r} is not an AINA Protocol 2.0 package",
            status_code=422,
        )
    return runtime


def load_skill(record: AinaRecord, skill_id: str, file: str | None = None) -> str:
    """Design §6.3: a skill body (or one supporting file), delimited as third-party guidance."""
    runtime = package_runtime(record)
    aina_id = record.manifest.aina.id
    skill = runtime.skill(skill_id)
    if skill is None:
        known = ", ".join(item.id for item in runtime.skills) or "none"
        raise PlatformError(
            "RESOURCE_NOT_FOUND",
            f"AINA {aina_id!r} has no skill {skill_id!r}; available skills: {known}",
            status_code=404,
            source="aina_package",
        )
    if file is None:
        text = skill.body
        listing = "".join(f"\n- {path}" for path in sorted(skill.files))
        footer = f"\n\nSupporting files (load with file=<path>):{listing}" if listing else ""
        attributes = f'aina="{aina_id}" id="{skill.id}"'
    else:
        path = file.strip().removeprefix("./")
        loaded = skill.files.get(path)
        if loaded is None:
            known = ", ".join(sorted(skill.files)) or "none"
            raise PlatformError(
                "RESOURCE_NOT_FOUND",
                f"Skill {skill_id!r} has no supporting file {file!r}; available files: {known}",
                status_code=404,
                source="aina_package",
            )
        text = loaded
        footer = ""
        attributes = f'aina="{aina_id}" id="{skill.id}" file="{path}"'
    # The closing tag cannot be forged from inside the skill text.
    safe = text.replace("</aina-skill", "<\\/aina-skill")
    return f"<aina-skill {attributes}>\n{safe}{footer}\n</aina-skill>"


def snapshot_tools(
    manifest: AinaPackageManifest,
    listed: Iterable[McpToolDescriptor],
) -> tuple[list[ToolSnapshot], list[str], list[str]]:
    """(snapshots, errors, warnings) of the exposed tools found in a ``tools/list`` result."""
    by_name = {tool.name: tool for tool in listed}
    snapshots: list[ToolSnapshot] = []
    errors: list[str] = []
    warnings: list[str] = []
    for name, exposed in manifest.tools.expose.items():
        tool = by_name.get(name)
        if tool is None:
            errors.append(f"tools.expose.{name}: the MCP server does not list a tool named {name!r}")
            continue
        description = tool.description or tool.title or ""
        if not description:
            errors.append(f"MCP tool {name!r}: the server gives it no description")
        description_errors, description_warnings = check_mcp_tool_description(name, description)
        errors.extend(description_errors)
        warnings.extend(description_warnings)
        try:
            Draft202012Validator.check_schema(tool.input_schema)
        except SchemaError as exc:
            errors.append(f"MCP tool {name!r}: inputSchema is not a valid JSON Schema ({exc.message})")
        snapshots.append(
            ToolSnapshot(
                **tool.model_dump(),
                digest=tool_digest(tool),
                effect=resolve_effect(exposed.effect, tool.annotations),
                visibility=exposed.visibility,
                timeout_seconds=exposed.timeout_seconds or DEFAULT_TOOL_TIMEOUT_SECONDS,
            )
        )
    return snapshots, errors, warnings


def tool_digest(tool: McpToolDescriptor) -> str:
    """Digest of everything the model is told about a tool; any change to it is drift."""
    reviewed = {
        "title": tool.title,
        "description": tool.description,
        "input_schema": tool.input_schema,
        "output_schema": tool.output_schema,
        "annotations": tool.annotations,
    }
    canonical = json.dumps(reviewed, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def resolve_effect(declared: Effect | None, annotations: dict[str, Any]) -> Effect:
    """Design §6.1: the stricter of the manifest effect and the effect the MCP annotations imply."""
    implied: Effect | None = None
    if annotations.get("destructiveHint") is True:
        implied = "destructive"
    elif annotations.get("readOnlyHint") is True:
        implied = "read"
    elif annotations.get("readOnlyHint") is False:
        implied = "write"
    candidates = [effect for effect in (declared, implied) if effect is not None]
    if not candidates:
        return "write"
    return max(candidates, key=lambda effect: EFFECT_ORDER[effect])


def validate_arguments(snapshot: ToolSnapshot, arguments: dict[str, Any]) -> None:
    error = next(iter(sorted(Draft202012Validator(snapshot.input_schema).iter_errors(arguments), key=str)), None)
    if error is not None:
        location = "/".join(str(part) for part in error.absolute_path) or "arguments"
        raise PlatformError(
            "INVALID_REQUEST",
            f"Tool {snapshot.name} {location}: {error.message}",
            status_code=400,
            source="tool",
            user_message="Invalid arguments; fix them and retry.",
        )


def project_manifest(manifest: AinaPackageManifest, runtime: PackageRuntimeDefinition) -> AinaManifest:
    """The 1.0-shaped registry record of a package, so listing, install and grants keep one code path."""
    identity = manifest.aina
    return AinaManifest(
        protocol_version="2.0",
        aina=AinaIdentity(
            id=identity.id,
            name=identity.name,
            version=identity.version,
            description=identity.description,
            publisher=Publisher(id=identity.publisher.id, name=identity.publisher.name),
        ),
        runtime=runtime,
        capabilities=AinaCapabilities(
            skills=[
                AinaCapability(id=skill.id, name=skill.name, description=skill.description)
                for skill in manifest.skills
            ],
            tools=[
                AinaCapability(
                    id=tool.name,
                    name=tool.title or tool.name,
                    description=tool.description,
                    input_schema=tool.input_schema,
                )
                for tool in runtime.tools
                if tool.model_visible
            ],
        ),
        permissions=[item.scope for item in manifest.permissions],
    )


def content_text(content: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in content:
        if item.get("type") == "text":
            parts.append(str(item.get("text") or ""))
        else:
            # Images, audio and resources are not forwarded to the model yet; it is told they exist.
            parts.append(f"[{item.get('type', 'content')} omitted: {item.get('mimeType') or item.get('uri') or ''}]")
    return "\n".join(part for part in parts if part)


def _same_json(text: str, value: Any) -> bool:
    if value is None:
        return False
    try:
        return bool(json.loads(text) == value)
    except ValueError:
        return False
