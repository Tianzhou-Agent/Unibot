"""Persistence contract consumed by ModelProviderService."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from tianzhou_agent_platform.model_providers.models import (
    ModelProviderCreate,
    ModelProviderRecord,
    ModelProviderUpdate,
)


@runtime_checkable
class ModelProviderRepository(Protocol):
    async def create_model_provider(self, payload: ModelProviderCreate) -> ModelProviderRecord: ...

    async def get_model_provider(self, provider_id: str) -> ModelProviderRecord | None: ...

    async def update_model_provider(
        self, provider_id: str, payload: ModelProviderUpdate
    ) -> ModelProviderRecord | None: ...

    async def delete_model_provider(self, provider_id: str) -> bool: ...

    async def list_model_providers(
        self, *, user_id: str, tenant_id: str
    ) -> list[ModelProviderRecord]: ...
