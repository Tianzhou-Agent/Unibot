from __future__ import annotations

import asyncio
from time import perf_counter
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

import httpx
from a2a import types as a2a_types
from a2a.client import A2ACardResolver, A2AClientError, ClientCallContext, ClientConfig, ClientFactory
from google.protobuf.json_format import MessageToDict, ParseDict  # type: ignore[import-untyped]
from google.protobuf.struct_pb2 import Struct, Value  # type: ignore[import-untyped]

from tianzhou_agent_platform.aina.protocol.models import (
    AinaInstallation,
    AinaInvokeRequest,
    AinaInvokeResponse,
    AinaManifest,
    AinaOutput,
)
from tianzhou_agent_platform.aina.security.models import Authentication
from tianzhou_agent_platform.aina.tool.models import ToolRecord
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.core.schema import validate_value

if TYPE_CHECKING:
    from tianzhou_agent_platform.aina.managed import ManagedAinaRuntime


class RemoteCapabilityGateway:
    def __init__(
        self,
        settings: AgentSettings,
        client: httpx.AsyncClient | None = None,
        managed_runtime: ManagedAinaRuntime | None = None,
    ) -> None:
        self.settings = settings
        self._client = client or httpx.AsyncClient()
        self._owns_client = client is None
        self._managed_runtime = managed_runtime

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
        if manifest.runtime.protocol == "a2a":
            card = await self._resolve_a2a_card(manifest)
            result = {
                "status": "healthy",
                "protocol": "a2a",
                "agent_card": MessageToDict(card, preserving_proto_field_name=True),
            }
            if manifest.health_check is not None:
                result["health"] = await self._request_json(
                    "GET",
                    str(manifest.health_check),
                    authentication=manifest.authentication,
                    timeout=self.settings.capability_timeout_seconds,
                    retries=0,
                    source="aina",
                )
            return result
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
        workspace_id: str | None = None,
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
        if workspace_id is not None:
            payload["workspace_id"] = workspace_id
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
        workspace_id: str | None = None,
        trace_id: str,
        available_tools: list[str],
    ) -> tuple[AinaInvokeResponse, float]:
        if manifest.runtime.type == "managed":
            if self._managed_runtime is None:
                raise PlatformError(
                    "DEPENDENCY_FAILED",
                    "Managed AINA runtime is unavailable",
                    status_code=503,
                    source="aina",
                )
            return await self._managed_runtime.invoke(
                manifest,
                installation,
                arguments=arguments,
                call_id=call_id,
                conversation_id=conversation_id,
                workspace_id=workspace_id,
                trace_id=trace_id,
                available_tools=available_tools,
            )
        if manifest.runtime.type != "remote":
            raise PlatformError(
                "INVALID_REQUEST",
                "A built-in AINA cannot be invoked through the capability gateway",
                status_code=400,
                source="aina",
            )
        if manifest.runtime.protocol == "a2a":
            return await self._invoke_a2a(
                manifest,
                installation,
                arguments=arguments,
                call_id=call_id,
                conversation_id=conversation_id,
                trace_id=trace_id,
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
            context={
                "source": "agent",
                **({"workspace_id": workspace_id} if workspace_id is not None else {}),
            },
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

    async def _resolve_a2a_card(self, manifest: AinaManifest) -> a2a_types.AgentCard:
        if manifest.runtime.type != "remote":
            raise PlatformError(
                "INVALID_REQUEST",
                "A built-in AINA does not have an A2A Agent Card",
                status_code=400,
                source="aina",
            )
        resolver = A2ACardResolver(
            httpx_client=self._client,
            base_url=str(manifest.runtime.endpoint).rstrip("/"),
        )
        try:
            return await resolver.get_agent_card(
                http_kwargs={
                    "headers": self._headers(manifest.authentication),
                    "timeout": self.settings.capability_timeout_seconds,
                }
            )
        except (A2AClientError, httpx.HTTPError, ValueError) as exc:
            raise PlatformError(
                "DEPENDENCY_FAILED",
                "The remote A2A Agent Card could not be resolved",
                status_code=502,
                retryable=True,
                source="aina",
            ) from exc

    async def _invoke_a2a(
        self,
        manifest: AinaManifest,
        installation: AinaInstallation,
        *,
        arguments: dict[str, Any],
        call_id: str,
        conversation_id: str,
        trace_id: str,
    ) -> tuple[AinaInvokeResponse, float]:
        if manifest.runtime.type != "remote":
            raise PlatformError(
                "INVALID_REQUEST",
                "A built-in AINA cannot be invoked through A2A",
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
        card = await self._resolve_a2a_card(manifest)
        client = ClientFactory(
            ClientConfig(
                streaming=manifest.runtime.streaming,
                httpx_client=self._client,
                accepted_output_modes=["text/plain", "application/json"],
            )
        ).create(card)
        data = ParseDict(arguments, Value())
        metadata = ParseDict(
            {
                "user_id": installation.user_id,
                "tenant_id": installation.tenant_id,
                "trace_id": trace_id,
                "permissions": installation.granted_permissions,
            },
            Struct(),
        )
        request = a2a_types.SendMessageRequest(
            tenant=installation.tenant_id,
            message=a2a_types.Message(
                message_id=call_id or f"req_{uuid4().hex}",
                context_id=conversation_id,
                role=a2a_types.Role.ROLE_USER,
                parts=[a2a_types.Part(data=data, media_type="application/json")],
                metadata=metadata,
            ),
            configuration=a2a_types.SendMessageConfiguration(
                accepted_output_modes=["text/plain", "application/json"],
                return_immediately=manifest.runtime.async_tasks,
            ),
        )
        context = ClientCallContext(
            service_parameters=self._headers(manifest.authentication),
            timeout=self.settings.capability_timeout_seconds,
        )
        started = perf_counter()
        final_state = a2a_types.TaskState.TASK_STATE_COMPLETED
        outputs: list[AinaOutput] = []
        last_payload: dict[str, Any] | None = None
        try:
            async for event in client.send_message(request, context=context):
                last_payload = MessageToDict(event, preserving_proto_field_name=True)
                payload_kind = event.WhichOneof("payload")
                if payload_kind == "task":
                    final_state = event.task.status.state
                    outputs.extend(_a2a_artifact_outputs(event.task.artifacts))
                    if event.task.status.HasField("message"):
                        outputs.extend(_a2a_message_outputs(event.task.status.message))
                elif payload_kind == "message":
                    outputs.extend(_a2a_message_outputs(event.message))
                elif payload_kind == "status_update":
                    final_state = event.status_update.status.state
                    if event.status_update.status.HasField("message"):
                        outputs.extend(_a2a_message_outputs(event.status_update.status.message))
                elif payload_kind == "artifact_update":
                    outputs.extend(_a2a_artifact_outputs([event.artifact_update.artifact]))
        except (A2AClientError, httpx.HTTPError, ValueError) as exc:
            raise PlatformError(
                "DEPENDENCY_FAILED",
                "The remote A2A agent invocation failed",
                status_code=502,
                retryable=True,
                source="aina",
            ) from exc

        status = _aina_status_from_a2a(final_state)
        if status == "completed" and not outputs:
            outputs.append(AinaOutput(type="a2a.task", content=last_payload or {}))
        response = AinaInvokeResponse(
            request_id=request.message.message_id,
            status=status,
            outputs=outputs,
            trace_id=trace_id,
        )
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


def _a2a_message_outputs(message: a2a_types.Message) -> list[AinaOutput]:
    return [_a2a_part_output(part) for part in message.parts]


def _a2a_artifact_outputs(artifacts: Any) -> list[AinaOutput]:
    return [_a2a_part_output(part) for artifact in artifacts for part in artifact.parts]


def _a2a_part_output(part: a2a_types.Part) -> AinaOutput:
    content_kind = part.WhichOneof("content")
    if content_kind == "text":
        return AinaOutput(type=part.media_type or "text", content=part.text)
    if content_kind == "data":
        return AinaOutput(
            type=part.media_type or "json",
            content=MessageToDict(part.data, preserving_proto_field_name=True),
        )
    if content_kind == "url":
        return AinaOutput(type=part.media_type or "url", content=part.url)
    if content_kind == "raw":
        return AinaOutput(
            type=part.media_type or "binary",
            content=MessageToDict(part, preserving_proto_field_name=True).get("raw", ""),
        )
    return AinaOutput(
        type="a2a.part",
        content=MessageToDict(part, preserving_proto_field_name=True),
    )


def _aina_status_from_a2a(
    state: int,
) -> Literal["completed", "failed", "input_required", "approval_required"]:
    if state == a2a_types.TaskState.TASK_STATE_COMPLETED:
        return "completed"
    if state == a2a_types.TaskState.TASK_STATE_INPUT_REQUIRED:
        return "input_required"
    if state == a2a_types.TaskState.TASK_STATE_AUTH_REQUIRED:
        return "approval_required"
    return "failed"
