"""Enforce unique account emails and GitHub links with database keys instead of a Redis lease.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-13 00:35:27.534719
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


EMAIL_NORMALIZED = sa.String(length=320).with_variant(mysql.VARCHAR(collation="utf8mb4_bin", length=320), "mysql")

# Frozen table shape for the backfill; never import application models into a migration.
users = sa.table(
    "unibot_users",
    sa.column("id", sa.String),
    sa.column("email", sa.String),
    sa.column("email_normalized", sa.String),
    sa.column("github_id", sa.String),
)


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(sa.select(users.c.id, users.c.email, users.c.github_id)).all()
    # Must match normalized_email() in store/memory_repository.py at the time of this revision.
    normalized = {user_id: email.strip().lower() for user_id, email, _ in rows}
    owners: dict[str, list[str]] = {}
    for user_id, email in normalized.items():
        owners.setdefault(email, []).append(user_id)
    duplicates = {email: ids for email, ids in owners.items() if len(ids) > 1}
    # Checked before any DDL: MySQL cannot roll schema changes back, so failing later would leave a half-applied
    # revision that cannot be rerun.
    if duplicates:
        raise RuntimeError(f"Users share an email; merge or remove them before upgrading: {duplicates}")

    op.add_column("unibot_users", sa.Column("email_normalized", EMAIL_NORMALIZED, nullable=True))
    for user_id, _, github_id in rows:
        connection.execute(
            users.update()
            .where(users.c.id == user_id)
            .values(email_normalized=normalized[user_id], github_id=github_id or None)
        )
    op.alter_column("unibot_users", "email_normalized", existing_type=EMAIL_NORMALIZED, nullable=False)
    op.create_index("ix_unibot_users_created", "unibot_users", ["created_at", "id"], unique=False)
    op.create_unique_constraint("uq_unibot_users_email_normalized", "unibot_users", ["email_normalized"])
    op.create_unique_constraint("uq_unibot_users_github_id", "unibot_users", ["github_id"])


def downgrade() -> None:
    op.drop_constraint("uq_unibot_users_github_id", "unibot_users", type_="unique")
    op.drop_constraint("uq_unibot_users_email_normalized", "unibot_users", type_="unique")
    op.drop_index("ix_unibot_users_created", table_name="unibot_users")
    op.drop_column("unibot_users", "email_normalized")
