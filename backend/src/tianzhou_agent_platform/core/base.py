from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict


def utc_now() -> datetime:
    return datetime.now(UTC)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Usage(StrictModel):
    input_tokens: int = 0
    output_tokens: int = 0
    estimated: bool = False
