"""Actor-scoped model provider settings and selection."""

from __future__ import annotations

from tianzhou_agent_platform.model_providers.models import (
    ActiveModel,
    ModelProviderCreate,
    ModelProviderRecord,
    ModelProviderUpdate,
    ModelRuntimeConfig,
    ModelSettingsResponse,
    provider_view,
)
from tianzhou_agent_platform.model_providers.repository import ModelProviderRepository


class ModelProviderService:
    def __init__(self, repository: ModelProviderRepository) -> None:
        self._repo = repository

    async def create(self, payload: ModelProviderCreate) -> ModelProviderRecord:
        return await self._repo.create_model_provider(payload)

    async def get(self, provider_id: str, *, user_id: str, tenant_id: str) -> ModelProviderRecord:
        return await self._repo.get_model_provider(provider_id, user_id=user_id, tenant_id=tenant_id)

    async def update(self, provider_id: str, payload: ModelProviderUpdate) -> ModelProviderRecord:
        return await self._repo.update_model_provider(provider_id, payload)

    async def remove(self, provider_id: str, *, user_id: str, tenant_id: str) -> None:
        await self._repo.remove_model_provider(provider_id, user_id=user_id, tenant_id=tenant_id)

    async def set_default_model(
        self, provider_id: str, model_id: str, *, user_id: str, tenant_id: str
    ) -> ModelProviderRecord:
        return await self._repo.set_default_model(provider_id, model_id, user_id=user_id, tenant_id=tenant_id)

    async def settings_for_actor(
        self,
        *,
        user_id: str,
        tenant_id: str,
        environment_model: str | None = None,
    ) -> ModelSettingsResponse:
        """The actor's providers and the model a turn would use: their default, else ``environment_model``."""
        providers = await self._repo.list_model_providers(user_id=user_id, tenant_id=tenant_id)
        runtime_model = await self.default_runtime(user_id=user_id, tenant_id=tenant_id)
        if runtime_model is not None:
            active_model = ActiveModel(
                source="user",
                provider_id=runtime_model.provider_id,
                provider_name=runtime_model.provider_name,
                model_id=runtime_model.model_id,
                model_name=runtime_model.model_name,
                model=runtime_model.model,
            )
        elif environment_model:
            active_model = ActiveModel(
                source="environment",
                provider_name="环境变量",
                model_name=environment_model,
                model=environment_model,
            )
        else:
            active_model = ActiveModel(source="unconfigured")
        return ModelSettingsResponse(providers=[provider_view(item) for item in providers], active_model=active_model)

    async def default_runtime(self, *, user_id: str, tenant_id: str) -> ModelRuntimeConfig | None:
        """The model the actor selected as default, or None to use the configured model."""
        return await self._repo.get_default_model_runtime(user_id=user_id, tenant_id=tenant_id)
