"""Capability model shared by the agent runtime and tool execution logic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from tianzhou_agent_platform.aina.protocol.models import AinaInstallation, AinaRecord
from tianzhou_agent_platform.aina.tool.models import ToolRecord


@dataclass(slots=True)
class Capability:
    kind: Literal["tool", "aina", "builtin"]
    capability_id: str
    function_name: str
    display_name: str
    description: str
    input_schema: dict[str, Any]
    requires_confirmation: bool
    value: ToolRecord | tuple[AinaRecord, AinaInstallation] | str
    owner_aina_id: str | None = None

    def llm_definition(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.function_name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }
