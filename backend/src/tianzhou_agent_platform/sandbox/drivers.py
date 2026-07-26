from __future__ import annotations

import asyncio
import hashlib
import os
import signal
import shutil
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from time import perf_counter
from typing import Any

import httpx

from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.sandbox.models import (
    DriverExecutionResult,
    DriverSandboxState,
    SandboxExecutionRequest,
    SandboxRecord,
)


class SandboxDriver(ABC):
    name: str

    @abstractmethod
    async def ensure(self, sandbox: SandboxRecord) -> DriverSandboxState: ...

    @abstractmethod
    async def execute(
        self,
        sandbox: SandboxRecord,
        request: SandboxExecutionRequest,
    ) -> DriverExecutionResult: ...

    @abstractmethod
    async def stop(self, sandbox: SandboxRecord) -> DriverSandboxState: ...

    @abstractmethod
    async def reset(self, sandbox: SandboxRecord) -> None: ...

    async def aclose(self) -> None:
        return None


class LocalProcessSandboxDriver(SandboxDriver):
    """Development driver implementing the production contract without Kubernetes.

    This driver executes processes on the host and must never be enabled for
    untrusted production traffic. The production driver uses gVisor-backed pods.
    """

    name = "local"

    def __init__(self, workspace_root: Path, *, output_limit_bytes: int = 1_000_000) -> None:
        self.workspace_root = workspace_root.resolve()
        self.output_limit_bytes = output_limit_bytes

    async def ensure(self, sandbox: SandboxRecord) -> DriverSandboxState:
        workspace = self._workspace(sandbox)
        workspace.mkdir(parents=True, exist_ok=True)
        return DriverSandboxState(
            status="ready",
            runtime_name="local-process-development",
            workspace=str(workspace),
        )

    async def execute(
        self,
        sandbox: SandboxRecord,
        request: SandboxExecutionRequest,
    ) -> DriverExecutionResult:
        workspace = self._workspace(sandbox)
        workspace.mkdir(parents=True, exist_ok=True)
        working_directory = (workspace / request.working_directory).resolve()
        if working_directory != workspace and workspace not in working_directory.parents:
            raise PlatformError("PERMISSION_DENIED", "Working directory escapes the sandbox", status_code=403)
        working_directory.mkdir(parents=True, exist_ok=True)
        command = self._command(request.language, request.script)
        python_packages = workspace / ".python-packages"
        npm_prefix = workspace / ".npm-global"
        temp_directory = workspace / ".tmp"
        temp_directory.mkdir(exist_ok=True)
        system_environment = {
            name: value
            for name in ("COMSPEC", "PATHEXT", "SystemRoot", "WINDIR")
            if (value := os.environ.get(name))
        }
        environment = {
            **system_environment,
            "PATH": os.pathsep.join(
                [
                    str(npm_prefix / "bin"),
                    str(python_packages / "bin"),
                    os.environ.get("PATH", ""),
                ]
            ),
            "HOME": str(workspace),
            "USERPROFILE": str(workspace),
            "TEMP": str(temp_directory),
            "TMP": str(temp_directory),
            "PYTHONUNBUFFERED": "1",
            "PYTHONPATH": str(python_packages),
            "PIP_TARGET": str(python_packages),
            "npm_config_prefix": str(npm_prefix),
            "UNIBOT_SANDBOX": "true",
            **request.environment,
        }
        started = perf_counter()
        kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = 0x00000200  # CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=working_directory,
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **kwargs,
        )
        assert process.stdout is not None and process.stderr is not None
        stdout_task = asyncio.create_task(_read_limited(process.stdout, self.output_limit_bytes))
        stderr_task = asyncio.create_task(_read_limited(process.stderr, self.output_limit_bytes))
        try:
            await asyncio.wait_for(
                process.wait(),
                timeout=request.timeout_seconds,
            )
            status = "succeeded" if process.returncode == 0 else "failed"
            exit_code = process.returncode
        except TimeoutError:
            _terminate_process_tree(process)
            await process.wait()
            status = "timed_out"
            exit_code = None
        (stdout, stdout_truncated), (stderr, stderr_truncated) = await asyncio.gather(
            stdout_task,
            stderr_task,
        )
        if status == "timed_out":
            stderr += f"\nExecution timed out after {request.timeout_seconds} seconds."
        duration_ms = (perf_counter() - started) * 1000
        return DriverExecutionResult(
            status=status,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            duration_ms=duration_ms,
            truncated=stdout_truncated or stderr_truncated,
        )

    async def stop(self, sandbox: SandboxRecord) -> DriverSandboxState:
        return DriverSandboxState(
            status="stopped",
            runtime_name="local-process-development",
            workspace=str(self._workspace(sandbox)),
        )

    async def reset(self, sandbox: SandboxRecord) -> None:
        workspace = self._workspace(sandbox)
        if workspace.exists():
            shutil.rmtree(workspace)

    def _workspace(self, sandbox: SandboxRecord) -> Path:
        digest = hashlib.sha256(f"{sandbox.tenant_id}:{sandbox.user_id}".encode()).hexdigest()[:24]
        workspace = (self.workspace_root / digest).resolve()
        if workspace.parent != self.workspace_root:
            raise PlatformError("PERMISSION_DENIED", "Invalid sandbox workspace", status_code=403)
        return workspace

    @staticmethod
    def _command(language: str, script: str) -> list[str]:
        if language == "python":
            return [sys.executable, "-c", script]
        if language == "node":
            executable = shutil.which("node")
            if executable is None:
                raise PlatformError("DEPENDENCY_FAILED", "Node.js is not installed in this sandbox", status_code=503)
            return [executable, "-e", script]
        if language == "bash":
            executable = _bash_executable()
            if executable is None:
                raise PlatformError("DEPENDENCY_FAILED", "Bash is not installed in this sandbox", status_code=503)
            return [executable, "-lc", script]
        if sys.platform == "win32":
            return ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script]
        return ["/bin/bash", "-lc", script]

def _bash_executable() -> str | None:
    if sys.platform == "win32":
        for path in (
            Path("C:/Program Files/Git/bin/bash.exe"),
            Path("C:/Program Files/Git/usr/bin/bash.exe"),
        ):
            if path.exists():
                return str(path)
    return shutil.which("bash")


class KubernetesSandboxDriver(SandboxDriver):
    """Kubernetes CRD client used by both single-node and HA K3s clusters."""

    name = "kubernetes"

    def __init__(
        self,
        *,
        api_url: str,
        namespace: str,
        token: str,
        ca_file: str | None,
        runtime_class: str,
        ready_timeout_seconds: int = 60,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.namespace = namespace
        self.token = token
        self.ca_file = ca_file
        self.runtime_class = runtime_class
        self.ready_timeout_seconds = ready_timeout_seconds
        self._client = client or httpx.AsyncClient(verify=ca_file or True)
        self._owns_client = client is None

    async def ensure(self, sandbox: SandboxRecord) -> DriverSandboxState:
        url = self._resource_url(sandbox.runtime_name)
        response = await self._request("GET", url)
        if response.status_code == 404:
            payload = {
                "apiVersion": "sandbox.unibot.ai/v1alpha1",
                "kind": "UserSandbox",
                "metadata": {
                    "name": sandbox.runtime_name,
                    "namespace": self.namespace,
                    "labels": {
                        "app.kubernetes.io/managed-by": "unibot",
                        "sandbox.unibot.ai/sandbox-id": sandbox.id,
                    },
                },
                "spec": {
                    "sandboxId": sandbox.id,
                    "tenantId": sandbox.tenant_id,
                    "userId": sandbox.user_id,
                    "image": sandbox.image,
                    "runtimeClassName": self.runtime_class,
                    "desiredState": "Running",
                },
            }
            response = await self._request("POST", self._collection_url(), json=payload)
        elif response.is_success:
            current = response.json()
            current_spec = current.get("spec", {})
            desired_spec = {
                "desiredState": "Running",
                "image": sandbox.image,
                "runtimeClassName": self.runtime_class,
            }
            if any(current_spec.get(key) != value for key, value in desired_spec.items()):
                response = await self._request(
                    "PATCH",
                    url,
                    json={"spec": desired_spec},
                    content_type="application/merge-patch+json",
                )
        self._raise_for_status(response, "provision sandbox")
        current = response.json()
        status = current.get("status", {})
        phase = str(status.get("phase") or "Provisioning")
        return DriverSandboxState(
            status=self._phase(phase),
            runtime_name=sandbox.runtime_name,
            workspace="/workspace",
            endpoint=status.get("endpoint"),
            error=status.get("message"),
        )

    async def execute(
        self,
        sandbox: SandboxRecord,
        request: SandboxExecutionRequest,
    ) -> DriverExecutionResult:
        state = await self._wait_ready(sandbox)
        if state.endpoint is None:
            raise PlatformError("DEPENDENCY_FAILED", "Sandbox endpoint is unavailable", status_code=503)
        try:
            response = await self._client.post(
                f"http://{state.endpoint}:8080/exec",
                json=request.model_dump(mode="json"),
                timeout=request.timeout_seconds + 10,
            )
            response.raise_for_status()
            return DriverExecutionResult.model_validate(response.json())
        except (httpx.HTTPError, ValueError) as exc:
            raise PlatformError(
                "DEPENDENCY_FAILED",
                "The sandbox execution service could not be reached",
                status_code=502,
                retryable=True,
                source="sandbox",
            ) from exc

    async def stop(self, sandbox: SandboxRecord) -> DriverSandboxState:
        response = await self._request(
            "PATCH",
            self._resource_url(sandbox.runtime_name),
            json={"spec": {"desiredState": "Stopped"}},
            content_type="application/merge-patch+json",
        )
        self._raise_for_status(response, "stop sandbox")
        return DriverSandboxState(
            status="stopped",
            runtime_name=sandbox.runtime_name,
            workspace="/workspace",
        )

    async def reset(self, sandbox: SandboxRecord) -> None:
        response = await self._request("DELETE", self._resource_url(sandbox.runtime_name))
        if response.status_code not in {200, 202, 404}:
            self._raise_for_status(response, "reset sandbox")
        if response.status_code == 404:
            return
        deadline = asyncio.get_running_loop().time() + 30
        while asyncio.get_running_loop().time() < deadline:
            response = await self._request("GET", self._resource_url(sandbox.runtime_name))
            if response.status_code == 404:
                return
            self._raise_for_status(response, "verify sandbox reset")
            await asyncio.sleep(0.5)
        raise PlatformError("TIMEOUT", "Sandbox reset did not complete in time", status_code=504)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _wait_ready(self, sandbox: SandboxRecord) -> DriverSandboxState:
        deadline = asyncio.get_running_loop().time() + self.ready_timeout_seconds
        while True:
            state = await self.ensure(sandbox)
            if state.status == "ready":
                return state
            if state.status == "error":
                raise PlatformError(
                    "DEPENDENCY_FAILED",
                    state.error or "Sandbox provisioning failed",
                    status_code=503,
                    source="sandbox",
                )
            if asyncio.get_running_loop().time() >= deadline:
                raise PlatformError("TIMEOUT", "Sandbox did not become ready in time", status_code=504)
            await asyncio.sleep(1)

    def _collection_url(self) -> str:
        return (
            f"{self.api_url}/apis/sandbox.unibot.ai/v1alpha1/namespaces/"
            f"{self.namespace}/usersandboxes"
        )

    def _resource_url(self, name: str) -> str:
        return f"{self._collection_url()}/{name}"

    async def _request(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        content_type: str = "application/json",
    ) -> httpx.Response:
        try:
            return await self._client.request(
                method,
                url,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": content_type,
                },
                json=json,
                timeout=10,
            )
        except httpx.HTTPError as exc:
            raise PlatformError(
                "DEPENDENCY_FAILED",
                "Kubernetes API is unavailable",
                status_code=503,
                retryable=True,
                source="sandbox",
            ) from exc

    @staticmethod
    def _raise_for_status(response: httpx.Response, action: str) -> None:
        if response.is_error:
            raise PlatformError(
                "DEPENDENCY_FAILED",
                f"Kubernetes could not {action}: HTTP {response.status_code}",
                status_code=502,
                source="sandbox",
            )

    @staticmethod
    def _phase(value: str) -> str:
        return {
            "Ready": "ready",
            "Running": "ready",
            "Busy": "busy",
            "Stopped": "stopped",
            "Error": "error",
            "Failed": "error",
        }.get(value, "provisioning")


def _terminate_process_tree(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    if sys.platform != "win32" and process.pid is not None:
        os.killpg(process.pid, signal.SIGKILL)
        return
    process.kill()


async def _read_limited(stream: asyncio.StreamReader, limit: int) -> tuple[str, bool]:
    chunks: list[bytes] = []
    captured = 0
    truncated = False
    while chunk := await stream.read(65_536):
        remaining = limit - captured
        if remaining > 0:
            kept = chunk[:remaining]
            chunks.append(kept)
            captured += len(kept)
        if len(chunk) > remaining:
            truncated = True
    return b"".join(chunks).decode("utf-8", errors="replace"), truncated
