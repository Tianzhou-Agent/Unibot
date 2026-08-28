from types import SimpleNamespace
from typing import cast

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.core.repository import WORKSPACES_RESOURCE
from tianzhou_agent_platform.core.workspace import WorkspaceCreate, WorkspaceUpdate
from tianzhou_agent_platform.main import create_app
from tianzhou_agent_platform.store.lifecycle import StorageStores
from tianzhou_agent_platform.store.repository import PersistentRepository, repository_tables
from tests.support.fake_llm import ScriptedLLM, assistant
from tests.test_aina_projects import FakeRepositoryMySqlStore, FakeRepositoryRedisStore


def _settings() -> AgentSettings:
    return AgentSettings(  # type: ignore[call-arg]
        _env_file=None,
        auth_secret=SecretStr("test-auth-secret-with-enough-entropy"),
        llm_base_url="https://model.invalid/v1",
        llm_api_key=SecretStr("test-key"),
        llm_model="test-model",
    )


def _register(client: TestClient, *, email: str, name: str) -> dict[str, object]:
    response = client.post(
        "/auth/register",
        json={"name": name, "email": email, "password": "correct-horse-battery"},
    )
    assert response.status_code == 201
    return response.json()["user"]


def test_workspace_crud_generates_storage_key_server_side() -> None:
    assert repository_tables[WORKSPACES_RESOURCE].name == "unibot_workspaces"

    with TestClient(create_app(settings=_settings(), llm=ScriptedLLM([]))) as client:
        created = client.post(
            "/workspaces",
            json={"name": "  Research  ", "description": "Workspace notes"},
        )
        workspace = created.json()
        listed = client.get("/workspaces")
        loaded = client.get(f"/workspaces/{workspace['id']}")
        updated = client.patch(
            f"/workspaces/{workspace['id']}",
            json={"name": "Published research", "description": "Ready"},
        )
        rejected_storage_key = client.post(
            "/workspaces",
            json={"name": "Invalid", "storage_key": "client-controlled"},
        )

    assert created.status_code == 201
    assert workspace["id"].startswith("workspace_")
    assert workspace["name"] == "Research"
    assert workspace["storage_key"].startswith("ws_")
    assert "/" not in workspace["storage_key"]
    assert listed.json() == [workspace]
    assert loaded.json() == workspace
    assert updated.status_code == 200
    assert updated.json()["name"] == "Published research"
    assert updated.json()["description"] == "Ready"
    assert updated.json()["storage_key"] == workspace["storage_key"]
    assert rejected_storage_key.status_code == 422


@pytest.mark.asyncio
async def test_persistent_workspaces_refresh_across_repository_instances() -> None:
    mysql = FakeRepositoryMySqlStore()
    redis = FakeRepositoryRedisStore()
    stores = cast(StorageStores, SimpleNamespace(mysql=mysql, redis=redis, nas=None))
    first = PersistentRepository(stores)
    second = PersistentRepository(stores)
    await first.initialize()
    await second.initialize()

    created = await first.create_workspace(
        WorkspaceCreate(user_id="user-a", tenant_id="tenant-a", name="Research")
    )

    assert await second.get_workspace(created.id) == created

    await first.update_workspace(created.id, WorkspaceUpdate(description="Shared notes"))
    updated = await second.update_workspace(created.id, WorkspaceUpdate(name="Published"))
    listed = await first.list_workspaces(user_id="user-a", tenant_id="tenant-a")

    assert updated.name == "Published"
    assert updated.description == "Shared notes"
    assert listed == [updated]


def test_workspace_and_conversation_ownership_are_bound_to_authenticated_actor() -> None:
    app = create_app(settings=_settings(), llm=ScriptedLLM([]), enforce_auth=True)

    with TestClient(app) as client:
        owner = _register(client, email="workspace-owner@example.com", name="Owner")
        workspace = client.post(
            "/workspaces",
            json={
                "name": "Owner workspace",
                "user_id": "spoofed-user",
                "tenant_id": "spoofed-tenant",
            },
        ).json()
        second = _register(client, email="workspace-second@example.com", name="Second")
        loaded = client.get(f"/workspaces/{workspace['id']}")
        updated = client.patch(f"/workspaces/{workspace['id']}", json={"name": "Stolen"})
        conversation = client.post(
            "/conversations",
            json={"title": "Not mine", "workspace_id": workspace["id"]},
        )

    assert workspace["user_id"] == owner["id"]
    assert workspace["tenant_id"] == owner["tenant_id"]
    assert second["id"] != owner["id"]
    assert loaded.status_code == 403
    assert updated.status_code == 403
    assert conversation.status_code == 403


def test_conversations_are_filtered_by_workspace_and_chat_rejects_mismatch() -> None:
    llm = ScriptedLLM([assistant("workspace answer"), assistant("new workspace answer")])
    with TestClient(create_app(settings=_settings(), llm=llm)) as client:
        first_workspace = client.post("/workspaces", json={"name": "First"}).json()
        second_workspace = client.post("/workspaces", json={"name": "Second"}).json()
        conversation = client.post(
            "/conversations",
            json={"title": "Scoped", "workspace_id": first_workspace["id"]},
        ).json()

        first_list = client.get(
            "/conversations",
            params={"workspace_id": first_workspace["id"]},
        )
        second_list = client.get(
            "/conversations",
            params={"workspace_id": second_workspace["id"]},
        )
        mismatch = client.post(
            "/chat",
            json={
                "message": "Wrong workspace",
                "conversation_id": conversation["id"],
                "workspace_id": second_workspace["id"],
            },
        )
        continued = client.post(
            "/chat",
            json={
                "message": "Correct workspace",
                "conversation_id": conversation["id"],
                "workspace_id": first_workspace["id"],
            },
        )
        created_by_chat = client.post(
            "/chat",
            json={"message": "New conversation", "workspace_id": second_workspace["id"]},
        )
        loaded = client.get(f"/conversations/{created_by_chat.json()['conversation_id']}")

    assert first_list.status_code == 200
    assert [item["id"] for item in first_list.json()] == [conversation["id"]]
    assert second_list.json() == []
    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "CONFLICT"
    assert continued.status_code == 200
    assert created_by_chat.status_code == 200
    assert loaded.json()["workspace_id"] == second_workspace["id"]
    assert len(llm.calls) == 2


def test_existing_independent_conversation_contract_remains_compatible() -> None:
    with TestClient(create_app(settings=_settings(), llm=ScriptedLLM([]))) as client:
        created = client.post("/conversations", json={"title": "Independent"})
        listed = client.get("/conversations")

    assert created.status_code == 201
    assert created.json()["workspace_id"] is None
    assert [item["id"] for item in listed.json()] == [created.json()["id"]]


def test_open_aina_accepts_workspace_context_and_rejects_conversation_mismatch() -> None:
    with TestClient(create_app(settings=_settings(), llm=ScriptedLLM([]))) as client:
        first_workspace = client.post("/workspaces", json={"name": "First"}).json()
        second_workspace = client.post("/workspaces", json={"name": "Second"}).json()
        conversation = client.post(
            "/conversations",
            json={"title": "Scoped", "workspace_id": first_workspace["id"]},
        ).json()

        opened = client.post(
            "/ainas/unibot-code-runner/open",
            json={
                "conversation_id": conversation["id"],
                "workspace_id": first_workspace["id"],
            },
        )
        mismatched = client.post(
            "/ainas/unibot-code-runner/open",
            json={
                "conversation_id": conversation["id"],
                "workspace_id": second_workspace["id"],
            },
        )

    assert opened.status_code == 200
    assert mismatched.status_code == 409
    assert mismatched.json()["error"]["code"] == "CONFLICT"
