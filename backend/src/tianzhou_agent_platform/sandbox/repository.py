"""Persistence contract consumed by SandboxService.

Lookups of a missing record raise ``not_found`` rather than returning None.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from tianzhou_agent_platform.sandbox.models import SandboxExecution, SandboxRecord


@runtime_checkable
class SandboxRepository(Protocol):
    async def put_sandbox(self, sandbox: SandboxRecord) -> SandboxRecord: ...

    async def get_sandbox_for_actor(
        self,
        *,
        user_id: str,
        tenant_id: str,
        workspace_id: str | None = None,
    ) -> SandboxRecord: ...

    async def put_sandbox_execution(self, execution: SandboxExecution) -> SandboxExecution: ...

    async def list_sandbox_executions(
        self,
        *,
        user_id: str,
        tenant_id: str,
        workspace_id: str | None = None,
        limit: int = 50,
    ) -> list[SandboxExecution]: ...

    async def remove_sandbox(self, sandbox_id: str) -> None: ...
