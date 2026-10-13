"""The shared capability registry in SQL, seen by several repository instances (replicas).

Runs on SQLite; concurrent cold starts are covered by ``test_registry_store_mysql.py``.
"""

from __future__ import annotations

import pytest

from tianzhou_agent_platform.aina.builtin import UNIBOT_MEMORY_ID, ensure_builtin_ainas
from tianzhou_agent_platform.aina.skill.models import SkillRecord
from tianzhou_agent_platform.aina.tool.models import ToolRecord
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.store.lifecycle import StorageStores
from tianzhou_agent_platform.store.repository import PersistentRepository
from tests.support.sqlite_store import sqlite_session_factory


class _SqlOnly:
    def __init__(self) -> None:
        self.session_factory = sqlite_session_factory()


def _replicas() -> tuple[PersistentRepository, PersistentRepository]:
    stores = StorageStores(mysql=_SqlOnly(), redis=None, nas=None)  # type: ignore[arg-type]
    return PersistentRepository(stores), PersistentRepository(stores)


async def _code(call) -> str:  # type: ignore[no-untyped-def]
    with pytest.raises(PlatformError) as error:
        await call
    return error.value.code


async def test_tools_and_skills_are_registered_once_for_every_replica() -> None:
    first, second = _replicas()
    tool = ToolRecord.model_validate(
        {
            "tool_id": "weather.lookup",
            "name": "Weather",
            "description": "Looks up weather.",
            "version": "1.0.0",
            "input_schema": {"type": "object"},
            "endpoint": "https://tools.example/weather",
        }
    )
    skill = SkillRecord.model_validate(
        {
            "skill_id": "trip.plan",
            "name": "Trip planner",
            "description": "Plans trips.",
            "version": "1.0.0",
            "instructions": "Plan the trip.",
            "tools": ["weather.lookup"],
        }
    )

    await first.register_tool(tool)
    await first.register_skill(skill)

    assert await second.get_tool("weather.lookup") == tool
    assert await second.list_skills() == [skill]
    assert await _code(second.register_tool(tool)) == "CONFLICT"
    assert await _code(second.register_skill(skill)) == "CONFLICT"
    await second.remove_tool("weather.lookup")
    await second.remove_skill("trip.plan")
    assert await first.list_tools() == []
    assert await _code(first.get_skill("trip.plan")) == "RESOURCE_NOT_FOUND"
    assert await _code(first.remove_tool("weather.lookup")) == "RESOURCE_NOT_FOUND"


async def test_built_in_ainas_are_registered_once_and_refreshed_on_restart() -> None:
    first, second = _replicas()

    await ensure_builtin_ainas(first, document_enabled=False)
    registered = await second.get_aina(UNIBOT_MEMORY_ID)
    await ensure_builtin_ainas(second, document_enabled=True)

    ainas = await first.list_ainas()
    assert len(ainas) == len({aina.manifest.aina.id for aina in ainas})
    assert (await first.get_aina(UNIBOT_MEMORY_ID)).registered_at == registered.registered_at
    assert await _code(second.register_aina(registered)) == "CONFLICT"
