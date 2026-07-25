from __future__ import annotations

import json

import httpx
import pytest

from tianzhou_agent_platform.sandbox.drivers import KubernetesSandboxDriver
from tianzhou_agent_platform.sandbox.models import SandboxExecutionRequest, SandboxRecord


@pytest.mark.asyncio
async def test_kubernetes_driver_creates_waits_executes_stops_and_resets() -> None:
    requests: list[tuple[str, str]] = []
    get_count = 0
    deleting = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal get_count, deleting
        requests.append((request.method, str(request.url)))
        if request.url.host == "unibot-test.unibot-sandboxes.svc.cluster.local":
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
                    },
                    "status": {
                        "phase": "Ready",
                        "endpoint": "unibot-test.unibot-sandboxes.svc.cluster.local",
                    },
                },
            )
        if request.method == "POST":
            body = json.loads(request.content)
            assert body["kind"] == "UserSandbox"
            assert body["spec"]["runtimeClassName"] == "gvisor"
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
        ready_timeout_seconds=1,
        client=client,
    )
    sandbox = SandboxRecord(
        id="sandbox-test",
        user_id="user-1",
        tenant_id="tenant-1",
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
            language="python",
            script="print('kubernetes-ready')",
        ),
    )
    assert result.status == "succeeded"
    assert result.stdout == "kubernetes-ready\n"

    stopped = await driver.stop(sandbox)
    assert stopped.status == "stopped"
    await driver.reset(sandbox)
    assert ("DELETE", driver._resource_url("unibot-test")) in requests
    await client.aclose()
