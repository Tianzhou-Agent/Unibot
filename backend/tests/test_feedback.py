from fastapi.testclient import TestClient
from pydantic import SecretStr

from tests.support.fake_llm import ScriptedLLM, assistant
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.main import create_app


def _settings() -> AgentSettings:
    return AgentSettings(  # type: ignore[call-arg]
        _env_file=None,
        auth_secret=SecretStr("test-auth-secret-with-enough-entropy"),
        admin_identities="admin@example.com",
    )


def _register(client: TestClient, email: str, name: str) -> None:
    response = client.post(
        "/auth/register",
        json={"name": name, "email": email, "password": "correct-horse-battery"},
    )
    assert response.status_code == 201


def test_feedback_uses_real_messages_and_admin_context_stops_at_feedback_time() -> None:
    app = create_app(
        settings=_settings(),
        llm=ScriptedLLM([assistant("第一条回复"), assistant("反馈之后的回复")]),
        enforce_auth=True,
    )

    with TestClient(app) as client:
        _register(client, "member@example.com", "反馈用户")
        first = client.post("/chat", json={"message": "第一轮问题"})
        assert first.status_code == 200
        conversation_id = first.json()["conversation_id"]
        message_id = first.json()["message_id"]
        first_trace_id = first.json()["trace_id"]

        submitted = client.put(
            f"/feedback/messages/{message_id}",
            json={
                "conversation_id": conversation_id,
                "rating": "down",
                "reason": "事实或结论错误",
                "comment": "这里的事实不正确",
            },
        )
        assert submitted.status_code == 200
        feedback_id = submitted.json()["id"]
        assert client.get(f"/feedback/messages/{message_id}").json()["rating"] == "down"

        second = client.post(
            "/chat",
            json={"message": "第二轮问题", "conversation_id": conversation_id},
        )
        assert second.status_code == 200
        second_trace_id = second.json()["trace_id"]
        assert client.get("/admin/feedback").status_code == 403

        assert client.post("/auth/logout").status_code == 204
        _register(client, "admin@example.com", "反馈管理员")

        filtered = client.get(
            "/admin/feedback",
            params={"rating": "down", "user_query": "反馈用户"},
        )
        assert filtered.status_code == 200
        assert [item["id"] for item in filtered.json()] == [feedback_id]
        assert client.get("/admin/feedback", params={"user_query": "不存在"}).json() == []

        detail = client.get(f"/admin/feedback/{feedback_id}")
        assert detail.status_code == 200
        context_ids = [item["trace_id"] for item in detail.json()["context_traces"]]
        assert first_trace_id in context_ids
        assert second_trace_id not in context_ids

        updated = client.patch(
            f"/admin/feedback/{feedback_id}/case",
            json={"status": "in_progress", "assignee": "反馈管理员", "conclusion": "正在核查"},
        )
        assert updated.status_code == 200
        assert updated.json()["case_status"] == "in_progress"
        assert updated.json()["history"][-1]["actor_name"] == "反馈管理员"

        metrics = client.get(
            "/admin/feedback/metrics",
            params={"from_at": "2020-01-01T00:00:00Z", "to_at": "2030-01-01T00:00:00Z"},
        )
        assert metrics.status_code == 422  # Ranges longer than one year are intentionally rejected.

        metrics = client.get(
            "/admin/feedback/metrics",
            params={"from_at": "2026-01-01T00:00:00Z", "to_at": "2026-12-31T23:59:59Z"},
        )
        assert metrics.status_code == 200
        assert metrics.json()["feedback_count"] == 1
        assert metrics.json()["answer_count"] == 2
        assert metrics.json()["reasons"][0]["reason"] == "事实或结论错误"


def test_feedback_rejects_another_users_message() -> None:
    app = create_app(
        settings=_settings(),
        llm=ScriptedLLM([assistant("仅属于用户甲")]),
        enforce_auth=True,
    )
    with TestClient(app) as client:
        _register(client, "owner@example.com", "用户甲")
        chat = client.post("/chat", json={"message": "我的问题"}).json()
        client.post("/auth/logout")
        _register(client, "other@example.com", "用户乙")

        denied = client.put(
            f"/feedback/messages/{chat['message_id']}",
            json={
                "conversation_id": chat["conversation_id"],
                "rating": "up",
                "reason": "",
                "comment": "",
            },
        )
        assert denied.status_code == 403
