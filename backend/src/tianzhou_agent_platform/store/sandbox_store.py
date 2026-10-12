"""User sandboxes and their execution history, read and written in SQL."""

from __future__ import annotations

from sqlalchemy import select

from tianzhou_agent_platform.core.errors import conflict
from tianzhou_agent_platform.sandbox.models import SandboxExecution, SandboxRecord
from tianzhou_agent_platform.store.repository_schema import sandbox_executions_table, sandboxes_table, table_values
from tianzhou_agent_platform.store.sql import SqlStore, matches

sandboxes = sandboxes_table
executions = sandbox_executions_table


class SandboxStore(SqlStore):
    async def put(self, sandbox: SandboxRecord) -> None:
        # Sandbox ids derive from the scope, so a different id in the same scope is a caller error, not a race.
        other = await self._one(
            SandboxRecord,
            select(sandboxes).where(
                sandboxes.c.user_id == sandbox.user_id,
                sandboxes.c.tenant_id == sandbox.tenant_id,
                matches(sandboxes.c.workspace_id, sandbox.workspace_id),
                sandboxes.c.id != sandbox.id,
            ),
        )
        if other is not None:
            raise conflict("The sandbox scope already owns a sandbox")
        await self._put(sandboxes, table_values(sandboxes, sandbox))

    async def get_for_actor(self, *, user_id: str, tenant_id: str, workspace_id: str | None) -> SandboxRecord | None:
        return await self._one(
            SandboxRecord,
            select(sandboxes).where(
                sandboxes.c.user_id == user_id,
                sandboxes.c.tenant_id == tenant_id,
                matches(sandboxes.c.workspace_id, workspace_id),
            ),
        )

    async def remove(self, sandbox_id: str) -> bool:
        return (
            await self._delete(
                sandboxes, sandboxes.c.id == sandbox_id, (executions, executions.c.sandbox_id == sandbox_id)
            )
            > 0
        )

    async def put_execution(self, execution: SandboxExecution) -> None:
        await self._put(executions, table_values(executions, execution))

    async def list_executions(
        self, *, user_id: str, tenant_id: str, workspace_id: str | None, limit: int
    ) -> list[SandboxExecution]:
        statement = (
            select(executions)
            .where(
                executions.c.user_id == user_id,
                executions.c.tenant_id == tenant_id,
                matches(executions.c.workspace_id, workspace_id),
            )
            .order_by(executions.c.started_at.desc(), executions.c.id.desc())
            .limit(limit)
        )
        return await self._all(SandboxExecution, statement)
