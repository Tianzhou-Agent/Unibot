from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import uuid4

from pydantic import Field, field_validator

from tianzhou_agent_platform.core.base import StrictModel, utc_now

DocumentEditTaskStatus = Literal[
    "queued",
    "running",
    "reviewing",
    "merging",
    "merged",
    "conflict",
    "failed",
]
DraftAiStatus = Literal["queued", "running", "ready", "failed"]


class DocumentSectionSelection(StrictModel):
    heading: str = Field(min_length=1, max_length=500)
    occurrence: int = Field(default=1, ge=1)

    @field_validator("heading")
    @classmethod
    def strip_heading(cls, value: str) -> str:
        heading = value.strip()
        if not heading:
            raise ValueError("Section heading must not be empty")
        return heading


class DocumentEditTaskCreate(StrictModel):
    user_id: str = "anonymous"
    tenant_id: str = "default"
    description: str = Field(min_length=1, max_length=20_000)
    sections: list[DocumentSectionSelection] = Field(min_length=1, max_length=50)

    @field_validator("description")
    @classmethod
    def strip_description(cls, value: str) -> str:
        description = value.strip()
        if not description:
            raise ValueError("Task description must not be empty")
        return description


class DocumentDraftSection(StrictModel):
    id: str = Field(default_factory=lambda: f"draft_section_{uuid4().hex}")
    heading: str
    occurrence: int
    level: int
    base_content: str
    draft_content: str
    draft_revision: int = 0
    ai_status: DraftAiStatus = "queued"
    ai_instruction: str | None = None
    ai_base_revision: int = 0
    ai_error: str | None = None
    updated_by: Literal["source", "ai", "user"] = "source"


class DocumentEditTask(StrictModel):
    id: str = Field(default_factory=lambda: f"document_edit_{uuid4().hex}")
    document_name: str
    title: str
    description: str
    status: DocumentEditTaskStatus = "queued"
    base_revision: str
    user_id: str
    tenant_id: str
    sections: list[DocumentDraftSection]
    version: int = 1
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    merged_at: datetime | None = None


class DocumentEditTaskListResponse(StrictModel):
    items: list[DocumentEditTask]
    total: int


class DocumentDraftUpdate(StrictModel):
    user_id: str = "anonymous"
    tenant_id: str = "default"
    content: str = Field(max_length=1_048_576)
    expected_draft_revision: int = Field(ge=0)


class DocumentDraftAiRevision(StrictModel):
    user_id: str = "anonymous"
    tenant_id: str = "default"
    instruction: str = Field(min_length=1, max_length=20_000)
    expected_draft_revision: int = Field(ge=0)

    @field_validator("instruction")
    @classmethod
    def strip_instruction(cls, value: str) -> str:
        instruction = value.strip()
        if not instruction:
            raise ValueError("AI revision instruction must not be empty")
        return instruction


class DocumentEditTaskActor(StrictModel):
    user_id: str = "anonymous"
    tenant_id: str = "default"
