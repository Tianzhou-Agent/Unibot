from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

from pydantic import Field, model_validator

from tianzhou_agent_platform.core.base import StrictModel, utc_now
from tianzhou_agent_platform.observability.models import TraceRecord


FeedbackRating = Literal["up", "down"]
FeedbackCaseStatus = Literal["pending", "in_progress", "resolved", "closed"]


class FeedbackUpsert(StrictModel):
    conversation_id: str = Field(min_length=1, max_length=160)
    rating: FeedbackRating
    reason: str = Field(default="", max_length=100)
    comment: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def validate_negative_reason(self) -> "FeedbackUpsert":
        self.reason = self.reason.strip()
        self.comment = self.comment.strip()
        if self.rating == "down" and not self.reason:
            raise ValueError("Negative feedback requires a reason")
        if self.rating == "up":
            self.reason = ""
            self.comment = ""
        return self


class FeedbackHistoryItem(StrictModel):
    at: datetime = Field(default_factory=utc_now)
    actor_id: str
    actor_name: str
    action: str


class FeedbackRecord(StrictModel):
    id: str
    user_id: str
    tenant_id: str
    user_name: str
    user_email: str = ""
    conversation_id: str
    message_id: str
    trace_id: str | None = None
    agent_name: str = "Superbot"
    agent_version: str = ""
    rating: FeedbackRating
    reason: str = ""
    comment: str = ""
    active: bool = True
    case_status: FeedbackCaseStatus = "pending"
    assignee: str = ""
    conclusion: str = ""
    history: list[FeedbackHistoryItem] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class FeedbackCaseUpdate(StrictModel):
    status: FeedbackCaseStatus
    assignee: str = Field(default="", max_length=80)
    conclusion: str = Field(default="", max_length=1000)


class FeedbackDetail(StrictModel):
    feedback: FeedbackRecord
    context_traces: list[TraceRecord]


class FeedbackTrendPoint(StrictModel):
    date: str
    feedback_count: int
    answer_count: int
    feedback_rate: float
    positive_rate: float


class FeedbackReasonCount(StrictModel):
    reason: str
    count: int
    percentage: float


class FeedbackMetrics(StrictModel):
    from_at: datetime
    to_at: datetime
    answer_count: int
    feedback_count: int
    positive_count: int
    pending_negative_count: int
    feedback_rate: float
    positive_feedback_rate: float
    positive_answer_rate: float
    feedback_rate_change: float
    positive_feedback_rate_change: float
    positive_answer_rate_change: float
    pending_negative_change: float
    trend: list[FeedbackTrendPoint]
    reasons: list[FeedbackReasonCount]


@runtime_checkable
class FeedbackRepository(Protocol):
    async def upsert_feedback(self, feedback: FeedbackRecord) -> FeedbackRecord: ...

    async def get_feedback_for_message(
        self,
        message_id: str,
        *,
        user_id: str,
        tenant_id: str,
    ) -> FeedbackRecord | None: ...

    async def get_feedback(self, feedback_id: str) -> FeedbackRecord: ...

    async def list_feedbacks(
        self,
        *,
        from_at: datetime | None = None,
        to_at: datetime | None = None,
        rating: str | None = None,
        user_query: str | None = None,
    ) -> list[FeedbackRecord]: ...

    async def cancel_feedback(
        self,
        message_id: str,
        *,
        user_id: str,
        tenant_id: str,
        actor_name: str,
    ) -> None: ...

    async def update_feedback_case(
        self,
        feedback_id: str,
        *,
        status: str,
        assignee: str,
        conclusion: str,
        actor_id: str,
        actor_name: str,
    ) -> FeedbackRecord: ...
