"""AINA 2.0 widgets in the conversation (design §9.2): which widget a call shows, and the data it renders from."""

from __future__ import annotations

import json
import logging
from typing import Any

from tianzhou_agent_platform.aina.package.manifest import AinaPackageManifest
from tianzhou_agent_platform.aina.package.models import PackageRuntimeDefinition
from tianzhou_agent_platform.aina.protocol.models import AinaRecord
from tianzhou_agent_platform.aina.protocol.widgets import WidgetAinaUi, WidgetDefinition

logger = logging.getLogger(__name__)

WIDGET_META_KEY = "unibot/widget"
# Larger results are not handed to a widget; the model and the transcript still get them.
MAX_WIDGET_DATA_BYTES = 64 * 1024


def result_widget_id(manifest: AinaPackageManifest, tool_name: str, meta: dict[str, Any]) -> str | None:
    """Design §7.1: the tool's ``result_widget``, unless the result names another declared widget or ``null``."""
    exposed = manifest.exposed().get(tool_name)
    chosen = exposed.result_widget if exposed is not None else None
    if WIDGET_META_KEY in meta:
        requested = meta[WIDGET_META_KEY]
        declared = {widget.id: widget for widget in manifest.widgets}
        if requested is None:
            return None
        if not isinstance(requested, str) or requested not in declared or declared[requested].purpose == "confirmation":
            # Undeclared widget ids are rejected: the result is shown without a widget.
            logger.warning(
                "AINA tool result named an undeclared widget",
                extra={"aina_id": manifest.aina.id, "tool": tool_name, "widget": str(requested)[:64]},
            )
            return None
        chosen = requested
    return chosen


def confirmation_widget(
    record: AinaRecord, tool_name: str, *, call_id: str, arguments: dict[str, Any]
) -> WidgetAinaUi | None:
    runtime = record.manifest.runtime
    exposed = runtime.package.exposed().get(tool_name) if isinstance(runtime, PackageRuntimeDefinition) else None
    if exposed is None or exposed.confirm_widget is None:
        return None
    return aina_widget(record, exposed.confirm_widget, tool_name=tool_name, call_id=call_id, arguments=arguments)


def aina_widget(
    record: AinaRecord,
    widget_id: str,
    *,
    tool_name: str,
    call_id: str | None,
    arguments: dict[str, Any],
    content: str | None = None,
    structured_content: Any = None,
) -> WidgetAinaUi | None:
    """The data a declared widget renders from; it is archived with the message so the widget re-renders later."""
    runtime = record.manifest.runtime
    if not isinstance(runtime, PackageRuntimeDefinition):
        return None
    manifest = runtime.package
    declared = next((widget for widget in manifest.widgets if widget.id == widget_id), None)
    if declared is None:
        return None
    size = len(json.dumps([arguments, content, structured_content], ensure_ascii=False, default=str).encode())
    if size > MAX_WIDGET_DATA_BYTES:
        return None
    return WidgetAinaUi(
        aina_id=manifest.aina.id,
        version=manifest.aina.version,
        widget_id=declared.id,
        entry=declared.entry,
        height=declared.height or 160,
        purpose=declared.purpose,
        tool_name=tool_name,
        call_id=call_id,
        arguments=arguments,
        content=content,
        structured_content=structured_content,
    )


def aina_widget_definition(widget: WidgetAinaUi) -> WidgetDefinition:
    """The host widget that renders one AINA widget in the conversation."""
    return WidgetDefinition(
        id=f"aina-widget-{widget.call_id or widget.widget_id}",
        kind="aina_widget",
        title=widget.widget_id,
        aina_widget=widget,
    )
