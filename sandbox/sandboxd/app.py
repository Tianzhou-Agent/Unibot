from __future__ import annotations

import asyncio
import os
import signal
import shutil
import sys
from pathlib import Path
from time import perf_counter
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, field_validator

WORKSPACE = Path(os.getenv("SANDBOX_WORKSPACE", "/workspace")).resolve()
RUNTIME = Path(os.getenv("SANDBOX_RUNTIME", str(WORKSPACE))).resolve()
OUTPUT_LIMIT_BYTES = int(os.getenv("SANDBOX_OUTPUT_LIMIT_BYTES", "1000000"))
FILE_LIMIT_BYTES = int(os.getenv("SANDBOX_FILE_LIMIT_BYTES", "25000000"))
EXECUTION_LOCK = asyncio.Lock()


class ExecutionRequest(BaseModel):
    language: Literal["python", "bash", "shell", "node"]
    script: str = Field(min_length=1, max_length=200_000)
    timeout_seconds: int = Field(default=60, ge=1, le=300)
    working_directory: str = Field(default=".", max_length=500)
    environment: dict[str, str] = Field(default_factory=dict)
    user_id: str = "anonymous"
    tenant_id: str = "default"

    @field_validator("working_directory")
    @classmethod
    def validate_working_directory(cls, value: str) -> str:
        normalized = value.replace("\\", "/").strip()
        if normalized.startswith("/") or normalized == ".." or normalized.startswith("../") or "/../" in normalized:
            raise ValueError("working_directory must stay inside /workspace")
        return normalized or "."

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 32:
            raise ValueError("environment cannot contain more than 32 entries")
        for name, item in value.items():
            if not name or len(name) > 128 or not name.replace("_", "A").isalnum() or not name[0].isalpha():
                raise ValueError(f"invalid environment variable name: {name!r}")
            if len(item) > 8_192:
                raise ValueError(f"environment variable {name!r} is too large")
        return value


class ExecutionResult(BaseModel):
    status: Literal["succeeded", "failed", "timed_out"]
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    duration_ms: float
    truncated: bool = False


app = FastAPI(title="Unibot sandboxd", version="1.0.0")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ready", "workspace": str(WORKSPACE)}


@app.post("/exec", response_model=ExecutionResult)
async def execute(payload: ExecutionRequest) -> ExecutionResult:
    async with EXECUTION_LOCK:
        return await execute_serialized(payload)


@app.put("/files/{relative_path:path}", status_code=204)
async def write_file(
    relative_path: str,
    request: Request,
    overwrite: bool = Query(default=True),
) -> Response:
    content = await request.body()
    if len(content) > FILE_LIMIT_BYTES:
        raise HTTPException(status_code=413, detail="Sandbox file exceeds the configured size limit")
    async with EXECUTION_LOCK:
        target = workspace_file(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not overwrite:
            raise HTTPException(status_code=409, detail="Sandbox file already exists")
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            await asyncio.to_thread(temporary.write_bytes, content)
            await asyncio.to_thread(temporary.replace, target)
        finally:
            await asyncio.to_thread(temporary.unlink, missing_ok=True)
    return Response(status_code=204)


@app.get("/files/{relative_path:path}")
async def read_file(relative_path: str) -> Response:
    async with EXECUTION_LOCK:
        target = workspace_file(relative_path)
        try:
            content = await asyncio.to_thread(target.read_bytes)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Sandbox file was not found") from exc
    if len(content) > FILE_LIMIT_BYTES:
        raise HTTPException(status_code=413, detail="Sandbox file exceeds the configured size limit")
    return Response(content, media_type="application/octet-stream")


@app.delete("/files/{relative_path:path}", status_code=204)
async def delete_file(relative_path: str) -> Response:
    async with EXECUTION_LOCK:
        target = workspace_file(relative_path)
        try:
            await asyncio.to_thread(target.unlink)
        except FileNotFoundError:
            return Response(status_code=204)
    return Response(status_code=204)


async def execute_serialized(payload: ExecutionRequest) -> ExecutionResult:
    working_directory = (WORKSPACE / payload.working_directory).resolve()
    if working_directory != WORKSPACE and WORKSPACE not in working_directory.parents:
        raise HTTPException(status_code=403, detail="Working directory escapes /workspace")
    working_directory.mkdir(parents=True, exist_ok=True)
    RUNTIME.mkdir(parents=True, exist_ok=True)
    python_packages = RUNTIME / ".python-packages"
    npm_prefix = RUNTIME / ".npm-global"
    command = command_for(payload.language, payload.script)
    environment = {
        "HOME": str(RUNTIME),
        "PYTHONUNBUFFERED": "1",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": str(python_packages),
        "PIP_TARGET": str(python_packages),
        "npm_config_prefix": str(npm_prefix),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": os.pathsep.join(
            [
                str(npm_prefix / "bin"),
                str(python_packages / "bin"),
                os.environ.get("PATH", ""),
            ]
        ),
        "UNIBOT_SANDBOX": "true",
        **payload.environment,
    }
    started = perf_counter()
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=working_directory,
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    assert process.stdout is not None and process.stderr is not None
    stdout_task = asyncio.create_task(read_limited(process.stdout, OUTPUT_LIMIT_BYTES))
    stderr_task = asyncio.create_task(read_limited(process.stderr, OUTPUT_LIMIT_BYTES))
    try:
        await asyncio.wait_for(
            process.wait(),
            timeout=payload.timeout_seconds,
        )
        status = "succeeded" if process.returncode == 0 else "failed"
        exit_code = process.returncode
    except TimeoutError:
        terminate_process_tree(process)
        await process.wait()
        status = "timed_out"
        exit_code = None
    (stdout, stdout_truncated), (stderr, stderr_truncated) = await asyncio.gather(
        stdout_task,
        stderr_task,
    )
    if status == "timed_out":
        stderr += f"\nExecution timed out after {payload.timeout_seconds} seconds."
    return ExecutionResult(
        status=status,
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        duration_ms=(perf_counter() - started) * 1000,
        truncated=stdout_truncated or stderr_truncated,
    )


def command_for(language: str, script: str) -> list[str]:
    if language == "python":
        return [sys.executable, "-c", script]
    if language == "node":
        executable = shutil.which("node")
        if executable is None:
            raise HTTPException(status_code=503, detail="Node.js is not installed")
        return [executable, "-e", script]
    executable = shutil.which("bash")
    if executable is None:
        raise HTTPException(status_code=503, detail="Bash is not installed")
    return [executable, "-lc", script]


def workspace_file(relative_path: str) -> Path:
    normalized = relative_path.replace("\\", "/")
    if (
        not normalized
        or normalized.startswith("/")
        or "\x00" in normalized
        or any(part in {"", ".", ".."} for part in normalized.split("/"))
    ):
        raise HTTPException(status_code=403, detail="File path escapes /workspace")
    target = (WORKSPACE / normalized).resolve()
    if not normalized or target == WORKSPACE or WORKSPACE not in target.parents:
        raise HTTPException(status_code=403, detail="File path escapes /workspace")
    if target.exists() and not target.is_file():
        raise HTTPException(status_code=409, detail="Sandbox path is not a file")
    return target


def terminate_process_tree(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    if sys.platform != "win32" and process.pid is not None:
        os.killpg(process.pid, signal.SIGKILL)
        return
    process.kill()


async def read_limited(stream: asyncio.StreamReader, limit: int) -> tuple[str, bool]:
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
