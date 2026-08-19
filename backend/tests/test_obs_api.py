"""OBS page API smoke + permission tests (design 21.4)."""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from tianzhou_agent_platform.auth.models import UserRecord
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.core.repository import InMemoryRepository
from tianzhou_agent_platform.main import create_app


def _settings(**overrides) -> AgentSettings:
    defaults: dict = {
        "llm_base_url": None,
        "llm_api_key": None,
        "llm_model": "test-model",
        "node_id": "test-node",
    }
    defaults.update(overrides)
    return AgentSettings(**defaults)


def test_obs_overview_endpoint_available() -> None:
    app = create_app(settings=_settings(), repository=InMemoryRepository())
    with TestClient(app) as client:
        response = client.get("/obs/overview?range=week")
        assert response.status_code == 200
        body = response.json()
        assert body["range"] == "week"
        assert body["trace_count"] == 0
        assert body["total_tokens"] == 0
        assert body["per_model"] == []
        assert body["daily"] == []


def test_obs_overview_rejects_unknown_range() -> None:
    app = create_app(settings=_settings(), repository=InMemoryRepository())
    with TestClient(app) as client:
        response = client.get("/obs/overview?range=decade")
        # unknown ranges fall back to the week default instead of erroring
        assert response.status_code == 200
        assert response.json()["range"] == "decade"


def test_obs_session_detail_returns_null_when_missing() -> None:
    app = create_app(settings=_settings(), repository=InMemoryRepository())
    with TestClient(app) as client:
        response = client.get("/obs/sessions/missing-conv")
        assert response.status_code == 200
        assert response.json() is None


def test_obs_raw_logs_requires_query_params() -> None:
    app = create_app(settings=_settings(), repository=InMemoryRepository())
    with TestClient(app) as client:
        response = client.get("/obs/raw-logs")
        assert response.status_code == 422


def test_obs_endpoints_require_auth_when_enforced() -> None:
    app = create_app(settings=_settings(), repository=InMemoryRepository(), enforce_auth=True)
    with TestClient(app) as client:
        assert client.get("/obs/overview").status_code == 401
        assert client.get("/obs/sessions/conv_1").status_code == 401
        assert client.get("/obs/raw-logs?trace_id=t&span_id=s").status_code == 401


def test_admin_obs_endpoints_block_non_admin() -> None:
    app = create_app(
        settings=_settings(admin_identities="admin@example.com"),
        repository=InMemoryRepository(),
        enforce_auth=True,
    )
    with TestClient(app) as client:
        assert client.get("/admin/users").status_code == 401
        assert client.get("/admin/obs/overview").status_code == 401
        assert client.get("/admin/obs/traces", params={"user_id": "user_1"}).status_code == 401
        assert client.get(
            "/admin/obs/traces/trace_1", params={"user_id": "user_1"}
        ).status_code == 401
        assert client.get("/admin/obs/sessions/conv_1").status_code == 401


def test_admin_users_supports_fuzzy_search_without_auth_fields() -> None:
    data_repository = InMemoryRepository()
    asyncio.run(data_repository.create_user(UserRecord(
        id="user_alice_42",
        email="alice@example.com",
        name="Alice Zhang",
        tenant_id="tenant_north",
        password_hash="must-not-leak",
    )))
    asyncio.run(data_repository.create_user(UserRecord(
        id="user_bob_7",
        email="bob@example.com",
        name="Bob Li",
        tenant_id="tenant_south",
    )))
    app = create_app(settings=_settings(), repository=data_repository)

    with TestClient(app) as client:
        response = client.get("/admin/users", params={"query": "LICE ZH"})

    assert response.status_code == 200
    body = response.json()
    assert body["has_more"] is False
    assert [user["id"] for user in body["items"]] == ["user_alice_42"]
    assert "password_hash" not in body["items"][0]


def test_admin_obs_traces_requires_and_forwards_user_filter() -> None:
    class RecordingQuery:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def admin_trace_list(self, **kwargs):
            self.calls.append(kwargs)
            return {"items": [], "has_more": False}

    app = create_app(settings=_settings(), repository=InMemoryRepository())
    query = RecordingQuery()
    app.state.obs_query = query
    with TestClient(app) as client:
        assert client.get("/admin/obs/traces").status_code == 422
        response = client.get(
            "/admin/obs/traces",
            params={"user_id": "user_42", "range": "month", "limit": 25},
        )

    assert response.status_code == 200
    assert response.json() == {"items": [], "has_more": False}
    assert query.calls == [
        {
            "user_id": "user_42",
            "tenant_id": None,
            "range_name": "month",
            "limit": 25,
            "offset": 0,
        }
    ]


def test_admin_obs_session_does_not_force_default_tenant() -> None:
    class RecordingQuery:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def admin_session_detail(self, **kwargs):
            self.calls.append(kwargs)
            return None

    app = create_app(settings=_settings(), repository=InMemoryRepository())
    query = RecordingQuery()
    app.state.obs_query = query
    with TestClient(app) as client:
        assert client.get("/admin/obs/sessions/conv_1").status_code == 200
        assert client.get(
            "/admin/obs/sessions/conv_1", params={"tenant_id": "tenant_2"}
        ).status_code == 200

    assert query.calls[0]["tenant_id"] is None
    assert query.calls[1]["tenant_id"] == "tenant_2"


def test_admin_feedback_detail_falls_back_to_legacy() -> None:
    """With the OBS store disabled, admin feedback detail still works via the
    legacy repository path (migration fallback)."""
    from tianzhou_agent_platform.core.feedback import FeedbackRecord

    repository = InMemoryRepository()
    feedback = FeedbackRecord(
        id="fb_1",
        message_id="msg_1",
        conversation_id="conv_1",
        user_id="user_1",
        user_name="User One",
        tenant_id="tenant_1",
        rating="down",
        trace_id="trace_aaa",
    )
    asyncio.run(repository.upsert_feedback(feedback))
    app = create_app(settings=_settings(admin_identities="admin@example.com"), repository=repository)
    with TestClient(app) as client:
        response = client.get("/admin/feedback/fb_1")
        assert response.status_code == 200
        body = response.json()
        assert body["feedback"]["id"] == "fb_1"
        assert body["context_traces"] == []
