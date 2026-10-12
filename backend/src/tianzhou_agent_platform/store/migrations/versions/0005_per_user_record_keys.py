"""Owner indexes for per-user records, and one feedback per actor and answer.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-13 01:19:32.259083
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


feedbacks = sa.table(
    "unibot_feedbacks",
    sa.column("id", sa.String),
    sa.column("tenant_id", sa.String),
    sa.column("user_id", sa.String),
    sa.column("message_id", sa.String),
)


def upgrade() -> None:
    # Checked before any DDL: MySQL cannot roll schema changes back.
    duplicates = (
        op.get_bind()
        .execute(
            sa.select(feedbacks.c.tenant_id, feedbacks.c.user_id, feedbacks.c.message_id, sa.func.count())
            .group_by(feedbacks.c.tenant_id, feedbacks.c.user_id, feedbacks.c.message_id)
            .having(sa.func.count() > 1)
        )
        .all()
    )
    if duplicates:
        raise RuntimeError(f"Several feedbacks share one actor and message; keep one of each first: {duplicates}")

    op.create_index(
        "ix_unibot_aina_projects_owner_created",
        "unibot_aina_projects",
        ["tenant_id", "user_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_unibot_document_edit_tasks_owner_created",
        "unibot_document_edit_tasks",
        ["tenant_id", "user_id", "created_at"],
        unique=False,
    )
    op.create_index("ix_unibot_feedbacks_created", "unibot_feedbacks", ["created_at"], unique=False)
    op.create_unique_constraint(
        "uq_unibot_feedbacks_message", "unibot_feedbacks", ["tenant_id", "user_id", "message_id"]
    )
    op.create_index("ix_unibot_installations_aina", "unibot_installations", ["aina_id"], unique=False)
    op.create_index("ix_unibot_installations_owner", "unibot_installations", ["tenant_id", "user_id"], unique=False)
    op.create_index(
        "ix_unibot_memories_owner_updated", "unibot_memories", ["tenant_id", "user_id", "updated_at"], unique=False
    )
    op.create_index(
        "ix_unibot_model_providers_owner_created",
        "unibot_model_providers",
        ["tenant_id", "user_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_unibot_workspaces_owner_updated", "unibot_workspaces", ["tenant_id", "user_id", "updated_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_unibot_workspaces_owner_updated", table_name="unibot_workspaces")
    op.drop_index("ix_unibot_model_providers_owner_created", table_name="unibot_model_providers")
    op.drop_index("ix_unibot_memories_owner_updated", table_name="unibot_memories")
    op.drop_index("ix_unibot_installations_owner", table_name="unibot_installations")
    op.drop_index("ix_unibot_installations_aina", table_name="unibot_installations")
    op.drop_constraint("uq_unibot_feedbacks_message", "unibot_feedbacks", type_="unique")
    op.drop_index("ix_unibot_feedbacks_created", table_name="unibot_feedbacks")
    op.drop_index("ix_unibot_document_edit_tasks_owner_created", table_name="unibot_document_edit_tasks")
    op.drop_index("ix_unibot_aina_projects_owner_created", table_name="unibot_aina_projects")
