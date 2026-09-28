from __future__ import annotations

import logging
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any, TypedDict, cast
from uuid import uuid4

from fastapi import APIRouter, Query, Request, Response

from tianzhou_agent_platform.api.dependencies import (
    repository,
    request_actor,
    require_platform_admin,
)
from tianzhou_agent_platform.core.conversation import Conversation, Message
from tianzhou_agent_platform.core.errors import PlatformError, not_found
from tianzhou_agent_platform.core.feedback import (
    FeedbackCaseUpdate,
    FeedbackDetail,
    FeedbackHistoryItem,
    FeedbackMetrics,
    FeedbackReasonCount,
    FeedbackRecord,
    FeedbackTrendPoint,
    FeedbackUpsert,
)
from tianzhou_agent_platform.core.chat import TraceRecord

logger = logging.getLogger(__name__)


def create_feedback_router() -> APIRouter:
    router = APIRouter()

    @router.get("/feedback/messages/{message_id}", response_model=FeedbackRecord | None)
    async def get_message_feedback(message_id: str, request: Request) -> FeedbackRecord | None:
        actor = request_actor(request)
        return await repository(request).get_feedback_for_message(
            message_id,
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
        )

    @router.put("/feedback/messages/{message_id}", response_model=FeedbackRecord)
    async def put_message_feedback(
        message_id: str,
        payload: FeedbackUpsert,
        request: Request,
    ) -> FeedbackRecord:
        actor = request_actor(request)
        data_repository = repository(request)
        conversation = await data_repository.require_conversation_actor(
            payload.conversation_id,
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
        )
        message = _assistant_message(conversation, message_id)
        trace = await _optional_trace(data_repository, message.trace_id)
        user = getattr(request.state, "user", None)
        user_name = getattr(user, "name", None) or actor.user_id
        user_email = str(getattr(user, "email", ""))
        agent_name, agent_version = _agent_identity(trace)
        existing = await data_repository.get_feedback_for_message(
            message_id,
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
        )
        record = FeedbackRecord(
            id=existing.id if existing else f"feedback_{uuid4().hex}",
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
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
                    actor_id=actor.user_id,
                    actor_name=user_name,
                    action="提交反馈",
                )
            ],
        )
        return await data_repository.upsert_feedback(record)

    @router.delete("/feedback/messages/{message_id}", status_code=204)
    async def delete_message_feedback(message_id: str, request: Request) -> Response:
        actor = request_actor(request)
        user = getattr(request.state, "user", None)
        await repository(request).cancel_feedback(
            message_id,
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            actor_name=getattr(user, "name", None) or actor.user_id,
        )
        return Response(status_code=204)

    @router.get("/admin/feedback", response_model=list[FeedbackRecord])
    async def list_admin_feedback(
        request: Request,
        from_at: datetime | None = None,
        to_at: datetime | None = None,
        rating: str | None = Query(default=None, pattern="^(up|down)$"),
        user_query: str | None = Query(default=None, max_length=100),
    ) -> list[FeedbackRecord]:
        require_platform_admin(request)
        return await repository(request).list_feedbacks(
            from_at=_aware(from_at),
            to_at=_aware(to_at),
            rating=rating,
            user_query=user_query,
        )

    @router.get("/admin/feedback/metrics", response_model=FeedbackMetrics)
    async def admin_feedback_metrics(
        request: Request,
        from_at: datetime,
        to_at: datetime,
    ) -> FeedbackMetrics:
        require_platform_admin(request)
        start, end = _validated_range(from_at, to_at)
        data_repository = repository(request)
        conversations = await data_repository.list_conversations()
        feedbacks = await data_repository.list_feedbacks(from_at=start, to_at=end)
        duration = end - start
        previous_start = start - duration
        previous_feedbacks = await data_repository.list_feedbacks(from_at=previous_start, to_at=start)
        current_answers = _answer_messages(conversations, start, end)
        previous_answers = _answer_messages(conversations, previous_start, start)
        current = _metric_values(feedbacks, len(current_answers))
        previous = _metric_values(previous_feedbacks, len(previous_answers))
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
            trend=_trend(start, end, current_answers, feedbacks),
            reasons=_reasons(feedbacks),
        )

    @router.get("/admin/feedback/{feedback_id}", response_model=FeedbackDetail)
    async def admin_feedback_detail(feedback_id: str, request: Request) -> FeedbackDetail:
        require_platform_admin(request)
        data_repository = repository(request)
        feedback = await data_repository.get_feedback(feedback_id)
        # New OBS query path first (design 12.4); legacy repository fallback
        # keeps pre-migration traces readable during the cut-over phase.
        context: list[Any] = []
        query = getattr(request.app.state, "obs_query", None)
        if query is not None and query.enabled:
            try:
                context = await query.feedback_context(
                    tenant_id=feedback.tenant_id,
                    user_id=feedback.user_id,
                    session_id=feedback.conversation_id,
                    before=feedback.created_at,
                )
            except Exception:
                logger.exception("OBS feedback context query failed; falling back to legacy traces")
                context = []
        if not context:
            traces = await data_repository.list_traces()
            legacy = sorted(
                (
                    trace
                    for trace in traces
                    if trace.conversation_id == feedback.conversation_id
                    and trace.created_at <= feedback.created_at
                ),
                key=lambda trace: trace.created_at,
            )
            context = [
                {
                    "trace_id": trace.trace_id,
                    "root_span_id": trace.root_span_id,
                    "conversation_id": trace.conversation_id,
                    "user_id": trace.user_id,
                    "tenant_id": trace.tenant_id,
                    "status": trace.status,
                    "created_at": trace.created_at,
                    "completed_at": trace.completed_at,
                    "spans": [span.model_dump(mode="json") for span in trace.spans],
                    "events": [event.model_dump(mode="json") for event in trace.events],
                }
                for trace in legacy
            ]
        return FeedbackDetail(feedback=feedback, context_traces=cast(list[TraceRecord], context))

    @router.patch("/admin/feedback/{feedback_id}/case", response_model=FeedbackRecord)
    async def update_admin_feedback_case(
        feedback_id: str,
        payload: FeedbackCaseUpdate,
        request: Request,
    ) -> FeedbackRecord:
        admin = require_platform_admin(request)
        return await repository(request).update_feedback_case(
            feedback_id,
            status=payload.status,
            assignee=payload.assignee.strip(),
            conclusion=payload.conclusion.strip(),
            actor_id=admin.id if admin else "admin",
            actor_name=admin.name if admin else "管理员",
        )

    return router


def _assistant_message(conversation: Conversation, message_id: str) -> Message:
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


async def _optional_trace(data_repository, trace_id: str | None):  # type: ignore[no-untyped-def]
    if not trace_id:
        return None
    try:
        return await data_repository.get_trace(trace_id)
    except PlatformError as exc:
        if exc.code == "RESOURCE_NOT_FOUND":
            return None
        raise


def _agent_identity(trace) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    if trace is None:
        return "Unibot", ""
    span = next((item for item in trace.spans if item.kind == "aina"), None)
    if span is None:
        span = next((item for item in trace.spans if item.kind == "model"), None)
    if span is None:
        return "Unibot", ""
    return span.target_id or span.name or "Unibot", span.target_version or ""


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _validated_range(from_at: datetime, to_at: datetime) -> tuple[datetime, datetime]:
    start = _aware(from_at)
    end = _aware(to_at)
    assert start is not None and end is not None
    if start >= end or end - start > timedelta(days=366):
        raise PlatformError(
            "INVALID_REQUEST",
            "Feedback metric time range is invalid",
            status_code=422,
            source="feedback",
        )
    return start, end


def _answer_messages(
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


def _metric_values(feedbacks: list[FeedbackRecord], answer_count: int) -> _MetricValues:
    positive_count = sum(item.rating == "up" for item in feedbacks)
    pending_negative_count = sum(
        item.rating == "down" and item.case_status in {"pending", "in_progress"}
        for item in feedbacks
    )
    return {
        "positive_count": positive_count,
        "pending_negative_count": pending_negative_count,
        "feedback_rate": _percentage(len(feedbacks), answer_count),
        "positive_feedback_rate": _percentage(positive_count, len(feedbacks)),
        "positive_answer_rate": _percentage(positive_count, answer_count),
    }


def _trend(
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
                feedback_rate=_percentage(len(daily_feedback), answer_count),
                positive_rate=_percentage(positive, len(daily_feedback)),
            )
        )
        day += timedelta(days=1)
    return points


def _reasons(feedbacks: list[FeedbackRecord]) -> list[FeedbackReasonCount]:
    counts = Counter(item.reason for item in feedbacks if item.rating == "down" and item.reason)
    total = sum(counts.values())
    return [
        FeedbackReasonCount(reason=reason, count=count, percentage=_percentage(count, total))
        for reason, count in counts.most_common()
    ]


def _percentage(value: int, total: int) -> float:
    return round(value * 100 / total, 1) if total else 0.0


def _relative_change(current: int | float, previous: int | float) -> float:
    if not previous:
        return 0.0 if not current else 100.0
    return round((current - previous) * 100 / previous, 1)
