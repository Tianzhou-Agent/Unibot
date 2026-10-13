"""Baseline: every application-owned table as of the first managed release.

Revision ID: 0001
Revises:
Create Date: 2026-10-13 00:08:14.124828
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "unibot_aina_projects",
        sa.Column("id", sa.String(length=160), nullable=False),
        sa.Column("user_id", sa.String(length=160), nullable=False),
        sa.Column("tenant_id", sa.String(length=160), nullable=False),
        sa.Column("source_filename", sa.String(length=255), nullable=False),
        sa.Column("archive_sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("uncompressed_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("file_count", sa.Integer(), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=False
        ),
        sa.Column(
            "deployed_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=True
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "unibot_ainas",
        sa.Column("id", sa.String(length=160), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "registered_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=False
        ),
        sa.Column("last_health", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "unibot_approvals",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("conversation_id", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.String(length=160), nullable=False),
        sa.Column("tenant_id", sa.String(length=160), nullable=False),
        sa.Column("trace_id", sa.String(length=255), nullable=False),
        sa.Column("tool_calls", sa.JSON(), nullable=False),
        sa.Column("capability_names", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=False
        ),
        sa.Column(
            "resolved_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=True
        ),
        sa.Column("runtime_ref", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("run_generation", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "unibot_conversations",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.String(length=160), nullable=False),
        sa.Column("tenant_id", sa.String(length=160), nullable=False),
        sa.Column("workspace_id", sa.String(length=255), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("run_status", sa.String(length=32), nullable=False),
        sa.Column("active_trace_id", sa.String(length=255), nullable=True),
        sa.Column("run_error", sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql"), nullable=True),
        sa.Column(
            "run_started_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=True
        ),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("enabled_ainas", sa.JSON(), nullable=False),
        sa.Column("active_aina_ids", sa.JSON(), nullable=False),
        sa.Column("primary_aina_id", sa.String(length=255), nullable=True),
        sa.Column("last_aina_id", sa.String(length=255), nullable=True),
        sa.Column("messages", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "unibot_document_edit_tasks",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("document_name", sa.String(length=512), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql"), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("base_revision", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.String(length=160), nullable=False),
        sa.Column("tenant_id", sa.String(length=160), nullable=False),
        sa.Column("workspace_id", sa.String(length=255), nullable=True),
        sa.Column("sections", sa.JSON(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql"), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=False
        ),
        sa.Column("merged_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=True),
        sa.Column(
            "abandoned_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=True
        ),
        sa.Column(
            "completed_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=True
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "unibot_feedbacks",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.String(length=160), nullable=False),
        sa.Column("tenant_id", sa.String(length=160), nullable=False),
        sa.Column("user_name", sa.String(length=255), nullable=False),
        sa.Column("user_email", sa.String(length=320), nullable=False),
        sa.Column("conversation_id", sa.String(length=255), nullable=False),
        sa.Column("message_id", sa.String(length=255), nullable=False),
        sa.Column("trace_id", sa.String(length=255), nullable=True),
        sa.Column("agent_name", sa.String(length=255), nullable=False),
        sa.Column("agent_version", sa.String(length=255), nullable=False),
        sa.Column("rating", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=100), nullable=False),
        sa.Column("comment", sa.String(length=500), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("case_status", sa.String(length=32), nullable=False),
        sa.Column("assignee", sa.String(length=255), nullable=False),
        sa.Column("conclusion", sa.Text(), nullable=False),
        sa.Column("history", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "unibot_installations",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=160), nullable=False),
        sa.Column("tenant_id", sa.String(length=160), nullable=False),
        sa.Column("granted_permissions", sa.JSON(), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("aina_id", sa.String(length=160), nullable=False),
        sa.Column("installed_version", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "installed_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "unibot_llm_calls",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("call_id", sa.String(length=255), nullable=False),
        sa.Column("trace_id", sa.String(length=255), nullable=True),
        sa.Column("span_id", sa.String(length=255), nullable=True),
        sa.Column("context_type", sa.String(length=64), nullable=True),
        sa.Column("context_id", sa.String(length=255), nullable=True),
        sa.Column("endpoint", sa.String(length=2083), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("response", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("duration_ms", sa.Double(), nullable=True),
        sa.Column(
            "first_token_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=True
        ),
        sa.Column("ttft_ms", sa.Double(), nullable=True),
        sa.Column("error", sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql"), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=False
        ),
        sa.Column(
            "completed_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=True
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "unibot_memories",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=160), nullable=False),
        sa.Column("tenant_id", sa.String(length=160), nullable=False),
        sa.Column("source_conversation_id", sa.String(length=255), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "unibot_model_providers",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.String(length=160), nullable=False),
        sa.Column("tenant_id", sa.String(length=160), nullable=False),
        sa.Column("provider_type", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("api_key", sa.String(length=1000), nullable=False),
        sa.Column("timeout_seconds", sa.Double(), nullable=False),
        sa.Column("models", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "unibot_sandbox_executions",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("sandbox_id", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.String(length=160), nullable=False),
        sa.Column("tenant_id", sa.String(length=160), nullable=False),
        sa.Column("workspace_id", sa.String(length=255), nullable=True),
        sa.Column("language", sa.String(length=32), nullable=False),
        sa.Column("script", sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql"), nullable=False),
        sa.Column("working_directory", sa.String(length=1024), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("stdout", sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql"), nullable=False),
        sa.Column("stderr", sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql"), nullable=False),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Double(), nullable=True),
        sa.Column("truncated", sa.Boolean(), nullable=False),
        sa.Column(
            "started_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=False
        ),
        sa.Column(
            "finished_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=True
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "unibot_sandboxes",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.String(length=160), nullable=False),
        sa.Column("tenant_id", sa.String(length=160), nullable=False),
        sa.Column("workspace_id", sa.String(length=255), nullable=True),
        sa.Column("workspace_storage_key", sa.String(length=160), nullable=True),
        sa.Column("image", sa.String(length=512), nullable=False),
        sa.Column("driver", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("runtime_name", sa.String(length=255), nullable=False),
        sa.Column("workspace", sa.String(length=1024), nullable=False),
        sa.Column("endpoint", sa.String(length=2083), nullable=True),
        sa.Column("last_error", sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql"), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=False
        ),
        sa.Column(
            "last_activity_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "unibot_scheduled_aina_executions",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("task_id", sa.String(length=255), nullable=False),
        sa.Column("aina_id", sa.String(length=160), nullable=False),
        sa.Column("user_id", sa.String(length=160), nullable=False),
        sa.Column("tenant_id", sa.String(length=160), nullable=False),
        sa.Column("trigger", sa.String(length=32), nullable=False),
        sa.Column(
            "scheduled_for", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=True
        ),
        sa.Column("call_id", sa.String(length=255), nullable=False),
        sa.Column("node_id", sa.String(length=255), nullable=False),
        sa.Column("input", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("result", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("error", sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql"), nullable=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=False
        ),
        sa.Column(
            "finished_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=True
        ),
        sa.Column("duration_ms", sa.Double(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "unibot_scheduled_aina_tasks",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("aina_id", sa.String(length=160), nullable=False),
        sa.Column("user_id", sa.String(length=160), nullable=False),
        sa.Column("tenant_id", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("schedule_type", sa.String(length=32), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("cron_expression", sa.String(length=255), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("prompt", sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql"), nullable=True),
        sa.Column("input", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "next_run_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=False
        ),
        sa.Column(
            "last_run_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=True
        ),
        sa.Column("last_status", sa.String(length=32), nullable=False),
        sa.Column("last_node_id", sa.String(length=255), nullable=True),
        sa.Column("last_error", sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql"), nullable=True),
        sa.Column("last_result", sa.JSON(none_as_null=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "unibot_skills",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("skill_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=False),
        sa.Column("version", sa.String(length=255), nullable=False),
        sa.Column("input_schema", sa.JSON(), nullable=False),
        sa.Column("output_schema", sa.JSON(), nullable=False),
        sa.Column("instructions", sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql"), nullable=False),
        sa.Column("tools", sa.JSON(), nullable=False),
        sa.Column("permissions", sa.JSON(), nullable=False),
        sa.Column("publisher", sa.String(length=255), nullable=False),
        sa.Column("visibility", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("owner_user_id", sa.String(length=160), nullable=True),
        sa.Column("tenant_id", sa.String(length=160), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "unibot_tools",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("tool_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=False),
        sa.Column("version", sa.String(length=255), nullable=False),
        sa.Column("input_schema", sa.JSON(), nullable=False),
        sa.Column("output_schema", sa.JSON(), nullable=False),
        sa.Column("endpoint", sa.String(length=2083), nullable=False),
        sa.Column("authentication", sa.JSON(), nullable=False),
        sa.Column("timeout_seconds", sa.Double(), nullable=False),
        sa.Column("retries", sa.Integer(), nullable=False),
        sa.Column("side_effect_level", sa.String(length=32), nullable=False),
        sa.Column("permissions", sa.JSON(), nullable=False),
        sa.Column("visibility", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("owner_user_id", sa.String(length=160), nullable=True),
        sa.Column("tenant_id", sa.String(length=160), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "unibot_traces",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("trace_id", sa.String(length=255), nullable=False),
        sa.Column("root_span_id", sa.String(length=255), nullable=True),
        sa.Column("conversation_id", sa.String(length=255), nullable=True),
        sa.Column("user_id", sa.String(length=160), nullable=False),
        sa.Column("tenant_id", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("events", sa.JSON(), nullable=False),
        sa.Column("spans", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=False
        ),
        sa.Column(
            "completed_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=True
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "unibot_users",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("github_id", sa.String(length=64), nullable=True),
        sa.Column("github_login", sa.String(length=255), nullable=True),
        sa.Column("avatar_url", sa.String(length=2083), nullable=True),
        sa.Column("tenant_id", sa.String(length=160), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "unibot_workspaces",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.String(length=160), nullable=False),
        sa.Column("tenant_id", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=False),
        sa.Column("storage_key", sa.String(length=160), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "unibot_graph_checkpoint_writes",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("thread_id", sa.String(length=64), nullable=False),
        sa.Column("checkpoint_ns", sa.String(length=255), nullable=False),
        sa.Column("checkpoint_id", sa.String(length=128), nullable=False),
        sa.Column("task_id", sa.String(length=255), nullable=False),
        sa.Column("task_path", sa.Text(), nullable=False),
        sa.Column("write_idx", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(length=255), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_unibot_graph_write_checkpoint",
        "unibot_graph_checkpoint_writes",
        ["thread_id", "checkpoint_ns", "checkpoint_id"],
        unique=False,
    )
    op.create_table(
        "unibot_graph_checkpoints",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("thread_id", sa.String(length=64), nullable=False),
        sa.Column("checkpoint_ns", sa.String(length=255), nullable=False),
        sa.Column("checkpoint_id", sa.String(length=128), nullable=False),
        sa.Column("parent_checkpoint_id", sa.String(length=128), nullable=True),
        sa.Column("checkpoint", sa.JSON(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_unibot_graph_checkpoint_thread", "unibot_graph_checkpoints", ["thread_id", "checkpoint_ns"], unique=False
    )
    op.create_table(
        "unibot_obs_events",
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("span_id", sa.String(length=32), nullable=True),
        sa.Column("session_id", sa.String(length=160), nullable=True),
        sa.Column("user_id", sa.String(length=160), nullable=False),
        sa.Column("tenant_id", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("record_version", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("sequence_no", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("attributes", sa.JSON(none_as_null=True), nullable=True),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index(
        "idx_obs_events_tenant_user_time", "unibot_obs_events", ["tenant_id", "user_id", "occurred_at"], unique=False
    )
    op.create_index("idx_obs_events_trace_time", "unibot_obs_events", ["trace_id", "occurred_at"], unique=False)
    op.create_table(
        "unibot_obs_spans",
        sa.Column("span_id", sa.String(length=32), nullable=False),
        sa.Column("legacy_span_id", sa.String(length=64), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("parent_span_id", sa.String(length=32), nullable=True),
        sa.Column("sequence_no", sa.BigInteger(), nullable=False),
        sa.Column("session_id", sa.String(length=160), nullable=True),
        sa.Column("user_id", sa.String(length=160), nullable=False),
        sa.Column("tenant_id", sa.String(length=160), nullable=False),
        sa.Column("producer_instance_id", sa.String(length=255), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("target_id", sa.String(length=255), nullable=True),
        sa.Column("target_version", sa.String(length=255), nullable=True),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_output_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Double(), nullable=True),
        sa.Column("ttft_ms", sa.Double(), nullable=True),
        sa.Column("input_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("cache_read_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("input_preview", sa.Text(), nullable=True),
        sa.Column("output_preview", sa.Text(), nullable=True),
        sa.Column("record_version", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("attributes", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("error", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("raw_io_path", sa.String(length=1024), nullable=True),
        sa.Column("raw_io_sha256", sa.String(length=64), nullable=True),
        sa.Column("raw_io_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("raw_io_status", sa.String(length=32), server_default="not_applicable", nullable=False),
        sa.PrimaryKeyConstraint("span_id"),
    )
    op.create_index("idx_obs_spans_kind_model_time", "unibot_obs_spans", ["kind", "model", "started_at"], unique=False)
    op.create_index("idx_obs_spans_legacy_id", "unibot_obs_spans", ["legacy_span_id"], unique=True)
    op.create_index("idx_obs_spans_status_time", "unibot_obs_spans", ["status", "started_at"], unique=False)
    op.create_index(
        "idx_obs_spans_tenant_session_time", "unibot_obs_spans", ["tenant_id", "session_id", "started_at"], unique=False
    )
    op.create_index(
        "idx_obs_spans_tenant_user_time", "unibot_obs_spans", ["tenant_id", "user_id", "started_at"], unique=False
    )
    op.create_index("idx_obs_spans_trace_parent", "unibot_obs_spans", ["trace_id", "parent_span_id"], unique=False)
    op.create_index("idx_obs_spans_trace_seq", "unibot_obs_spans", ["trace_id", "sequence_no"], unique=False)
    op.create_table(
        "unibot_obs_traces",
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("legacy_trace_id", sa.String(length=64), nullable=True),
        sa.Column("root_span_id", sa.String(length=32), nullable=True),
        sa.Column("session_id", sa.String(length=160), nullable=True),
        sa.Column("user_id", sa.String(length=160), nullable=False),
        sa.Column("tenant_id", sa.String(length=160), nullable=False),
        sa.Column("producer_instance_id", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Double(), nullable=True),
        sa.Column("input_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("cache_read_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("message_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("compression_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("record_version", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("attributes", sa.JSON(none_as_null=True), nullable=True),
        sa.PrimaryKeyConstraint("trace_id"),
    )
    op.create_index("idx_obs_traces_legacy_id", "unibot_obs_traces", ["legacy_trace_id"], unique=True)
    op.create_index("idx_obs_traces_status_time", "unibot_obs_traces", ["status", "started_at"], unique=False)
    op.create_index(
        "idx_obs_traces_tenant_session_time",
        "unibot_obs_traces",
        ["tenant_id", "session_id", "started_at"],
        unique=False,
    )
    op.create_index(
        "idx_obs_traces_tenant_user_time", "unibot_obs_traces", ["tenant_id", "user_id", "started_at"], unique=False
    )
    op.create_table(
        "unibot_ops_first_use",
        sa.Column("tenant_id", sa.String(length=160), nullable=False),
        sa.Column("user_id", sa.String(length=160), nullable=False),
        sa.Column("scope_type", sa.String(length=16), nullable=False),
        sa.Column("scope_id", sa.String(length=255), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("agent_version", sa.String(length=255), nullable=True),
        sa.Column("first_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "user_id", "scope_type", "scope_id"),
    )
    op.create_index(
        "idx_ops_first_use_tenant_scope_time",
        "unibot_ops_first_use",
        ["tenant_id", "scope_type", "scope_id", "first_at"],
        unique=False,
    )
    op.create_table(
        "unibot_ops_request_agents",
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("agent_id", sa.String(length=255), nullable=False),
        sa.Column("agent_version", sa.String(length=255), nullable=True),
        sa.Column("user_id", sa.String(length=160), nullable=False),
        sa.Column("tenant_id", sa.String(length=160), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("record_version", sa.BigInteger(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("trace_id", "agent_id"),
    )
    op.create_index(
        "idx_ops_request_agents_tenant_agent_time",
        "unibot_ops_request_agents",
        ["tenant_id", "agent_id", "used_at"],
        unique=False,
    )
    op.create_index(
        "idx_ops_request_agents_tenant_user_time",
        "unibot_ops_request_agents",
        ["tenant_id", "user_id", "used_at"],
        unique=False,
    )
    op.create_table(
        "unibot_ops_user_events",
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.String(length=160), nullable=True),
        sa.Column("user_id", sa.String(length=160), nullable=False),
        sa.Column("tenant_id", sa.String(length=160), nullable=False),
        sa.Column("source", sa.String(length=32), server_default="chat", nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("record_version", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("metric_version", sa.String(length=32), server_default="v1", nullable=False),
        sa.PrimaryKeyConstraint("trace_id"),
    )
    op.create_index("idx_ops_events_status_time", "unibot_ops_user_events", ["status", "started_at"], unique=False)
    op.create_index("idx_ops_events_tenant_time", "unibot_ops_user_events", ["tenant_id", "started_at"], unique=False)
    op.create_index(
        "idx_ops_events_tenant_user_time",
        "unibot_ops_user_events",
        ["tenant_id", "user_id", "started_at"],
        unique=False,
    )
    op.create_table(
        "session_task_meta",
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("session_id"),
    )
    op.create_table(
        "session_tasks",
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("verification_status", sa.String(length=20), nullable=False),
        sa.Column("verification_reason", sa.Text(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("parent_task_id", sa.String(length=36), nullable=True),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("task_id"),
        sa.UniqueConstraint("session_id", "idempotency_key", name="uq_session_tasks_session_idempotency"),
    )
    op.create_index(
        "ix_session_tasks_session_parent_sort",
        "session_tasks",
        ["session_id", "parent_task_id", "sort_order"],
        unique=False,
    )
    op.create_table(
        "storage_runtime_probe",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("storage_runtime_probe")
    op.drop_index("ix_session_tasks_session_parent_sort", table_name="session_tasks")
    op.drop_table("session_tasks")
    op.drop_table("session_task_meta")
    op.drop_index("idx_ops_events_tenant_user_time", table_name="unibot_ops_user_events")
    op.drop_index("idx_ops_events_tenant_time", table_name="unibot_ops_user_events")
    op.drop_index("idx_ops_events_status_time", table_name="unibot_ops_user_events")
    op.drop_table("unibot_ops_user_events")
    op.drop_index("idx_ops_request_agents_tenant_user_time", table_name="unibot_ops_request_agents")
    op.drop_index("idx_ops_request_agents_tenant_agent_time", table_name="unibot_ops_request_agents")
    op.drop_table("unibot_ops_request_agents")
    op.drop_index("idx_ops_first_use_tenant_scope_time", table_name="unibot_ops_first_use")
    op.drop_table("unibot_ops_first_use")
    op.drop_index("idx_obs_traces_tenant_user_time", table_name="unibot_obs_traces")
    op.drop_index("idx_obs_traces_tenant_session_time", table_name="unibot_obs_traces")
    op.drop_index("idx_obs_traces_status_time", table_name="unibot_obs_traces")
    op.drop_index("idx_obs_traces_legacy_id", table_name="unibot_obs_traces")
    op.drop_table("unibot_obs_traces")
    op.drop_index("idx_obs_spans_trace_seq", table_name="unibot_obs_spans")
    op.drop_index("idx_obs_spans_trace_parent", table_name="unibot_obs_spans")
    op.drop_index("idx_obs_spans_tenant_user_time", table_name="unibot_obs_spans")
    op.drop_index("idx_obs_spans_tenant_session_time", table_name="unibot_obs_spans")
    op.drop_index("idx_obs_spans_status_time", table_name="unibot_obs_spans")
    op.drop_index("idx_obs_spans_legacy_id", table_name="unibot_obs_spans")
    op.drop_index("idx_obs_spans_kind_model_time", table_name="unibot_obs_spans")
    op.drop_table("unibot_obs_spans")
    op.drop_index("idx_obs_events_trace_time", table_name="unibot_obs_events")
    op.drop_index("idx_obs_events_tenant_user_time", table_name="unibot_obs_events")
    op.drop_table("unibot_obs_events")
    op.drop_index("ix_unibot_graph_checkpoint_thread", table_name="unibot_graph_checkpoints")
    op.drop_table("unibot_graph_checkpoints")
    op.drop_index("ix_unibot_graph_write_checkpoint", table_name="unibot_graph_checkpoint_writes")
    op.drop_table("unibot_graph_checkpoint_writes")
    op.drop_table("unibot_workspaces")
    op.drop_table("unibot_users")
    op.drop_table("unibot_traces")
    op.drop_table("unibot_tools")
    op.drop_table("unibot_skills")
    op.drop_table("unibot_scheduled_aina_tasks")
    op.drop_table("unibot_scheduled_aina_executions")
    op.drop_table("unibot_sandboxes")
    op.drop_table("unibot_sandbox_executions")
    op.drop_table("unibot_model_providers")
    op.drop_table("unibot_memories")
    op.drop_table("unibot_llm_calls")
    op.drop_table("unibot_installations")
    op.drop_table("unibot_feedbacks")
    op.drop_table("unibot_document_edit_tasks")
    op.drop_table("unibot_conversations")
    op.drop_table("unibot_approvals")
    op.drop_table("unibot_ainas")
    op.drop_table("unibot_aina_projects")
