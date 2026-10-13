"""SQLite stand-in for the repository's MySQL tables in tests that cannot reach a MySQL server.

SQLite ignores ``SELECT ... FOR UPDATE`` and MySQL collations, so row locking and constraint behavior are covered by
the live MySQL tests instead.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tianzhou_agent_platform.store.repository_schema import repository_metadata

# Removed when the test process exits.
_ROOT = tempfile.TemporaryDirectory(prefix="unibot-sqlite-")


def sqlite_session_factory() -> async_sessionmaker[AsyncSession]:
    """Sessions on a fresh database holding every repository table."""
    path = Path(_ROOT.name) / f"{uuid4().hex}.db"
    schema_engine = create_engine(f"sqlite:///{path}")
    repository_metadata.create_all(schema_engine)
    schema_engine.dispose()
    return async_sessionmaker(create_async_engine(f"sqlite+aiosqlite:///{path}", poolclass=NullPool))
