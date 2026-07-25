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
            },
            "timeout_seconds": {
                "type": "integer",
                "description": "Execution timeout between 1 and 300 seconds.",
            },
            "working_directory": {
                "type": "string",
                "description": "Relative directory below /workspace.",
            },
        },
        "required": ["script"],
        "additionalProperties": False,
    }
    return [
        AinaCapability(
            id=RUN_PYTHON_TOOL_ID,
            name="运行 Python",
            description="在当前用户的隔离沙箱中运行 Python 代码，并返回标准输出、错误和退出码。",
            input_schema=common_schema,
        ),
        AinaCapability(
            id=RUN_BASH_TOOL_ID,
            name="运行 Bash",
            description="在当前用户的隔离沙箱中运行 Bash 脚本，可下载用户级依赖或处理工作区文件。",
            input_schema=common_schema,
        ),
        AinaCapability(
            id=RUN_NODE_TOOL_ID,
            name="运行 Node.js",
            description="在当前用户的隔离沙箱中运行 Node.js 代码，并返回执行结果。",
            input_schema=common_schema,
        ),
    ]


def unibot_code_runner_record() -> AinaRecord:
    return AinaRecord(
        manifest=AinaManifest(
            protocol_version="1.0",
            aina=AinaIdentity(
                id=UNIBOT_CODE_RUNNER_ID,
                name="代码运行器",
                version="1.0.0",
                description="为每个用户提供独立工作区，在隔离沙箱中运行 Python、Bash 和 Node.js 脚本。",
                publisher=Publisher(id="unibot", name="Unibot"),
            ),
            runtime=BuiltinRuntimeDefinition(),
            capabilities=AinaCapabilities(
                tools=code_runner_tool_capabilities(),
                ui=[
                    AinaUiCapability(
                        id="code-runner",
                        kind="panel",
                        description="编辑脚本、运行调试、查看输出和执行历史。",
                    )
                ],
            ),
            main_widget=WidgetDefinition(
                id="unibot-code-runner-main",
                kind="panel",
                title="代码运行器",
                description="在当前用户的隔离沙箱中执行脚本。",
                markdown="工作区会跨沙箱重启保留；运行环境空闲后可停止，下一次使用时自动恢复。",
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
            language=language,
            script=str(arguments.get("script") or ""),
            timeout_seconds=int(arguments.get("timeout_seconds") or 60),
            working_directory=str(arguments.get("working_directory") or "."),
        )
    )
    return execution.model_dump(mode="json"), []
