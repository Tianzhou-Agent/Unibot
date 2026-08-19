"""MCP registration, refreshed discovery and deletion are shared by SQL-backed replicas."""

from __future__ import annotations

import asyncio
from importlib import import_module

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from tianzhou_agent_platform.aina.mcp.models import McpServerRecord, McpToolDescriptor
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.conversations.models import Conversation
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.services.agent_integration.capabilities import CapabilityCatalog
from tianzhou_agent_platform.store.lifecycle import StorageStores
from tianzhou_agent_platform.store.repository import PersistentRepository
from tianzhou_agent_platform.store.repository_schema import mcp_servers_table
from tests.support.sqlite_store import sqlite_session_factory


class _SqlOnly:
    def __init__(self) -> None:
        self.session_factory = sqlite_session_factory()


def _replicas() -> tuple[PersistentRepository, PersistentRepository]:
    stores = StorageStores(mysql=_SqlOnly(), redis=None, nas=None)  # type: ignore[arg-type]
    return PersistentRepository(stores), PersistentRepository(stores)


def _server() -> McpServerRecord:
    return McpServerRecord.model_validate({
        "server_id": "calculator", "name": "Calculator", "endpoint": "http://mcp.invalid/mcp",
        "protocol_version": "2025-11-25", "server_version": "1.0.0",
        "tools": [{"name": "add", "description": "Add two integers", "input_schema": {"type": "object"}}],
    })


async def test_mcp_registry_is_shared_after_registration_refresh_and_restart() -> None:
    first, second = _replicas()
    server = _server()
    await first.register_mcp_server(server)
    assert await second.get_mcp_server(server.server_id) == server
    assert await second.list_mcp_servers() == [server]
    # A freshly constructed node sees SQL records without hydrating the in-process repository.
    restarted = PersistentRepository(first.stores)
    catalog = CapabilityCatalog(settings=AgentSettings(_env_file=None), registry=restarted)
    conversation = Conversation(id="conv", user_id="anonymous", tenant_id="default", title="MCP registry")
    assert any(item.capability_id == "calculator:add" for item in (await catalog.system(conversation)).values())
    refreshed = server.model_copy(update={
        "server_version": "2.0.0",
        "tools": [McpToolDescriptor(name="multiply", description="Multiply two integers")],
    })
    assert await second.replace_mcp_server(refreshed) == refreshed
    assert await first.get_mcp_server(server.server_id) == refreshed
    assert any(
        item.capability_id == "calculator:multiply" for item in (await catalog.system(conversation)).values()
    )
    await restarted.remove_mcp_server(server.server_id)
    assert await first.list_mcp_servers() == []
    assert await second.list_mcp_servers() == []
    assert first._mcp_servers == second._mcp_servers == restarted._mcp_servers == {}
    for operation in (
        first.get_mcp_server(server.server_id), first.replace_mcp_server(server), first.remove_mcp_server(server.server_id),
    ):
        with pytest.raises(PlatformError, match="was not found"):
            await operation


async def test_concurrent_mcp_registration_has_one_shared_winner() -> None:
    first, second = _replicas()
    results = await asyncio.gather(
        first.register_mcp_server(_server()), second.register_mcp_server(_server()), return_exceptions=True,
    )
    assert sum(isinstance(item, McpServerRecord) for item in results) == 1
    errors = [item for item in results if isinstance(item, PlatformError)]
    assert len(errors) == 1 and errors[0].code == "CONFLICT"
    assert len(await second.list_mcp_servers()) == 1


def test_mcp_migration_creates_the_record_schema_and_preserves_existing_table() -> None:
    migration = import_module("tianzhou_agent_platform.store.migrations.versions.0007_mcp_server_registry")
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            columns = {column["name"] for column in inspect(connection).get_columns("unibot_mcp_servers")}
            assert columns == set(mcp_servers_table.c.keys())
            migration.upgrade()
            migration.downgrade()
            assert not inspect(connection).has_table("unibot_mcp_servers")
    engine.dispose()
