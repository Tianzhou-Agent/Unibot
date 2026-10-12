"""Per-user model providers, read and written in SQL."""

from __future__ import annotations

from sqlalchemy import ColumnElement, func, insert, select, update

from tianzhou_agent_platform.model_providers.models import (
    ModelProviderCreate,
    ModelProviderRecord,
    ModelProviderUpdate,
    ModelRuntimeConfig,
)
from tianzhou_agent_platform.store.memory_repository import (
    default_model_runtime,
    new_model_provider,
    providers_with_default_model,
    updated_model_provider,
)
from tianzhou_agent_platform.store.repository_schema import model_from_mapping, model_providers_table, table_values
from tianzhou_agent_platform.store.sql import SqlStore

providers = model_providers_table


def _owned_by(user_id: str, tenant_id: str) -> ColumnElement[bool]:
    return (providers.c.user_id == user_id) & (providers.c.tenant_id == tenant_id)


class ModelProviderStore(SqlStore):
    async def create(self, data: ModelProviderCreate) -> ModelProviderRecord:
        """The per-actor cap is read before inserting, so concurrent creates on different replicas can exceed it."""
        async with self._session() as session:
            count = (
                await session.execute(
                    select(func.count()).select_from(providers).where(_owned_by(data.user_id, data.tenant_id))
                )
            ).scalar_one()
        provider = new_model_provider(data, existing_count=count)
        async with self._session() as session, session.begin():
            await session.execute(insert(providers).values(table_values(providers, provider)))
        return provider

    async def get(self, provider_id: str) -> ModelProviderRecord | None:
        return await self._one(ModelProviderRecord, select(providers).where(providers.c.id == provider_id))

    async def list_for_actor(self, *, user_id: str, tenant_id: str) -> list[ModelProviderRecord]:
        statement = select(providers).where(_owned_by(user_id, tenant_id))
        return await self._all(ModelProviderRecord, statement.order_by(providers.c.created_at, providers.c.id))

    async def update(self, provider_id: str, data: ModelProviderUpdate) -> ModelProviderRecord | None:
        return await self._update_locked(
            providers, ModelProviderRecord, provider_id, lambda provider: updated_model_provider(provider, data)
        )

    async def remove(self, provider_id: str) -> None:
        await self._delete(providers, providers.c.id == provider_id)

    async def set_default_model(
        self, provider_id: str, model_id: str, *, user_id: str, tenant_id: str
    ) -> ModelProviderRecord:
        """Make one model the actor's only default, locking all of the actor's providers for the change."""
        async with self._session() as session, session.begin():
            rows = (
                await session.execute(
                    select(providers)
                    .where(_owned_by(user_id, tenant_id))
                    .order_by(providers.c.created_at, providers.c.id)
                    .with_for_update()
                )
            ).mappings()
            owned = [model_from_mapping(ModelProviderRecord, row) for row in rows]
            changed = {item.id: item for item in providers_with_default_model(owned, provider_id, model_id)}
            for item in changed.values():
                await session.execute(
                    update(providers).where(providers.c.id == item.id).values(table_values(providers, item))
                )
        return next(changed.get(item.id, item) for item in owned if item.id == provider_id)

    async def default_runtime(self, *, user_id: str, tenant_id: str) -> ModelRuntimeConfig | None:
        return default_model_runtime(await self.list_for_actor(user_id=user_id, tenant_id=tenant_id))
