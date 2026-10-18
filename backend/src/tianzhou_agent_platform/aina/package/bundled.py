"""Bundled first-party AINAs (design §11): packages shipped with Unibot under ``unibot_plugins``.

Each ``unibot_plugins/<name>/`` holds ``package/`` (an ordinary AINA 2.0 package folder) and ``server.py`` (its MCP
server, an ``mcp`` object built with the MCP SDK). The host registers them at startup and serves their tools in
process; their code imports only the MCP SDK and ``unibot_aina``, so moving one to its own remote MCP server needs
no change apart from its URL.

Bundled packages are platform-signed: they are available to every user without an installation, with every
declared scope granted, like the built-in AINAs they replace.
"""

from __future__ import annotations

import importlib
import json
import pkgutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlsplit

from tianzhou_agent_platform.aina.package.models import PackageRuntimeDefinition
from tianzhou_agent_platform.aina.project import build_project_archive
from tianzhou_agent_platform.aina.protocol.models import AinaInstallation, AinaRecord
from tianzhou_agent_platform.core.errors import PlatformError

if TYPE_CHECKING:
    from tianzhou_agent_platform.aina.package.service import AinaPackageService

PLUGINS_PACKAGE = "unibot_plugins"
# The MCP URL host of bundled packages; it never resolves, their servers are reached in process.
BUNDLED_HOST = "bundled.unibot.invalid"


@dataclass(frozen=True)
class BundledPlugin:
    name: str
    archive: bytes
    server: Any
    mcp_url: str


def is_bundled_url(url: str) -> bool:
    return urlsplit(url).hostname == BUNDLED_HOST


def discover_bundled() -> list[BundledPlugin]:
    """Every bundled plugin, with its archive built from its package folder."""
    root = importlib.import_module(PLUGINS_PACKAGE)
    plugins: list[BundledPlugin] = []
    for module in sorted(pkgutil.iter_modules(root.__path__), key=lambda item: item.name):
        folder = Path(root.__path__[0]) / module.name / "package"
        if not (folder / "aina.json").is_file():
            continue
        files = {
            path.relative_to(folder).as_posix(): path.read_bytes()
            for path in sorted(folder.rglob("*"))
            if path.is_file() and not any(part.startswith(".") for part in path.relative_to(folder).parts)
        }
        mcp_url = str(json.loads(files["aina.json"])["tools"]["mcp"]["url"])
        if not is_bundled_url(mcp_url):
            raise RuntimeError(f"Bundled plugin {module.name!r} must serve its MCP tools at https://{BUNDLED_HOST}/")
        server = importlib.import_module(f"{PLUGINS_PACKAGE}.{module.name}.server").mcp
        plugins.append(BundledPlugin(module.name, build_project_archive(files), server, mcp_url))
    return plugins


async def register_bundled(service: AinaPackageService) -> list[AinaRecord]:
    """Serve every bundled plugin in process and register (or update) its package."""
    records: list[AinaRecord] = []
    for plugin in discover_bundled():
        service.mcp.servers[plugin.mcp_url] = plugin.server
        records.append(await service.register(plugin.archive, bundled=True))
    return records


def is_bundled(record: AinaRecord) -> bool:
    runtime = record.manifest.runtime
    return isinstance(runtime, PackageRuntimeDefinition) and runtime.bundled


def bundled_installation(record: AinaRecord, *, tenant_id: str, user_id: str) -> AinaInstallation:
    """The implicit installation of a bundled package: every declared scope, default configuration."""
    runtime = record.manifest.runtime
    assert isinstance(runtime, PackageRuntimeDefinition)
    return AinaInstallation(
        user_id=user_id,
        tenant_id=tenant_id,
        aina_id=record.manifest.aina.id,
        installed_version=record.manifest.aina.version,
        granted_permissions=sorted(item.scope for item in runtime.package.permissions),
    )


async def effective_installation(
    repository: Any, record: AinaRecord, *, tenant_id: str, user_id: str
) -> AinaInstallation | None:
    """The user's installation of an AINA, or a bundled package's implicit one."""
    if is_bundled(record):
        return bundled_installation(record, tenant_id=tenant_id, user_id=user_id)
    try:
        installation = await repository.get_installation(
            tenant_id=tenant_id, user_id=user_id, aina_id=record.manifest.aina.id
        )
        return cast(AinaInstallation, installation)
    except PlatformError as exc:
        if exc.code != "RESOURCE_NOT_FOUND":
            raise
    return None


async def with_bundled(
    repository: Any, installations: list[AinaInstallation], *, tenant_id: str, user_id: str
) -> list[AinaInstallation]:
    """A user's installations plus the implicit ones of every registered bundled package."""
    bundled = {
        record.manifest.aina.id: bundled_installation(record, tenant_id=tenant_id, user_id=user_id)
        for record in await repository.list_ainas()
        if record.status == "registered" and is_bundled(record)
    }
    return [*(item for item in installations if item.aina_id not in bundled), *bundled.values()]
