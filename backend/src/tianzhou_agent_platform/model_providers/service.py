"""Actor-scoped model provider settings and selection."""

from __future__ import annotations

from tianzhou_agent_platform.core.errors import not_found
from tianzhou_agent_platform.model_providers.models import (
    ActiveModel,
    ModelHealthResult,
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

    async def get(self, provider_id: str) -> ModelProviderRecord:
        record = await self._repo.get_model_provider(provider_id)
        if record is None:
            raise not_found("model_provider", provider_id)
        return record

    async def update(self, provider_id: str, payload: ModelProviderUpdate) -> ModelProviderRecord:
        record = await self._repo.update_model_provider(provider_id, payload)
        if record is None:
            raise not_found("model_provider", provider_id)
        return record

    async def delete(self, provider_id: str) -> None:
        deleted = await self._repo.delete_model_provider(provider_id)
        if not deleted:
            raise not_found("model_provider", provider_id)

    async def list_for_actor(self, *, user_id: str, tenant_id: str) -> list[ModelProviderRecord]:
        return await self._repo.list_model_providers(user_id=user_id, tenant_id=tenant_id)

    async def settings_for_actor(
        self,
        *,
        user_id: str,
        tenant_id: str,
        default_model: str | None = None,
    ) -> ModelSettingsResponse:
        records = await self.list_for_actor(user_id=user_id, tenant_id=tenant_id)
        views = [provider_view(record) for record in records]
        active = self._resolve_active(records, default_model=default_model)
        return ModelSettingsResponse(providers=views, active_model=active)

    def _resolve_active(
        self, records: list[ModelProviderRecord], *, default_model: str | None
    ) -> ActiveModel:
        for record in records:
            for definition in record.models:
                if definition.is_default and definition.enabled:
                    return ActiveModel(
                        source="user",
                        provider_id=record.id,
                        provider_name=record.name,
                        model_id=definition.id,
                        model_name=definition.name,
                        model=definition.model,
                    )
        if default_model:
            return ActiveModel(source="environment", model=default_model)
        return ActiveModel(source="unconfigured")

    async def default_runtime(self, *, user_id: str, tenant_id: str) -> ModelRuntimeConfig | None:
        """The model the actor selected as default, or None to use the configured model."""
        return await self._repo.get_default_model_runtime(user_id=user_id, tenant_id=tenant_id)

    async def resolve_runtime(
        self,
        *,
        user_id: str,
        tenant_id: str,
        provider_id: str | None = None,
        model_id: str | None = None,
    ) -> ModelRuntimeConfig | None:
        """Return actor-scoped runtime config for native model construction."""
        if provider_id is not None:
            record = await self.get(provider_id)
        else:
            records = await self.list_for_actor(user_id=user_id, tenant_id=tenant_id)
            record = next((r for r in records if any(m.is_default and m.enabled for m in r.models)), None)
            if record is None and records:
                record = records[0]
            if record is None:
                return None
        definition = None
        if model_id is not None:
            definition = next((m for m in record.models if m.id == model_id), None)
        else:
            definition = next((m for m in record.models if m.is_default and m.enabled), None)
            if definition is None:
                definition = next((m for m in record.models if m.enabled), None)
        if definition is None:
            return None
        return ModelRuntimeConfig(
            provider_id=record.id,
            provider_name=record.name,
            base_url=record.base_url,
            api_key=record.api_key,
            model_id=definition.id,
            model_name=definition.name,
            model=definition.model,
            context_window_tokens=definition.context_window_tokens,
            timeout_seconds=record.timeout_seconds,
        )

    def health_result(self, *, latency_ms: float, error: str | None = None) -> ModelHealthResult:
        return ModelHealthResult(
            status="healthy" if error is None else "unhealthy",
            latency_ms=latency_ms,
            error=error,
        )
