"""Move conversation messages from a JSON column into one row per message.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-13 00:23:06.154807
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BATCH = 200
TIMESTAMP = sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql")

# Frozen table shapes for the data copy; never import application models into a migration.
conversations = sa.table(
    "unibot_conversations",
    sa.column("id", sa.String),
    sa.column("messages", sa.JSON),
    sa.column("next_message_sequence", sa.Integer),
)
messages = sa.table(
    "unibot_messages",
    sa.column("conversation_id", sa.String),
    sa.column("id", sa.String),
    sa.column("sequence_no", sa.Integer),
    sa.column("role", sa.String),
    sa.column("content", sa.Text),
    sa.column("content_type", sa.String),
    sa.column("reasoning", sa.Text),
    sa.column("tool_calls", sa.JSON(none_as_null=True)),
    sa.column("tool_call_id", sa.String),
    sa.column("name", sa.String),
    sa.column("widgets", sa.JSON),
    sa.column("trace_id", sa.String),
    sa.column("created_at", TIMESTAMP),
)


def upgrade() -> None:
    connection = op.get_bind()
    # Every stored message is converted once before any DDL: MySQL cannot roll schema changes back, so a message
    # that fails to convert halfway through would leave a half-applied revision that cannot be rerun.
    unreadable = []
    for conversation_id, stored in _batches(connection, conversations.c.messages):
        try:
            _message_rows(conversation_id, stored or [])
        except (KeyError, TypeError, ValueError, AttributeError):
            unreadable.append(conversation_id)
    if unreadable:
        raise RuntimeError(f"Conversations with unreadable messages; repair or delete them first: {unreadable}")

    op.create_table(
        "unibot_messages",
        sa.Column("conversation_id", sa.String(length=255), nullable=False),
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql"), nullable=False),
        sa.Column("content_type", sa.String(length=32), nullable=False),
        sa.Column("reasoning", sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql"), nullable=True),
        sa.Column("tool_calls", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("tool_call_id", sa.String(length=255), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("widgets", sa.JSON(), nullable=False),
        sa.Column("trace_id", sa.String(length=255), nullable=True),
        sa.Column("created_at", TIMESTAMP, nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["unibot_conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("conversation_id", "id"),
        sa.UniqueConstraint("conversation_id", "sequence_no", name="uq_unibot_messages_sequence"),
    )
    op.create_index(
        "ix_unibot_approvals_conversation_status", "unibot_approvals", ["conversation_id", "status"], unique=False
    )
    op.create_index(
        "ix_unibot_approvals_owner_created", "unibot_approvals", ["tenant_id", "user_id", "created_at"], unique=False
    )
    op.add_column(
        "unibot_conversations",
        sa.Column("next_message_sequence", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_unibot_conversations_owner_updated",
        "unibot_conversations",
        ["tenant_id", "user_id", "updated_at", "id"],
        unique=False,
    )

    for conversation_id, stored in _batches(connection, conversations.c.messages):
        rows = _message_rows(conversation_id, stored or [])
        if rows:
            connection.execute(messages.insert(), rows)
        connection.execute(
            conversations.update().where(conversations.c.id == conversation_id).values(next_message_sequence=len(rows))
        )

    op.alter_column(
        "unibot_conversations",
        "next_message_sequence",
        existing_type=sa.Integer(),
        existing_nullable=False,
        server_default=None,
    )
    op.drop_column("unibot_conversations", "messages")


def downgrade() -> None:
    op.add_column("unibot_conversations", sa.Column("messages", mysql.JSON(), nullable=True))

    connection = op.get_bind()
    for conversation_id, _ in _batches(connection, conversations.c.id):
        rows = connection.execute(
            sa.select(messages).where(messages.c.conversation_id == conversation_id).order_by(messages.c.sequence_no)
        ).mappings()
        connection.execute(
            conversations.update()
            .where(conversations.c.id == conversation_id)
            .values(messages=[_stored_message(row) for row in rows])
        )

    op.alter_column("unibot_conversations", "messages", existing_type=mysql.JSON(), nullable=False)
    op.drop_index("ix_unibot_conversations_owner_updated", table_name="unibot_conversations")
    op.drop_column("unibot_conversations", "next_message_sequence")
    op.drop_index("ix_unibot_approvals_owner_created", table_name="unibot_approvals")
    op.drop_index("ix_unibot_approvals_conversation_status", table_name="unibot_approvals")
    op.drop_table("unibot_messages")


def _batches(connection: sa.Connection, column: Any) -> Any:
    """Yield ``(conversation id, column value)`` for every conversation, a bounded number of rows at a time."""
    last_id = ""
    while True:
        rows = connection.execute(
            sa.select(conversations.c.id, column)
            .where(conversations.c.id > last_id)
            .order_by(conversations.c.id)
            .limit(_BATCH)
        ).all()
        if not rows:
            return
        yield from rows
        last_id = rows[-1][0]


def _message_rows(conversation_id: str, stored: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for message in stored:
        if message["id"] in seen:
            continue
        seen.add(message["id"])
        rows.append(
            {
                "conversation_id": conversation_id,
                "id": message["id"],
                "sequence_no": len(rows),
                "role": message["role"],
                "content": message.get("content") or "",
                "content_type": message.get("content_type") or "text",
                "reasoning": message.get("reasoning"),
                "tool_calls": message.get("tool_calls"),
                "tool_call_id": message.get("tool_call_id"),
                "name": message.get("name"),
                "widgets": message.get("widgets") or [],
                "trace_id": message.get("trace_id"),
                "created_at": datetime.fromisoformat(message["created_at"].replace("Z", "+00:00")).astimezone(UTC),
            }
        )
    return rows


def _stored_message(row: Any) -> dict[str, Any]:
    created_at = row["created_at"]
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return {
        "id": row["id"],
        "role": row["role"],
        "content": row["content"],
        "content_type": row["content_type"],
        "reasoning": row["reasoning"],
        "tool_calls": row["tool_calls"],
        "tool_call_id": row["tool_call_id"],
        "name": row["name"],
        "widgets": row["widgets"],
        "trace_id": row["trace_id"],
        "created_at": created_at.isoformat(),
    }
