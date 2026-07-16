from typing import Literal

from pydantic import Field

from tianzhou_agent_platform.core.base import StrictModel


class WidgetField(StrictModel):
    id: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=160)
    input_type: Literal["text", "number", "textarea"] = "text"
    placeholder: str = ""
    required: bool = False
    value: str | None = None


class WidgetAction(StrictModel):
    id: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=160)
    kind: Literal["open_aina", "prompt"]
    aina_id: str | None = None
    prompt: str | None = None
    style: Literal["primary", "secondary"] = "primary"


class WidgetApp(StrictModel):
    aina_id: str
    name: str
    description: str
    version: str
    publisher: str
    installed: bool = True
    has_main_widget: bool = False


class WidgetDefinition(StrictModel):
    id: str = Field(min_length=1, max_length=160)
    kind: Literal["app_list", "form", "markdown", "panel", "navigation", "memory", "document"]
    title: str
    description: str = ""
    markdown: str | None = None
    fields: list[WidgetField] = Field(default_factory=list)
    actions: list[WidgetAction] = Field(default_factory=list)
    apps: list[WidgetApp] = Field(default_factory=list)
