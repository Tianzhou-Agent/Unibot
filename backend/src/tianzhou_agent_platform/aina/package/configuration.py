"""Per-installation configuration of an AINA 2.0 package (design §5.3 ``config``, §10).

Submitted values are validated against the package's ``config.schema``. ``x-secret: true`` fields are sealed with
the installation as context and kept out of ``configuration``, so they are never returned, logged or sent in
``_meta``; the host uses them only for ``tools.mcp.auth``.
"""

from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from tianzhou_agent_platform.aina.package.manifest import AinaPackageManifest, McpAuth
from tianzhou_agent_platform.aina.package.secrets import SecretBox
from tianzhou_agent_platform.aina.protocol.models import AinaInstallation
from tianzhou_agent_platform.core.errors import PlatformError


def secret_context(*, tenant_id: str, user_id: str, aina_id: str) -> str:
    return f"aina-installation-secret\x1f{tenant_id}\x1f{user_id}\x1f{aina_id}"


def prepare_configuration(
    manifest: AinaPackageManifest,
    submitted: dict[str, Any],
    *,
    box: SecretBox,
    tenant_id: str,
    user_id: str,
    previous: AinaInstallation | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """(public configuration, sealed secrets) of an installation; omitted secrets keep their previous value."""
    if manifest.config is None:
        if submitted:
            raise _invalid("This AINA takes no configuration")
        return {}, {}
    schema = manifest.config.json_schema
    secret_fields = manifest.secret_config_fields()
    context = secret_context(tenant_id=tenant_id, user_id=user_id, aina_id=manifest.aina.id)
    kept = {
        name: box.open(value, context=context)
        for name, value in (previous.secrets if previous is not None else {}).items()
        if name in secret_fields and name not in submitted
    }
    values: dict[str, Any] = {**kept, **submitted}
    properties = schema.get("properties")
    for name, field in (properties if isinstance(properties, dict) else {}).items():
        if name not in values and isinstance(field, dict) and "default" in field:
            values[name] = field["default"]
    error = next(iter(sorted(Draft202012Validator(schema).iter_errors(values), key=str)), None)
    if error is not None:
        location = "/".join(str(part) for part in error.absolute_path) or "configuration"
        # The message may quote the value; never echo a secret back.
        if location.split("/")[0] in secret_fields:
            raise _invalid(f"Configuration {location} is not valid")
        raise _invalid(f"Configuration {location}: {error.message}")
    public = {name: value for name, value in values.items() if name not in secret_fields}
    sealed = {
        name: box.seal(str(value), context=context)
        for name, value in values.items()
        if name in secret_fields and value not in (None, "")
    }
    return public, sealed


def mcp_auth_headers(
    auth: McpAuth,
    installation: AinaInstallation | None,
    *,
    box: SecretBox,
    credential: str | None = None,
) -> dict[str, str]:
    """HTTP headers for the package's MCP server: from the installation secret, or a given credential."""
    if auth.type == "none":
        return {}
    if auth.type == "oauth2":
        raise _invalid("MCP OAuth 2.0 authorization is not supported yet")
    value = credential
    if value is None and installation is not None and auth.secret is not None:
        sealed = installation.secrets.get(auth.secret)
        if sealed is not None:
            value = box.open(
                sealed,
                context=secret_context(
                    tenant_id=installation.tenant_id,
                    user_id=installation.user_id,
                    aina_id=installation.aina_id,
                ),
            )
    if not value:
        raise PlatformError(
            "AUTHENTICATION_FAILED",
            f"The MCP server needs the {auth.secret!r} setting, which is not configured for this installation",
            status_code=502,
            source="mcp",
        )
    if auth.type == "bearer":
        return {"Authorization": f"Bearer {value}"}
    return {auth.header or "X-API-Key": value}


def _invalid(message: str) -> PlatformError:
    return PlatformError("INVALID_REQUEST", message, status_code=422, source="aina_package")
