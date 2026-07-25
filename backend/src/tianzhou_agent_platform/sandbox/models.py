from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from tianzhou_agent_platform.core.base import StrictModel, utc_now

SandboxStatus = Literal["provisioning", "ready", "busy", "stopped", "error"]
ExecutionStatus = Literal["running", "succeeded", "failed", "timed_out"]
ExecutionLanguage = Literal["python", "bash", "shell", "node"]


class SandboxEnsureRequest(StrictModel):
    user_id: str = Field(default="anonymous", min_length=1, max_length=160)
    tenant_id: str = Field(default="default", min_length=1, max_length=160)


class SandboxExecutionRequest(SandboxEnsureRequest):
    language: ExecutionLanguage
    script: str = Field(min_length=1, max_length=200_000)
    timeout_seconds: int = Field(default=60, ge=1, le=300)
    working_directory: str = Field(default=".", max_length=500)
    environment: dict[str, str] = Field(default_factory=dict)

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


class SandboxRecord(StrictModel):
    id: str
    user_id: str
    tenant_id: str
    image: str
    driver: Literal["local", "kubernetes"]
    status: SandboxStatus = "provisioning"
    runtime_name: str
    workspace: str
    endpoint: str | None = None
    last_error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    last_activity_at: datetime = Field(default_factory=utc_now)


class SandboxExecution(StrictModel):
    id: str
    sandbox_id: str
    user_id: str
    tenant_id: str
    language: ExecutionLanguage
    script: str
    working_directory: str = "."
    status: ExecutionStatus = "running"
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    duration_ms: float | None = None
    truncated: bool = False
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None


class DriverSandboxState(StrictModel):
    status: SandboxStatus
    runtime_name: str
    workspace: str
    endpoint: str | None = None
    error: str | None = None


class DriverExecutionResult(StrictModel):
    status: ExecutionStatus
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    duration_ms: float
    truncated: bool = False
