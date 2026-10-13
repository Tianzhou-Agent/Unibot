"""The shared capability registry (tools, skills and AINAs), read and written in SQL.

Each record's id is its registry id, so the primary key rejects a second registration of the same id.
"""

from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy import Table, insert, select, update
from sqlalchemy.exc import IntegrityError

from tianzhou_agent_platform.aina.mcp.models import McpServerRecord
from tianzhou_agent_platform.aina.protocol.models import AinaRecord
from tianzhou_agent_platform.aina.skill.models import SkillRecord
from tianzhou_agent_platform.aina.tool.models import ToolRecord
from tianzhou_agent_platform.core.errors import conflict
from tianzhou_agent_platform.store.repository_schema import (
    ainas_table,
    installations_table,
    mcp_servers_table,
    skills_table,
    table_values,
    tools_table,
)
from tianzhou_agent_platform.store.sql import SqlStore

tools = tools_table
skills = skills_table
ainas = ainas_table
installations = installations_table
mcp_servers = mcp_servers_table


class RegistryStore(SqlStore):
    async def register_mcp_server(self, server: McpServerRecord) -> None:
        await self._register(
            mcp_servers, server, server.server_id, f"MCP server {server.server_id!r} is already registered"
        )

    async def replace_mcp_server(self, server: McpServerRecord) -> McpServerRecord | None:
        async with self._session() as session, session.begin():
            result = await session.execute(
                update(mcp_servers).where(mcp_servers.c.id == server.server_id).values(
                    table_values(mcp_servers, server, id=server.server_id)
                )
            )
        return server if result.rowcount else None  # type: ignore[attr-defined]

    async def get_mcp_server(self, server_id: str) -> McpServerRecord | None:
        return await self._one(McpServerRecord, select(mcp_servers).where(mcp_servers.c.id == server_id))

    async def list_mcp_servers(self) -> list[McpServerRecord]:
        return await self._all(McpServerRecord, select(mcp_servers).order_by(mcp_servers.c.created_at, mcp_servers.c.id))

    async def remove_mcp_server(self, server_id: str) -> bool:
        return await self._delete(mcp_servers, mcp_servers.c.id == server_id) > 0

    async def register_tool(self, tool: ToolRecord) -> None:
        await self._register(tools, tool, tool.tool_id, f"Tool {tool.tool_id!r} is already registered")

    async def get_tool(self, tool_id: str) -> ToolRecord | None:
        return await self._one(ToolRecord, select(tools).where(tools.c.id == tool_id))

    async def list_tools(self) -> list[ToolRecord]:
        return await self._all(ToolRecord, select(tools).order_by(tools.c.created_at, tools.c.id))

    async def remove_tool(self, tool_id: str) -> bool:
        return await self._delete(tools, tools.c.id == tool_id) > 0

    async def register_skill(self, skill: SkillRecord) -> None:
        await self._register(skills, skill, skill.skill_id, f"Skill {skill.skill_id!r} is already registered")

    async def get_skill(self, skill_id: str) -> SkillRecord | None:
        return await self._one(SkillRecord, select(skills).where(skills.c.id == skill_id))

    async def list_skills(self) -> list[SkillRecord]:
        return await self._all(SkillRecord, select(skills).order_by(skills.c.created_at, skills.c.id))

    async def remove_skill(self, skill_id: str) -> bool:
        return await self._delete(skills, skills.c.id == skill_id) > 0

    async def register_aina(self, aina: AinaRecord) -> None:
        aina_id = aina.manifest.aina.id
        await self._register(ainas, aina, aina_id, f"AINA {aina_id!r} is already registered")

    async def put_aina(self, aina: AinaRecord) -> None:
        await self._put(ainas, table_values(ainas, aina, id=aina.manifest.aina.id))

    async def get_aina(self, aina_id: str) -> AinaRecord | None:
        return await self._one(AinaRecord, select(ainas).where(ainas.c.id == aina_id))

    async def list_ainas(self) -> list[AinaRecord]:
        return await self._all(AinaRecord, select(ainas).order_by(ainas.c.registered_at, ainas.c.id))

    async def remove_aina(self, aina_id: str) -> bool:
        """Remove the AINA and every user's installation of it."""
        return await self._delete(ainas, ainas.c.id == aina_id, (installations, installations.c.aina_id == aina_id)) > 0

    async def _register(self, table: Table, record: BaseModel, record_id: str, duplicate_message: str) -> None:
        try:
            async with self._session() as session, session.begin():
                await session.execute(insert(table).values(table_values(table, record, id=record_id)))
        except IntegrityError:
            raise conflict(duplicate_message) from None
