from __future__ import annotations

import hashlib
import json
import math
import uuid
from time import perf_counter
from typing import Any

from tianzhou_agent_platform.aina.project import AinaProjectRecord, validate_project_archive
from tianzhou_agent_platform.aina.project_service import AinaProjectService
from tianzhou_agent_platform.aina.protocol.models import (
    AinaInstallation,
    AinaInvokeRequest,
    AinaInvokeResponse,
    AinaManifest,
    AinaRecord,
)
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.core.errors import PlatformError, conflict
from tianzhou_agent_platform.core.repository import InMemoryRepository
from tianzhou_agent_platform.sandbox.models import SandboxExecution, SandboxExecutionRequest
from tianzhou_agent_platform.sandbox.service import SandboxService

RESPONSE_MARKER = "__UNIBOT_AINA_RESPONSE__"
_DEPENDENCY_INSTALL_TIMEOUT_SECONDS = 300

_DEPLOY_SCRIPT = r'''
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from uuid import uuid4

root = Path.cwd()
archive_path = root / "project.aina.zip"
staging = root / f".staging-{uuid4().hex}"
destination = root / "app"
language = os.environ["AINA_LANGUAGE"]
dependency_file = os.environ.get("AINA_DEPENDENCY_FILE") or None

try:
    staging.mkdir()
    with zipfile.ZipFile(archive_path) as archive:
        archive.extractall(staging)
    if language == "python" and dependency_file:
        requirements = staging / dependency_file
        meaningful = [
            line for line in requirements.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        if meaningful:
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    "--no-input",
                    "--target",
                    str(staging / ".dependencies"),
                    "-r",
                    str(requirements),
                ],
                cwd=staging,
                check=True,
            )
    elif language == "node" and dependency_file:
        package = json.loads((staging / dependency_file).read_text(encoding="utf-8"))
        if package.get("dependencies"):
            subprocess.run(
                ["npm", "install", "--omit=dev", "--ignore-scripts", "--no-audit", "--no-fund"],
                cwd=staging,
                check=True,
            )
    if destination.exists():
        shutil.rmtree(destination)
    staging.replace(destination)
    (root / ".unibot-deployment.json").write_text(
        json.dumps({
            "project_id": os.environ["AINA_PROJECT_ID"],
            "archive_sha256": os.environ["AINA_ARCHIVE_SHA256"],
        }, separators=(",", ":")),
        encoding="utf-8",
    )
finally:
    if staging.exists():
        shutil.rmtree(staging)
'''

_PYTHON_RUNNER = r'''
import asyncio
import importlib.util
import inspect
import json
import os
import sys
from pathlib import Path

entrypoint = Path(os.environ["AINA_ENTRYPOINT"]).resolve()
sys.path.insert(0, str(entrypoint.parent))
spec = importlib.util.spec_from_file_location("unibot_managed_aina", entrypoint)
if spec is None or spec.loader is None:
    raise RuntimeError(f"Cannot load managed AINA entrypoint {entrypoint}")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
handler = getattr(module, os.environ["AINA_HANDLER"])
request = json.loads(Path(os.environ["AINA_REQUEST_FILE"]).read_text(encoding="utf-8"))
result = handler(request)
if inspect.isawaitable(result):
    result = asyncio.run(result)
print("__UNIBOT_AINA_RESPONSE__" + json.dumps(result, ensure_ascii=False, separators=(",", ":")))
'''

_NODE_RUNNER = r'''
(async () => {
    const { readFile } = await import("node:fs/promises");
    const { resolve } = await import("node:path");
    const { pathToFileURL } = await import("node:url");
    const module = await import(pathToFileURL(resolve(process.env.AINA_ENTRYPOINT)).href);
    const handler = module[process.env.AINA_HANDLER];
    if (typeof handler !== "function") throw new Error(`Missing handler ${process.env.AINA_HANDLER}`);
    const request = JSON.parse(await readFile(process.env.AINA_REQUEST_FILE, "utf8"));
    const result = await handler(request);
    process.stdout.write("__UNIBOT_AINA_RESPONSE__" + JSON.stringify(result));
})().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
'''

_REMOVE_DEPLOYMENT_SCRIPT = r'''
import os
import shutil
from pathlib import Path

deployment = Path(os.environ["AINA_DEPLOYMENT"])
if deployment.exists():
    shutil.rmtree(deployment)
'''


class ManagedAinaRuntime:
    def __init__(
        self,
        settings: AgentSettings,
        repository: InMemoryRepository,
        projects: AinaProjectService,
        sandboxes: SandboxService,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.projects = projects
        self.sandboxes = sandboxes

    async def deploy(self, project_id: str, *, user_id: str, tenant_id: str) -> AinaProjectRecord:
        record, payload = await self.projects.get_archive(
            project_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        await self._ensure_deployment(record, payload, user_id=user_id, tenant_id=tenant_id)

        created_registration = False
        try:
            try:
                existing = await self.repository.get_aina(record.manifest.aina.id)
            except PlatformError as exc:
                if exc.code != "RESOURCE_NOT_FOUND":
                    raise
                existing = await self.repository.register_aina(
                    AinaRecord(
                        manifest=record.manifest,
                        last_health={
                            "status": "healthy",
                            "runtime": "managed",
                            "sandbox": self.sandboxes.driver.name,
                            "project_id": record.id,
                            "archive_sha256": record.archive_sha256,
                        },
                    )
                )
                created_registration = True
            if existing.manifest != record.manifest:
                raise conflict(f"AINA {record.manifest.aina.id!r} is already registered from another project")
            if record.status == "deployed":
                return record
            return await self.repository.set_aina_project_deployed(
                record.id,
                deployed=True,
                user_id=user_id,
                tenant_id=tenant_id,
            )
        except Exception:
            if created_registration:
                await self.repository.remove_aina(record.manifest.aina.id)
            raise

    async def undeploy(self, project_id: str, *, user_id: str, tenant_id: str) -> AinaProjectRecord:
        record = await self.projects.get_project(
            project_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        if record.status != "deployed":
            return record
        await self._remove_deployment(record, user_id=user_id, tenant_id=tenant_id)
        try:
            registered = await self.repository.get_aina(record.manifest.aina.id)
        except PlatformError as exc:
            if exc.code != "RESOURCE_NOT_FOUND":
                raise
        else:
            if registered.manifest.runtime.type != "managed":
                raise conflict(f"AINA {record.manifest.aina.id!r} is not owned by this managed project")
            await self.repository.remove_aina(record.manifest.aina.id)
        return await self.repository.set_aina_project_deployed(
            record.id,
            deployed=False,
            user_id=user_id,
            tenant_id=tenant_id,
        )

    async def invoke(
        self,
        manifest: AinaManifest,
        installation: AinaInstallation,
        *,
        arguments: dict[str, Any],
        call_id: str,
        conversation_id: str,
        trace_id: str,
        available_tools: list[str],
    ) -> tuple[AinaInvokeResponse, float]:
        missing_permissions = set(manifest.permissions) - set(installation.granted_permissions)
        if missing_permissions:
            raise PlatformError(
                "PERMISSION_DENIED",
                f"AINA is missing grants: {', '.join(sorted(missing_permissions))}",
                status_code=403,
                source="aina",
            )
        projects = await self.projects.list_projects(
            user_id=installation.user_id,
            tenant_id=installation.tenant_id,
        )
        record = next(
            (
                item
                for item in projects
                if item.status == "deployed"
                and item.manifest.aina.id == manifest.aina.id
                and item.manifest.aina.version == manifest.aina.version
            ),
            None,
        )
        if record is None:
            raise PlatformError(
                "DEPENDENCY_FAILED",
                "The managed AINA deployment is unavailable",
                status_code=503,
                source="aina",
            )
        request = AinaInvokeRequest(
            request_id=call_id,
            user_id=installation.user_id,
            tenant_id=installation.tenant_id,
            session_id=conversation_id,
            conversation_id=conversation_id,
            input=arguments,
            context={"source": "agent"},
            authorization={"permissions": installation.granted_permissions},
            trace={"trace_id": trace_id},
            available_tools=available_tools,
        )
        started = perf_counter()
        raw = await self._invoke_in_sandbox(record, request.model_dump_json())
        try:
            response = AinaInvokeResponse.model_validate(raw)
        except ValueError as exc:
            raise PlatformError(
                "DEPENDENCY_FAILED",
                "Managed AINA returned a response that does not match Protocol 1.0",
                status_code=502,
                source="aina",
            ) from exc
        return response, (perf_counter() - started) * 1000

    async def _ensure_deployment(
        self,
        record: AinaProjectRecord,
        payload: bytes,
        *,
        user_id: str,
        tenant_id: str,
    ) -> None:
        validate_project_archive(payload)
        deployment = self._deployment_path(record)
        marker_path = f"{deployment}/.unibot-deployment.json"
        try:
            marker = json.loads(
                (await self.sandboxes.read_file(
                    user_id=user_id,
                    tenant_id=tenant_id,
                    path=marker_path,
                )).decode("utf-8")
            )
        except (PlatformError, UnicodeDecodeError, json.JSONDecodeError):
            marker = None
        runtime = record.manifest.runtime
        if runtime.type != "managed":
            raise PlatformError("INVALID_REQUEST", "AINA project is not managed", status_code=422)
        if isinstance(marker, dict) and marker.get("archive_sha256") == record.archive_sha256:
            entrypoint = runtime.entrypoint.partition(":")[0]
            try:
                await self.sandboxes.read_file(
                    user_id=user_id,
                    tenant_id=tenant_id,
                    path=f"{deployment}/app/{entrypoint}",
                )
            except PlatformError as exc:
                if exc.code != "RESOURCE_NOT_FOUND":
                    raise
            else:
                return

        archive_path = f"{deployment}/project.aina.zip"
        await self.sandboxes.write_file(
            user_id=user_id,
            tenant_id=tenant_id,
            path=archive_path,
            content=payload,
        )
        try:
            execution = await self.sandboxes.execute(
                SandboxExecutionRequest(
                    user_id=user_id,
                    tenant_id=tenant_id,
                    language="python",
                    script=_DEPLOY_SCRIPT,
                    timeout_seconds=_DEPENDENCY_INSTALL_TIMEOUT_SECONDS,
                    working_directory=deployment,
                    environment={
                        "AINA_LANGUAGE": runtime.language,
                        "AINA_DEPENDENCY_FILE": runtime.dependency_file or "",
                        "AINA_PROJECT_ID": record.id,
                        "AINA_ARCHIVE_SHA256": record.archive_sha256,
                    },
                )
            )
            self._require_success(execution, action="deployment")
        finally:
            await self.sandboxes.delete_file(
                user_id=user_id,
                tenant_id=tenant_id,
                path=archive_path,
            )

    async def _invoke_in_sandbox(self, record: AinaProjectRecord, request_json: str) -> dict[str, Any]:
        runtime = record.manifest.runtime
        if runtime.type != "managed":
            raise PlatformError("INVALID_REQUEST", "AINA runtime is not managed", status_code=400)
        deployment = self._deployment_path(record)
        request_name = hashlib.sha256(
            f"{uuid.uuid4().hex}:{request_json}".encode("utf-8")
        ).hexdigest()
        request_path = f"{deployment}/requests/{request_name}.json"
        await self.sandboxes.write_file(
            user_id=record.user_id,
            tenant_id=record.tenant_id,
            path=request_path,
            content=request_json.encode("utf-8"),
        )
        entrypoint, _, handler = runtime.entrypoint.partition(":")
        try:
            execution = await self.sandboxes.execute(
                SandboxExecutionRequest(
                    user_id=record.user_id,
                    tenant_id=record.tenant_id,
                    language=runtime.language,
                    script=_PYTHON_RUNNER if runtime.language == "python" else _NODE_RUNNER,
                    timeout_seconds=max(1, min(300, math.ceil(self.settings.capability_timeout_seconds))),
                    working_directory=f"{deployment}/app",
                    environment={
                        "AINA_ENTRYPOINT": entrypoint,
                        "AINA_HANDLER": handler,
                        "AINA_REQUEST_FILE": f"../requests/{request_name}.json",
                        "PYTHONPATH": ".dependencies",
                        "UNIBOT_MANAGED_AINA": "true",
                    },
                )
            )
            self._require_success(execution, action="invocation")
            return self._parse_response(execution.stdout)
        finally:
            await self.sandboxes.delete_file(
                user_id=record.user_id,
                tenant_id=record.tenant_id,
                path=request_path,
            )

    async def _remove_deployment(
        self,
        record: AinaProjectRecord,
        *,
        user_id: str,
        tenant_id: str,
    ) -> None:
        execution = await self.sandboxes.execute(
            SandboxExecutionRequest(
                user_id=user_id,
                tenant_id=tenant_id,
                language="python",
                script=_REMOVE_DEPLOYMENT_SCRIPT,
                timeout_seconds=30,
                working_directory="managed-ainas",
                environment={"AINA_DEPLOYMENT": self._deployment_name(record)},
            )
        )
        self._require_success(execution, action="undeployment")

    @staticmethod
    def _require_success(execution: SandboxExecution, *, action: str) -> None:
        if execution.status == "succeeded" and not execution.truncated:
            return
        if execution.status == "timed_out":
            raise PlatformError(
                "TIMEOUT",
                f"Managed AINA {action} timed out",
                status_code=504,
                retryable=True,
                source="aina",
            )
        message = execution.stderr.strip()
        raise PlatformError(
            "DEPENDENCY_FAILED",
            f"Managed AINA {action} failed",
            status_code=502,
            source="aina",
            user_message=message[-2000:] or None,
        )

    @staticmethod
    def _parse_response(stdout: str) -> dict[str, Any]:
        marker_at = stdout.rfind(RESPONSE_MARKER)
        if marker_at < 0:
            raise PlatformError(
                "DEPENDENCY_FAILED",
                "Managed AINA did not return a protocol response",
                status_code=502,
                source="aina",
            )
        try:
            value = json.loads(stdout[marker_at + len(RESPONSE_MARKER) :])
        except json.JSONDecodeError as exc:
            raise PlatformError(
                "DEPENDENCY_FAILED",
                "Managed AINA returned invalid JSON",
                status_code=502,
                source="aina",
            ) from exc
        if not isinstance(value, dict):
            raise PlatformError(
                "DEPENDENCY_FAILED",
                "Managed AINA response must be an object",
                status_code=502,
                source="aina",
            )
        return value

    @staticmethod
    def _deployment_path(record: AinaProjectRecord) -> str:
        return f"managed-ainas/{ManagedAinaRuntime._deployment_name(record)}"

    @staticmethod
    def _deployment_name(record: AinaProjectRecord) -> str:
        value = f"{record.id}:{record.archive_sha256}".encode("utf-8")
        return hashlib.sha256(value).hexdigest()[:24]
