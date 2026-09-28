from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.core.feedback import FeedbackRecord
from tianzhou_agent_platform.core.operations_analytics import OperationsAnalyticsService, operations_bounds
from tianzhou_agent_platform.core.repository import InMemoryRepository
from tianzhou_agent_platform.main import create_app
from tianzhou_agent_platform.store.observability_store import (
    _agent_first_use_values,
    _operation_event_values,
    _platform_first_use_values,
    _request_agent_values,
)
from tianzhou_agent_platform.store.observability_wal import ObsRecord


class FakeOperationsStore:
    def __init__(self) -> None:
        self.events = [
            _event("trace-1", "tenant-1", "user-1", "2026-08-01T01:00:00Z"),
            _event("trace-2", "tenant-1", "user-1", "2026-08-02T01:00:00Z"),
            _event("trace-3", "tenant-1", "user-1", "2026-08-08T01:00:00Z"),
            _event("trace-4", "tenant-1", "user-2", "2026-08-08T03:00:00Z", status="failed"),
            _event("trace-5", "tenant-1", "user-2", "2026-08-14T01:00:00Z"),
            _event("trace-6", "tenant-1", "user-1", "2026-08-14T02:00:00Z"),
        ]
        self.agent_events = [
            {**row, "agent_id": "assistant-a", "agent_version": "1.2.0"}
            for row in self.events
            if row["user_id"] == "user-1"
        ]
        self.first_uses = [
            {"tenant_id": "tenant-1", "user_id": "user-1", "first_at": _dt("2026-08-01T01:00:00Z")},
            {"tenant_id": "tenant-1", "user_id": "user-2", "first_at": _dt("2026-08-07T01:00:00Z")},
        ]
        self.agent_first_uses = [
            {
                "tenant_id": "tenant-1",
                "user_id": "user-1",
                "agent_id": "assistant-a",
                "agent_version": "1.2.0",
                "first_at": _dt("2026-08-01T01:00:00Z"),
            }
        ]

    async def list_operation_events(self, **_: object) -> list[dict]:
        return self.events

    async def list_operation_agent_events(self, **_: object) -> list[dict]:
        return self.agent_events

    async def list_platform_first_uses(self, **_: object) -> list[dict]:
        return self.first_uses

    async def list_agent_first_uses(self, **_: object) -> list[dict]:
        return self.agent_first_uses


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _event(trace_id: str, tenant_id: str, user_id: str, started_at: str, *, status: str = "completed") -> dict:
    return {
        "trace_id": trace_id,
        "request_id": trace_id,
        "session_id": "session-1",
        "tenant_id": tenant_id,
        "user_id": user_id,
        "source": "chat",
        "status": status,
        "started_at": _dt(started_at),
        "completed_at": _dt(started_at),
        "metric_version": "v1",
    }


@pytest.mark.asyncio
async def test_overview_uses_projected_events_and_feedback() -> None:
    service = OperationsAnalyticsService(FakeOperationsStore())  # type: ignore[arg-type]
    feedbacks = [
        FeedbackRecord(
            id="feedback-1",
            user_id="user-1",
            tenant_id="tenant-1",
            user_name="User",
            conversation_id="session-1",
            message_id="message-1",
            agent_name="assistant-a",
            rating="up",
        ),
        FeedbackRecord(
            id="feedback-2",
            user_id="user-1",
            tenant_id="tenant-1",
            user_name="User",
            conversation_id="session-1",
            message_id="message-2",
            agent_name="assistant-a",
            rating="down",
        ),
    ]

    overview = await service.overview(
        tenant_id="tenant-1",
        range_name="week",
        feedbacks=feedbacks,
        now=_dt("2026-08-14T04:00:00Z"),
    )

    assert overview["summary"]["dau"] == 2
    assert overview["summary"]["request_count"] == 4
    assert overview["summary"]["failed_requests"] == 1
    assert overview["retention"]["d7"] == {"rate": 100.0, "cohort_users": 2}
    assert len(overview["trend"]) == 7
    assert overview["agents"][0]["agent_id"] == "assistant-a"
    assert overview["agents"][0]["positive_rate"] == 50.0
    assert overview["agents"][0]["eligible_users"] is None
    assert overview["availability"]["eligible_users"] is False


@pytest.mark.asyncio
async def test_disabled_operations_service_returns_real_empty_shape() -> None:
    overview = await OperationsAnalyticsService(None).overview(
        tenant_id=None,
        range_name="week",
        now=_dt("2026-08-14T04:00:00Z"),
    )
    assert overview["summary"]["request_count"] == 0
    assert overview["availability"]["operations"] is False
    assert len(overview["trend"]) == 7


def test_operation_projection_values_are_stable_for_wal_replay() -> None:
    trace = ObsRecord(
        record_type="trace_finished",
        producer_instance_id="node-1",
        sequence_no=10,
        occurred_at=_dt("2026-08-14T01:00:02Z"),
        trace_id="canonical-trace",
        payload={
            "legacy_trace_id": "trace-logical-request",
            "session_id": "session-1",
            "tenant_id": "tenant-1",
            "user_id": "user-1",
            "status": "completed",
            "started_at": "2026-08-14T01:00:00Z",
            "completed_at": "2026-08-14T01:00:02Z",
        },
    )
    span = ObsRecord(
        record_type="span_finished",
        producer_instance_id="node-1",
        sequence_no=11,
        occurred_at=_dt("2026-08-14T01:00:01Z"),
        trace_id="canonical-trace",
        span_id="span-1",
        payload={
            "kind": "aina",
            "target_id": "assistant-a",
            "target_version": "1.2.0",
            "tenant_id": "tenant-1",
            "user_id": "user-1",
            "started_at": "2026-08-14T01:00:00Z",
        },
    )

    event = _operation_event_values(trace)
    agent = _request_agent_values(span)

    assert event["trace_id"] == "canonical-trace"
    assert event["request_id"] == "trace-logical-request"
    assert event["source"] == "chat"
    assert _platform_first_use_values(trace)["scope_id"] == "platform"
    assert agent["agent_id"] == "assistant-a"
    assert agent["agent_version"] == "1.2.0"
    assert _agent_first_use_values(span)["scope_id"] == "assistant-a"


def test_operations_bounds_use_shanghai_natural_days() -> None:
    start, _, today, days = operations_bounds("week", now=_dt("2026-08-14T04:00:00Z"))
    assert start == _dt("2026-08-07T16:00:00Z")
    assert today.isoformat() == "2026-08-14"
    assert days == 7


def test_operations_endpoint_available_and_validates_range() -> None:
    settings = AgentSettings(llm_base_url=None, llm_api_key=None, llm_model="test-model", node_id="test-node")
    app = create_app(settings=settings, repository=InMemoryRepository())
    with TestClient(app) as client:
        response = client.get("/admin/operations/overview?range=week")
        assert response.status_code == 200
        assert response.json()["summary"]["request_count"] == 0
        assert client.get("/admin/operations/overview?range=year").status_code == 422


def test_operations_endpoint_requires_admin_when_auth_is_enforced() -> None:
    settings = AgentSettings(llm_base_url=None, llm_api_key=None, llm_model="test-model", node_id="test-node")
    app = create_app(settings=settings, repository=InMemoryRepository(), enforce_auth=True)
    with TestClient(app) as client:
        assert client.get("/admin/operations/overview").status_code == 401
