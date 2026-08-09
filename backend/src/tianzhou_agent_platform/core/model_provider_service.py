"""Model provider domain logic: /models discovery and health checks.

HTTP and LLM calls live here so route handlers stay thin and the LLM health
check reuses the shared OpenAI client from ``core.llm``.
"""

from __future__ import annotations

from time import perf_counter

import httpx
import openai
from fastapi import status

from tianzhou_agent_platform.core.errors import PlatformError, not_found
from tianzhou_agent_platform.core.llm import create_openai_chat_model
from tianzhou_agent_platform.core.model_settings import (
    DiscoveredModel,
    ModelActor,
    ModelDiscoveryRequest,
    ModelDiscoveryResponse,
    ModelHealthResult,
    models_url,
)
from tianzhou_agent_platform.core.repository import InMemoryRepository


async def discover_provider_models(
    repository: InMemoryRepository,
    *,
    scoped: ModelDiscoveryRequest,
    http_client: httpx.AsyncClient,
) -> ModelDiscoveryResponse:
    api_key = scoped.api_key or ""
    if scoped.provider_id and not api_key:
        provider = await repository.get_model_provider(
            scoped.provider_id,
            user_id=scoped.user_id,
            tenant_id=scoped.tenant_id,
        )
        api_key = provider.api_key
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        response = await http_client.get(
            models_url(scoped.base_url),
            headers=headers,
            timeout=scoped.timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPStatusError as exc:
        provider_status = exc.response.status_code
        if provider_status == status.HTTP_404_NOT_FOUND:
            user_message = "该 Provider 未提供可用的 /models 接口，请继续手动添加模型。"
        elif provider_status in {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN}:
            user_message = "Provider 拒绝访问 /models，请检查 API Key。"
        else:
            user_message = f"Provider 的 /models 接口返回 HTTP {provider_status}。"
        raise PlatformError(
            code="DEPENDENCY_FAILED",
            message=f"Provider models endpoint returned HTTP {provider_status}",
            status_code=status.HTTP_502_BAD_GATEWAY,
            retryable=provider_status == status.HTTP_429_TOO_MANY_REQUESTS or provider_status >= 500,
            source="model-provider",
            user_message=user_message,
        ) from exc
    except httpx.RequestError as exc:
        raise PlatformError(
            code="DEPENDENCY_FAILED",
            message="The provider models endpoint could not be reached",
            status_code=status.HTTP_502_BAD_GATEWAY,
            retryable=True,
            source="model-provider",
            user_message="无法连接 Provider 的 /models 接口，请检查 Base URL 和网络。",
        ) from exc
    except ValueError as exc:
        raise PlatformError(
            code="DEPENDENCY_FAILED",
            message="The provider models endpoint returned invalid JSON",
            status_code=status.HTTP_502_BAD_GATEWAY,
            source="model-provider",
            user_message="Provider 的 /models 接口未返回有效 JSON，请继续手动添加模型。",
        ) from exc

    items = data.get("data") if isinstance(data, dict) else data
    if isinstance(data, dict) and not isinstance(items, list):
        items = data.get("models")
    if not isinstance(items, list):
        raise PlatformError(
            code="DEPENDENCY_FAILED",
            message="The provider models response did not contain a model list",
            status_code=status.HTTP_502_BAD_GATEWAY,
            source="model-provider",
            user_message="Provider 的 /models 返回格式不兼容，请继续手动添加模型。",
        )

    discovered: list[DiscoveredModel] = []
    seen: set[str] = set()
    for item in items:
        if isinstance(item, str):
            model_id = item.strip()
            model_name = model_id
        elif isinstance(item, dict):
            identifier = item.get("id") or item.get("model") or item.get("name")
            model_id = identifier.strip() if isinstance(identifier, str) else ""
            display_name = item.get("display_name") or item.get("name")
            model_name = display_name.strip() if isinstance(display_name, str) else model_id
        else:
            continue
        normalized = model_id.casefold()
        if not model_id or normalized in seen:
            continue
        seen.add(normalized)
        discovered.append(DiscoveredModel(id=model_id, name=model_name or model_id))
    return ModelDiscoveryResponse(models=discovered)


async def check_model_health(
    repository: InMemoryRepository,
    *,
    provider_id: str,
    model_id: str,
    scoped: ModelActor,
    http_client: httpx.AsyncClient,
) -> ModelHealthResult:
    provider = await repository.get_model_provider(
        provider_id,
        user_id=scoped.user_id,
        tenant_id=scoped.tenant_id,
    )
    model = next((item for item in provider.models if item.id == model_id), None)
    if model is None:
        raise not_found("Model", model_id)
    started = perf_counter()
    error: str | None = None
    try:
        chat_model = create_openai_chat_model(
            model=model.model,
            api_key=provider.api_key,
            base_url=provider.base_url,
            timeout_seconds=provider.timeout_seconds,
            client=http_client,
            max_completion_tokens=1,
        )
        await chat_model.ainvoke([{"role": "user", "content": "Reply with OK."}])
    except (openai.OpenAIError, ValueError) as exc:
        error = str(exc)
    return ModelHealthResult(
        status="unhealthy" if error else "healthy",
        latency_ms=round((perf_counter() - started) * 1000, 1),
        error=error,
    )
