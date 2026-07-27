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

UNIBOT_IMAGE_RECOGNITION_ID = "unibot-image-recognition"


def unibot_image_recognition_record() -> AinaRecord:
    return AinaRecord(
        manifest=AinaManifest(
            protocol_version="1.0",
            aina=AinaIdentity(
                id=UNIBOT_IMAGE_RECOGNITION_ID,
                name="图片识别",
                version="1.0.0",
                description="粘贴或选择图片，使用 YOLO26m 检测其中的对象、数量、位置和置信度。",
                publisher=Publisher(id="unibot", name="Unibot"),
            ),
            runtime=BuiltinRuntimeDefinition(),
            capabilities=AinaCapabilities(
                ui=[
                    AinaUiCapability(
                        id="image-recognition",
                        kind="panel",
                        description="上传图片、显示目标检测框和结构化识别结果。",
                        instructions="图片识别在专属 Canvas 中完成，不保存用户上传的原图。",
                    )
                ],
            ),
            main_widget=WidgetDefinition(
                id="unibot-image-recognition-main",
                kind="panel",
                title="图片识别",
                description="粘贴截图或选择一张图片，自动识别其中的目标。",
                markdown="使用 YOLO26m 进行目标检测；图片仅在本次推理期间处理，不会持久化保存。",
            ),
            permissions=[],
            authentication=Authentication(type="none"),
        ),
        last_health={"status": "configured", "runtime": "builtin", "model": "yolo26m"},
    )
