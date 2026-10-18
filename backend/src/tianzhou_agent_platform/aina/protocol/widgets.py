from typing import Any, Literal

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


class WidgetDocumentSection(StrictModel):
    index: int = Field(ge=1)
    heading: str = Field(min_length=1, max_length=500)
    level: int = Field(ge=1, le=6)
    occurrence: int = Field(ge=1)
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)


class WidgetCanvas(StrictModel):
    """The web app of an AINA 2.0 canvas: a file of the package (``entry``) or the developer's HTTPS app (``url``)."""

    kind: Literal["package", "url"]
    version: str
    entry: str | None = None
    url: str | None = None


class WidgetAinaUi(StrictModel):
    """One AINA 2.0 widget (design §9.2): a packaged HTML file rendered in a sandboxed frame, with its input."""

    aina_id: str
    version: str
    widget_id: str
    entry: str
    height: int = Field(default=160, ge=1, le=480)
    purpose: Literal["confirmation", "result", "display"]
    tool_name: str
    call_id: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    # ``content`` text and ``structuredContent`` of the result, for result and display widgets.
    content: str | None = None
    structured_content: Any = None


class WidgetDefinition(StrictModel):
    id: str = Field(min_length=1, max_length=160)
    kind: Literal[
        "aina_chooser",
        "app_list",
        "form",
        "markdown",
        "panel",
        "navigation",
        "memory",
        "document",
        "document_outline",
        "aina_canvas",
        "aina_widget",
    ]
    title: str
    description: str = ""
    markdown: str | None = None
    fields: list[WidgetField] = Field(default_factory=list)
    actions: list[WidgetAction] = Field(default_factory=list)
    apps: list[WidgetApp] = Field(default_factory=list)
    document_name: str | None = None
    sections: list[WidgetDocumentSection] = Field(default_factory=list)
    canvas: WidgetCanvas | None = None
    aina_widget: WidgetAinaUi | None = None
