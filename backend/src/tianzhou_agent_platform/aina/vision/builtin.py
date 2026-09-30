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
                name="Image Recognition",
                version="1.0.0",
                description="Paste or pick an image and detect its objects, counts, positions and confidence with YOLO26m.",
                publisher=Publisher(id="unibot", name="Unibot"),
            ),
            runtime=BuiltinRuntimeDefinition(),
            capabilities=AinaCapabilities(
                ui=[
                    AinaUiCapability(
                        id="image-recognition",
                        kind="panel",
                        description="Upload an image and show detection boxes and structured recognition results.",
                        instructions="Image recognition happens in its own Canvas; uploaded originals are not stored.",
                    )
                ],
            ),
            main_widget=WidgetDefinition(
                id="unibot-image-recognition-main",
                kind="panel",
                title="Image Recognition",
                description="Paste a screenshot or pick an image to detect the objects in it.",
                markdown="Object detection with YOLO26m; images are processed only during inference and never persisted.",
            ),
            permissions=[],
            authentication=Authentication(type="none"),
        ),
        last_health={"status": "configured", "runtime": "builtin", "model": "yolo26m"},
    )
