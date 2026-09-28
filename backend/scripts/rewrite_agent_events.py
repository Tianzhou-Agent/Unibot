"""Rewrite agent.py event surface to RunEventPublisher."""
from pathlib import Path
import re

p = Path("src/tianzhou_agent_platform/core/agent.py")
text = p.read_text(encoding="utf-8")
text = text.replace("self.observability = observability", "self.events = events or NULL_RUN_EVENTS")
text = text.replace("observability: Any | None = None", "events: Any | None = None")
text = text.replace("await self._obs_event(", "await self.events.push(")
text = text.replace("await self._obs_start(", "await self.events.start(")
text = text.replace("await self._obs_finish(", "await self.events.finish(")
text = text.replace("if self.observability is None:", "if False:")
text = re.sub(
    r"    async def _obs_event\(self.*?logger\.debug\(\"observability record_event failed\", exc_info=True\)\n",
    "",
    text,
    flags=re.S,
)
text = re.sub(
    r"    async def _obs_start\(self.*?return root_span_id\n",
    "",
    text,
    flags=re.S,
)
text = re.sub(
    r"    async def _obs_finish\(self.*?logger\.debug\(\"observability finish failed\", exc_info=True\)\n",
    "",
    text,
    flags=re.S,
)
if "NULL_RUN_EVENTS" not in text.split("logger =")[0]:
    text = text.replace(
        "logger = logging.getLogger(__name__)",
        "logger = logging.getLogger(__name__)\n\nfrom tianzhou_agent_platform.core.run_events import NULL_RUN_EVENTS  # noqa: E402",
    )
p.write_text(text, encoding="utf-8")
print("observability", "observability" in text)
print("observation", "observation" in text)
print("record_event(", "record_event(" in text)
