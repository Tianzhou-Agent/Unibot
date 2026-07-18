from time import perf_counter

import httpx
from fastapi import APIRouter, Query, Request, Response, status

from tianzhou_agent_platform.api.dependencies import repository, settings
from tianzhou_agent_platform.core.model_settings import (
    ActiveModel,
    ModelActor,
    ModelHealthResult,
    ModelProviderCreate,
    ModelProviderUpdate,
    ModelProviderView,
    ModelSettingsResponse,
    provider_view,
    chat_completions_url,
)


def create_model_settings_router() -> APIRouter:
    router = APIRouter(prefix="/model-settings", tags=["model-settings"])

    @router.get("", response_model=ModelSettingsResponse)
    async def get_model_settings(
        request: Request,
        user_id: str = "anonymous",
        tenant_id: str = "default",
    ) -> ModelSettingsResponse:
        data_repository = repository(request)
        providers = await data_repository.list_model_providers(user_id=user_id, tenant_id=tenant_id)
        runtime_model = await data_repository.get_default_model_runtime(user_id=user_id, tenant_id=tenant_id)
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
        return provider_view(await repository(request).create_model_provider(payload))

    @router.put("/providers/{provider_id}", response_model=ModelProviderView)
    async def update_provider(
        provider_id: str,
        payload: ModelProviderUpdate,
        request: Request,
    ) -> ModelProviderView:
        return provider_view(await repository(request).update_model_provider(provider_id, payload))

    @router.delete("/providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_provider(
        provider_id: str,
        request: Request,
        user_id: str = Query(default="anonymous"),
        tenant_id: str = Query(default="default"),
    ) -> Response:
        await repository(request).remove_model_provider(
            provider_id,
            user_id=user_id,
            tenant_id=tenant_id,
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
        provider = await repository(request).set_default_model(
            provider_id,
            model_id,
            user_id=payload.user_id,
            tenant_id=payload.tenant_id,
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
        provider = await repository(request).get_model_provider(
            provider_id,
            user_id=payload.user_id,
            tenant_id=payload.tenant_id,
        )
        model = next((item for item in provider.models if item.id == model_id), None)
        if model is None:
            from tianzhou_agent_platform.core.errors import not_found

            raise not_found("Model", model_id)
        headers = {"Content-Type": "application/json"}
        if provider.api_key:
            headers["Authorization"] = f"Bearer {provider.api_key}"
        started = perf_counter()
        error: str | None = None
        try:
            client: httpx.AsyncClient = request.app.state.model_health_http_client
            response = await client.post(
                    chat_completions_url(provider.base_url),
                    headers=headers,
                    json={
                        "model": model.model,
                        "messages": [{"role": "user", "content": "Reply with OK."}],
                        "max_tokens": 1,
                        "stream": False,
                    },
                    timeout=provider.timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data.get("choices"), list) or not data["choices"]:
                raise ValueError("Provider response did not contain choices")
        except (httpx.HTTPError, ValueError) as exc:
            error = str(exc)
        return ModelHealthResult(
            status="unhealthy" if error else "healthy",
            latency_ms=round((perf_counter() - started) * 1000, 1),
            error=error,
        )

    return router
