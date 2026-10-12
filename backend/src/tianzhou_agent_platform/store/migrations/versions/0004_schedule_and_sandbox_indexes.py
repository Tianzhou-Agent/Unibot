"""Indexes for the scheduler due-task query and per-owner schedule and sandbox history.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-13 01:04:26.081998
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_unibot_sandbox_executions_owner_started",
        "unibot_sandbox_executions",
        ["tenant_id", "user_id", "started_at"],
        unique=False,
    )
    op.create_index("ix_unibot_sandbox_executions_sandbox", "unibot_sandbox_executions", ["sandbox_id"], unique=False)
    op.create_index(
        "ix_unibot_sandboxes_scope", "unibot_sandboxes", ["tenant_id", "user_id", "workspace_id"], unique=False
    )
    op.create_index(
        "ix_unibot_scheduled_aina_executions_task_started",
        "unibot_scheduled_aina_executions",
        ["task_id", "started_at"],
        unique=False,
    )
    op.create_index(
        "ix_unibot_scheduled_aina_tasks_due", "unibot_scheduled_aina_tasks", ["enabled", "next_run_at"], unique=False
    )
    op.create_index(
        "ix_unibot_scheduled_aina_tasks_owner", "unibot_scheduled_aina_tasks", ["tenant_id", "user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_unibot_scheduled_aina_tasks_owner", table_name="unibot_scheduled_aina_tasks")
    op.drop_index("ix_unibot_scheduled_aina_tasks_due", table_name="unibot_scheduled_aina_tasks")
    op.drop_index("ix_unibot_scheduled_aina_executions_task_started", table_name="unibot_scheduled_aina_executions")
    op.drop_index("ix_unibot_sandboxes_scope", table_name="unibot_sandboxes")
    op.drop_index("ix_unibot_sandbox_executions_sandbox", table_name="unibot_sandbox_executions")
    op.drop_index("ix_unibot_sandbox_executions_owner_started", table_name="unibot_sandbox_executions")
