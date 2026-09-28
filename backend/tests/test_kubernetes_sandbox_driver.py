from __future__ import annotations

import json

import httpx
import pytest
from pydantic import ValidationError

from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.core.repository import InMemoryRepository
from tianzhou_agent_platform.sandbox.drivers import KubernetesSandboxDriver
from tianzhou_agent_platform.sandbox.factory import create_sandbox_service
from tianzhou_agent_platform.sandbox.models import SandboxExecutionRequest, SandboxRecord


@pytest.mark.asyncio
async def test_kubernetes_driver_creates_waits_executes_stops_and_resets() -> None:
    requests: list[tuple[str, str]] = []
    files: dict[str, bytes] = {}
    get_count = 0
    deleting = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal get_count, deleting
        requests.append((request.method, str(request.url)))
        if request.url.host == "unibot-test.unibot-sandboxes.svc.cluster.local":
            if request.url.path.startswith("/files/"):
                path = request.url.path.removeprefix("/files/")
                if request.method == "PUT":
                    if request.url.params.get("overwrite") == "false" and path in files:
                        return httpx.Response(409)
                    files[path] = request.content
                    return httpx.Response(204)
                if request.method == "GET":
                    if path not in files:
                        return httpx.Response(404)
                    return httpx.Response(200, content=files[path])
                if request.method == "DELETE":
                    files.pop(path, None)
                    return httpx.Response(204)
            assert request.method == "POST"
            payload = json.loads(request.content)
            assert payload["language"] == "python"
            return httpx.Response(
                200,
                json={
                    "status": "succeeded",
                    "stdout": "kubernetes-ready\n",
                    "stderr": "",
                    "exit_code": 0,
                    "duration_ms": 12.5,
                    "truncated": False,
                },
            )
        if request.method == "DELETE":
            deleting = True
            return httpx.Response(202, json={})
        if request.method == "GET":
            get_count += 1
            if deleting:
                return httpx.Response(404, json={"message": "not found"})
            if get_count == 1:
                return httpx.Response(404, json={"message": "not found"})
            return httpx.Response(
                200,
                json={
                    "spec": {
                        "desiredState": "Running",
                        "image": "unibot/sandboxd:test",
                        "runtimeClassName": "gvisor",
                        "workspaceId": "workspace-test",
                        "workspaceStorageKey": "ws_test",
                        "workspacePersistentVolumeClaim": "shared-workspaces",
                    },
                    "status": {
                        "phase": "Ready",
                        "endpoint": "unibot-test.unibot-sandboxes.svc.cluster.local",
                        "workspaceMountReady": True,
                        "workspaceStorageKey": "ws_test",
                        "workspacePersistentVolumeClaim": "shared-workspaces",
                    },
                },
            )
        if request.method == "POST":
            body = json.loads(request.content)
            assert body["kind"] == "UserSandbox"
            assert body["spec"]["runtimeClassName"] == "gvisor"
            assert body["spec"]["workspaceId"] == "workspace-test"
            assert body["spec"]["workspaceStorageKey"] == "ws_test"
            assert body["spec"]["workspacePersistentVolumeClaim"] == "shared-workspaces"
            return httpx.Response(
                201,
                json={"spec": body["spec"], "status": {"phase": "Provisioning"}},
            )
        if request.method == "PATCH":
            return httpx.Response(200, json={"spec": {"desiredState": "Stopped"}})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    driver = KubernetesSandboxDriver(
        api_url="https://kubernetes.test",
        namespace="unibot-sandboxes",
        token="test-token",
        ca_file=None,
        runtime_class="gvisor",
        workspace_pvc="shared-workspaces",
        ready_timeout_seconds=1,
        client=client,
    )
    sandbox = SandboxRecord(
        id="sandbox-test",
        user_id="user-1",
        tenant_id="tenant-1",
        workspace_id="workspace-test",
        workspace_storage_key="ws_test",
        image="unibot/sandboxd:test",
        driver="kubernetes",
        runtime_name="unibot-test",
        workspace="/workspace",
    )

    state = await driver.ensure(sandbox)
    assert state.status == "provisioning"

    result = await driver.execute(
        sandbox,
        SandboxExecutionRequest(
            user_id="user-1",
            tenant_id="tenant-1",
            workspace_id="workspace-test",
            language="python",
            script="print('kubernetes-ready')",
        ),
    )
    assert result.status == "succeeded"
    assert result.stdout == "kubernetes-ready\n"

    await driver.write_file(sandbox, "managed-ainas/request.json", b'{"name":"Ada"}', overwrite=True)
    assert await driver.read_file(sandbox, "managed-ainas/request.json") == b'{"name":"Ada"}'
    await driver.delete_file(sandbox, "managed-ainas/request.json")
    assert files == {}

    stopped = await driver.stop(sandbox)
    assert stopped.status == "stopped"
    await driver.reset(sandbox)
    assert ("DELETE", driver._resource_url("unibot-test")) in requests
    await client.aclose()


@pytest.mark.asyncio
async def test_kubernetes_driver_rejects_workspace_without_shared_claim() -> None:
    requests = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(500)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    driver = KubernetesSandboxDriver(
        api_url="https://kubernetes.test",
        namespace="unibot-sandboxes",
        token="test-token",
        ca_file=None,
        runtime_class="gvisor",
        client=client,
    )
    sandbox = SandboxRecord(
        id="sandbox-workspace",
        user_id="user-1",
        tenant_id="tenant-1",
        workspace_id="workspace-test",
        workspace_storage_key="ws_test",
        image="unibot/sandboxd:test",
        driver="kubernetes",
        runtime_name="unibot-workspace-test",
        workspace="/workspace",
    )

    with pytest.raises(PlatformError) as caught:
        await driver.ensure(sandbox)

    assert caught.value.code == "DEPENDENCY_FAILED"
    assert caught.value.status_code == 503
    assert "UNIBOT_SANDBOX_KUBERNETES_WORKSPACE_PVC" in caught.value.message
    assert requests == 0
    await client.aclose()


@pytest.mark.asyncio
async def test_kubernetes_driver_does_not_trust_legacy_ready_workspace_pod() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "spec": {
                    "desiredState": "Running",
                    "image": "unibot/sandboxd:test",
                    "runtimeClassName": "gvisor",
                    "workspaceId": "workspace-test",
                    "workspaceStorageKey": "ws_test",
                    "workspacePersistentVolumeClaim": "shared-workspaces",
                },
                "status": {
                    "phase": "Ready",
                    "endpoint": "legacy-runtime-pvc-pod",
                },
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    driver = KubernetesSandboxDriver(
        api_url="https://kubernetes.test",
        namespace="unibot-sandboxes",
        token="test-token",
        ca_file=None,
        runtime_class="gvisor",
        workspace_pvc="shared-workspaces",
        client=client,
    )
    sandbox = SandboxRecord(
        id="sandbox-workspace",
        user_id="user-1",
        tenant_id="tenant-1",
        workspace_id="workspace-test",
        workspace_storage_key="ws_test",
        image="unibot/sandboxd:test",
        driver="kubernetes",
        runtime_name="unibot-workspace-test",
        workspace="/workspace",
    )

    state = await driver.ensure(sandbox)

    assert state.status == "provisioning"
    assert state.endpoint is None
    await client.aclose()


@pytest.mark.asyncio
async def test_sandbox_factory_passes_workspace_claim_setting(tmp_path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("test-token")
    settings = AgentSettings(
        _env_file=None,
        sandbox_driver="kubernetes",
        sandbox_kubernetes_token_file=token_file,
        sandbox_kubernetes_ca_file=tmp_path / "missing-ca.crt",
        sandbox_kubernetes_workspace_pvc="shared-workspaces",
    )

    service = create_sandbox_service(settings, InMemoryRepository())

    assert isinstance(service.driver, KubernetesSandboxDriver)
    assert service.driver.workspace_pvc == "shared-workspaces"
    await service.aclose()


def test_workspace_claim_setting_rejects_invalid_kubernetes_names() -> None:
    assert AgentSettings(_env_file=None, sandbox_kubernetes_workspace_pvc="").sandbox_kubernetes_workspace_pvc is None
    with pytest.raises(ValidationError):
        AgentSettings(
            _env_file=None,
            sandbox_kubernetes_workspace_pvc="../other-claim",
        )
