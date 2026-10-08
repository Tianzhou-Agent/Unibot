"""Column-per-field MySQL schema for the business records of ``PersistentRepository``.

Every table mirrors one Pydantic record model: each top-level model field is a column, nested models and
collections are JSON columns. ``id`` is the repository record id; it is the model's own ``id`` field where the
model has one (tools, skills, AINAs, installations, traces and LLM calls key on another value).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import JSON, BigInteger, Boolean, Column, DateTime, Double, Integer, MetaData, String, Table, Text
from sqlalchemy.dialects import mysql

from tianzhou_agent_platform.store.memory_repository import (
    AINA_PROJECTS_RESOURCE,
    AINAS_RESOURCE,
    APPROVALS_RESOURCE,
    CONVERSATIONS_RESOURCE,
    DOCUMENT_EDIT_TASKS_RESOURCE,
    FEEDBACKS_RESOURCE,
    INSTALLATIONS_RESOURCE,
    LLM_CALLS_RESOURCE,
    MEMORIES_RESOURCE,
    MODEL_PROVIDERS_RESOURCE,
    SANDBOX_EXECUTIONS_RESOURCE,
    SANDBOXES_RESOURCE,
    SCHEDULED_AINA_EXECUTIONS_RESOURCE,
    SCHEDULED_AINA_TASKS_RESOURCE,
    SKILLS_RESOURCE,
    TOOLS_RESOURCE,
    TRACES_RESOURCE,
    USERS_RESOURCE,
    WORKSPACES_RESOURCE,
)
from tianzhou_agent_platform.store.models import StoreRecord

repository_metadata = MetaData()

ID = String(255)
ACTOR = String(160)
STATUS = String(32)
URL = String(2083)
# Microsecond precision: MySQL DATETIME defaults to whole seconds, which would reorder records and change
# values (e.g. scheduler claim ids built from next_run_at) after a reload.
TIMESTAMP = DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql")
LONG_TEXT = Text().with_variant(mysql.MEDIUMTEXT(), "mysql")
NULLABLE_JSON = JSON(none_as_null=True)


def _table(resource: str, *columns: Column[Any]) -> Table:
    return Table(f"unibot_{resource}", repository_metadata, *columns)


users_table = _table(
    USERS_RESOURCE,
    Column("id", ID, primary_key=True),
    Column("email", String(320), nullable=False),
    Column("name", String(80), nullable=False),
    Column("password_hash", String(255), nullable=True),
    Column("github_id", String(64), nullable=True),
    Column("github_login", String(255), nullable=True),
    Column("avatar_url", URL, nullable=True),
    Column("tenant_id", ACTOR, nullable=False),
    Column("created_at", TIMESTAMP, nullable=False),
    Column("updated_at", TIMESTAMP, nullable=False),
)

workspaces_table = _table(
    WORKSPACES_RESOURCE,
    Column("id", ID, primary_key=True),
    Column("user_id", ACTOR, nullable=False),
    Column("tenant_id", ACTOR, nullable=False),
    Column("name", String(160), nullable=False),
    Column("description", String(2000), nullable=False),
    Column("storage_key", String(160), nullable=False),
    Column("created_at", TIMESTAMP, nullable=False),
    Column("updated_at", TIMESTAMP, nullable=False),
)

conversations_table = _table(
    CONVERSATIONS_RESOURCE,
    Column("id", ID, primary_key=True),
    Column("user_id", ACTOR, nullable=False),
    Column("tenant_id", ACTOR, nullable=False),
    Column("workspace_id", ID, nullable=True),
    Column("title", Text, nullable=False),
    Column("category", String(40), nullable=False),
    Column("status", STATUS, nullable=False),
    Column("run_status", STATUS, nullable=False),
    Column("active_trace_id", ID, nullable=True),
    Column("run_error", LONG_TEXT, nullable=True),
    Column("run_started_at", TIMESTAMP, nullable=True),
    Column("config", JSON, nullable=False),
    Column("enabled_ainas", JSON, nullable=False),
    Column("active_aina_ids", JSON, nullable=False),
    Column("primary_aina_id", ID, nullable=True),
    Column("last_aina_id", ID, nullable=True),
    Column("messages", JSON, nullable=False),
    Column("created_at", TIMESTAMP, nullable=False),
    Column("updated_at", TIMESTAMP, nullable=False),
)

approvals_table = _table(
    APPROVALS_RESOURCE,
    Column("id", ID, primary_key=True),
    Column("conversation_id", ID, nullable=False),
    Column("user_id", ACTOR, nullable=False),
    Column("tenant_id", ACTOR, nullable=False),
    Column("trace_id", ID, nullable=False),
    Column("tool_calls", JSON, nullable=False),
    Column("capability_names", JSON, nullable=False),
    Column("status", STATUS, nullable=False),
    Column("created_at", TIMESTAMP, nullable=False),
    Column("resolved_at", TIMESTAMP, nullable=True),
    Column("runtime_ref", NULLABLE_JSON, nullable=True),
    Column("run_generation", Integer, nullable=False),
)

memories_table = _table(
    MEMORIES_RESOURCE,
    Column("id", ID, primary_key=True),
    Column("content", Text, nullable=False),
    Column("category", STATUS, nullable=False),
    Column("user_id", ACTOR, nullable=False),
    Column("tenant_id", ACTOR, nullable=False),
    Column("source_conversation_id", ID, nullable=True),
    Column("metadata", JSON, nullable=False),
    Column("created_at", TIMESTAMP, nullable=False),
    Column("updated_at", TIMESTAMP, nullable=False),
)

feedbacks_table = _table(
    FEEDBACKS_RESOURCE,
    Column("id", ID, primary_key=True),
    Column("user_id", ACTOR, nullable=False),
    Column("tenant_id", ACTOR, nullable=False),
    Column("user_name", String(255), nullable=False),
    Column("user_email", String(320), nullable=False),
    Column("conversation_id", ID, nullable=False),
    Column("message_id", ID, nullable=False),
    Column("trace_id", ID, nullable=True),
    Column("agent_name", String(255), nullable=False),
    Column("agent_version", String(255), nullable=False),
    Column("rating", STATUS, nullable=False),
    Column("reason", String(100), nullable=False),
    Column("comment", String(500), nullable=False),
    Column("active", Boolean, nullable=False),
    Column("case_status", STATUS, nullable=False),
    Column("assignee", String(255), nullable=False),
    Column("conclusion", Text, nullable=False),
    Column("history", JSON, nullable=False),
    Column("created_at", TIMESTAMP, nullable=False),
    Column("updated_at", TIMESTAMP, nullable=False),
)

model_providers_table = _table(
    MODEL_PROVIDERS_RESOURCE,
    Column("id", ID, primary_key=True),
    Column("user_id", ACTOR, nullable=False),
    Column("tenant_id", ACTOR, nullable=False),
    Column("provider_type", STATUS, nullable=False),
    Column("name", String(100), nullable=False),
    Column("base_url", String(500), nullable=False),
    Column("api_key", String(1000), nullable=False),
    Column("timeout_seconds", Double, nullable=False),
    Column("models", JSON, nullable=False),
    Column("created_at", TIMESTAMP, nullable=False),
    Column("updated_at", TIMESTAMP, nullable=False),
)

tools_table = _table(
    TOOLS_RESOURCE,
    Column("id", ID, primary_key=True),
    Column("tool_id", String(128), nullable=False),
    Column("name", String(128), nullable=False),
    Column("description", String(2000), nullable=False),
    Column("version", String(255), nullable=False),
    Column("input_schema", JSON, nullable=False),
    Column("output_schema", JSON, nullable=False),
    Column("endpoint", URL, nullable=False),
    Column("authentication", JSON, nullable=False),
    Column("timeout_seconds", Double, nullable=False),
    Column("retries", Integer, nullable=False),
    Column("side_effect_level", STATUS, nullable=False),
    Column("permissions", JSON, nullable=False),
    Column("visibility", STATUS, nullable=False),
    Column("status", STATUS, nullable=False),
    Column("owner_user_id", ACTOR, nullable=True),
    Column("tenant_id", ACTOR, nullable=True),
    Column("created_at", TIMESTAMP, nullable=False),
)

skills_table = _table(
    SKILLS_RESOURCE,
    Column("id", ID, primary_key=True),
    Column("skill_id", String(128), nullable=False),
    Column("name", String(128), nullable=False),
    Column("description", String(2000), nullable=False),
    Column("version", String(255), nullable=False),
    Column("input_schema", JSON, nullable=False),
    Column("output_schema", JSON, nullable=False),
    Column("instructions", LONG_TEXT, nullable=False),
    Column("tools", JSON, nullable=False),
    Column("permissions", JSON, nullable=False),
    Column("publisher", String(255), nullable=False),
    Column("visibility", STATUS, nullable=False),
    Column("status", STATUS, nullable=False),
    Column("owner_user_id", ACTOR, nullable=True),
    Column("tenant_id", ACTOR, nullable=True),
    Column("created_at", TIMESTAMP, nullable=False),
)

ainas_table = _table(
    AINAS_RESOURCE,
    Column("id", String(160), primary_key=True),  # manifest.aina.id
    Column("manifest", JSON, nullable=False),
    Column("status", STATUS, nullable=False),
    Column("registered_at", TIMESTAMP, nullable=False),
    Column("last_health", JSON, nullable=False),
)

aina_projects_table = _table(
    AINA_PROJECTS_RESOURCE,
    Column("id", String(160), primary_key=True),
    Column("user_id", ACTOR, nullable=False),
    Column("tenant_id", ACTOR, nullable=False),
    Column("source_filename", String(255), nullable=False),
    Column("archive_sha256", String(64), nullable=False),
    Column("size_bytes", BigInteger, nullable=False),
    Column("uncompressed_size_bytes", BigInteger, nullable=False),
    Column("file_count", Integer, nullable=False),
    Column("manifest", JSON, nullable=False),
    Column("status", STATUS, nullable=False),
    Column("created_at", TIMESTAMP, nullable=False),
    Column("updated_at", TIMESTAMP, nullable=False),
    Column("deployed_at", TIMESTAMP, nullable=True),
)

installations_table = _table(
    INSTALLATIONS_RESOURCE,
    Column("id", String(64), primary_key=True),  # sha256 of (tenant_id, user_id, aina_id)
    Column("user_id", ACTOR, nullable=False),
    Column("tenant_id", ACTOR, nullable=False),
    Column("granted_permissions", JSON, nullable=False),
    Column("configuration", JSON, nullable=False),
    Column("aina_id", String(160), nullable=False),
    Column("installed_version", String(255), nullable=False),
    Column("status", STATUS, nullable=False),
    Column("installed_at", TIMESTAMP, nullable=False),
)

scheduled_aina_tasks_table = _table(
    SCHEDULED_AINA_TASKS_RESOURCE,
    Column("id", ID, primary_key=True),
    Column("aina_id", String(160), nullable=False),
    Column("user_id", ACTOR, nullable=False),
    Column("tenant_id", ACTOR, nullable=False),
    Column("name", String(160), nullable=False),
    Column("schedule_type", STATUS, nullable=False),
    Column("interval_seconds", Integer, nullable=False),
    Column("cron_expression", String(255), nullable=True),
    Column("timezone", String(64), nullable=False),
    Column("prompt", LONG_TEXT, nullable=True),
    Column("input", JSON, nullable=False),
    Column("enabled", Boolean, nullable=False),
    Column("next_run_at", TIMESTAMP, nullable=False),
    Column("last_run_at", TIMESTAMP, nullable=True),
    Column("last_status", STATUS, nullable=False),
    Column("last_node_id", String(255), nullable=True),
    Column("last_error", LONG_TEXT, nullable=True),
    Column("last_result", NULLABLE_JSON, nullable=True),
    Column("created_at", TIMESTAMP, nullable=False),
    Column("updated_at", TIMESTAMP, nullable=False),
)

scheduled_aina_executions_table = _table(
    SCHEDULED_AINA_EXECUTIONS_RESOURCE,
    Column("id", ID, primary_key=True),
    Column("task_id", ID, nullable=False),
    Column("aina_id", String(160), nullable=False),
    Column("user_id", ACTOR, nullable=False),
    Column("tenant_id", ACTOR, nullable=False),
    Column("trigger", STATUS, nullable=False),
    Column("scheduled_for", TIMESTAMP, nullable=True),
    Column("call_id", ID, nullable=False),
    Column("node_id", String(255), nullable=False),
    Column("input", JSON, nullable=False),
    Column("status", STATUS, nullable=False),
    Column("result", NULLABLE_JSON, nullable=True),
    Column("error", LONG_TEXT, nullable=True),
    Column("started_at", TIMESTAMP, nullable=False),
    Column("finished_at", TIMESTAMP, nullable=True),
    Column("duration_ms", Double, nullable=True),
)

document_edit_tasks_table = _table(
    DOCUMENT_EDIT_TASKS_RESOURCE,
    Column("id", ID, primary_key=True),
    Column("document_name", String(512), nullable=False),
    Column("title", Text, nullable=False),
    Column("description", LONG_TEXT, nullable=False),
    Column("status", STATUS, nullable=False),
    Column("base_revision", String(255), nullable=False),
    Column("user_id", ACTOR, nullable=False),
    Column("tenant_id", ACTOR, nullable=False),
    Column("workspace_id", ID, nullable=True),
    Column("sections", JSON, nullable=False),
    Column("attempt_count", Integer, nullable=False),
    Column("version", Integer, nullable=False),
    Column("error", LONG_TEXT, nullable=True),
    Column("created_at", TIMESTAMP, nullable=False),
    Column("updated_at", TIMESTAMP, nullable=False),
    Column("merged_at", TIMESTAMP, nullable=True),
    Column("abandoned_at", TIMESTAMP, nullable=True),
    Column("completed_at", TIMESTAMP, nullable=True),
    Column("deleted_at", TIMESTAMP, nullable=True),
)

sandboxes_table = _table(
    SANDBOXES_RESOURCE,
    Column("id", ID, primary_key=True),
    Column("user_id", ACTOR, nullable=False),
    Column("tenant_id", ACTOR, nullable=False),
    Column("workspace_id", ID, nullable=True),
    Column("workspace_storage_key", String(160), nullable=True),
    Column("image", String(512), nullable=False),
    Column("driver", STATUS, nullable=False),
    Column("status", STATUS, nullable=False),
    Column("runtime_name", String(255), nullable=False),
    Column("workspace", String(1024), nullable=False),
    Column("endpoint", URL, nullable=True),
    Column("last_error", LONG_TEXT, nullable=True),
    Column("created_at", TIMESTAMP, nullable=False),
    Column("updated_at", TIMESTAMP, nullable=False),
    Column("last_activity_at", TIMESTAMP, nullable=False),
)

sandbox_executions_table = _table(
    SANDBOX_EXECUTIONS_RESOURCE,
    Column("id", ID, primary_key=True),
    Column("sandbox_id", ID, nullable=False),
    Column("user_id", ACTOR, nullable=False),
    Column("tenant_id", ACTOR, nullable=False),
    Column("workspace_id", ID, nullable=True),
    Column("language", STATUS, nullable=False),
    Column("script", LONG_TEXT, nullable=False),
    Column("working_directory", String(1024), nullable=False),
    Column("status", STATUS, nullable=False),
    Column("stdout", LONG_TEXT, nullable=False),
    Column("stderr", LONG_TEXT, nullable=False),
    Column("exit_code", Integer, nullable=True),
    Column("duration_ms", Double, nullable=True),
    Column("truncated", Boolean, nullable=False),
    Column("started_at", TIMESTAMP, nullable=False),
    Column("finished_at", TIMESTAMP, nullable=True),
)

# Legacy observability records, written only while the OBS pipeline is disabled.
traces_table = _table(
    TRACES_RESOURCE,
    Column("id", ID, primary_key=True),  # trace_id
    Column("trace_id", ID, nullable=False),
    Column("root_span_id", ID, nullable=True),
    Column("conversation_id", ID, nullable=True),
    Column("user_id", ACTOR, nullable=False),
    Column("tenant_id", ACTOR, nullable=False),
    Column("status", STATUS, nullable=False),
    Column("events", JSON, nullable=False),
    Column("spans", JSON, nullable=False),
    Column("created_at", TIMESTAMP, nullable=False),
    Column("completed_at", TIMESTAMP, nullable=True),
)

llm_calls_table = _table(
    LLM_CALLS_RESOURCE,
    Column("id", ID, primary_key=True),  # call_id
    Column("call_id", ID, nullable=False),
    Column("trace_id", ID, nullable=True),
    Column("span_id", ID, nullable=True),
    Column("context_type", String(64), nullable=True),
    Column("context_id", ID, nullable=True),
    Column("endpoint", URL, nullable=False),
    Column("model", String(255), nullable=False),
    Column("status", STATUS, nullable=False),
    Column("request", JSON, nullable=False),
    Column("response", NULLABLE_JSON, nullable=True),
    Column("duration_ms", Double, nullable=True),
    Column("first_token_at", TIMESTAMP, nullable=True),
    Column("ttft_ms", Double, nullable=True),
    Column("error", LONG_TEXT, nullable=True),
    Column("created_at", TIMESTAMP, nullable=False),
    Column("completed_at", TIMESTAMP, nullable=True),
)

repository_tables: dict[str, Table] = {
    WORKSPACES_RESOURCE: workspaces_table,
    CONVERSATIONS_RESOURCE: conversations_table,
    MEMORIES_RESOURCE: memories_table,
    TOOLS_RESOURCE: tools_table,
    SKILLS_RESOURCE: skills_table,
    AINAS_RESOURCE: ainas_table,
    AINA_PROJECTS_RESOURCE: aina_projects_table,
    INSTALLATIONS_RESOURCE: installations_table,
    LLM_CALLS_RESOURCE: llm_calls_table,
    TRACES_RESOURCE: traces_table,
    APPROVALS_RESOURCE: approvals_table,
    MODEL_PROVIDERS_RESOURCE: model_providers_table,
    SCHEDULED_AINA_TASKS_RESOURCE: scheduled_aina_tasks_table,
    SCHEDULED_AINA_EXECUTIONS_RESOURCE: scheduled_aina_executions_table,
    DOCUMENT_EDIT_TASKS_RESOURCE: document_edit_tasks_table,
    SANDBOXES_RESOURCE: sandboxes_table,
    SANDBOX_EXECUTIONS_RESOURCE: sandbox_executions_table,
    USERS_RESOURCE: users_table,
    FEEDBACKS_RESOURCE: feedbacks_table,
}


def row_values(resource: str, record_id: str, value: BaseModel) -> dict[str, Any]:
    """Column values for ``value``; datetimes are stored as UTC because MySQL DATETIME keeps no offset."""
    data = value.model_dump(mode="json")
    row: dict[str, Any] = {"id": record_id}
    for column in repository_tables[resource].columns:
        if column.name == "id":
            continue
        # Fields serialized with exclude_if are omitted at their defaults; store the default itself.
        field_value = data[column.name] if column.name in data else getattr(value, column.name)
        if isinstance(column.type, DateTime) and field_value is not None:
            field_value = datetime.fromisoformat(field_value).astimezone(UTC)
        row[column.name] = field_value
    return row


def model_from_row[ModelT: BaseModel](model: type[ModelT], record: StoreRecord) -> ModelT:
    """Rebuild a record model from its row, restoring the UTC offset MySQL drops from datetimes."""
    data = {
        name: value.replace(tzinfo=UTC) if isinstance(value, datetime) and value.tzinfo is None else value
        for name, value in {"id": record.id, **record.values}.items()
        if name in model.model_fields
    }
    return model.model_validate(data)
