from __future__ import annotations

from typing import Any

from tianzhou_agent_platform.aina.protocol.models import (
    AinaCapabilities,
    AinaCapability,
    AinaIdentity,
    AinaManifest,
    AinaRecord,
    AinaUiCapability,
    BuiltinRuntimeDefinition,
    Publisher,
)
from tianzhou_agent_platform.aina.protocol.widgets import WidgetDefinition
from tianzhou_agent_platform.aina.security.models import Authentication
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.sandbox.models import SandboxExecutionRequest
from tianzhou_agent_platform.sandbox.service import SandboxService

UNIBOT_CODE_RUNNER_ID = "unibot-code-runner"
RUN_PYTHON_TOOL_ID = "sandbox.run_python"
RUN_BASH_TOOL_ID = "sandbox.run_bash"
RUN_NODE_TOOL_ID = "sandbox.run_node"
CODE_RUNNER_TOOL_IDS = {
    RUN_PYTHON_TOOL_ID,
    RUN_BASH_TOOL_ID,
    RUN_NODE_TOOL_ID,
}


def code_runner_tool_capabilities() -> list[AinaCapability]:
    common_schema = {
        "type": "object",
        "properties": {
            "script": {
                "type": "string",
                "description": "The complete source code or shell script to execute.",
                "minLength": 1,
                "maxLength": 200_000,
            },
            "timeout_seconds": {
                "type": "integer",
                "description": "Execution timeout between 1 and 300 seconds.",
                "minimum": 1,
                "maximum": 300,
            },
            "working_directory": {
                "type": "string",
                "description": "Relative directory below /workspace; parent traversal and absolute paths are forbidden.",
                "maxLength": 500,
                "pattern": r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$)).*$",
            },
        },
        "required": ["script"],
        "additionalProperties": False,
    }
    return [
        AinaCapability(
            id=RUN_PYTHON_TOOL_ID,
            name="Run Python",
            description="Run Python code in the current user's isolated sandbox and return stdout, stderr and the exit code.",
            input_schema=common_schema,
        ),
        AinaCapability(
            id=RUN_BASH_TOOL_ID,
            name="Run Bash",
            description="Run a Bash script in the current user's isolated sandbox, e.g. to install user-level dependencies or process workspace files.",
            input_schema=common_schema,
        ),
        AinaCapability(
            id=RUN_NODE_TOOL_ID,
            name="Run Node.js",
            description="Run Node.js code in the current user's isolated sandbox and return the result.",
            input_schema=common_schema,
        ),
    ]


def unibot_code_runner_record() -> AinaRecord:
    return AinaRecord(
        manifest=AinaManifest(
            protocol_version="1.0",
            aina=AinaIdentity(
                id=UNIBOT_CODE_RUNNER_ID,
                name="Code Runner",
                version="1.0.0",
                description="A separate workspace per user that runs Python, Bash and Node.js scripts in an isolated sandbox.",
                publisher=Publisher(id="unibot", name="Unibot"),
            ),
            runtime=BuiltinRuntimeDefinition(),
            capabilities=AinaCapabilities(
                tools=code_runner_tool_capabilities(),
                ui=[
                    AinaUiCapability(
                        id="code-runner",
                        kind="panel",
                        description="Edit scripts, run and debug them, and view output and run history.",
                    )
                ],
            ),
            main_widget=WidgetDefinition(
                id="unibot-code-runner-main",
                kind="panel",
                title="Code Runner",
                description="Run scripts in the current user's isolated sandbox.",
                markdown="The workspace survives sandbox restarts; an idle runtime may stop and resumes automatically on next use.",
            ),
            permissions=["sandbox.execute", "network.download"],
            authentication=Authentication(type="none"),
        ),
        last_health={"status": "healthy", "runtime": "builtin", "isolation": "gvisor"},
    )


async def invoke_code_runner_tool(
    service: SandboxService,
    tool_id: str,
    arguments: dict[str, Any],
    *,
    user_id: str,
    tenant_id: str,
    workspace_id: str | None = None,
) -> tuple[dict[str, Any], list[WidgetDefinition]]:
    language = {
        RUN_PYTHON_TOOL_ID: "python",
        RUN_BASH_TOOL_ID: "bash",
        RUN_NODE_TOOL_ID: "node",
    }.get(tool_id)
    if language is None:
        raise PlatformError("RESOURCE_NOT_FOUND", f"Unknown code runner tool {tool_id!r}", status_code=404)
    execution = await service.execute(
        SandboxExecutionRequest(
            user_id=user_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            language=language,
            script=str(arguments.get("script") or ""),
            timeout_seconds=int(arguments.get("timeout_seconds") or 60),
            working_directory=str(arguments.get("working_directory") or "."),
        )
    )
    return execution.model_dump(mode="json"), []
