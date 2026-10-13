from __future__ import annotations

import os

import pytest

from tianzhou_agent_platform.store.migrations import upgrade_database


@pytest.fixture(scope="session", autouse=True)
def migrated_live_databases() -> None:
    """Bring each opted-in live MySQL database to the head revision; the application no longer creates tables.

    Only databases named explicitly are migrated, never the default DSN, which may be a developer's own database.
    """
    dsns = set()
    if os.getenv("TZ_STORAGE_E2E") == "1" and os.getenv("TZ_STORAGE_E2E_MYSQL_DSN"):
        dsns.add(os.environ["TZ_STORAGE_E2E_MYSQL_DSN"])
    if os.getenv("OBS_TEST_MYSQL_DSN"):
        dsns.add(os.environ["OBS_TEST_MYSQL_DSN"])
    for dsn in dsns:
        upgrade_database(dsn)
