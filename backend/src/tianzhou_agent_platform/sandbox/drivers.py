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
from urllib.parse import quote

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
    async def write_file(
        self,
        sandbox: SandboxRecord,
        path: str,
        content: bytes,
        *,
        overwrite: bool,
    ) -> None: ...

    @abstractmethod
    async def read_file(self, sandbox: SandboxRecord, path: str) -> bytes: ...

    @abstractmethod
    async def delete_file(self, sandbox: SandboxRecord, path: str) -> None: ...

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

    def __init__(
        self,
        workspace_root: Path,
        *,
        persistent_workspace_root: Path | None = None,
        output_limit_bytes: int = 1_000_000,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.persistent_workspace_root = (
            persistent_workspace_root.resolve() if persistent_workspace_root is not None else None
        )
        self.output_limit_bytes = output_limit_bytes

    async def ensure(self, sandbox: SandboxRecord) -> DriverSandboxState:
        workspace = self._workspace(sandbox)
        workspace.mkdir(parents=True, exist_ok=True)
        self._runtime_workspace(sandbox).mkdir(parents=True, exist_ok=True)
        return DriverSandboxState(
            status="ready",
            runtime_name=sandbox.runtime_name,
            workspace=str(workspace),
        )

    async def execute(
        self,
        sandbox: SandboxRecord,
        request: SandboxExecutionRequest,
    ) -> DriverExecutionResult:
        workspace = self._workspace(sandbox)
        workspace.mkdir(parents=True, exist_ok=True)
        runtime_workspace = self._runtime_workspace(sandbox)
        runtime_workspace.mkdir(parents=True, exist_ok=True)
        working_directory = (workspace / request.working_directory).resolve()
        if working_directory != workspace and workspace not in working_directory.parents:
            raise PlatformError("PERMISSION_DENIED", "Working directory escapes the sandbox", status_code=403)
        working_directory.mkdir(parents=True, exist_ok=True)
        command = self._command(request.language, request.script)
        python_packages = runtime_workspace / ".python-packages"
        npm_prefix = runtime_workspace / ".npm-global"
        temp_directory = runtime_workspace / ".tmp"
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
            "HOME": str(runtime_workspace),
            "USERPROFILE": str(runtime_workspace),
            "TEMP": str(temp_directory),
            "TMP": str(temp_directory),
            "PYTHONUNBUFFERED": "1",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONPATH": str(python_packages),
            "PIP_TARGET": str(python_packages),
            "npm_config_prefix": str(npm_prefix),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
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
            runtime_name=sandbox.runtime_name,
            workspace=str(self._workspace(sandbox)),
        )

    async def write_file(
        self,
        sandbox: SandboxRecord,
        path: str,
        content: bytes,
        *,
        overwrite: bool,
    ) -> None:
        target = self._file(sandbox, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not overwrite:
            raise PlatformError("CONFLICT", "Sandbox file already exists", status_code=409, source="sandbox")
        await asyncio.to_thread(target.write_bytes, content)

    async def read_file(self, sandbox: SandboxRecord, path: str) -> bytes:
        target = self._file(sandbox, path)
        try:
            return await asyncio.to_thread(target.read_bytes)
        except FileNotFoundError as exc:
            raise PlatformError(
                "RESOURCE_NOT_FOUND",
                "Sandbox file was not found",
                status_code=404,
                source="sandbox",
            ) from exc

    async def delete_file(self, sandbox: SandboxRecord, path: str) -> None:
        target = self._file(sandbox, path)
        try:
            await asyncio.to_thread(target.unlink)
        except FileNotFoundError:
            return

    async def reset(self, sandbox: SandboxRecord) -> None:
        runtime_workspace = self._runtime_workspace(sandbox)
        if runtime_workspace.exists():
            shutil.rmtree(runtime_workspace)

    def _workspace(self, sandbox: SandboxRecord) -> Path:
        if sandbox.workspace_id is None or self.persistent_workspace_root is None:
            return self._runtime_workspace(sandbox)
        storage_key = sandbox.workspace_storage_key
        if storage_key is None:
            raise PlatformError(
                "DEPENDENCY_FAILED",
                "Workspace sandbox storage key is unavailable",
                status_code=500,
                source="sandbox",
            )
        workspace_root = (self.persistent_workspace_root / storage_key).resolve()
        if workspace_root.parent != self.persistent_workspace_root:
            raise PlatformError("PERMISSION_DENIED", "Invalid workspace storage key", status_code=403)
        return workspace_root / "files"

    def _runtime_workspace(self, sandbox: SandboxRecord) -> Path:
        identity = f"{sandbox.tenant_id}:{sandbox.user_id}"
        if sandbox.workspace_id is not None:
            identity = f"{identity}:workspace:{sandbox.workspace_id}"
        digest = hashlib.sha256(identity.encode()).hexdigest()[:24]
        workspace = (self.workspace_root / digest).resolve()
        if workspace.parent != self.workspace_root:
            raise PlatformError("PERMISSION_DENIED", "Invalid sandbox workspace", status_code=403)
        return workspace

    def _file(self, sandbox: SandboxRecord, path: str) -> Path:
        workspace = self._workspace(sandbox)
        target = (workspace / path).resolve()
        if target == workspace or workspace not in target.parents:
            raise PlatformError("PERMISSION_DENIED", "Sandbox file path escapes the workspace", status_code=403)
        return target

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
            utf8_script = (
                "$OutputEncoding = [Console]::OutputEncoding = "
                "[System.Text.UTF8Encoding]::new($false); "
                f"{script}"
            )
            return ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", utf8_script]
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
        workspace_pvc: str | None = None,
        ready_timeout_seconds: int = 60,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.namespace = namespace
        self.token = token
        self.ca_file = ca_file
        self.runtime_class = runtime_class
        self.workspace_pvc = workspace_pvc
        self.ready_timeout_seconds = ready_timeout_seconds
        self._client = client or httpx.AsyncClient(verify=ca_file or True)
        self._owns_client = client is None

    async def ensure(self, sandbox: SandboxRecord) -> DriverSandboxState:
        workspace_spec = self._workspace_spec(sandbox)
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
                    **workspace_spec,
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
                **workspace_spec,
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
        endpoint = status.get("endpoint")
        if workspace_spec and not (
            status.get("workspaceMountReady") is True
            and status.get("workspaceStorageKey") == workspace_spec["workspaceStorageKey"]
            and status.get("workspacePersistentVolumeClaim")
            == workspace_spec["workspacePersistentVolumeClaim"]
        ):
            phase = "Provisioning"
            endpoint = None
        return DriverSandboxState(
            status=self._phase(phase),
            runtime_name=sandbox.runtime_name,
            workspace="/workspace",
            endpoint=endpoint,
            error=status.get("message"),
        )

    def _workspace_spec(self, sandbox: SandboxRecord) -> dict[str, str]:
        if sandbox.workspace_id is None:
            return {}
        if self.workspace_pvc is None:
            raise PlatformError(
                "DEPENDENCY_FAILED",
                "Kubernetes workspace sandboxes require "
                "UNIBOT_SANDBOX_KUBERNETES_WORKSPACE_PVC",
                status_code=503,
                source="sandbox",
            )
        if sandbox.workspace_storage_key is None:
            raise PlatformError(
                "DEPENDENCY_FAILED",
                "Workspace sandbox storage key is unavailable",
                status_code=500,
                source="sandbox",
            )
        return {
            "workspaceId": sandbox.workspace_id,
            "workspaceStorageKey": sandbox.workspace_storage_key,
            "workspacePersistentVolumeClaim": self.workspace_pvc,
        }

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

    async def write_file(
        self,
        sandbox: SandboxRecord,
        path: str,
        content: bytes,
        *,
        overwrite: bool,
    ) -> None:
        state = await self._wait_ready(sandbox)
        response = await self._sandboxd_request(
            state,
            "PUT",
            path,
            content=content,
            params={"overwrite": str(overwrite).lower()},
        )
        if response.status_code == 409:
            raise PlatformError("CONFLICT", "Sandbox file already exists", status_code=409, source="sandbox")
        self._raise_sandboxd_status(response, "write sandbox file")

    async def read_file(self, sandbox: SandboxRecord, path: str) -> bytes:
        state = await self._wait_ready(sandbox)
        response = await self._sandboxd_request(state, "GET", path)
        if response.status_code == 404:
            raise PlatformError(
                "RESOURCE_NOT_FOUND",
                "Sandbox file was not found",
                status_code=404,
                source="sandbox",
            )
        self._raise_sandboxd_status(response, "read sandbox file")
        return response.content

    async def delete_file(self, sandbox: SandboxRecord, path: str) -> None:
        state = await self._wait_ready(sandbox)
        response = await self._sandboxd_request(state, "DELETE", path)
        if response.status_code == 404:
            return
        self._raise_sandboxd_status(response, "delete sandbox file")

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

    async def _sandboxd_request(
        self,
        state: DriverSandboxState,
        method: str,
        path: str,
        *,
        content: bytes | None = None,
        params: dict[str, str] | None = None,
    ) -> httpx.Response:
        if state.endpoint is None:
            raise PlatformError("DEPENDENCY_FAILED", "Sandbox endpoint is unavailable", status_code=503)
        try:
            return await self._client.request(
                method,
                f"http://{state.endpoint}:8080/files/{quote(path, safe='/')}",
                content=content,
                params=params,
                timeout=30,
            )
        except httpx.HTTPError as exc:
            raise PlatformError(
                "DEPENDENCY_FAILED",
                "The sandbox file service could not be reached",
                status_code=502,
                retryable=True,
                source="sandbox",
            ) from exc

    @staticmethod
    def _raise_sandboxd_status(response: httpx.Response, action: str) -> None:
        if response.is_error:
            raise PlatformError(
                "DEPENDENCY_FAILED",
                f"Sandbox could not {action}: HTTP {response.status_code}",
                status_code=502,
                source="sandbox",
            )

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
