"""Plugin Host for AINA Protocol 2.0 packages: registration, tool snapshot and drift check, tool calls and skills.

Design §6.1: tools are discovered once, when a package version is registered. The host stores a snapshot of every
exposed tool and only ever shows the model that snapshot; a later ``tools/list`` that disagrees with it disables
the tool until the publisher ships a new version.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, cast
from uuid import uuid4

import httpx
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import SchemaError  # type: ignore[import-untyped]

from tianzhou_agent_platform.aina.package.configuration import mcp_auth_headers, prepare_configuration, secret_context
from tianzhou_agent_platform.aina.package.context import CONTEXT_META_KEY, Source, unibot_context
from tianzhou_agent_platform.aina.package.manifest import EFFECT_ORDER, AinaPackageManifest, Effect, McpServer
from tianzhou_agent_platform.aina.package.bundled import BUNDLED_HOST, effective_installation, is_bundled_url
from tianzhou_agent_platform.aina.package.mcp_client import McpServerInfo, McpToolClient
from tianzhou_agent_platform.aina.package.oauth import OAUTH_SECRET, McpOAuth, OAuthServer
from tianzhou_agent_platform.aina.package.secrets import SecretBox, aina_key_material
from tianzhou_agent_platform.aina.package.tasks import TaskStart, parse_task_start
from tianzhou_agent_platform.aina.package.tokens import ContextTokenSigner, HostReference
from tianzhou_agent_platform.aina.package.ui import PackageUiStore
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
from tianzhou_agent_platform.aina.protocol.widgets import WidgetAinaUi, WidgetCanvas, WidgetDefinition
from tianzhou_agent_platform.aina.package.widgets import aina_widget, result_widget_id
from tianzhou_agent_platform.aina.security.destinations import require_approved_destination
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.conversations.attachments import Attachment, passed_refs
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

    async def list_installations(
        self, *, tenant_id: str | None = None, user_id: str | None = None
    ) -> list[AinaInstallation]: ...

    async def list_ainas(self) -> list[AinaRecord]: ...

    async def list_chat_attachments(self, file_refs: list[str]) -> list[Attachment]: ...


class AinaPackageService:
    def __init__(
        self,
        *,
        repository: PackageRegistry,
        settings: AgentSettings,
        mcp: McpToolClient | None = None,
        ui: PackageUiStore | None = None,
        enforce_destination_policy: bool = False,
        oauth_http: httpx.AsyncClient | None = None,
    ) -> None:
        self.repository = repository
        self.settings = settings
        self.mcp = mcp or McpToolClient()
        self.ui = ui
        self.enforce_destination_policy = enforce_destination_policy
        self.box = SecretBox.from_settings(settings)
        self.tokens = ContextTokenSigner.from_settings(settings)
        self.oauth = McpOAuth(
            box=self.box,
            redirect_uri=settings.aina_oauth_callback_url,
            require_destination=lambda url: self._require_destination(url, settings.capability_allowed_origins),
            http=oauth_http,
        )

    async def register(
        self, payload: bytes, *, credential: str | None = None, bundled: bool = False, replace: bool = False
    ) -> AinaRecord:
        """Validate a package, snapshot its exposed MCP tools and register (or upgrade) the AINA.

        ``credential`` authenticates the registration-time ``tools/list`` of a ``bearer``/``api_key`` server, or is
        an access token for an ``oauth2`` one; it is used once and never stored. ``bundled`` registers a first-party package shipped with Superbot (design §11),
        whose content may change without a version bump between releases. ``replace`` (``aina dev``) re-registers the
        same version with new content and a fresh tool snapshot.
        """
        report = validate_package_archive(payload)
        manifest = require_valid_package(report)
        servers = manifest.servers()
        if any(is_bundled_url(server.url) != bundled for server in servers):
            raise PlatformError(
                "INVALID_REQUEST",
                f"Only packages bundled with Superbot serve their MCP tools at {BUNDLED_HOST}",
                status_code=422,
                source="aina_package",
            )
        for server in servers:
            self._require_destination(server.url, self.settings.capability_allowed_origins)
        if manifest.canvas is not None and manifest.canvas.url is not None:
            self._require_destination(manifest.canvas.url, self.settings.aina_canvas_allowed_origins)
        existing = await self._existing(manifest.aina.id)
        if existing is not None:
            runtime = existing.manifest.runtime
            if not isinstance(runtime, PackageRuntimeDefinition) or runtime.bundled != bundled:
                raise conflict(f"AINA {manifest.aina.id!r} is already registered with another runtime")
            if existing.manifest.aina.version == manifest.aina.version and not (bundled or replace):
                if runtime.archive_sha256 == report.archive_sha256:
                    return existing
                # Its own code, so the Add app page can point at the version field.
                raise PlatformError(
                    code="VERSION_EXISTS",
                    message=(
                        f"AINA {manifest.aina.id!r} version {manifest.aina.version!r} was already registered with "
                        "different content; publish a new version"
                    ),
                    status_code=409,
                )
        try:
            listed = {
                server.id: await self._list_tools(server, self._discovery_headers(server, credential=credential))
                for server in servers
            }
        except PlatformError as exc:
            if exc.code != "AUTHENTICATION_FAILED" or manifest.oauth_server() is None or credential:
                raise
            raise PlatformError(
                "AUTHENTICATION_FAILED",
                "This OAuth MCP server lists its tools only to an authorized client: register it with an access token "
                "for the server as the one-time credential (aina publish --credential, or the upload dialog)",
                status_code=422,
                source="aina_package",
            ) from exc
        snapshots, errors, warnings = snapshot_tools(manifest, {key: info.tools for key, info in listed.items()})
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
            server_name=", ".join(info.name for info in listed.values() if info.name) or None,
            server_version=", ".join(info.version for info in listed.values() if info.version) or None,
            bundled=bundled,
            oauth=_kept_oauth(existing, manifest),
        )
        if existing is not None and bundled and _same_package(existing, runtime):
            return existing
        if self.ui is not None:
            # Canvas, widget and asset files are served from the stored archive (design §9.1).
            await self.ui.save(manifest.aina.id, runtime.archive_sha256, payload)
        previous = existing.manifest.runtime if existing is not None else None
        record = AinaRecord(
            manifest=project_manifest(manifest, runtime),
            last_health={
                "status": "healthy",
                "protocol": "mcp",
                "checked_at": runtime.tools_checked_at.isoformat(),
                "warnings": [*report.warnings, *warnings],
            },
        )
        registered = await self.repository.upsert_aina(record)
        if isinstance(previous, PackageRuntimeDefinition):
            await self._upgrade_installations(previous, runtime)
        return registered

    async def _upgrade_installations(self, previous: PackageRuntimeDefinition, current: PackageRuntimeDefinition) -> None:
        """Design §10: an upgrade that widens what the AINA can do pauses installations until the user re-consents.

        Other installations move to the new version silently.
        """
        widened = consent_changes(previous, current)
        required = current.package.required_scopes()
        aina_id = current.package.aina.id
        for installation in await self.repository.list_installations():
            if installation.aina_id != aina_id or installation.status == "disabled":
                continue
            missing = sorted(required - set(installation.granted_permissions))
            changes = [*widened, *(f"Needs the {scope!r} permission" for scope in missing)]
            if changes:
                update: dict[str, Any] = {
                    "status": "consent_required",
                    "consent_changes": list(dict.fromkeys([*installation.consent_changes, *changes])),
                }
            elif installation.status == "active":
                update = {"installed_version": current.package.aina.version}
            else:
                continue  # still waiting for the user's approval of an earlier upgrade
            await self.repository.put_installation(installation.model_copy(update=update))

    async def install(self, aina_id: str, request: InstallationRequest) -> AinaInstallation:
        """Install a package for one user: configuration, sealed secrets, scopes and a fresh tool check."""
        record = await self.repository.get_aina(aina_id)
        if package_runtime(record).bundled:
            raise PlatformError("CONFLICT", "The system AINA is always available", status_code=409)
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
        oauth = manifest.oauth_server() is not None
        if oauth and previous is not None and OAUTH_SECRET in previous.secrets:
            sealed[OAUTH_SECRET] = previous.secrets[OAUTH_SECRET]  # the user's connection outlives a re-install
        installation = AinaInstallation(
            user_id=request.user_id,
            tenant_id=request.tenant_id,
            granted_permissions=sorted(set(request.granted_permissions) | (declared & AUTO_GRANTED_SCOPES)),
            configuration=public,
            secrets=sealed,
            aina_id=aina_id,
            installed_version=record.manifest.aina.version,
            # An OAuth server's tools are listed as the user, so they connect their account first.
            status="authorization_required" if oauth and OAUTH_SECRET not in sealed else "active",
        )
        if installation.status == "active":
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
        listed: dict[str, dict[str, McpToolDescriptor]] = {}
        for server in runtime.package.servers():
            headers = (
                await self._auth_headers(record, installation, server)
                if installation is not None
                else self._discovery_headers(server, credential=credential)
            )
            info = await self._list_tools(server, headers)
            listed[server.id] = {tool.name: tool for tool in info.tools}
        tools: list[ToolSnapshot] = []
        for snapshot in runtime.tools:
            status = snapshot.status
            if status == "active":
                current = listed[runtime.package.server_for(snapshot.name).id].get(snapshot.name)
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

    async def check_all_tools(self) -> dict[str, str]:
        """Design §6.1: re-check every package's tools on a schedule, as one of its active installations.

        A server that needs per-user credentials is listed with that installation's; one that cannot be reached is
        left as it is and reported. Returns each AINA's outcome.
        """
        outcomes: dict[str, str] = {}
        installations = await self.repository.list_installations()
        for record in await self.repository.list_ainas():
            runtime = record.manifest.runtime
            if record.status != "registered" or not isinstance(runtime, PackageRuntimeDefinition) or runtime.bundled:
                continue
            aina_id = record.manifest.aina.id
            installation = next(
                (item for item in installations if item.aina_id == aina_id and item.status == "active"), None
            )
            if not runtime.package.servers():
                continue  # an app of only skills or a canvas has no tools to check
            if installation is None and any(server.auth.type != "none" for server in runtime.package.servers()):
                outcomes[aina_id] = "skipped: no active installation to list its tools with"
                continue
            try:
                checked = await self.refresh_tools(aina_id, installation=installation)
            except PlatformError as exc:
                outcomes[aina_id] = f"unreachable: {exc.message}"
                continue
            disabled = checked.last_health.get("disabled_tools") or []
            outcomes[aina_id] = f"disabled: {', '.join(disabled)}" if disabled else "ok"
        return outcomes

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
    ) -> ToolOutcome:
        """Call one exposed MCP tool: what the model sees (design §7.1), the background task a ``long_running``
        tool started (design §7.3) and the result widget to show (design §9.2), if any."""
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
        server = manifest.server_for(name)
        self._require_destination(server.url, self.settings.capability_allowed_origins)
        headers = await self._auth_headers(record, installation, server)
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
        context["token"] = self.mint_token(
            installation,
            context,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            files=await self._passed_files(arguments, installation, conversation_id),
        )
        meta = {"progressToken": invocation_id, CONTEXT_META_KEY: context}
        url, timeout = server.url, snapshot.timeout_seconds
        try:
            result = await self.mcp.call_tool(url, name, arguments, meta=meta, headers=headers, timeout=timeout)
        except PlatformError as exc:
            if exc.code != "AUTHENTICATION_FAILED" or server.auth.type != "oauth2":
                raise
            # The server rejected the user's token: refresh it once (a failed refresh asks the user to reconnect).
            headers = await self._auth_headers(record, installation, server, force_refresh=True)
            result = await self.mcp.call_tool(url, name, arguments, meta=meta, headers=headers, timeout=timeout)
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
        exposed = manifest.exposed().get(name)
        task = parse_task_start(result.meta) if exposed is not None and exposed.long_running else None
        widget_id = result_widget_id(manifest, name, result.meta)
        widget = (
            aina_widget(
                record,
                widget_id,
                tool_name=name,
                call_id=invocation_id,
                arguments=arguments,
                content=text,
                structured_content=result.structured_content,
            )
            if widget_id is not None and task is None
            else None
        )
        return ToolOutcome(visible=visible or {"content": ""}, task=task, widget=widget, images=tool_images(result.content))

    async def call_app_tool(
        self,
        aina_id: str,
        name: str,
        arguments: dict[str, Any],
        *,
        tenant_id: str,
        user_id: str,
        source: Source,
        confirmed: bool,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        """A tool call from the AINA's own canvas or widget, made by the host shell for the signed-in user.

        Only ``app``-visible tools are callable (design §6.1). Anything but a ``read`` tool needs the host's own
        confirmation, which the shell asks for outside the iframe; the AINA's UI can never confirm for the user.
        """
        record, installation = await self._usable_installation(aina_id, tenant_id=tenant_id, user_id=user_id)
        runtime = package_runtime(record)
        snapshot = runtime.tool(name)
        exposed = runtime.package.exposed().get(name)
        if snapshot is None or exposed is None or "app" not in snapshot.visibility:
            raise PlatformError(
                "PERMISSION_DENIED",
                f"Tool {name!r} cannot be called from the AINA's UI",
                status_code=403,
                source="aina_package",
            )
        if exposed.long_running:
            raise PlatformError(
                "CONFLICT",
                f"Long-running tool {name!r} can only be started from the conversation",
                status_code=409,
                source="aina_package",
            )
        if snapshot.effect != "read" and not confirmed:
            raise PlatformError(
                "CONFIRMATION_REQUIRED",
                f"Tool {name!r} changes data and needs the user's confirmation",
                status_code=428,
                source="aina_package",
            )
        outcome = await self.call_tool(
            record,
            installation,
            name,
            arguments,
            invocation_id=f"app_{uuid4().hex}",
            conversation_id=conversation_id,
            workspace_id=None,
            source=source,
        )
        return outcome.visible

    async def identity_token(self, aina_id: str, *, tenant_id: str, user_id: str) -> str:
        """Design §9.1: a short-lived pairwise identity for a URL canvas's own backend; it carries no Host API scope."""
        record, installation = await self._usable_installation(aina_id, tenant_id=tenant_id, user_id=user_id)
        canvas = package_runtime(record).package.canvas
        if canvas is None or canvas.url is None:
            raise PlatformError(
                "PERMISSION_DENIED",
                "Only a URL canvas can request an identity token",
                status_code=403,
                source="aina_package",
            )
        context = unibot_context(
            secret=aina_key_material(self.settings),
            manifest=package_runtime(record).package,
            installation=installation,
            invocation_id="identity",
            source="canvas",
            conversation_id=None,
            workspace_id=None,
        )
        claims = {
            "use": "identity",
            "sub": context["user"]["id"],
            "tenant": context["tenant_id"],
            "installation_id": context["installation_id"],
            "scopes": [],
        }
        reference = HostReference(tenant_id=tenant_id, user_id=user_id, aina_id=aina_id)
        return self.tokens.mint(reference=reference, claims=claims)

    async def _usable_installation(
        self, aina_id: str, *, tenant_id: str, user_id: str
    ) -> tuple[AinaRecord, AinaInstallation]:
        record = await self.repository.get_aina(aina_id)
        installation = await effective_installation(self.repository, record, tenant_id=tenant_id, user_id=user_id)
        runtime = package_runtime(record)
        if (
            installation is None
            or installation.status != "active"
            or runtime.package.required_scopes() - set(installation.granted_permissions)
        ):
            raise PlatformError(
                "PERMISSION_DENIED",
                f"AINA {aina_id!r} is not installed and authorized for this user",
                status_code=403,
                source="aina_package",
            )
        return record, installation

    def mint_token(
        self,
        installation: AinaInstallation,
        context: dict[str, Any],
        *,
        conversation_id: str | None = None,
        workspace_id: str | None = None,
        files: list[str] | None = None,
    ) -> str:
        """Context token carrying the same pairwise identity as ``context`` (design §8).

        The raw conversation and workspace ids travel only inside the encrypted host reference. ``files`` are the
        attachments this call was passed, which the token may read with ``GET /files/{file_ref}``.
        """
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
        if files:
            claims["files"] = files
        reference = HostReference(
            tenant_id=installation.tenant_id,
            user_id=installation.user_id,
            aina_id=installation.aina_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
        )
        return self.tokens.mint(reference=reference, claims=claims)

    async def _passed_files(
        self, arguments: dict[str, Any], installation: AinaInstallation, conversation_id: str | None
    ) -> list[str]:
        """The user's attachments in this conversation whose file_ref appears in the call's arguments."""
        refs = passed_refs(arguments)
        if not refs or conversation_id is None:
            return []
        owner = (conversation_id, installation.tenant_id, installation.user_id)
        found = await self.repository.list_chat_attachments(sorted(refs))
        return sorted(item.id for item in found if (item.conversation_id, item.tenant_id, item.user_id) == owner)

    async def _installation(self, aina_id: str, *, tenant_id: str, user_id: str) -> AinaInstallation | None:
        try:
            return await self.repository.get_installation(tenant_id=tenant_id, user_id=user_id, aina_id=aina_id)
        except PlatformError as exc:
            if exc.code == "RESOURCE_NOT_FOUND":
                return None
            raise

    def _discovery_headers(self, server: McpServer, *, credential: str | None) -> dict[str, str]:
        if server.auth.type == "none" or credential is None:
            return {}
        if server.auth.type == "oauth2":
            return {"Authorization": f"Bearer {credential}"}  # an access token the admin got from the server
        return mcp_auth_headers(server.auth, None, box=self.box, credential=credential)

    # --- OAuth MCP servers (oauth.py) -------------------------------------------------------------------------

    async def _auth_headers(
        self, record: AinaRecord, installation: AinaInstallation, server: McpServer, *, force_refresh: bool = False
    ) -> dict[str, str]:
        if server.auth.type != "oauth2":
            return mcp_auth_headers(server.auth, installation, box=self.box)
        return {"Authorization": f"Bearer {await self._access_token(record, installation, force_refresh=force_refresh)}"}

    async def _access_token(self, record: AinaRecord, installation: AinaInstallation, *, force_refresh: bool) -> str:
        server = package_runtime(record).oauth
        sealed = installation.secrets.get(OAUTH_SECRET)
        if server is None or sealed is None:
            raise self._needs_connection(record)
        context = _installation_context(installation)
        tokens = self.oauth.open_tokens(sealed, context=context)
        if not (force_refresh or tokens.expiring):
            return tokens.access_token
        try:
            tokens = await self.oauth.refresh(server, aina_id=installation.aina_id, tokens=tokens)
        except PlatformError as exc:
            await self._disconnect(installation)
            raise self._needs_connection(record) from exc
        current = await self._installation(installation.aina_id, tenant_id=installation.tenant_id, user_id=installation.user_id)
        if current is not None:
            secrets = {**current.secrets, OAUTH_SECRET: self.oauth.seal_tokens(tokens, context=context)}
            await self.repository.put_installation(current.model_copy(update={"secrets": secrets}))
        return tokens.access_token

    def _needs_connection(self, record: AinaRecord) -> PlatformError:
        name = record.manifest.aina.name
        return PlatformError(
            "AUTHENTICATION_FAILED",
            f"{name} needs the user to connect their account again (Plugins → {name} → Connect)",
            status_code=409,
            source="mcp_oauth",
            user_message=f"Connect your {name} account to use it.",
        )

    async def _disconnect(self, installation: AinaInstallation) -> AinaInstallation:
        current = await self._installation(installation.aina_id, tenant_id=installation.tenant_id, user_id=installation.user_id)
        current = current or installation
        secrets = {key: value for key, value in current.secrets.items() if key != OAUTH_SECRET}
        status = "authorization_required" if current.status == "active" else current.status
        return await self.repository.put_installation(current.model_copy(update={"secrets": secrets, "status": status}))

    async def start_authorization(self, aina_id: str, *, tenant_id: str, user_id: str, return_to: str) -> str:
        """The URL that sends the user to the OAuth MCP server's authorization page (registering Superbot first)."""
        record = await self.repository.get_aina(aina_id)
        runtime = package_runtime(record)
        oauth_server = runtime.package.oauth_server()
        if oauth_server is None:
            raise PlatformError("INVALID_REQUEST", f"AINA {aina_id!r} does not use OAuth", status_code=409)
        if await self._installation(aina_id, tenant_id=tenant_id, user_id=user_id) is None:
            raise PlatformError("INVALID_REQUEST", "Install the AINA before connecting it", status_code=409)
        server = runtime.oauth
        if server is None or server.redirect_uri != self.oauth.redirect_uri:
            server = await self.oauth.register_client(
                aina_id=aina_id, mcp_url=oauth_server.url, scopes=list(oauth_server.auth.scopes or [])
            )
            updated = runtime.model_copy(update={"oauth": server})
            record = await self.repository.upsert_aina(
                record.model_copy(update={"manifest": project_manifest(runtime.package, updated)})
            )
        return self.oauth.authorization_url(
            server, aina_id=aina_id, tenant_id=tenant_id, user_id=user_id, return_to=return_to
        )

    async def complete_authorization(self, *, code: str, state: str, tenant_id: str, user_id: str) -> tuple[str, str]:
        """Store the user's tokens from the authorization response; returns the AINA id and where to send the user."""
        opened = self.oauth.open_state(state)
        if (opened.tenant_id, opened.user_id) != (tenant_id, user_id):
            raise PlatformError("PERMISSION_DENIED", "This authorization was started by another user", status_code=403)
        record = await self.repository.get_aina(opened.aina_id)
        server = package_runtime(record).oauth
        installation = await self._installation(opened.aina_id, tenant_id=tenant_id, user_id=user_id)
        if server is None or installation is None:
            raise PlatformError("CONFLICT", "The AINA was uninstalled or upgraded meanwhile; connect again", status_code=409)
        tokens = await self.oauth.exchange(server, aina_id=opened.aina_id, code=code, verifier=opened.verifier)
        secrets = {**installation.secrets, OAUTH_SECRET: self.oauth.seal_tokens(tokens, context=_installation_context(installation))}
        status = "active" if installation.status == "authorization_required" else installation.status
        connected = installation.model_copy(update={"secrets": secrets, "status": status})
        if status == "active":
            # Design §6.1: the tool snapshot is re-checked before an installation is used, as this user.
            await self.refresh_tools(opened.aina_id, installation=connected)
        await self.repository.put_installation(connected)
        return opened.aina_id, opened.return_to

    async def disconnect(self, aina_id: str, *, tenant_id: str, user_id: str) -> AinaInstallation:
        installation = await self._installation(aina_id, tenant_id=tenant_id, user_id=user_id)
        if installation is None:
            raise PlatformError("RESOURCE_NOT_FOUND", f"AINA {aina_id!r} is not installed", status_code=404)
        return await self._disconnect(installation)

    async def inspect_server(self, server: McpServer, *, credential: str | None = None) -> McpServerInfo:
        """The tools an MCP server lists, for the Add app page to choose from before anything is registered."""
        return await self._list_tools(server, self._discovery_headers(server, credential=credential))

    def canvas_origin_allowed(self, url: str) -> bool:
        try:
            self._require_destination(url, self.settings.aina_canvas_allowed_origins)
        except PlatformError:
            return False
        return True

    async def _existing(self, aina_id: str) -> AinaRecord | None:
        try:
            return await self.repository.get_aina(aina_id)
        except PlatformError as exc:
            if exc.code == "RESOURCE_NOT_FOUND":
                return None
            raise

    async def _list_tools(self, server: McpServer, headers: dict[str, str]) -> McpServerInfo:
        self._require_destination(server.url, self.settings.capability_allowed_origins)
        return await self.mcp.list_tools(
            server.url,
            headers=headers,
            timeout=self.settings.capability_timeout_seconds,
        )

    def _require_destination(self, url: str, allowed_origins: str) -> None:
        # Only bundled packages may use the bundled host (``register``), and their servers run in process.
        if self.enforce_destination_policy and not is_bundled_url(url):
            require_approved_destination(url, allowed_origins)


def _kept_oauth(existing: AinaRecord | None, manifest: AinaPackageManifest) -> OAuthServer | None:
    """An upgrade keeps Superbot's client registration while the MCP server stays the same, so tokens keep working."""
    server = manifest.oauth_server()
    if existing is None or server is None:
        return None
    previous = existing.manifest.runtime
    if not isinstance(previous, PackageRuntimeDefinition):
        return None
    previous_server = previous.package.oauth_server()
    if previous_server is None or previous_server.url != server.url:
        return None
    return previous.oauth


def _installation_context(installation: AinaInstallation) -> str:
    return secret_context(tenant_id=installation.tenant_id, user_id=installation.user_id, aina_id=installation.aina_id)


def _same_package(record: AinaRecord, runtime: PackageRuntimeDefinition) -> bool:
    """A bundled package is unchanged when its archive and its server's tools are (``tools_checked_at`` aside)."""
    current = record.manifest.runtime
    return isinstance(current, PackageRuntimeDefinition) and current.model_dump(
        exclude={"tools_checked_at"}
    ) == runtime.model_dump(exclude={"tools_checked_at"})


def package_runtime(record: AinaRecord) -> PackageRuntimeDefinition:
    runtime = record.manifest.runtime
    if not isinstance(runtime, PackageRuntimeDefinition):
        raise PlatformError(
            "INVALID_REQUEST",
            f"AINA {record.manifest.aina.id!r} is not an AINA Protocol 2.0 package",
            status_code=422,
        )
    return runtime


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    visible: dict[str, Any]
    task: TaskStart | None = None
    widget: WidgetAinaUi | None = None
    # Image content blocks of the result (``data`` base64, ``mimeType``), for a model that reads images.
    images: list[dict[str, str]] = field(default_factory=list)


def consent_changes(previous: PackageRuntimeDefinition, current: PackageRuntimeDefinition) -> list[str]:
    """What a new version can do that the user did not consent to: new tools, model access, looser effects."""
    changes: list[str] = []

    def surface(runtime: PackageRuntimeDefinition) -> dict[str, tuple[Effect, bool]]:
        tools: dict[str, tuple[Effect, bool]] = {
            tool.name: (tool.effect, "model" in tool.visibility) for tool in runtime.tools
        }
        canvas = runtime.package.canvas
        for canvas_tool in canvas.tools if canvas is not None else []:
            tools[canvas_tool.name] = (canvas_tool.effect or "write", True)
        return tools

    before, after = surface(previous), surface(current)
    for name, (effect, model_visible) in sorted(after.items()):
        if name not in before:
            changes.append(f"New tool {name!r} ({effect})")
            continue
        old_effect, old_visible = before[name]
        if EFFECT_ORDER[effect] < EFFECT_ORDER[old_effect]:
            changes.append(f"Tool {name!r} now asks less often ({old_effect} → {effect})")
        if model_visible and not old_visible:
            changes.append(f"The agent can now call {name!r}")
    known_servers = {server.url for server in previous.package.servers()}
    for server in current.package.servers():
        if server.url not in known_servers:
            changes.append(f"Tools from a new MCP server at {server.url}")
    old_url = previous.package.canvas.url if previous.package.canvas is not None else None
    new_url = current.package.canvas.url if current.package.canvas is not None else None
    if new_url is not None and new_url != old_url:
        changes.append(f"Canvas web app at {new_url}")
    return changes


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
    listed: Mapping[str, Iterable[McpToolDescriptor]],
) -> tuple[list[ToolSnapshot], list[str], list[str]]:
    """(snapshots, errors, warnings) of the exposed tools found in each server's ``tools/list`` result.

    ``listed`` maps a server id to the tools it lists.
    """
    by_server = {server_id: {tool.name: tool for tool in tools} for server_id, tools in listed.items()}
    snapshots: list[ToolSnapshot] = []
    errors: list[str] = []
    warnings: list[str] = []
    several = len(manifest.servers()) > 1
    for name, exposed in manifest.exposed().items():
        server = manifest.server_for(name)
        tool = by_server.get(server.id, {}).get(name)
        if tool is None:
            where = f"MCP server {server.id!r}" if several else "MCP server"
            errors.append(f"tools.expose.{name}: the {where} does not list a tool named {name!r}")
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
    validate_schema_arguments(snapshot.name, snapshot.input_schema, arguments)


def validate_schema_arguments(name: str, schema: dict[str, Any], arguments: dict[str, Any]) -> None:
    """Arguments against a reviewed JSON Schema (the full draft, not the function-calling subset)."""
    error = next(iter(sorted(Draft202012Validator(schema).iter_errors(arguments), key=str)), None)
    if error is not None:
        location = "/".join(str(part) for part in error.absolute_path) or "arguments"
        raise PlatformError(
            "INVALID_REQUEST",
            f"Tool {name} {location}: {error.message}",
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
                AinaCapability(id=skill.id, name=skill.name, description=skill.description) for skill in manifest.skills
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
        main_widget=_canvas_widget(manifest),
    )


def _canvas_widget(manifest: AinaPackageManifest) -> WidgetDefinition | None:
    """The host widget that embeds the package canvas (design §9.3) in the canvas pane."""
    canvas = manifest.canvas
    if canvas is None:
        return None
    return WidgetDefinition(
        id=f"{manifest.aina.id}-canvas",
        kind="aina_canvas",
        title=canvas.title,
        description=manifest.aina.description,
        canvas=WidgetCanvas(
            kind="url" if canvas.url is not None else "package",
            version=manifest.aina.version,
            entry=canvas.entry,
            url=canvas.url,
        ),
    )


MAX_TOOL_IMAGES = 3
MAX_TOOL_IMAGE_BYTES = 1024 * 1024
TOOL_IMAGE_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})


def tool_images(content: list[dict[str, Any]]) -> list[dict[str, str]]:
    """The result's images a vision model may see: common types, at most 3 of at most 1 MiB each."""
    images: list[dict[str, str]] = []
    for item in content:
        data, mime = item.get("data"), item.get("mimeType")
        if item.get("type") != "image" or not isinstance(data, str) or mime not in TOOL_IMAGE_TYPES:
            continue
        if len(data) * 3 // 4 > MAX_TOOL_IMAGE_BYTES:
            continue
        images.append({"data": data, "mimeType": str(mime)})
        if len(images) == MAX_TOOL_IMAGES:
            break
    return images


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
