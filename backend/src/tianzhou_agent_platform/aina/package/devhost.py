"""``aina dev``: a local host for developing an AINA 2.0 package (design §12).

It runs the real Superbot host in process (in-memory storage, no sign-in), so validation, tool snapshots, effects, the
Host API, context tokens and the sandboxed UI behave exactly as in production. On top it serves a developer page at
``/`` that embeds the package's canvas and widgets as the shell would, lets the developer call agent tools and canvas
tools by hand, and shows every request: bridge messages, MCP calls and Host API calls.

The package folder is re-read whenever it changes, and re-registered with a fresh tool snapshot of the developer's MCP
server, so editing the canvas or restarting the server needs only a reload.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import Body, FastAPI, Query, Request, Response
from fastapi.responses import FileResponse

from tianzhou_agent_platform.aina.package.cli import read_package_folder
from tianzhou_agent_platform.aina.package.mcp_client import McpCallResult, McpServerInfo, McpToolClient
from tianzhou_agent_platform.aina.package.service import AinaPackageService, package_runtime
from tianzhou_agent_platform.aina.package.validation import validate_package_files
from tianzhou_agent_platform.aina.project import build_project_archive
from tianzhou_agent_platform.aina.protocol.models import AinaInstallation, AinaRecord, InstallationRequest
from tianzhou_agent_platform.api.aina_host import HOST_API_PREFIX
from tianzhou_agent_platform.api.aina_ui import AINA_UI_PREFIX
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.conversations.models import ConversationCreate
from tianzhou_agent_platform.core.errors import PlatformError

STATIC = Path(__file__).with_name("devhost_static")
MAX_LOG = 500
# The developer is the host's local actor: no sign-in, every request acts as this user.
USER_ID, TENANT_ID = "anonymous", "default"


@dataclass
class DevLog:
    entries: list[dict[str, Any]] = field(default_factory=list)
    counter: int = 0

    def add(self, kind: str, summary: str, **details: Any) -> None:
        self.counter += 1
        self.entries.append({"seq": self.counter, "at": time.time(), "kind": kind, "summary": summary, **details})
        del self.entries[:-MAX_LOG]

    def after(self, seq: int) -> list[dict[str, Any]]:
        return [entry for entry in self.entries if entry["seq"] > seq]


class DevMcpClient(McpToolClient):
    """The host's MCP client, logging every ``tools/list`` and ``tools/call`` it makes."""

    def __init__(self, log: DevLog) -> None:
        super().__init__()
        self.log = log

    async def list_tools(self, url: str, *, headers: Mapping[str, str] | None = None, timeout: float = 30.0) -> McpServerInfo:
        return await self._logged(
            "tools/list", url, {}, lambda: super(DevMcpClient, self).list_tools(url, headers=headers, timeout=timeout)
        )

    async def call_tool(
        self,
        url: str,
        name: str,
        arguments: dict[str, Any],
        *,
        meta: dict[str, Any],
        headers: Mapping[str, str] | None = None,
        timeout: float = 30.0,
    ) -> McpCallResult:
        context = dict(meta.get("unibot/context") or {})
        if "token" in context:
            context["token"] = "…"  # a bearer credential; the log shows that it was sent
        return await self._logged(
            f"tools/call {name}",
            url,
            {"arguments": arguments, "context": context},
            lambda: super(DevMcpClient, self).call_tool(
                url, name, arguments, meta=meta, headers=headers, timeout=timeout
            ),
        )

    async def _logged[T: McpServerInfo | McpCallResult](
        self, summary: str, url: str, request: dict[str, Any], call: Callable[[], Awaitable[T]]
    ) -> T:
        started = time.perf_counter()
        try:
            result = await call()
        except PlatformError as exc:
            self.log.add("mcp", summary, url=url, request=request, error=exc.message, ms=_ms(started))
            raise
        self.log.add("mcp", summary, url=url, request=request, response=result.model_dump(mode="json"), ms=_ms(started))
        return result


@dataclass
class DevPackage:
    """The package folder under development and the host's current registration of it."""

    folder: Path
    service: AinaPackageService
    log: DevLog
    configuration: dict[str, Any] = field(default_factory=dict)
    granted: list[str] | None = None  # None: every declared scope
    record: AinaRecord | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    signature: str = ""
    conversation_id: str | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def folder_signature(self) -> str:
        files = sorted(path for path in self.folder.rglob("*") if path.is_file())
        stamp = "|".join(f"{path}:{path.stat().st_mtime_ns}:{path.stat().st_size}" for path in files)
        return hashlib.sha256(stamp.encode()).hexdigest()

    async def reload(self, *, force: bool = False) -> None:
        """Register the folder again if it changed (or ``force``: the MCP server's tools may have)."""
        async with self.lock:
            signature = self.folder_signature()
            if signature == self.signature and not force:
                return
            self.signature = signature
            files = read_package_folder(self.folder)
            report = validate_package_files(files)
            self.warnings = list(report.warnings)
            if report.errors or report.manifest is None:
                self.errors = list(report.errors)
                self.log.add("host", "package invalid", errors=self.errors)
                return
            try:
                self.record = await self.service.register(build_project_archive(files), replace=True)
                await self._install()
            except PlatformError as exc:
                self.errors = [exc.message]
                self.log.add("host", "registration failed", errors=self.errors)
                return
            self.errors = []
            self.warnings.extend(self.record.last_health.get("warnings") or [])
            self.log.add("host", f"registered {self.record.manifest.aina.id} {self.record.manifest.aina.version}")

    async def install(self, configuration: dict[str, Any], granted: list[str] | None) -> None:
        async with self.lock:
            self.configuration, self.granted = configuration, granted
            await self._install()

    async def installation(self) -> AinaInstallation:
        assert self.record is not None
        return await self.service.repository.get_installation(
            tenant_id=TENANT_ID, user_id=USER_ID, aina_id=self.record.manifest.aina.id
        )

    async def _install(self) -> None:
        if self.record is None:
            return
        declared: list[str] = [item.scope for item in package_runtime(self.record).package.permissions]
        granted = declared if self.granted is None else [scope for scope in self.granted if scope in declared]
        await self.service.install(
            self.record.manifest.aina.id,
            InstallationRequest(
                user_id=USER_ID, tenant_id=TENANT_ID, granted_permissions=granted, configuration=self.configuration
            ),
        )


def create_dev_app(folder: Path, *, host: str = "127.0.0.1", port: int = 8790) -> FastAPI:
    from tianzhou_agent_platform.main import create_app  # the full host; imported late, as it is large

    origin = f"http://{host}:{port}"
    settings = AgentSettings(
        _env_file=None,  # type: ignore[call-arg]
        env="development",
        dev_auth_bypass=False,
        obs_enabled=False,  # the observability pipeline needs Redis and MySQL
        frontend_base_url=origin,  # packaged UI may be framed by the dev page
        aina_host_api_base=f"{origin}{HOST_API_PREFIX}",
    )
    log = DevLog()
    app = create_app(settings=settings, mcp_client=DevMcpClient(log))
    package = DevPackage(folder=folder.resolve(), service=app.state.aina_packages, log=log)
    app.state.dev_package = package

    @app.middleware("http")
    async def log_host_api(request: Request, call_next: Any) -> Response:
        if not request.url.path.startswith(f"{HOST_API_PREFIX}/"):
            response: Response = await call_next(request)
            if request.url.path.startswith(f"{AINA_UI_PREFIX}/"):
                response.headers["Cache-Control"] = "no-store"  # edits show on the next reload
            return response
        started = time.perf_counter()
        response = await call_next(request)
        path = request.url.path.removeprefix(HOST_API_PREFIX)
        query = f"?{request.url.query}" if request.url.query else ""
        log.add("host-api", f"{request.method} {path}{query}", status=response.status_code, ms=_ms(started))
        return response

    @app.get("/", include_in_schema=False)
    async def dev_page() -> FileResponse:
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/dev/static/{name}", include_in_schema=False)
    async def dev_static(name: str) -> FileResponse:
        if name not in {"dev.js", "dev.css"}:
            raise PlatformError("RESOURCE_NOT_FOUND", f"No dev file {name!r}", status_code=404)
        return FileResponse(STATIC / name, headers={"Cache-Control": "no-store"})

    @app.get("/dev/state")
    async def dev_state() -> dict[str, Any]:
        """The package as the host sees it, re-registered first if its folder changed."""
        await package.reload()
        installation = None
        if package.record is not None:
            try:
                installation = (await package.installation()).model_dump(mode="json")
            except PlatformError:
                installation = None
        return {
            "folder": str(package.folder),
            "record": package.record.model_dump(mode="json") if package.record is not None else None,
            "installation": installation,
            "errors": package.errors,
            "warnings": package.warnings,
        }

    @app.post("/dev/reload")
    async def dev_reload() -> dict[str, Any]:
        await package.reload(force=True)
        return {"errors": package.errors}

    @app.post("/dev/install")
    async def dev_install(
        configuration: dict[str, Any] = Body(default_factory=dict),
        granted_permissions: list[str] | None = Body(default=None),
    ) -> dict[str, Any]:
        await package.install(configuration, granted_permissions)
        return (await package.installation()).model_dump(mode="json")

    @app.post("/dev/agent-call")
    async def dev_agent_call(
        tool: str = Body(...), arguments: dict[str, Any] = Body(default_factory=dict)
    ) -> dict[str, Any]:
        """Call a tool as the agent would, in the dev conversation; long-running tools start a real task."""
        if package.record is None:
            raise PlatformError("CONFLICT", "The package is not registered: " + "; ".join(package.errors), status_code=409)
        installation = await package.installation()
        if package.conversation_id is None:
            conversation = await app.state.repository.create_conversation(
                ConversationCreate(user_id=USER_ID, tenant_id=TENANT_ID, title="aina dev")
            )
            package.conversation_id = conversation.id
        call_id = f"call_dev_{uuid4().hex[:12]}"
        outcome = await package.service.call_tool(
            package.record,
            installation,
            tool,
            arguments,
            invocation_id=call_id,
            conversation_id=package.conversation_id,
            workspace_id=None,
        )
        task = None
        if outcome.task is not None:
            task = await app.state.aina_tasks.register(
                outcome.task,
                installation=installation,
                tool_name=tool,
                invocation_id=call_id,
                conversation_id=package.conversation_id,
                workspace_id=None,
            )
        return {
            "call_id": call_id,
            "visible": outcome.visible,
            "widget": outcome.widget.model_dump(mode="json") if outcome.widget is not None else None,
            "task": task.model_dump(mode="json") if task is not None else None,
        }

    @app.get("/dev/log")
    async def dev_log(after: int = Query(default=0, ge=0)) -> list[dict[str, Any]]:
        return log.after(after)

    return app


def _ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)
