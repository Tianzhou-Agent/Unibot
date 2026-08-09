from __future__ import annotations

import asyncio
import hashlib
import io
import json
import zipfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tianzhou_agent_platform.aina.project import AinaProjectRecord, build_project_archive, validate_project_archive
from tianzhou_agent_platform.aina.project_service import (
    AinaProjectArtifactStore,
    AinaProjectService,
    NasAinaProjectArtifactStore,
)
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.api.dependencies import RequestActor, request_actor
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.core.repository import AINA_PROJECTS_RESOURCE, InMemoryRepository
from tianzhou_agent_platform.main import create_app
from tianzhou_agent_platform.store.errors import StorageNotFoundError, StorageValidationError
from tianzhou_agent_platform.store.lifecycle import StorageStores
from tianzhou_agent_platform.store.models import StoragePath, StorePage, StoreQuery, StoreRecord
from tianzhou_agent_platform.store.nas.filesystem import NasStore
from tianzhou_agent_platform.store.repository import PersistentRepository, repository_tables
from tests.support.fake_llm import ScriptedLLM, assistant, call_first_tool


class InspectableArtifactStore(AinaProjectArtifactStore):
    def __init__(self) -> None:
        self.artifacts: dict[str, bytes] = {}

    async def write(self, path: StoragePath, payload: bytes) -> bool:
        existing = self.artifacts.get(path.relative_path)
        if existing is not None:
            if existing != payload:
                raise AssertionError("test artifact content-address collision")
            return False
        self.artifacts[path.relative_path] = bytes(payload)
        return True

    async def read(self, path: StoragePath) -> bytes:
        try:
            return self.artifacts[path.relative_path]
        except KeyError as exc:
            raise StorageNotFoundError("test artifact was not found") from exc

    async def delete(self, path: StoragePath) -> None:
        self.artifacts.pop(path.relative_path, None)


class FailingDeleteArtifactStore(InspectableArtifactStore):
    fail_delete = True

    async def delete(self, path: StoragePath) -> None:
        if self.fail_delete:
            raise StorageValidationError("test artifact deletion failure")
        await super().delete(path)


class FailingReserveRepository(InMemoryRepository):
    async def _save_record(self, resource: str, record_id: str, value: Any) -> None:
        del record_id, value
        if resource == AINA_PROJECTS_RESOURCE:
            raise StorageValidationError("test metadata reservation failure")


class FailingValidationOnceRepository(InMemoryRepository):
    def __init__(self) -> None:
        super().__init__()
        self.fail_validation = True

    async def _save_record(self, resource: str, record_id: str, value: Any) -> None:
        del record_id
        if (
            resource == AINA_PROJECTS_RESOURCE
            and isinstance(value, AinaProjectRecord)
            and value.status == "validated"
            and self.fail_validation
        ):
            self.fail_validation = False
            raise StorageValidationError("test metadata validation failure")


class FakeRepositoryMySqlStore:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, StoreRecord]] = {}

    async def create_tables(self, metadata: Any) -> None:
        del metadata

    async def read(self, resource: str, record_id: str) -> StoreRecord | None:
        return self.records.get(resource, {}).get(record_id)

    async def create(self, resource: str, values: dict[str, Any]) -> StoreRecord:
        record_id = str(values["id"])
        if record_id in self.records.get(resource, {}):
            raise StorageValidationError("test duplicate record")
        record = StoreRecord(
            resource=resource,
            id=record_id,
            values={key: value for key, value in values.items() if key != "id"},
        )
        self.records.setdefault(resource, {})[str(record.id)] = record
        return record

    async def update(self, resource: str, record_id: str, values: dict[str, Any]) -> StoreRecord:
        record = self.records[resource][record_id]
        updated = StoreRecord(resource=resource, id=record_id, values={**record.values, **values})
        self.records[resource][record_id] = updated
        return updated

    async def delete(self, resource: str, record_id: str) -> None:
        self.records.get(resource, {}).pop(record_id, None)

    async def query(self, resource: str, query: StoreQuery) -> StorePage:
        records = list(self.records.get(resource, {}).values())
        page = records[query.offset : query.offset + query.limit]
        return StorePage(items=page, limit=query.limit, offset=query.offset)


class FakeRepositoryRedisStore:
    def __init__(self) -> None:
        self.acquire_lease = True

    async def set(self, namespace: str, key: str, value: Any) -> None:
        del namespace, key, value

    async def delete(self, namespace: str, key: str) -> None:
        del namespace, key

    @asynccontextmanager
    async def lease(self, namespace: str, key: str, *, ttl_seconds: int) -> AsyncIterator[bool]:
        del namespace, key, ttl_seconds
        yield self.acquire_lease


def _settings() -> AgentSettings:
    return AgentSettings(
        _env_file=None,
        llm_base_url="https://model.invalid/v1",
        llm_api_key="test-key",
        llm_model="test-model",
    )


def _use_actor(app: FastAPI, *, user_id: str, tenant_id: str) -> None:
    actor = RequestActor(user_id=user_id, tenant_id=tenant_id)
    app.dependency_overrides[request_actor] = lambda: actor


def _managed_manifest(**overrides: Any) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "protocol_version": "1.0",
        "aina": {
            "id": "com.example.managed",
            "name": "Managed AINA",
            "version": "0.1.0",
            "description": "A packaged managed AINA.",
            "publisher": {"id": "tests", "name": "Tests"},
        },
        "runtime": {
            "type": "managed",
            "language": "python",
            "entrypoint": "src/main.py:invoke",
            "dependency_file": "requirements.txt",
        },
        "capabilities": {"skills": [], "tools": [], "ui": [], "events": []},
        "permissions": [],
        "authentication": {"type": "none"},
    }
    manifest.update(overrides)
    return manifest


def _archive(manifest: dict[str, Any], **files: str) -> bytes:
    return build_project_archive(
        {
            "aina.yaml": yaml.safe_dump(manifest, sort_keys=False),
            "src/main.py": "async def invoke(request):\n    return request\n",
            "requirements.txt": "",
            **files,
        }
    )


def test_scaffold_is_deterministic_and_platform_can_validate_it() -> None:
    payload = {
        "aina_id": "com.example.scaffold",
        "name": "Scaffold AINA",
        "description": "Created from the platform project template.",
        "language": "python",
    }
    with TestClient(create_app(settings=_settings(), llm=ScriptedLLM([]))) as client:
        first = client.post("/aina-projects/scaffold", json=payload)
        second = client.post("/aina-projects/scaffold", json=payload)
        validated = client.post(
            "/aina-projects/validate",
            files={"file": ("scaffold.aina.zip", first.content, "application/zip")},
        )

    assert first.status_code == 200
    assert first.headers["content-type"] == "application/zip"
    assert first.content == second.content
    with zipfile.ZipFile(io.BytesIO(first.content)) as archive:
        assert set(archive.namelist()) == {"README.md", "aina.yaml", "requirements.txt", "src/main.py"}
    report = validated.json()
    assert validated.status_code == 200
    assert report["format_version"] == "1.0"
    assert report["archive_sha256"] == hashlib.sha256(first.content).hexdigest()
    assert report["manifest"]["aina"]["id"] == "com.example.scaffold"
    assert report["manifest"]["runtime"]["type"] == "managed"
    assert report["ready_for_registration"] is False
    assert "require deployment" in report["warnings"][0]


def test_node_scaffold_uses_node_entrypoint_and_package_metadata() -> None:
    with TestClient(create_app(settings=_settings(), llm=ScriptedLLM([]))) as client:
        response = client.post(
            "/aina-projects/scaffold",
            json={
                "aina_id": "com.example.node",
                "name": "Node AINA",
                "description": "Node project",
                "language": "node",
            },
        )
        validated = client.post(
            "/aina-projects/validate",
            files={"file": ("node.aina.zip", response.content, "application/zip")},
        )

    assert response.status_code == 200
    assert validated.status_code == 200
    assert validated.json()["manifest"]["runtime"] == {
        "type": "managed",
        "language": "node",
        "entrypoint": "src/index.mjs:invoke",
        "dependency_file": "package.json",
    }


def test_project_validation_rejects_missing_entrypoint_and_invalid_schema() -> None:
    missing_entrypoint = build_project_archive(
        {
            "aina.yaml": yaml.safe_dump(_managed_manifest(), sort_keys=False),
            "requirements.txt": "",
        }
    )
    invalid_manifest = _managed_manifest()
    invalid_manifest["capabilities"]["tools"] = [
        {
            "id": "bad.tool",
            "name": "Bad tool",
            "description": "Invalid schema",
            "input_schema": {"type": "string", "minLength": -1},
        }
    ]
    invalid_schema = _archive(invalid_manifest)

    with TestClient(create_app(settings=_settings(), llm=ScriptedLLM([]))) as client:
        missing = client.post(
            "/aina-projects/validate",
            files={"file": ("missing.zip", missing_entrypoint, "application/zip")},
        )
        invalid = client.post(
            "/aina-projects/validate",
            files={"file": ("invalid.zip", invalid_schema, "application/zip")},
        )

    assert missing.status_code == 422
    assert "entrypoint 'src/main.py' is missing" in missing.json()["error"]["message"]
    assert invalid.status_code == 422
    assert "minLength must be a non-negative integer" in invalid.json()["error"]["message"]


def test_project_validation_rejects_archive_path_traversal() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w") as archive:
        archive.writestr("../aina.yaml", yaml.safe_dump(_managed_manifest(), sort_keys=False))

    with TestClient(create_app(settings=_settings(), llm=ScriptedLLM([]))) as client:
        response = client.post(
            "/aina-projects/validate",
            files={"file": ("unsafe.zip", buffer.getvalue(), "application/zip")},
        )

    assert response.status_code == 422
    assert response.json()["error"]["source"] == "aina_project"
    assert "escapes the project root" in response.json()["error"]["message"]


def test_managed_project_manifest_cannot_be_registered_before_deployment() -> None:
    with TestClient(create_app(settings=_settings(), llm=ScriptedLLM([]))) as client:
        response = client.post("/ainas", json=_managed_manifest())

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PERMISSION_DENIED"
    assert "deployed remote" in response.json()["error"]["message"]


def test_imported_managed_project_can_be_listed_downloaded_and_deleted_without_registration() -> None:
    archive = _archive(_managed_manifest())
    artifacts = InspectableArtifactStore()
    app = create_app(
        settings=_settings(),
        llm=ScriptedLLM([]),
        aina_project_artifact_store=artifacts,
    )
    _use_actor(app, user_id="user-a", tenant_id="tenant-a")

    with TestClient(app) as client:
        imported = client.post(
            "/aina-projects",
            files={"file": ("managed.aina.zip", archive, "application/zip")},
        )
        project = imported.json()
        listed = client.get("/aina-projects")
        downloaded = client.get(f"/aina-projects/{project['id']}/archive")
        registered = client.get("/ainas")
        deleted = client.delete(f"/aina-projects/{project['id']}")
        after_delete = client.get("/aina-projects")
        missing = client.get(f"/aina-projects/{project['id']}/archive")

    assert imported.status_code == 201
    assert project["status"] == "validated"
    assert project["source_filename"] == "managed.aina.zip"
    assert project["archive_sha256"] == hashlib.sha256(archive).hexdigest()
    assert project["manifest"]["runtime"]["type"] == "managed"
    assert "archive_path" not in project
    assert listed.status_code == 200
    assert listed.json() == [project]
    assert downloaded.status_code == 200
    assert downloaded.content == archive
    assert "managed.aina.zip" in downloaded.headers["content-disposition"]
    assert all(item["manifest"]["aina"]["id"] != "com.example.managed" for item in registered.json())
    assert deleted.status_code == 204
    assert after_delete.json() == []
    assert missing.status_code == 404
    assert artifacts.artifacts == {}


def test_managed_project_can_be_deployed_installed_invoked_and_undeployed(tmp_path: Path) -> None:
    handler = """
async def invoke(request):
    name = request["input"]["input"]
    return {
        "request_id": request["request_id"],
        "status": "completed",
        "outputs": [{"type": "text", "content": f"Hello, {name}!"}],
        "usage": {"input_tokens": 0, "output_tokens": 0},
        "trace_id": request["trace"]["trace_id"],
    }
"""
    archive = _archive(_managed_manifest(), **{"src/main.py": handler})
    llm = ScriptedLLM(
        [
            call_first_tool(prefix="builtin_list_app_"),
            assistant("The managed AINA is available."),
            call_first_tool(prefix="aina_", arguments='{"input":"Ada"}'),
            assistant("The managed AINA greeted Ada."),
        ]
    )
    settings = AgentSettings(
        _env_file=None,
        llm_base_url="https://model.invalid/v1",
        llm_api_key="test-key",
        llm_model="test-model",
        sandbox_workspace_root=tmp_path / "sandboxes",
    )
    with TestClient(create_app(settings=settings, llm=llm)) as client:
        imported = client.post(
            "/aina-projects",
            files={"file": ("managed.aina.zip", archive, "application/zip")},
        )
        project_id = imported.json()["id"]
        deployed = client.post(f"/aina-projects/{project_id}/deploy")
        deployed_again = client.post(f"/aina-projects/{project_id}/deploy")
        registered = client.get("/ainas/com.example.managed")
        installed = client.post("/ainas/com.example.managed/install", json={})
        listed_apps = client.post(
            "/chat",
            json={"message": "List applications", "capability": "builtin:list_app"},
        )
        opened = client.post(
            "/ainas/com.example.managed/open",
            json={"conversation_id": listed_apps.json()["conversation_id"]},
        )
        chat = client.post(
            "/chat",
            json={
                "message": "Greet Ada",
                "capability": "aina:com.example.managed",
            },
        )
        blocked_delete = client.delete(f"/aina-projects/{project_id}")
        undeployed = client.delete(f"/aina-projects/{project_id}/deployment")
        missing = client.get("/ainas/com.example.managed")
        installations = client.get("/installations")

    assert deployed.status_code == 200
    assert deployed.json()["status"] == "deployed"
    assert deployed.json()["deployed_at"] is not None
    assert deployed_again.status_code == 200
    assert deployed_again.json() == deployed.json()
    assert registered.status_code == 200
    assert registered.json()["manifest"]["runtime"]["type"] == "managed"
    assert installed.status_code == 200
    assert listed_apps.status_code == 200
    app_widget = listed_apps.json()["widgets"][0]
    assert "com.example.managed" in {item["aina_id"] for item in app_widget["apps"]}
    assert opened.status_code == 200
    assert opened.json()["main_widget"]["kind"] == "panel"
    assert opened.json()["main_widget"]["title"] == "Managed AINA"
    assert chat.status_code == 200
    assert chat.json()["status"] == "completed"
    tool_message = next(
        item for item in llm.calls[3]["messages"] if item.get("tool_call_id") == "call_1"
    )
    tool_result = json.loads(tool_message["content"])
    assert tool_result["status"] == "completed"
    assert tool_result["outputs"][0]["content"] == "Hello, Ada!"
    assert blocked_delete.status_code == 409
    assert undeployed.status_code == 200
    assert undeployed.json()["status"] == "validated"
    assert missing.status_code == 404
    assert installations.json() == []


def test_project_import_is_idempotent_actor_scoped_and_rejects_changed_content() -> None:
    archive = _archive(_managed_manifest())
    changed_archive = _archive(_managed_manifest(), **{"src/extra.py": "CHANGED = True\n"})
    app = create_app(settings=_settings(), llm=ScriptedLLM([]))
    _use_actor(app, user_id="user-a", tenant_id="tenant-a")

    with TestClient(app) as client:
        first = client.post(
            "/aina-projects",
            files={"file": ("first.zip", archive, "application/zip")},
        )
        repeated = client.post(
            "/aina-projects",
            files={"file": ("renamed.zip", archive, "application/zip")},
        )
        _use_actor(app, user_id="user-b", tenant_id="tenant-a")
        other_actor = client.post(
            "/aina-projects",
            files={"file": ("other.zip", archive, "application/zip")},
        )
        second_list = client.get("/aina-projects")
        _use_actor(app, user_id="user-a", tenant_id="tenant-a")
        changed = client.post(
            "/aina-projects",
            files={"file": ("changed.zip", changed_archive, "application/zip")},
        )
        first_list = client.get("/aina-projects")
        _use_actor(app, user_id="user-b", tenant_id="tenant-a")
        foreign_download = client.get(f"/aina-projects/{first.json()['id']}/archive")
        foreign_delete = client.delete(f"/aina-projects/{first.json()['id']}")

    assert first.status_code == 201
    assert repeated.status_code == 201
    assert repeated.json() == first.json()
    assert other_actor.status_code == 201
    assert other_actor.json()["id"] != first.json()["id"]
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "CONFLICT"
    assert len(first_list.json()) == 1
    assert len(second_list.json()) == 1
    assert foreign_download.status_code == 403
    assert foreign_delete.status_code == 403


def test_project_import_rejects_remote_runtime_without_registering_it() -> None:
    remote_manifest = _managed_manifest(
        runtime={
            "type": "remote",
            "endpoint": "https://remote.invalid/aina",
            "protocol": "aina",
        }
    )
    archive = _archive(remote_manifest)
    artifacts = InspectableArtifactStore()

    app = create_app(
        settings=_settings(),
        llm=ScriptedLLM([]),
        aina_project_artifact_store=artifacts,
    )
    _use_actor(app, user_id="user-a", tenant_id="tenant-a")
    with TestClient(app) as client:
        imported = client.post(
            "/aina-projects",
            files={"file": ("remote.zip", archive, "application/zip")},
        )
        projects = client.get("/aina-projects")

    assert imported.status_code == 422
    assert "registration API" in imported.json()["error"]["message"]
    assert projects.json() == []
    assert artifacts.artifacts == {}


def test_project_archive_download_fails_closed_when_blob_is_tampered() -> None:
    archive = _archive(_managed_manifest())
    artifacts = InspectableArtifactStore()
    app = create_app(
        settings=_settings(),
        llm=ScriptedLLM([]),
        aina_project_artifact_store=artifacts,
    )
    _use_actor(app, user_id="user-a", tenant_id="tenant-a")

    with TestClient(app) as client:
        imported = client.post(
            "/aina-projects",
            files={"file": ("managed.zip", archive, "application/zip")},
        )
        artifact_path = next(iter(artifacts.artifacts))
        artifacts.artifacts[artifact_path] = b"tampered"
        downloaded = client.get(f"/aina-projects/{imported.json()['id']}/archive")

    assert downloaded.status_code == 500
    assert downloaded.json()["error"]["code"] == "INTEGRITY_CHECK_FAILED"
    assert downloaded.json()["error"]["source"] == "aina_project"


async def test_persistent_repository_restores_aina_project_metadata() -> None:
    mysql = FakeRepositoryMySqlStore()
    redis = FakeRepositoryRedisStore()
    stores = cast(StorageStores, SimpleNamespace(mysql=mysql, redis=redis, nas=None))
    artifacts = InspectableArtifactStore()
    archive = _archive(_managed_manifest())

    first_repository = PersistentRepository(stores)
    second_repository = PersistentRepository(stores)
    await first_repository.initialize()
    await second_repository.initialize()
    imported = await AinaProjectService(first_repository, artifacts).import_project(
        archive,
        source_filename="managed.zip",
        user_id="user-a",
        tenant_id="tenant-a",
    )

    listed = await second_repository.list_aina_projects(user_id="user-a", tenant_id="tenant-a")
    restored = await second_repository.get_aina_project(
        imported.id,
        user_id="user-a",
        tenant_id="tenant-a",
    )

    assert AINA_PROJECTS_RESOURCE in repository_tables
    assert imported.id in mysql.records[AINA_PROJECTS_RESOURCE]
    assert listed == [imported]
    assert restored == imported

    redis.acquire_lease = False
    contended_result = await second_repository.mark_aina_project_validated(
        imported.id,
        archive_sha256=imported.archive_sha256,
        user_id="user-a",
        tenant_id="tenant-a",
    )
    assert contended_result == imported
    redis.acquire_lease = True

    next_identity = imported.manifest.aina.model_copy(update={"version": "0.2.0"})
    pending = imported.model_copy(
        update={
            "id": f"aina_project_{'1' * 32}",
            "manifest": imported.manifest.model_copy(update={"aina": next_identity}),
            "status": "importing",
        }
    )
    await second_repository.create_aina_project(pending)
    redis.acquire_lease = False
    with pytest.raises(PlatformError, match="being imported") as busy:
        await first_repository.mark_aina_project_validated(
            pending.id,
            archive_sha256=pending.archive_sha256,
            user_id="user-a",
            tenant_id="tenant-a",
        )
    assert busy.value.retryable is True
    redis.acquire_lease = True

    repeated = await AinaProjectService(second_repository, artifacts).import_project(
        archive,
        source_filename="renamed.zip",
        user_id="user-a",
        tenant_id="tenant-a",
    )
    assert repeated == imported

    changed_archive = _archive(_managed_manifest(), **{"src/extra.py": "CHANGED = True\n"})
    changed_report = validate_project_archive(changed_archive)
    changed_record = imported.model_copy(
        update={
            "source_filename": "changed.zip",
            "archive_sha256": changed_report.archive_sha256,
            "size_bytes": changed_report.size_bytes,
            "uncompressed_size_bytes": changed_report.uncompressed_size_bytes,
            "file_count": changed_report.file_count,
        }
    )
    with pytest.raises(PlatformError, match="different content"):
        await second_repository.create_aina_project(changed_record)
    with pytest.raises(PlatformError, match="different content") as raised:
        await AinaProjectService(second_repository, artifacts).import_project(
            changed_archive,
            source_filename="changed.zip",
            user_id="user-a",
            tenant_id="tenant-a",
        )
    assert raised.value.status_code == 409
    assert (
        await first_repository.get_aina_project(
            imported.id,
            user_id="user-a",
            tenant_id="tenant-a",
        )
        == imported
    )


async def test_metadata_reservation_failure_does_not_write_project_blob() -> None:
    repository = FailingReserveRepository()
    artifacts = InspectableArtifactStore()
    service = AinaProjectService(repository, artifacts)

    with pytest.raises(StorageValidationError, match="reservation failure"):
        await service.import_project(
            _archive(_managed_manifest()),
            source_filename="managed.zip",
            user_id="user-a",
            tenant_id="tenant-a",
        )

    assert await repository.list_aina_projects(user_id="user-a", tenant_id="tenant-a") == []
    assert artifacts.artifacts == {}


async def test_failed_validation_transition_is_visible_and_resumes_same_import() -> None:
    repository = FailingValidationOnceRepository()
    artifacts = InspectableArtifactStore()
    service = AinaProjectService(repository, artifacts)
    archive = _archive(_managed_manifest())

    with pytest.raises(StorageValidationError, match="validation failure"):
        await service.import_project(
            archive,
            source_filename="managed.zip",
            user_id="user-a",
            tenant_id="tenant-a",
        )

    pending = await service.list_projects(user_id="user-a", tenant_id="tenant-a")
    assert len(pending) == 1
    assert pending[0].status == "importing"
    assert len(artifacts.artifacts) == 1
    with pytest.raises(PlatformError, match="has not completed"):
        await service.get_archive(
            pending[0].id,
            user_id="user-a",
            tenant_id="tenant-a",
        )

    changed_archive = _archive(_managed_manifest(), **{"src/extra.py": "CHANGED = True\n"})
    with pytest.raises(PlatformError, match="different content"):
        await service.import_project(
            changed_archive,
            source_filename="changed.zip",
            user_id="user-a",
            tenant_id="tenant-a",
        )
    assert len(artifacts.artifacts) == 1

    resumed = await service.import_project(
        archive,
        source_filename="renamed.zip",
        user_id="user-a",
        tenant_id="tenant-a",
    )
    assert resumed.id == pending[0].id
    assert resumed.source_filename == "managed.zip"
    assert resumed.status == "validated"
    _, downloaded = await service.get_archive(
        resumed.id,
        user_id="user-a",
        tenant_id="tenant-a",
    )
    assert downloaded == archive


async def test_blob_delete_failure_keeps_project_metadata_for_retry() -> None:
    repository = InMemoryRepository()
    artifacts = FailingDeleteArtifactStore()
    service = AinaProjectService(repository, artifacts)
    imported = await service.import_project(
        _archive(_managed_manifest()),
        source_filename="managed.zip",
        user_id="user-a",
        tenant_id="tenant-a",
    )

    with pytest.raises(StorageValidationError, match="deletion failure"):
        await service.delete_project(
            imported.id,
            user_id="user-a",
            tenant_id="tenant-a",
        )

    assert await service.list_projects(user_id="user-a", tenant_id="tenant-a") == [imported]
    assert artifacts.artifacts
    artifacts.fail_delete = False
    await service.delete_project(
        imported.id,
        user_id="user-a",
        tenant_id="tenant-a",
    )
    assert await service.list_projects(user_id="user-a", tenant_id="tenant-a") == []
    assert artifacts.artifacts == {}


async def test_nas_artifact_store_concurrent_writes_publish_one_complete_archive(tmp_path: Path) -> None:
    payload = _archive(_managed_manifest())
    digest = hashlib.sha256(payload).hexdigest()

    for iteration in range(20):
        stores = [NasAinaProjectArtifactStore(NasStore(tmp_path)) for _ in range(8)]
        path = StoragePath(relative_path=f"aina-projects/test/{iteration}-{digest}.aina.zip")
        results = await asyncio.gather(*(store.write(path, payload) for store in stores))

        assert results.count(True) == 1
        assert results.count(False) == 7
        assert await stores[0].read(path) == payload
    assert list(tmp_path.rglob("*.tmp-*")) == []


def test_undeploy_keeps_shared_registration_owned_by_another_user() -> None:
    import asyncio

    from tianzhou_agent_platform.aina.managed import ManagedAinaRuntime
    from tianzhou_agent_platform.aina.project import AinaProjectRecord
    from tianzhou_agent_platform.aina.protocol.models import AinaManifest, AinaRecord
    from tianzhou_agent_platform.core.repository import InMemoryRepository
    from tianzhou_agent_platform.sandbox.models import SandboxExecution, SandboxExecutionRequest

    repository = InMemoryRepository()
    manifest = AinaManifest.model_validate(_managed_manifest())
    project = AinaProjectRecord(
        id="aina_project_" + "a" * 32,
        user_id="user_b",
        tenant_id="default",
        source_filename="proj.zip",
        archive_sha256="b" * 64,
        size_bytes=100,
        uncompressed_size_bytes=100,
        file_count=1,
        manifest=manifest,
        status="deployed",
    )

    class FakeProjects:
        async def get_project(self, project_id, *, user_id, tenant_id):
            return project.model_copy(update={"id": project_id})

        async def set_aina_project_deployed(self, project_id, *, deployed, user_id, tenant_id):
            project.status = "deployed" if deployed else "validated"
            return project

    class FakeSandboxes:
        driver = type("Driver", (), {"name": "fake"})()

        async def execute(self, request: SandboxExecutionRequest) -> SandboxExecution:
            return SandboxExecution(
                id="exec_1",
                sandbox_id="sbx_1",
                user_id=request.user_id,
                tenant_id=request.tenant_id,
                language="python",
                script=request.script,
                status="succeeded",
                stdout="",
                stderr="",
                exit_code=0,
                duration_ms=1.0,
                started_at=project.created_at,
            )

    async def scenario() -> None:
        # Shared registration owned by user A; user B undeploys their project.
        await repository.register_aina(
            AinaRecord(manifest=manifest, owner_user_id="user_a", owner_tenant_id="default")
        )
        await repository.create_aina_project(project)
        await repository.create_aina_project(
            project.model_copy(update={"id": "aina_project_" + "c" * 32, "user_id": "user_a"})
        )
        runtime = ManagedAinaRuntime(
            settings=_settings(),
            repository=repository,
            projects=FakeProjects(),  # type: ignore[arg-type]
            sandboxes=FakeSandboxes(),  # type: ignore[arg-type]
        )
        await runtime.undeploy(project.id, user_id="user_b", tenant_id="default")
        # The shared registration survives because user B did not create it.
        registered = await repository.get_aina(manifest.aina.id)
        assert registered.owner_user_id == "user_a"

        # The owning user's undeploy removes the registration.
        project2 = project.model_copy(update={"id": "aina_project_" + "c" * 32})
        await runtime.undeploy(project2.id, user_id="user_a", tenant_id="default")
        from tianzhou_agent_platform.core.errors import PlatformError

        try:
            await repository.get_aina(manifest.aina.id)
            raise AssertionError("registration should have been removed")
        except PlatformError as exc:
            assert exc.code == "RESOURCE_NOT_FOUND"

    asyncio.run(scenario())


def test_owner_undeploy_keeps_registration_while_sharer_still_deployed() -> None:
    import asyncio

    from tianzhou_agent_platform.aina.managed import ManagedAinaRuntime
    from tianzhou_agent_platform.aina.project import AinaProjectRecord
    from tianzhou_agent_platform.aina.protocol.models import AinaManifest, AinaRecord
    from tianzhou_agent_platform.core.repository import InMemoryRepository
    from tianzhou_agent_platform.sandbox.models import SandboxExecution, SandboxExecutionRequest

    repository = InMemoryRepository()
    manifest = AinaManifest.model_validate(_managed_manifest())
    owner_project = AinaProjectRecord(
        id="aina_project_" + "d" * 32,
        user_id="user_a",
        tenant_id="default",
        source_filename="proj.zip",
        archive_sha256="e" * 64,
        size_bytes=100,
        uncompressed_size_bytes=100,
        file_count=1,
        manifest=manifest,
        status="deployed",
    )

    class FakeProjects:
        async def get_project(self, project_id, *, user_id, tenant_id):
            return owner_project.model_copy(update={"id": project_id, "user_id": user_id})

        async def set_aina_project_deployed(self, project_id, *, deployed, user_id, tenant_id):
            return owner_project.model_copy(
                update={"id": project_id, "user_id": user_id, "status": "deployed" if deployed else "validated"}
            )

    class FakeSandboxes:
        driver = type("Driver", (), {"name": "fake"})()

        async def execute(self, request: SandboxExecutionRequest) -> SandboxExecution:
            return SandboxExecution(
                id="exec_1",
                sandbox_id="sbx_1",
                user_id=request.user_id,
                tenant_id=request.tenant_id,
                language="python",
                script=request.script,
                status="succeeded",
                stdout="",
                stderr="",
                exit_code=0,
                duration_ms=1.0,
                started_at=owner_project.created_at,
            )

    async def scenario() -> None:
        await repository.register_aina(
            AinaRecord(manifest=manifest, owner_user_id="user_a", owner_tenant_id="default")
        )
        # A sharer (user_b) also has a live deployment of the same manifest.
        await repository.create_aina_project(
            owner_project.model_copy(update={"id": "aina_project_" + "f" * 32, "user_id": "user_b"})
        )
        await repository.create_aina_project(owner_project)
        runtime = ManagedAinaRuntime(
            settings=_settings(),
            repository=repository,
            projects=FakeProjects(),  # type: ignore[arg-type]
            sandboxes=FakeSandboxes(),  # type: ignore[arg-type]
        )
        await runtime.undeploy(owner_project.id, user_id="user_a", tenant_id="default")
        # The owner's undeploy keeps the shared registration because user_b
        # still has a deployed project referencing it.
        registered = await repository.get_aina(manifest.aina.id)
        assert registered.owner_user_id == "user_a"

    asyncio.run(scenario())
