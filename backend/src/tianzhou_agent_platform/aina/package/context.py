"""The Unibot context sent to an AINA's MCP server in ``_meta["unibot/context"]`` (design §6.4).

User, tenant and installation are **pairwise identifiers**: stable per (AINA, subject) and derived with a host
secret, so two AINAs cannot correlate a user and internal ids never leave the host. The context token and Host API
base (design §8) are added with the Host API phase.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Any, Literal

from tianzhou_agent_platform.aina.package.manifest import AinaPackageManifest
from tianzhou_agent_platform.aina.protocol.models import AinaInstallation

CONTEXT_META_KEY = "unibot/context"
Source = Literal["agent", "canvas", "widget", "schedule"]

_PREFIXES = {"user": "pu", "tenant": "pt", "installation": "inst", "workspace": "ws", "conversation": "conv"}


def pairwise_id(secret: str, aina_id: str, kind: str, value: str) -> str:
    digest = hmac.new(
        secret.encode("utf-8"),
        "\x1f".join(("aina-pairwise-v1", aina_id, kind, value)).encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return f"{_PREFIXES[kind]}_{base64.b32encode(digest[:15]).decode('ascii').lower()}"


def unibot_context(
    *,
    secret: str,
    manifest: AinaPackageManifest,
    installation: AinaInstallation,
    invocation_id: str,
    source: Source,
    conversation_id: str | None,
    workspace_id: str | None,
) -> dict[str, Any]:
    aina_id = manifest.aina.id

    def pairwise(kind: str, value: str) -> str:
        return pairwise_id(secret, aina_id, kind, value)

    secret_fields = manifest.secret_config_fields()
    context: dict[str, Any] = {
        "invocation_id": invocation_id,
        "source": source,
        "user": {"id": pairwise("user", f"{installation.tenant_id}\x1f{installation.user_id}")},
        "tenant_id": pairwise("tenant", installation.tenant_id),
        "installation_id": pairwise(
            "installation",
            f"{installation.tenant_id}\x1f{installation.user_id}\x1f{installation.aina_id}",
        ),
        "granted_permissions": sorted(installation.granted_permissions),
        "config": {key: value for key, value in installation.configuration.items() if key not in secret_fields},
    }
    if conversation_id is not None:
        context["conversation_id"] = pairwise("conversation", conversation_id)
    if workspace_id is not None:
        context["workspace_id"] = pairwise("workspace", workspace_id)
    return context
