"""Persist discovered MCP server tools in the shared SQL registry.

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    timestamp = sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql")
    columns: tuple[sa.Column[Any], ...] = (
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("server_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("endpoint", sa.String(length=2083), nullable=False),
        sa.Column("authentication", sa.JSON(), nullable=False),
        sa.Column("credential_env", sa.String(length=128), nullable=True),
        sa.Column("timeout_seconds", sa.Double(), nullable=False),
        sa.Column("side_effect_level", sa.String(length=32), nullable=False),
        sa.Column("visibility", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("owner_user_id", sa.String(length=160), nullable=True),
        sa.Column("tenant_id", sa.String(length=160), nullable=True),
        sa.Column("protocol_version", sa.String(length=255), nullable=True),
        sa.Column("server_name", sa.String(length=255), nullable=True),
        sa.Column("server_version", sa.String(length=255), nullable=True),
        sa.Column("instructions", sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql"), nullable=True),
        sa.Column("tools", sa.JSON(), nullable=False),
        sa.Column("refreshed_at", timestamp, nullable=False),
        sa.Column("created_at", timestamp, nullable=False),
    )
    # Earlier releases used create_all(); stamping their baseline must retain a compatible MCP registry.
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("unibot_mcp_servers"):
        existing = {column["name"] for column in inspector.get_columns("unibot_mcp_servers")}
        missing = {column.name for column in columns} - existing - {"credential_env"}
        if missing:
            raise RuntimeError(f"The existing MCP registry is missing columns: {sorted(missing)}")
        if "credential_env" not in existing:
            op.add_column("unibot_mcp_servers", sa.Column("credential_env", sa.String(length=128), nullable=True))
        return
    op.create_table("unibot_mcp_servers", *columns, sa.PrimaryKeyConstraint("id"))


def downgrade() -> None:
    op.drop_table("unibot_mcp_servers")
