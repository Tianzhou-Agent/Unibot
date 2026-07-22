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
                name="定时任务 AINA",
                version="1.0.0",
                description="通过固定间隔或 Cron 表达式调度已安装的远程 AINA，并支持立即调试。",
                publisher=Publisher(id="unibot", name="Unibot"),
            ),
            runtime=BuiltinRuntimeDefinition(),
            capabilities=AinaCapabilities(
                ui=[
                    AinaUiCapability(
                        id="schedule-manager",
                        kind="panel",
                        description="管理 AINA 定时任务、运行状态和调试结果。",
                    )
                ]
            ),
            main_widget=WidgetDefinition(
                id="unibot-scheduler-main",
                kind="panel",
                title="定时任务 AINA",
                description="管理分布式 AINA 调度任务。",
                markdown=(
                    "### 分布式定时调度\n\n支持固定间隔和五段 Cron 表达式。"
                    "多个 Unibot 节点通过 Redis 租约竞争，同一计划时间仅由一个节点执行。"
                ),
            ),
            authentication=Authentication(type="none"),
        ),
        last_health={"status": "healthy", "runtime": "builtin"},
    )
