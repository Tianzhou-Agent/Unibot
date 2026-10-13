"""Columns for Huawei, Google, Microsoft and Apple sign-in, and one user per Huawei account.

Databases created by an earlier release through create_all() may already have the columns; they are added only where
missing.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-13 15:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

users = sa.table("unibot_users", sa.column("id", sa.String), sa.column("huawei_id", sa.String))


def upgrade() -> None:
    connection = op.get_bind()
    existing = {column["name"] for column in sa.inspect(connection).get_columns("unibot_users")}
    if "huawei_id" in existing:
        # Checked before any DDL: MySQL cannot roll schema changes back.
        duplicates = connection.execute(
            sa.select(users.c.huawei_id, sa.func.count())
            .where(users.c.huawei_id.is_not(None), users.c.huawei_id != "")
            .group_by(users.c.huawei_id)
            .having(sa.func.count() > 1)
        ).all()
        if duplicates:
            raise RuntimeError(f"Several users share a Huawei account; keep one of each first: {duplicates}")
        connection.execute(users.update().where(users.c.huawei_id == "").values(huawei_id=None))
    else:
        op.add_column("unibot_users", sa.Column("huawei_id", sa.String(length=255), nullable=True))
    if "oidc_identities" not in existing:
        op.add_column("unibot_users", sa.Column("oidc_identities", sa.JSON(none_as_null=True), nullable=True))
    op.create_unique_constraint("uq_unibot_users_huawei_id", "unibot_users", ["huawei_id"])


def downgrade() -> None:
    op.drop_constraint("uq_unibot_users_huawei_id", "unibot_users", type_="unique")
    op.drop_column("unibot_users", "oidc_identities")
    op.drop_column("unibot_users", "huawei_id")
