from tianzhou_agent_platform.aina.protocol.models import (
    AinaCapabilities,
    AinaIdentity,
    AinaManifest,
    AinaRecord,
    AinaUiCapability,
    BuiltinRuntimeDefinition,
    Publisher,
)
from tianzhou_agent_platform.aina.protocol.widgets import WidgetDefinition
from tianzhou_agent_platform.aina.security.models import Authentication

UNIBOT_SCHEDULER_ID = "unibot-scheduler"


def unibot_scheduler_record() -> AinaRecord:
    return AinaRecord(
        manifest=AinaManifest(
            protocol_version="1.0",
            aina=AinaIdentity(
                id=UNIBOT_SCHEDULER_ID,
                name="Scheduler",
                version="1.0.0",
                description="Schedule Agent tasks or a selected AINA on a fixed interval or a cron expression, with an immediate debug run.",
                publisher=Publisher(id="unibot", name="Unibot"),
            ),
            runtime=BuiltinRuntimeDefinition(),
            capabilities=AinaCapabilities(
                ui=[
                    AinaUiCapability(
                        id="schedule-manager",
                        kind="panel",
                        description="Manage scheduled Agent and AINA tasks, their run status and debug results.",
                    )
                ]
            ),
            main_widget=WidgetDefinition(
                id="unibot-scheduler-main",
                kind="panel",
                title="Scheduler",
                description="Manage distributed Agent and AINA schedules.",
                markdown=(
                    "### Distributed scheduling\n\nRun a general Agent task or select an AINA. "
                    "Supports fixed intervals and five-field cron expressions. "
                    "Unibot nodes coordinate through renewable Redis leases to prevent overlapping runs of one task."
                ),
            ),
            authentication=Authentication(type="none"),
        ),
        last_health={"status": "healthy", "runtime": "builtin"},
    )
