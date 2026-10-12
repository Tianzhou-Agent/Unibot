"""Imported AINA projects and per-user AINA installations, read and written in SQL.

A project's id derives from its tenant, owner, AINA id and version, so the primary key alone stops one identity from
being imported twice.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError

from tianzhou_agent_platform.aina.project import AinaProjectRecord
from tianzhou_agent_platform.aina.protocol.models import AinaInstallation
from tianzhou_agent_platform.store.memory_repository import InMemoryRepository, installation_record_id
from tianzhou_agent_platform.store.repository_schema import aina_projects_table, installations_table, table_values
from tianzhou_agent_platform.store.sql import SqlStore

projects = aina_projects_table
installations = installations_table


class AinaProjectStore(SqlStore):
    async def create_project(self, project: AinaProjectRecord) -> AinaProjectRecord:
        """Store an import, or return the identical earlier import of the same identity (a retry)."""
        try:
            async with self._session() as session, session.begin():
                await session.execute(insert(projects).values(table_values(projects, project)))
            return project
        except IntegrityError:
            existing = await self.get_project(project.id)
            if existing is None:
                raise
            return InMemoryRepository._resolve_aina_project_import(existing, project)  # noqa: SLF001

    async def get_project(self, project_id: str) -> AinaProjectRecord | None:
        return await self._one(AinaProjectRecord, select(projects).where(projects.c.id == project_id))

    async def list_projects(self, *, user_id: str, tenant_id: str) -> list[AinaProjectRecord]:
        statement = select(projects).where(projects.c.user_id == user_id, projects.c.tenant_id == tenant_id)
        return await self._all(
            AinaProjectRecord, statement.order_by(projects.c.created_at.desc(), projects.c.id.desc())
        )

    async def update_project(
        self, project_id: str, change: Callable[[AinaProjectRecord], AinaProjectRecord | None]
    ) -> AinaProjectRecord | None:
        return await self._update_locked(projects, AinaProjectRecord, project_id, change)

    async def remove_project(self, project_id: str) -> None:
        await self._delete(projects, projects.c.id == project_id)

    async def put_installation(self, installation: AinaInstallation) -> None:
        record_id = installation_record_id(installation.tenant_id, installation.user_id, installation.aina_id)
        await self._put(installations, table_values(installations, installation, id=record_id))

    async def get_installation(self, *, tenant_id: str, user_id: str, aina_id: str) -> AinaInstallation | None:
        record_id = installation_record_id(tenant_id, user_id, aina_id)
        return await self._one(AinaInstallation, select(installations).where(installations.c.id == record_id))

    async def list_installations(self, *, tenant_id: str | None, user_id: str | None) -> list[AinaInstallation]:
        statement = select(installations)
        if tenant_id is not None:
            statement = statement.where(installations.c.tenant_id == tenant_id)
        if user_id is not None:
            statement = statement.where(installations.c.user_id == user_id)
        return await self._all(AinaInstallation, statement.order_by(installations.c.installed_at, installations.c.id))

    async def remove_installation(self, *, tenant_id: str, user_id: str, aina_id: str) -> bool:
        record_id = installation_record_id(tenant_id, user_id, aina_id)
        return await self._delete(installations, installations.c.id == record_id) > 0
