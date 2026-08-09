"""Feedback domain logic: record assembly, metrics and admin detail views.

Business rules live here so route handlers stay thin and testable without
HTTP plumbing.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import TypedDict
from uuid import uuid4

from tianzhou_agent_platform.core.chat import TraceRecord
from tianzhou_agent_platform.core.conversation import Conversation, Message
from tianzhou_agent_platform.core.errors import PlatformError, not_found
from tianzhou_agent_platform.core.feedback import (
    FeedbackDetail,
    FeedbackHistoryItem,
    FeedbackMetrics,
    FeedbackReasonCount,
    FeedbackRecord,
    FeedbackTrendPoint,
    FeedbackUpsert,
)
from tianzhou_agent_platform.core.repository import InMemoryRepository


async def upsert_message_feedback(
    repository: InMemoryRepository,
    *,
    user_id: str,
    tenant_id: str,
    user_name: str,
    user_email: str,
    payload: FeedbackUpsert,
    message_id: str,
) -> FeedbackRecord:
    conversation = await repository.require_conversation_actor(
        payload.conversation_id,
        user_id=user_id,
        tenant_id=tenant_id,
    )
    message = assistant_message(conversation, message_id)
    trace = await optional_trace(repository, message.trace_id)
    agent_name, agent_version = agent_identity(trace)
    existing = await repository.get_feedback_for_message(
        message_id,
        user_id=user_id,
        tenant_id=tenant_id,
    )
    record = FeedbackRecord(
        id=existing.id if existing else f"feedback_{uuid4().hex}",
        user_id=user_id,
        tenant_id=tenant_id,
        user_name=user_name,
        user_email=user_email,
        conversation_id=conversation.id,
        message_id=message.id,
        trace_id=message.trace_id,
        agent_name=agent_name,
        agent_version=agent_version,
        rating=payload.rating,
        reason=payload.reason,
        comment=payload.comment,
        history=[]
        if existing
        else [
            FeedbackHistoryItem(
                actor_id=user_id,
                actor_name=user_name,
                action="提交反馈",
            )
        ],
    )
    return await repository.upsert_feedback(record)


async def compute_feedback_metrics(
    repository: InMemoryRepository,
    *,
    from_at: datetime,
    to_at: datetime,
) -> FeedbackMetrics:
    start, end = validated_range(from_at, to_at)
    conversations = await repository.list_conversations()
    feedbacks = await repository.list_feedbacks(from_at=start, to_at=end)
    duration = end - start
    previous_start = start - duration
    previous_feedbacks = await repository.list_feedbacks(from_at=previous_start, to_at=start)
    current_answers = answer_messages(conversations, start, end)
    previous_answers = answer_messages(conversations, previous_start, start)
    current = metric_values(feedbacks, len(current_answers))
    previous = metric_values(previous_feedbacks, len(previous_answers))
    return FeedbackMetrics(
        from_at=start,
        to_at=end,
        answer_count=len(current_answers),
        feedback_count=len(feedbacks),
        positive_count=current["positive_count"],
        pending_negative_count=current["pending_negative_count"],
        feedback_rate=current["feedback_rate"],
        positive_feedback_rate=current["positive_feedback_rate"],
        positive_answer_rate=current["positive_answer_rate"],
        feedback_rate_change=current["feedback_rate"] - previous["feedback_rate"],
        positive_feedback_rate_change=current["positive_feedback_rate"]
        - previous["positive_feedback_rate"],
        positive_answer_rate_change=current["positive_answer_rate"]
        - previous["positive_answer_rate"],
        pending_negative_change=_relative_change(
            current["pending_negative_count"], previous["pending_negative_count"]
        ),
        trend=trend(start, end, current_answers, feedbacks),
        reasons=reasons(feedbacks),
    )


async def feedback_detail(
    repository: InMemoryRepository,
    *,
    feedback_id: str,
) -> FeedbackDetail:
    feedback = await repository.get_feedback(feedback_id)
    traces = await repository.list_traces()
    context = sorted(
        (
            trace
            for trace in traces
            if trace.conversation_id == feedback.conversation_id
            and trace.created_at <= feedback.created_at
        ),
        key=lambda trace: trace.created_at,
    )
    return FeedbackDetail(feedback=feedback, context_traces=context)


def assistant_message(conversation: Conversation, message_id: str) -> Message:
    message = next((item for item in conversation.messages if item.id == message_id), None)
    if message is None:
        raise not_found("Message", message_id)
    if message.role != "assistant":
        raise PlatformError(
            "INVALID_REQUEST",
            "Only assistant messages can receive feedback",
            status_code=422,
            source="feedback",
        )
    return message


async def optional_trace(
    repository: InMemoryRepository,
    trace_id: str | None,
) -> TraceRecord | None:
    if not trace_id:
        return None
    try:
        return await repository.get_trace(trace_id)
    except PlatformError as exc:
        if exc.code == "RESOURCE_NOT_FOUND":
            return None
        raise


def agent_identity(trace: TraceRecord | None) -> tuple[str, str]:
    if trace is None:
        return "Unibot", ""
    span = next((item for item in trace.spans if item.kind == "aina"), None)
    if span is None:
        span = next((item for item in trace.spans if item.kind == "model"), None)
    if span is None:
        return "Unibot", ""
    return span.target_id or span.name or "Unibot", span.target_version or ""


def aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def validated_range(from_at: datetime, to_at: datetime) -> tuple[datetime, datetime]:
    start = aware(from_at)
    end = aware(to_at)
    assert start is not None and end is not None
    if start >= end or end - start > timedelta(days=366):
        raise PlatformError(
            "INVALID_REQUEST",
            "Feedback metric time range is invalid",
            status_code=422,
            source="feedback",
        )
    return start, end


def answer_messages(
    conversations: list[Conversation],
    from_at: datetime,
    to_at: datetime,
) -> list[Message]:
    return [
        message
        for conversation in conversations
        for message in conversation.messages
        if message.role == "assistant" and from_at <= message.created_at < to_at
    ]


class _MetricValues(TypedDict):
    positive_count: int
    pending_negative_count: int
    feedback_rate: float
    positive_feedback_rate: float
    positive_answer_rate: float


def metric_values(feedbacks: list[FeedbackRecord], answer_count: int) -> _MetricValues:
    positive_count = sum(item.rating == "up" for item in feedbacks)
    pending_negative_count = sum(
        item.rating == "down" and item.case_status in {"pending", "in_progress"}
        for item in feedbacks
    )
    return {
        "positive_count": positive_count,
        "pending_negative_count": pending_negative_count,
        "feedback_rate": percentage(len(feedbacks), answer_count),
        "positive_feedback_rate": percentage(positive_count, len(feedbacks)),
        "positive_answer_rate": percentage(positive_count, answer_count),
    }


def trend(
    start: datetime,
    end: datetime,
    answers: list[Message],
    feedbacks: list[FeedbackRecord],
) -> list[FeedbackTrendPoint]:
    points: list[FeedbackTrendPoint] = []
    day = start.date()
    while day <= end.date():
        answer_count = sum(item.created_at.date() == day for item in answers)
        daily_feedback = [item for item in feedbacks if item.created_at.date() == day]
        positive = sum(item.rating == "up" for item in daily_feedback)
        points.append(
            FeedbackTrendPoint(
                date=day.isoformat(),
                feedback_count=len(daily_feedback),
                answer_count=answer_count,
                feedback_rate=percentage(len(daily_feedback), answer_count),
                positive_rate=percentage(positive, len(daily_feedback)),
            )
        )
        day += timedelta(days=1)
    return points


def reasons(feedbacks: list[FeedbackRecord]) -> list[FeedbackReasonCount]:
    counts = Counter(item.reason for item in feedbacks if item.rating == "down" and item.reason)
    total = sum(counts.values())
    return [
        FeedbackReasonCount(reason=reason, count=count, percentage=percentage(count, total))
        for reason, count in counts.most_common()
    ]


def percentage(value: int, total: int) -> float:
    return round(value * 100 / total, 1) if total else 0.0


def _relative_change(current: int | float, previous: int | float) -> float:
    if not previous:
        return 0.0 if not current else 100.0
    return round((current - previous) * 100 / previous, 1)
