from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from tianzhou_agent_platform.core.base import StrictModel, utc_now

TaskStatus = Literal["pending", "in_progress", "verifying", "completed", "skipped", "failed"]
WritableTaskStatus = Literal["pending", "in_progress", "verifying", "skipped", "failed"]
VerificationStatus = Literal["none", "pending", "passed", "failed", "error"]


class SessionTask(StrictModel):
    task_id: str
    session_id: str
    owner_user_id: str
    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    status: TaskStatus = "pending"
    reason: str = Field(default="", max_length=500)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    verification_status: VerificationStatus = "none"
    verification_reason: str = ""
    verified_at: datetime | None = None
    parent_task_id: str | None = None
    depth: int = Field(default=0, ge=0, le=2)
    sort_order: int = Field(default=0, ge=0)
    version: int = Field(default=1, ge=1)
    idempotency_key: str | None = Field(default=None, max_length=200)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class TaskNode(SessionTask):
    children: list[TaskNode] = Field(default_factory=list)


class TaskTreeSnapshot(StrictModel):
    session_id: str
    revision: int = Field(default=0, ge=0)
    tasks: list[TaskNode] = Field(default_factory=list)


class TaskCreateItem(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    parent_task_id: str | None = None
    client_ref: str | None = Field(default=None, min_length=1, max_length=80)
    parent_ref: str | None = Field(default=None, min_length=1, max_length=80)

    @model_validator(mode="after")
    def validate_parent_reference(self) -> TaskCreateItem:
        if self.parent_task_id and self.parent_ref:
            raise ValueError("parent_task_id and parent_ref cannot both be provided")
        return self


class TaskCreateRequest(StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str = ""
    parent_task_id: str | None = None
    client_ref: str | None = Field(default=None, min_length=1, max_length=80)
    parent_ref: str | None = Field(default=None, min_length=1, max_length=80)
    tasks: list[TaskCreateItem] | None = Field(default=None, min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_shape(self) -> TaskCreateRequest:
        if self.tasks is not None:
            if self.title is not None or self.parent_task_id is not None or self.parent_ref is not None:
                raise ValueError("Use either tasks or the single-task fields, not both")
            return self
        if self.title is None:
            raise ValueError("title is required when tasks is not provided")
        if self.parent_task_id and self.parent_ref:
            raise ValueError("parent_task_id and parent_ref cannot both be provided")
        return self

    def items(self) -> list[TaskCreateItem]:
        if self.tasks is not None:
            return self.tasks
        return [
            TaskCreateItem(
                title=self.title or "",
                description=self.description,
                parent_task_id=self.parent_task_id,
                client_ref=self.client_ref,
                parent_ref=self.parent_ref,
            )
        ]


class TaskUpdateRequest(StrictModel):
    task_id: str = Field(min_length=1)
    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    parent_task_id: str | None = None
    status: WritableTaskStatus | None = None
    reason: str | None = Field(default=None, max_length=500)
    evidence: list[dict[str, Any]] | None = None

    @model_validator(mode="after")
    def require_change(self) -> TaskUpdateRequest:
        if not (self.model_fields_set - {"task_id", "expected_version"}):
            raise ValueError("At least one task field must be updated")
        return self


class TaskDeleteRequest(StrictModel):
    task_ids: list[str] = Field(min_length=1, max_length=20)


class TaskMutationResponse(StrictModel):
    affected_task_ids: list[str]
    snapshot: TaskTreeSnapshot


class GateResult(StrictModel):
    status: Literal["passed", "failed", "error"]
    reason: str = ""
