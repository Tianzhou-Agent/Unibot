"""Persistence contract consumed by ModelProviderService.

Lookups of a missing or foreign provider raise platform errors rather than returning None.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from tianzhou_agent_platform.model_providers.models import (
    ModelProviderCreate,
    ModelProviderRecord,
    ModelProviderUpdate,
    ModelRuntimeConfig,
)


@runtime_checkable
class ModelProviderRepository(Protocol):
    async def create_model_provider(self, data: ModelProviderCreate) -> ModelProviderRecord: ...

    async def get_model_provider(self, provider_id: str, *, user_id: str, tenant_id: str) -> ModelProviderRecord: ...

    async def list_model_providers(self, *, user_id: str, tenant_id: str) -> list[ModelProviderRecord]: ...

    async def update_model_provider(self, provider_id: str, data: ModelProviderUpdate) -> ModelProviderRecord: ...

    async def remove_model_provider(self, provider_id: str, *, user_id: str, tenant_id: str) -> None: ...

    async def set_default_model(
        self, provider_id: str, model_id: str, *, user_id: str, tenant_id: str
    ) -> ModelProviderRecord: ...

    async def get_default_model_runtime(self, *, user_id: str, tenant_id: str) -> ModelRuntimeConfig | None: ...
