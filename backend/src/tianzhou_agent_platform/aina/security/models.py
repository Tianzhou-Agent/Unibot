from typing import Literal

from pydantic import Field, SecretStr

from tianzhou_agent_platform.core.base import StrictModel


class Authentication(StrictModel):
    type: Literal["none", "bearer", "api_key", "oauth2"] = "none"
    header_name: str = "Authorization"
    credential: SecretStr | None = Field(default=None, exclude=True)
