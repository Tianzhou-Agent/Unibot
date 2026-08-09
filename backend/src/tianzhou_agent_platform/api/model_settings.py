from fastapi import APIRouter, Query, Request, Response, status

from tianzhou_agent_platform.api.dependencies import actor_scope, bind_actor, repository, settings
from tianzhou_agent_platform.core.model_provider_service import (
    check_model_health as check_model_health_service,
    discover_provider_models as discover_provider_models_service,
)
from tianzhou_agent_platform.core.model_settings import (
    ActiveModel,
    ModelActor,
    ModelDiscoveryRequest,
    ModelDiscoveryResponse,
    ModelHealthResult,
    ModelProviderCreate,
    ModelProviderUpdate,
    ModelProviderView,
    ModelSettingsResponse,
    provider_view,
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
        http_client = request.app.state.model_health_http_client
        return await discover_provider_models_service(
            repository(request),
            scoped=scoped,
            http_client=http_client,
        )

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
        http_client = request.app.state.model_health_http_client
        return await check_model_health_service(
            repository(request),
            provider_id=provider_id,
            model_id=model_id,
            scoped=scoped,
            http_client=http_client,
        )

    return router
