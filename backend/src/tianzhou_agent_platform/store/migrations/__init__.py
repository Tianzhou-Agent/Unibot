"""Alembic schema management for every MySQL table the application owns.

Deployments run ``tianzhou-agent-platform-migrate`` (``alembic upgrade head``) before starting the application. The
application never creates or alters tables itself; it refuses to start unless the database is at the head revision.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import MetaData
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from tianzhou_agent_platform.store.checkpoint import graph_checkpoint_metadata
from tianzhou_agent_platform.store.observability_store import OBS_METADATA
from tianzhou_agent_platform.store.repository_schema import repository_metadata
from tianzhou_agent_platform.store.runtime_check import runtime_check_metadata
from tianzhou_agent_platform.store.settings import StorageSettings
from tianzhou_agent_platform.tasks.store import task_metadata

SCRIPT_LOCATION = Path(__file__).resolve().parent

APP_METADATA: list[MetaData] = [
    repository_metadata,
    graph_checkpoint_metadata,
    OBS_METADATA,
    task_metadata,
    runtime_check_metadata,
]


class DatabaseRevisionError(RuntimeError):
    pass


def alembic_config(dsn: str | None = None) -> Config:
    config = Config()
    config.set_main_option("script_location", str(SCRIPT_LOCATION))
    # Passed as an attribute, not a main option: ini interpolation would treat "%" in passwords as syntax.
    config.attributes["dsn"] = dsn
    return config


def upgrade_database(dsn: str, revision: str = "head") -> None:
    """Run migrations synchronously; call from a thread when an event loop is already running."""
    command.upgrade(alembic_config(dsn), revision)


async def require_database_revision(engine: AsyncEngine) -> None:
    expected = set(ScriptDirectory.from_config(alembic_config()).get_heads())

    def current_heads(connection: Connection) -> set[str]:
        return set(MigrationContext.configure(connection).get_current_heads())

    async with engine.connect() as connection:
        current = await connection.run_sync(current_heads)
    if current != expected:
        raise DatabaseRevisionError(
            f"Database schema revision is {sorted(current) or 'empty'}, expected {sorted(expected)}. "
            "Run `tianzhou-agent-platform-migrate` before starting the application."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage the Unibot MySQL schema (TZ_STORAGE_MYSQL_DSN).")
    parser.add_argument(
        "action",
        nargs="?",
        default="upgrade",
        choices=["upgrade", "stamp"],
        help="upgrade: apply migrations (default). stamp: record a revision without running it, for a database "
        "whose tables were created by an earlier release.",
    )
    parser.add_argument("revision", nargs="?", default="head")
    args = parser.parse_args()
    dsn = StorageSettings().mysql_dsn.get_secret_value()
    if args.action == "stamp":
        command.stamp(alembic_config(dsn), args.revision)
    else:
        upgrade_database(dsn, args.revision)
