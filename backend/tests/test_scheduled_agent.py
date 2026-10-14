from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from tianzhou_agent_platform.aina.protocol.models import AinaInstallation, AinaManifest, AinaRecord
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.main import create_app
from tianzhou_agent_platform.model_providers.models import current_model_runtime
from tianzhou_agent_platform.store.memory_repository import InMemoryRepository
from tests.support.fake_llm import ScriptedLLM, assistant, call_first_tool


def _settings() -> AgentSettings:
    return AgentSettings(
        _env_file=None,
        llm_base_url="https://model.invalid/v1",
        llm_api_key="test-key",
        llm_model="test-model",
    )


def _remote_manifest() -> AinaManifest:
    return AinaManifest.model_validate(
        {
            "protocol_version": "1.0",
            "aina": {
                "id": "scheduled-remote",
                "name": "Scheduled remote",
                "version": "1.0.0",
                "description": "Remote schedule test.",
                "publisher": {"id": "tests", "name": "Tests"},
            },
            "runtime": {"type": "remote", "endpoint": "https://aina.invalid/runtime"},
        }
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"prompt": "生成每日简报"},
        {"aina_id": None, "input": {"message": "生成每日简报"}},
        {"aina_id": "", "input": {"message": "生成每日简报"}},
    ],
)
def test_general_agent_schedule_accepts_an_empty_target(payload: dict[str, Any]) -> None:
    with TestClient(create_app(settings=_settings(), llm=ScriptedLLM([]))) as client:
        created = client.post("/aina-schedules", json={"name": "Daily Agent", **payload})
        listed = client.get("/aina-schedules")

    assert created.status_code == 201
    assert created.json()["aina_id"] == ""
    assert created.json()["input"] == {"message": "生成每日简报"}
    assert listed.json() == [created.json()]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"prompt": "   "},
        {"input": {}},
        {"input": {"message": ""}},
        {"input": {"message": "\n\t"}},
        {"input": {"message": 12}},
    ],
)
def test_general_agent_schedule_requires_a_nonempty_message(payload: dict[str, Any]) -> None:
    with TestClient(create_app(settings=_settings(), llm=ScriptedLLM([]))) as client:
        response = client.post("/aina-schedules", json={"name": "Invalid Agent", **payload})
        assert client.get("/aina-schedules").json() == []

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_manual_agent_run_uses_the_native_tool_loop_and_keeps_overrides_ephemeral() -> None:
    invoked: list[dict[str, Any]] = []
    selected_models: list[str] = []

    async def remote(request: httpx.Request) -> httpx.Response:
        invoked.append(json.loads(request.content))
        return httpx.Response(200, json={"summary": "工具结果"})

    def answer(**_: Any):
        runtime = current_model_runtime()
        assert runtime is not None
        selected_models.append(runtime.model)
        return assistant("简报完成")

    llm = ScriptedLLM(
        [call_first_tool(arguments='{"topic":"今日进展"}', description_contains="scheduled lookup"), answer, answer]
    )
    capability_client = httpx.AsyncClient(transport=httpx.MockTransport(remote))
    with TestClient(create_app(settings=_settings(), llm=llm, capability_http_client=capability_client)) as client:
        provider = client.post(
            "/model-settings/providers",
            json={
                "user_id": "schedule-owner",
                "tenant_id": "team-a",
                "provider_type": "custom",
                "name": "Owner model",
                "base_url": "https://owner-model.invalid/v1",
                "api_key": "owner-key",
                "models": [{"name": "Owner selected model", "model": "owner-model"}],
            },
        )
        assert provider.status_code == 201
        provider_data = provider.json()
        selected = client.post(
            f"/model-settings/providers/{provider_data['id']}/models/{provider_data['models'][0]['id']}/default",
            json={"user_id": "schedule-owner", "tenant_id": "team-a"},
        )
        assert selected.status_code == 200
        tool = client.post(
            "/tools",
            json={
                "tool_id": "scheduled.lookup",
                "name": "Scheduled lookup",
                "description": "A scheduled lookup for this test.",
                "input_schema": {
                    "type": "object",
                    "properties": {"topic": {"type": "string"}},
                    "required": ["topic"],
                },
                "endpoint": "https://tool.invalid/lookup",
            },
        )
        assert tool.status_code == 201
        created = client.post(
            "/aina-schedules",
            json={
                "name": "Daily Agent",
                "user_id": "schedule-owner",
                "tenant_id": "team-a",
                "prompt": "保存的定时指令",
                "enabled": False,
            },
        ).json()
        first = client.post(
            f"/aina-schedules/{created['id']}/run",
            json={
                "input": {
                    "message": "临时调试指令",
                    "user_id": "attacker",
                    "tenant_id": "other-team",
                    "conversation_id": "unrelated-conversation",
                }
            },
        )
        assert first.status_code == 200
        second = client.post(f"/aina-schedules/{created['id']}/run", json={})
        history = client.get(f"/aina-schedules/{created['id']}/executions").json()
        conversations = [
            client.get(f"/conversations/{run.json()['last_result']['conversation_id']}").json()
            for run in (first, second)
        ]

    assert second.status_code == 200
    assert first.json()["last_status"] == second.json()["last_status"] == "succeeded"
    assert first.json()["last_result"]["content"] == "简报完成"
    assert first.json()["last_result"]["trace_id"]
    assert first.json()["next_run_at"] == second.json()["next_run_at"] == created["next_run_at"]
    assert second.json()["prompt"] == "保存的定时指令"
    assert second.json()["input"] == {"message": "保存的定时指令"}
    assert second.json()["enabled"] is False
    assert len(invoked) == 1
    assert selected_models == ["owner-model", "owner-model"]
    assert conversations[0]["id"] != conversations[1]["id"]
    assert all(item["user_id"] == "schedule-owner" and item["tenant_id"] == "team-a" for item in conversations)
    assert [message["role"] for message in conversations[0]["messages"]] == ["user", "assistant", "tool", "assistant"]
    assert conversations[0]["messages"][0]["content"] == "临时调试指令"
    assert conversations[1]["messages"][0]["content"] == "保存的定时指令"
    assert llm.calls[2]["messages"][-1]["content"] == "保存的定时指令"
    assert not any(message["content"] == "临时调试指令" for message in llm.calls[2]["messages"])
    assert len(history) == 2
    assert all(item["trigger"] == "manual" and item["status"] == "succeeded" for item in history)
    assert {item["result"]["conversation_id"] for item in history} == {item["id"] for item in conversations}
    assert all(item["duration_ms"] >= 0 and item["node_id"] and item["finished_at"] for item in history)


def test_due_agent_run_advances_schedule_and_records_scheduled_execution() -> None:
    llm = ScriptedLLM([assistant("Scheduled result")])
    app = create_app(settings=_settings(), llm=llm)
    with TestClient(app) as client:
        created = client.post(
            "/aina-schedules",
            json={"name": "Due Agent", "prompt": "Run when due", "interval_seconds": 3600},
        ).json()
        due_at = datetime.fromisoformat(created["next_run_at"])
        client.portal.call(lambda: app.state.aina_scheduler.tick(now=due_at))
        stored = client.get("/aina-schedules").json()[0]
        history = client.get(f"/aina-schedules/{created['id']}/executions").json()

    assert stored["last_status"] == "succeeded"
    assert stored["last_result"]["content"] == "Scheduled result"
    assert stored["next_run_at"] != created["next_run_at"]
    assert len(history) == 1
    assert history[0]["trigger"] == "scheduled"
    assert history[0]["scheduled_for"] == created["next_run_at"]
    assert history[0]["input"] == {"message": "Run when due"}
    assert history[0]["result"] == stored["last_result"]


def test_builtin_schedule_forces_native_aina_scope_without_an_installation() -> None:
    llm = ScriptedLLM(
        [
            call_first_tool(
                arguments='{"content":"每天阅读","category":"goal"}',
                description_contains="Store one durable fact",
            ),
            assistant("目标已保存"),
        ]
    )
    with TestClient(create_app(settings=_settings(), llm=llm)) as client:
        assert client.get("/installations").json() == []
        created = client.post(
            "/aina-schedules",
            json={
                "aina_id": "unibot-memory",
                "name": "Memory Agent",
                "prompt": "记住每天阅读的目标",
                "user_id": "memory-owner",
                "tenant_id": "team-memory",
            },
        )
        assert created.status_code == 201
        result = client.post(f"/aina-schedules/{created.json()['id']}/run", json={})
        assert result.status_code == 200
        assert result.json()["last_status"] == "succeeded"
        conversation = client.get(f"/conversations/{result.json()['last_result']['conversation_id']}").json()
        trace = client.get(f"/traces/{result.json()['last_result']['trace_id']}").json()
        memories = client.get("/memories", params={"user_id": "memory-owner", "tenant_id": "team-memory"}).json()[
            "items"
        ]
        assert client.get("/installations", params={"user_id": "memory-owner", "tenant_id": "team-memory"}).json() == []

    resolution = next(event for event in trace["events"] if event["kind"] == "routing.scope.resolved")
    assert resolution["details"]["source"] == "explicit_capability"
    assert resolution["details"]["requested_capability"] == "aina:unibot-memory"
    assert resolution["target_id"] == "unibot-memory"
    assert "unibot-memory" in conversation["active_aina_ids"]
    assert result.json()["last_result"]["status"] == "completed"
    assert result.json()["last_result"]["content"] == "目标已保存"
    assert [item["content"] for item in memories] == ["每天阅读"]
    assert memories[0]["source_conversation_id"] == conversation["id"]


def test_scheduled_agent_preserves_pending_approval_without_executing_the_tool() -> None:
    invoked: list[httpx.Request] = []

    async def remote(request: httpx.Request) -> httpx.Response:
        invoked.append(request)
        return httpx.Response(200, json={"sent": True})

    llm = ScriptedLLM([call_first_tool(description_contains="Scheduled high-risk send")])
    capability_client = httpx.AsyncClient(transport=httpx.MockTransport(remote))
    with TestClient(create_app(settings=_settings(), llm=llm, capability_http_client=capability_client)) as client:
        assert (
            client.post(
                "/tools",
                json={
                    "tool_id": "scheduled.send",
                    "name": "Scheduled send",
                    "description": "Scheduled high-risk send.",
                    "input_schema": {"type": "object"},
                    "endpoint": "https://tool.invalid/send",
                    "side_effect_level": "high",
                },
            ).status_code
            == 201
        )
        created = client.post("/aina-schedules", json={"name": "Approval Agent", "prompt": "Send a message"}).json()
        pending = client.post(f"/aina-schedules/{created['id']}/run", json={})
        assert pending.status_code == 200
        result = pending.json()["last_result"]
        conversation = client.get(f"/conversations/{result['conversation_id']}").json()
        approvals = client.get("/approvals", params={"conversation_id": result["conversation_id"]}).json()
        history = client.get(f"/aina-schedules/{created['id']}/executions").json()

    assert pending.json()["last_status"] == "approval_required"
    assert pending.json()["last_error"] is None
    assert result["status"] == "approval_required"
    assert result["approval"]["status"] == "pending"
    assert conversation["run_status"] == "approval_required"
    assert [item["id"] for item in approvals] == [result["approval"]["id"]]
    assert history[0]["status"] == "approval_required"
    assert history[0]["result"] == result
    assert history[0]["error"] is None
    assert invoked == []


def test_schedule_owner_is_bound_to_authentication_and_isolates_execution_history() -> None:
    app = create_app(settings=_settings(), llm=ScriptedLLM([assistant("Owner result")]), enforce_auth=True)
    with TestClient(app) as client:
        owner = client.post(
            "/auth/register",
            json={"email": "schedule-owner@example.com", "name": "Owner", "password": "correct-horse-battery"},
        ).json()["user"]
        created = client.post(
            "/aina-schedules",
            json={"name": "Private Agent", "prompt": "Owner task", "user_id": "forged", "tenant_id": "forged"},
        )
        assert created.status_code == 201
        task_id = created.json()["id"]
        run = client.post(f"/aina-schedules/{task_id}/run", json={})
        assert run.status_code == 200
        assert run.json()["last_status"] == "succeeded"
        owner_history = client.get(f"/aina-schedules/{task_id}/executions").json()
        other = client.post(
            "/auth/register",
            json={"email": "schedule-other@example.com", "name": "Other", "password": "correct-horse-battery"},
        )
        assert other.status_code == 201
        assert client.get("/aina-schedules").json() == []
        for method, suffix, body in (
            ("GET", "/executions", None),
            ("POST", "/run", {}),
            ("PATCH", "", {"name": "Stolen"}),
            ("DELETE", "", None),
        ):
            response = client.request(method, f"/aina-schedules/{task_id}{suffix}", json=body)
            assert response.status_code == 403
            assert response.json()["error"]["code"] == "PERMISSION_DENIED"
        assert client.get(f"/conversations/{run.json()['last_result']['conversation_id']}").status_code == 403

    assert created.json()["user_id"] == owner["id"]
    assert created.json()["tenant_id"] == owner["tenant_id"]
    assert len(owner_history) == 1
    assert owner_history[0]["user_id"] == owner["id"]
    assert owner_history[0]["tenant_id"] == owner["tenant_id"]


@pytest.mark.parametrize("invalid_state", ["missing", "uninstalled", "disabled-record", "disabled-installation"])
def test_invalid_or_disabled_target_cannot_be_created_or_invoked(invalid_state: str) -> None:
    repository = InMemoryRepository()
    app = create_app(settings=_settings(), repository=repository, llm=ScriptedLLM([]))
    with TestClient(app) as client:
        if invalid_state != "missing":
            record = AinaRecord(manifest=_remote_manifest())
            client.portal.call(repository.register_aina, record)
            if invalid_state != "uninstalled":
                installation = AinaInstallation(aina_id="scheduled-remote", installed_version="1.0.0")
                client.portal.call(repository.put_installation, installation)
                created = client.post(
                    "/aina-schedules",
                    json={"aina_id": "scheduled-remote", "name": "Remote task", "input": {"value": 1}},
                )
                assert created.status_code == 201
                if invalid_state == "disabled-record":
                    client.portal.call(repository.upsert_aina, record.model_copy(update={"status": "disabled"}))
                else:
                    client.portal.call(
                        repository.put_installation, installation.model_copy(update={"status": "disabled"})
                    )
                failed = client.post(f"/aina-schedules/{created.json()['id']}/run", json={})
                assert failed.status_code == 200
                assert failed.json()["last_status"] == "failed"
                assert failed.json()["last_result"] is None
                history = client.get(f"/aina-schedules/{created.json()['id']}/executions").json()
                assert history[0]["status"] == "failed"
                paused = client.patch(f"/aina-schedules/{created.json()['id']}", json={"enabled": False})
                assert paused.status_code == 200
                assert paused.json()["enabled"] is False
        rejected = client.post(
            "/aina-schedules", json={"aina_id": "scheduled-remote", "name": "Rejected", "input": {"value": 2}}
        )

    assert rejected.status_code == (404 if invalid_state in {"missing", "uninstalled"} else 403)


def test_schedule_updates_validate_the_merged_task_and_clear_stale_prompt() -> None:
    repository = InMemoryRepository()
    app = create_app(settings=_settings(), repository=repository, llm=ScriptedLLM([]))
    with TestClient(app) as client:
        client.portal.call(repository.register_aina, AinaRecord(manifest=_remote_manifest()))
        client.portal.call(
            repository.put_installation, AinaInstallation(aina_id="scheduled-remote", installed_version="1.0.0")
        )
        original = client.post(
            "/aina-schedules", json={"aina_id": "scheduled-remote", "name": "Switch target", "input": {"value": 1}}
        ).json()
        path = f"/aina-schedules/{original['id']}"
        invalid_target = client.patch(path, json={"aina_id": None})
        assert invalid_target.status_code == 422
        assert client.get("/aina-schedules").json() == [original]
        general = client.patch(path, json={"aina_id": None, "prompt": "Original prompt"})
        assert general.status_code == 200
        assert general.json()["aina_id"] == ""
        for patch in ({"input": {}}, {"schedule_type": "cron"}, {"aina_id": "unknown"}):
            rejected = client.patch(path, json=patch)
            assert rejected.status_code in {404, 422}
            assert client.get("/aina-schedules").json() == [general.json()]
        updated = client.patch(path, json={"input": {"message": "Replacement input"}})
        cron = client.patch(path, json={"schedule_type": "cron", "cron_expression": "0 9 * * *"})

    assert updated.status_code == 200
    assert updated.json()["prompt"] is None
    assert updated.json()["input"] == {"message": "Replacement input"}
    assert cron.status_code == 200
    assert cron.json()["schedule_type"] == "cron"
    assert cron.json()["cron_expression"] == "0 9 * * *"


def test_invalid_general_debug_input_is_rejected_without_creating_an_execution() -> None:
    with TestClient(create_app(settings=_settings(), llm=ScriptedLLM([]))) as client:
        created = client.post("/aina-schedules", json={"name": "Agent", "prompt": "Saved prompt"}).json()
        invalid = client.post(f"/aina-schedules/{created['id']}/run", json={"input": {"message": " "}})
        stored = client.get("/aina-schedules").json()[0]
        history = client.get(f"/aina-schedules/{created['id']}/executions").json()

    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "INVALID_REQUEST"
    assert stored == created
    assert history == []


def test_uncatalogued_builtin_target_is_unavailable_even_with_a_stored_installation() -> None:
    repository = InMemoryRepository()
    app = create_app(settings=_settings(), repository=repository, llm=ScriptedLLM([]))
    manifest = _remote_manifest().model_dump(mode="json")
    manifest["runtime"] = {"type": "builtin"}
    record = AinaRecord(manifest=AinaManifest.model_validate(manifest))
    with TestClient(app) as client:
        client.portal.call(repository.register_aina, record)
        client.portal.call(
            repository.put_installation, AinaInstallation(aina_id="scheduled-remote", installed_version="1.0.0")
        )
        response = client.post(
            "/aina-schedules",
            json={"aina_id": "scheduled-remote", "name": "Unavailable builtin", "prompt": "Run"},
        )
        assert client.get("/aina-schedules").json() == []

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PERMISSION_DENIED"


def test_invalid_builtin_debug_input_is_recorded_as_failed_without_calling_the_model() -> None:
    llm = ScriptedLLM([])
    with TestClient(create_app(settings=_settings(), llm=llm)) as client:
        created = client.post(
            "/aina-schedules", json={"aina_id": "unibot-memory", "name": "Builtin", "prompt": "Saved prompt"}
        ).json()
        failed = client.post(f"/aina-schedules/{created['id']}/run", json={"input": {}})
        history = client.get(f"/aina-schedules/{created['id']}/executions").json()

    assert failed.status_code == 200
    assert failed.json()["last_status"] == "failed"
    assert failed.json()["last_result"] is None
    assert failed.json()["input"] == created["input"]
    assert failed.json()["prompt"] == created["prompt"]
    assert failed.json()["next_run_at"] == created["next_run_at"]
    assert len(history) == 1
    assert history[0]["status"] == "failed"
    assert history[0]["input"] == {}
    assert llm.calls == []


def test_unexpected_agent_failure_is_recorded_without_leaking_exception_details() -> None:
    def fail(**_: Any):
        raise RuntimeError("private-api-key=never-persist-this")

    with TestClient(create_app(settings=_settings(), llm=ScriptedLLM([fail]))) as client:
        created = client.post("/aina-schedules", json={"name": "Failing Agent", "prompt": "Run"}).json()
        failed = client.post(f"/aina-schedules/{created['id']}/run", json={})
        history = client.get(f"/aina-schedules/{created['id']}/executions")

    assert failed.status_code == 200
    assert failed.json()["last_status"] == "failed"
    assert failed.json()["last_result"] is None
    assert failed.json()["last_error"]
    assert history.json()[0]["status"] == "failed"
    assert history.json()[0]["error"] == failed.json()["last_error"]
    assert "never-persist-this" not in failed.text + history.text
