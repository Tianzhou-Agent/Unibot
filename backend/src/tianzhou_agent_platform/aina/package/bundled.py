"""Compatibility helpers for existing bundled package records and ordinary user installations."""

from __future__ import annotations

from typing import Any, cast
from urllib.parse import urlsplit

from tianzhou_agent_platform.aina.package.models import PackageRuntimeDefinition
from tianzhou_agent_platform.aina.protocol.models import AinaInstallation, AinaRecord
from tianzhou_agent_platform.core.errors import PlatformError

# Reserved for legacy in-process packages; external packages cannot claim this host.
BUNDLED_HOST = "bundled.unibot.invalid"


def is_bundled_url(url: str) -> bool:
    return urlsplit(url).hostname == BUNDLED_HOST


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
