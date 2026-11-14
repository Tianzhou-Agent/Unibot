from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict

from unibot_agent.types import Usage as Usage


def utc_now() -> datetime:
    return datetime.now(UTC)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
