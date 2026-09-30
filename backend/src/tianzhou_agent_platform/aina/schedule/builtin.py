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
                description="Schedule installed remote AINA on a fixed interval or a cron expression, with an immediate debug run.",
                publisher=Publisher(id="unibot", name="Unibot"),
            ),
            runtime=BuiltinRuntimeDefinition(),
            capabilities=AinaCapabilities(
                ui=[
                    AinaUiCapability(
                        id="schedule-manager",
                        kind="panel",
                        description="Manage scheduled AINA tasks, their run status and debug results.",
                    )
                ]
            ),
            main_widget=WidgetDefinition(
                id="unibot-scheduler-main",
                kind="panel",
                title="Scheduler",
                description="Manage distributed AINA schedules.",
                markdown=(
                    "### Distributed scheduling\n\nSupports fixed intervals and five-field cron expressions. "
                    "Unibot nodes compete for a Redis lease, so each scheduled time runs on exactly one node."
                ),
            ),
            authentication=Authentication(type="none"),
        ),
        last_health={"status": "healthy", "runtime": "builtin"},
    )
