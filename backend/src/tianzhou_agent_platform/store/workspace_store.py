"""Workspaces, read and written in SQL."""

from __future__ import annotations

from sqlalchemy import insert, select

from tianzhou_agent_platform.core.workspace import Workspace, WorkspaceUpdate
from tianzhou_agent_platform.store.memory_repository import updated_workspace
from tianzhou_agent_platform.store.repository_schema import table_values, workspaces_table
from tianzhou_agent_platform.store.sql import SqlStore

workspaces = workspaces_table


class WorkspaceStore(SqlStore):
    async def create(self, workspace: Workspace) -> None:
        async with self._session() as session, session.begin():
            await session.execute(insert(workspaces).values(table_values(workspaces, workspace)))

    async def get(self, workspace_id: str) -> Workspace | None:
        return await self._one(Workspace, select(workspaces).where(workspaces.c.id == workspace_id))

    async def list_workspaces(self, *, user_id: str | None, tenant_id: str | None) -> list[Workspace]:
        statement = select(workspaces)
        if user_id is not None:
            statement = statement.where(workspaces.c.user_id == user_id)
        if tenant_id is not None:
            statement = statement.where(workspaces.c.tenant_id == tenant_id)
        return await self._all(Workspace, statement.order_by(workspaces.c.updated_at.desc(), workspaces.c.id.desc()))

    async def update(self, workspace_id: str, data: WorkspaceUpdate) -> Workspace | None:
        return await self._update_locked(
            workspaces, Workspace, workspace_id, lambda workspace: updated_workspace(workspace, data)
        )
