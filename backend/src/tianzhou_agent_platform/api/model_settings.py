from time import perf_counter

import httpx
import openai
from fastapi import APIRouter, Query, Request, Response, status

from tianzhou_agent_platform.api.dependencies import actor_scope, bind_actor, repository, settings
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.core.model_settings import (
    ActiveModel,
    DiscoveredModel,
    ModelActor,
    ModelDiscoveryRequest,
    ModelDiscoveryResponse,
    ModelHealthResult,
    ModelProviderCreate,
    ModelProviderUpdate,
    ModelProviderView,
    ModelSettingsResponse,
    MAX_CONTEXT_WINDOW_TOKENS,
    MIN_CONTEXT_WINDOW_TOKENS,
    models_url,
    provider_view,
)
from tianzhou_agent_platform.core.llm import create_openai_chat_model


def create_model_settings_router() -> APIRouter:
    router = APIRouter(prefix="/model-settings", tags=["model-settings"])

    @router.get("", response_model=ModelSettingsResponse)
    async def get_model_settings(
        request: Request,
        user_id: str = "anonymous",
        tenant_id: str = "default",
    ) -> ModelSettingsResponse:
        data_repository = repository(request)
        actor = actor_scope(request, user_id=user_id, tenant_id=tenant_id)
        providers = await data_repository.list_model_providers(user_id=actor.user_id, tenant_id=actor.tenant_id)
        runtime_model = await data_repository.get_default_model_runtime(
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
        )
        if runtime_model is not None:
            active_model = ActiveModel(
                source="user",
                provider_id=runtime_model.provider_id,
                provider_name=runtime_model.provider_name,
                model_id=runtime_model.model_id,
                model_name=runtime_model.model_name,
                model=runtime_model.model,
            )
        else:
            app_settings = settings(request)
            if app_settings.llm_base_url and app_settings.llm_model:
                active_model = ActiveModel(
                    source="environment",
                    provider_name="环境变量",
                    model_name=app_settings.llm_model,
                    model=app_settings.llm_model,
                )
            else:
                active_model = ActiveModel(source="unconfigured")
        return ModelSettingsResponse(
            providers=[provider_view(item) for item in providers],
            active_model=active_model,
        )

    @router.post("/providers", response_model=ModelProviderView, status_code=status.HTTP_201_CREATED)
    async def create_provider(payload: ModelProviderCreate, request: Request) -> ModelProviderView:
        return provider_view(await repository(request).create_model_provider(bind_actor(request, payload)))

    @router.post("/providers/discover-models", response_model=ModelDiscoveryResponse)
    async def discover_provider_models(
        payload: ModelDiscoveryRequest,
        request: Request,
    ) -> ModelDiscoveryResponse:
        scoped = bind_actor(request, payload)
        api_key = scoped.api_key or ""
        if scoped.provider_id and not api_key:
            provider = await repository(request).get_model_provider(
                scoped.provider_id,
                user_id=scoped.user_id,
                tenant_id=scoped.tenant_id,
            )
            api_key = provider.api_key
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        client: httpx.AsyncClient = request.app.state.model_health_http_client
        try:
            response = await client.get(
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
                context_window_tokens = None
            elif isinstance(item, dict):
                identifier = item.get("id") or item.get("model") or item.get("name")
                model_id = identifier.strip() if isinstance(identifier, str) else ""
                display_name = item.get("display_name") or item.get("name")
                model_name = display_name.strip() if isinstance(display_name, str) else model_id
                context_window_tokens = _context_window_tokens(item)
            else:
                continue
            normalized = model_id.casefold()
            if not model_id or normalized in seen:
                continue
            seen.add(normalized)
            discovered.append(
                DiscoveredModel(
                    id=model_id,
                    name=model_name or model_id,
                    context_window_tokens=context_window_tokens,
                )
            )
        return ModelDiscoveryResponse(models=discovered)

    @router.put("/providers/{provider_id}", response_model=ModelProviderView)
    async def update_provider(
        provider_id: str,
        payload: ModelProviderUpdate,
        request: Request,
    ) -> ModelProviderView:
        return provider_view(await repository(request).update_model_provider(provider_id, bind_actor(request, payload)))

    @router.delete("/providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_provider(
        provider_id: str,
        request: Request,
        user_id: str = Query(default="anonymous"),
        tenant_id: str = Query(default="default"),
    ) -> Response:
        actor = actor_scope(request, user_id=user_id, tenant_id=tenant_id)
        await repository(request).remove_model_provider(
            provider_id,
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post(
        "/providers/{provider_id}/models/{model_id}/default",
        response_model=ModelProviderView,
    )
    async def set_default_model(
        provider_id: str,
        model_id: str,
        payload: ModelActor,
        request: Request,
    ) -> ModelProviderView:
        scoped = bind_actor(request, payload)
        provider = await repository(request).set_default_model(
            provider_id,
            model_id,
            user_id=scoped.user_id,
            tenant_id=scoped.tenant_id,
        )
        return provider_view(provider)

    @router.post(
        "/providers/{provider_id}/models/{model_id}/health",
        response_model=ModelHealthResult,
    )
    async def check_model_health(
        provider_id: str,
        model_id: str,
        payload: ModelActor,
        request: Request,
    ) -> ModelHealthResult:
        scoped = bind_actor(request, payload)
        provider = await repository(request).get_model_provider(
            provider_id,
            user_id=scoped.user_id,
            tenant_id=scoped.tenant_id,
        )
        model = next((item for item in provider.models if item.id == model_id), None)
        if model is None:
            from tianzhou_agent_platform.core.errors import not_found

            raise not_found("Model", model_id)
        started = perf_counter()
        error: str | None = None
        try:
            client: httpx.AsyncClient = request.app.state.model_health_http_client
            chat_model = create_openai_chat_model(
                model=model.model,
                api_key=provider.api_key,
                base_url=provider.base_url,
                timeout_seconds=provider.timeout_seconds,
                client=client,
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

    return router


_CONTEXT_WINDOW_FIELDS = (
    "context_window_tokens",
    "context_length",
    "context_window",
    "max_context_tokens",
    "max_context_length",
    "max_model_len",
    "max_input_tokens",
)
_CONTEXT_WINDOW_CONTAINERS = ("architecture", "top_provider", "limits", "capabilities")


def _context_window_tokens(model: dict[str, object]) -> int | None:
    containers = [model]
    containers.extend(
        nested
        for key in _CONTEXT_WINDOW_CONTAINERS
        if isinstance((nested := model.get(key)), dict)
    )
    for container in containers:
        for field in _CONTEXT_WINDOW_FIELDS:
            value = container.get(field)
            if isinstance(value, bool):
                continue
            try:
                tokens = int(value) if isinstance(value, (int, float, str)) else 0
            except ValueError:
                continue
            if MIN_CONTEXT_WINDOW_TOKENS <= tokens <= MAX_CONTEXT_WINDOW_TOKENS:
                return tokens
    return None
