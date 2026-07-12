from __future__ import annotations

import asyncio
from time import perf_counter
from typing import Any
from uuid import uuid4

import httpx

from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.core.models import (
    AinaInstallation,
    AinaInvokeRequest,
    AinaInvokeResponse,
    AinaManifest,
    Authentication,
    ToolRecord,
)
from tianzhou_agent_platform.core.schema import validate_value


class RemoteCapabilityGateway:
    def __init__(self, settings: AgentSettings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self._client = client or httpx.AsyncClient()
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @staticmethod
    def _headers(authentication: Authentication) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if authentication.type == "none":
            return headers
        if authentication.credential is None:
            raise PlatformError(
                "AUTHENTICATION_FAILED",
                f"No credential is configured for {authentication.type} authentication",
                status_code=502,
                source="capability",
            )
        credential = authentication.credential.get_secret_value()
        if authentication.type in {"bearer", "oauth2"}:
            credential = f"Bearer {credential}"
        headers[authentication.header_name] = credential
        return headers

    async def probe_aina(self, manifest: AinaManifest) -> dict[str, Any]:
        if manifest.runtime.type != "remote":
            raise PlatformError(
                "PERMISSION_DENIED",
                "Built-in AINA runtimes are managed by the platform",
                status_code=403,
                source="aina",
            )
        endpoint = str(manifest.runtime.endpoint).rstrip("/")
        describe = await self._request_json(
            "GET",
            f"{endpoint}/describe",
            authentication=manifest.authentication,
            timeout=self.settings.capability_timeout_seconds,
            retries=0,
            source="aina",
        )
        protocol_version = describe.get("protocol_version")
        if protocol_version is not None and protocol_version != manifest.protocol_version:
            raise PlatformError(
                "UNSUPPORTED_PROTOCOL",
                f"AINA describe returned protocol {protocol_version!r}",
                status_code=422,
                source="aina",
            )
        health_url = str(manifest.health_check) if manifest.health_check else f"{endpoint}/health"
        health = await self._request_json(
            "GET",
            health_url,
            authentication=manifest.authentication,
            timeout=self.settings.capability_timeout_seconds,
            retries=0,
            source="aina",
        )
        if str(health.get("status", "ok")).lower() not in {"ok", "healthy", "ready"}:
            raise PlatformError(
                "DEPENDENCY_FAILED",
                "AINA health check did not report a healthy state",
                status_code=422,
                source="aina",
            )
        return health

    async def invoke_tool(
        self,
        tool: ToolRecord,
        *,
        arguments: dict[str, Any],
        call_id: str,
        user_id: str,
        tenant_id: str,
        conversation_id: str,
        trace_id: str,
    ) -> tuple[Any, float]:
        validate_value(arguments, tool.input_schema, label=f"Tool {tool.tool_id} arguments")
        payload = {
            "request_id": call_id,
            "tool_id": tool.tool_id,
            "arguments": arguments,
            "user_id": user_id,
            "tenant_id": tenant_id,
            "conversation_id": conversation_id,
            "trace_id": trace_id,
        }
        started = perf_counter()
        result = await self._request_json(
            "POST",
            str(tool.endpoint),
            authentication=tool.authentication,
            timeout=tool.timeout_seconds,
            retries=tool.retries,
            source="tool",
            json=payload,
            extra_headers={"Idempotency-Key": call_id},
        )
        if tool.output_schema:
            validate_value(result, tool.output_schema, label=f"Tool {tool.tool_id} result")
        return result, (perf_counter() - started) * 1000

    async def invoke_aina(
        self,
        manifest: AinaManifest,
        installation: AinaInstallation,
        *,
        arguments: dict[str, Any],
        call_id: str,
        conversation_id: str,
        trace_id: str,
        available_tools: list[str],
    ) -> tuple[AinaInvokeResponse, float]:
        if manifest.runtime.type != "remote":
            raise PlatformError(
                "INVALID_REQUEST",
                "A built-in AINA cannot be invoked through the remote gateway",
                status_code=400,
                source="aina",
            )
        missing_permissions = set(manifest.permissions) - set(installation.granted_permissions)
        if missing_permissions:
            raise PlatformError(
                "PERMISSION_DENIED",
                f"AINA is missing grants: {', '.join(sorted(missing_permissions))}",
                status_code=403,
                source="aina",
            )
        request = AinaInvokeRequest(
            request_id=call_id or f"req_{uuid4().hex}",
            user_id=installation.user_id,
            tenant_id=installation.tenant_id,
            session_id=conversation_id,
            conversation_id=conversation_id,
            input=arguments,
            context={"source": "agent"},
            authorization={"permissions": installation.granted_permissions},
            trace={"trace_id": trace_id},
            available_tools=available_tools,
        )
        started = perf_counter()
        raw = await self._request_json(
            "POST",
            f"{str(manifest.runtime.endpoint).rstrip('/')}/invoke",
            authentication=manifest.authentication,
            timeout=self.settings.capability_timeout_seconds,
            retries=1,
            source="aina",
            json=request.model_dump(mode="json"),
            extra_headers={"Idempotency-Key": request.request_id},
        )
        try:
            response = AinaInvokeResponse.model_validate(raw)
        except ValueError as exc:
            raise PlatformError(
                "DEPENDENCY_FAILED",
                "AINA returned a response that does not match Protocol 1.0",
                status_code=502,
                source="aina",
            ) from exc
        return response, (perf_counter() - started) * 1000

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        authentication: Authentication,
        timeout: float,
        retries: int,
        source: str,
        json: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        headers = self._headers(authentication)
        headers.update(extra_headers or {})
        for attempt in range(retries + 1):
            try:
                response = await self._client.request(
                    method,
                    url,
                    headers=headers,
                    json=json,
                    timeout=timeout,
                )
                if response.status_code >= 500 and attempt < retries:
                    await asyncio.sleep(0.1 * (2**attempt))
                    continue
                if response.is_error:
                    raise PlatformError(
                        "DEPENDENCY_FAILED",
                        f"Remote {source} returned HTTP {response.status_code}",
                        status_code=502,
                        retryable=response.status_code >= 500 or response.status_code == 429,
                        source=source,
                        debug={"remote_status": response.status_code},
                    )
                data = response.json()
                if not isinstance(data, dict):
                    raise ValueError("response root is not an object")
                return data
            except PlatformError:
                raise
            except httpx.TimeoutException as exc:
                if attempt < retries:
                    continue
                raise PlatformError(
                    "TIMEOUT",
                    f"Remote {source} timed out",
                    status_code=504,
                    retryable=True,
                    source=source,
                ) from exc
            except httpx.RequestError as exc:
                if attempt < retries:
                    continue
                raise PlatformError(
                    "DEPENDENCY_FAILED",
                    f"Remote {source} could not be reached",
                    status_code=502,
                    retryable=True,
                    source=source,
                ) from exc
            except ValueError as exc:
                raise PlatformError(
                    "DEPENDENCY_FAILED",
                    f"Remote {source} returned invalid JSON",
                    status_code=502,
                    source=source,
                ) from exc
        raise AssertionError("unreachable")
