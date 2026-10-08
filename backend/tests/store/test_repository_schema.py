from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import BaseModel

from tianzhou_agent_platform.aina.document.task_models import DocumentEditTask
from tianzhou_agent_platform.aina.memory.models import MemoryRecord
from tianzhou_agent_platform.aina.project import AinaProjectRecord
from tianzhou_agent_platform.aina.protocol.models import AinaInstallation, AinaRecord
from tianzhou_agent_platform.aina.scheduler import ScheduledAinaExecution, ScheduledAinaTask
from tianzhou_agent_platform.aina.skill.models import SkillRecord
from tianzhou_agent_platform.aina.tool.models import ToolRecord
from tianzhou_agent_platform.auth.models import UserRecord
from tianzhou_agent_platform.conversations.models import Conversation
from tianzhou_agent_platform.conversations.schemas import ApprovalRecord
from tianzhou_agent_platform.core.feedback import FeedbackRecord
from tianzhou_agent_platform.core.workspace import Workspace
from tianzhou_agent_platform.model_providers.models import ModelProviderRecord
from tianzhou_agent_platform.observability.models import LLMCallRecord, TraceRecord
from tianzhou_agent_platform.sandbox.models import SandboxExecution, SandboxRecord
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
from tianzhou_agent_platform.store.repository_schema import model_from_row, repository_tables, row_values

RECORD_MODELS: dict[str, type[BaseModel]] = {
    USERS_RESOURCE: UserRecord,
    WORKSPACES_RESOURCE: Workspace,
    CONVERSATIONS_RESOURCE: Conversation,
    APPROVALS_RESOURCE: ApprovalRecord,
    MEMORIES_RESOURCE: MemoryRecord,
    FEEDBACKS_RESOURCE: FeedbackRecord,
    MODEL_PROVIDERS_RESOURCE: ModelProviderRecord,
    TOOLS_RESOURCE: ToolRecord,
    SKILLS_RESOURCE: SkillRecord,
    AINAS_RESOURCE: AinaRecord,
    AINA_PROJECTS_RESOURCE: AinaProjectRecord,
    INSTALLATIONS_RESOURCE: AinaInstallation,
    SCHEDULED_AINA_TASKS_RESOURCE: ScheduledAinaTask,
    SCHEDULED_AINA_EXECUTIONS_RESOURCE: ScheduledAinaExecution,
    DOCUMENT_EDIT_TASKS_RESOURCE: DocumentEditTask,
    SANDBOXES_RESOURCE: SandboxRecord,
    SANDBOX_EXECUTIONS_RESOURCE: SandboxExecution,
    TRACES_RESOURCE: TraceRecord,
    LLM_CALLS_RESOURCE: LLMCallRecord,
}


def test_every_repository_table_has_a_record_model() -> None:
    assert RECORD_MODELS.keys() == repository_tables.keys()


@pytest.mark.parametrize(("resource", "model"), RECORD_MODELS.items())
def test_table_has_one_column_per_model_field(resource: str, model: type[BaseModel]) -> None:
    columns = set(repository_tables[resource].c.keys())

    assert columns - {"id"} == set(model.model_fields) - {"id"}


def test_row_round_trip_keeps_omitted_defaults_and_utc_datetimes() -> None:
    created = datetime(2026, 10, 8, 20, 30, 15, 123456, tzinfo=timezone(timedelta(hours=8)))
    approval = ApprovalRecord(
        id="approval_1",
        conversation_id="conv_1",
        user_id="user_1",
        tenant_id="tenant_1",
        trace_id="trace_1",
        tool_calls=[{"name": "search", "args": {}}],
        capability_names=["search"],
        created_at=created,
    )

    row = row_values(APPROVALS_RESOURCE, approval.id, approval)

    assert row["runtime_ref"] is None
    assert row["run_generation"] == 0
    assert row["created_at"] == created
    assert row["created_at"].tzinfo == UTC
    # MySQL returns DATETIME values without an offset.
    stored = {**row, "created_at": row["created_at"].replace(tzinfo=None)}
    restored = model_from_row(ApprovalRecord, StoreRecord(resource=APPROVALS_RESOURCE, id=approval.id, values=stored))
    assert restored == approval
    assert restored.created_at.tzinfo == UTC


def test_row_uses_record_id_for_models_without_an_id_field() -> None:
    call = LLMCallRecord(call_id="llm_1", endpoint="https://model.invalid/v1", model="m", request={})

    row = row_values(LLM_CALLS_RESOURCE, call.call_id, call)

    assert row["id"] == "llm_1"
    record = StoreRecord(resource=LLM_CALLS_RESOURCE, id=row.pop("id"), values=row)
    assert model_from_row(LLMCallRecord, record) == call
