from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


@dataclass
class JudgeSettings:
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    temperature: float = 0.0

    @property
    def configured(self) -> bool:
        return bool(self.model and self.api_key)


@dataclass
class EvalSettings:
    base_url: str = "http://127.0.0.1:8000"
    timeout_s: float = 180.0
    headers: dict[str, str] = field(default_factory=dict)
    tenant_id: str = "default"
    repeats: int = 1
    concurrency: int = 2
    stream: bool = True
    trace_wait_s: float = 10.0
    keep_conversations: bool = False
    judge: JudgeSettings = field(default_factory=JudgeSettings)

    @classmethod
    def from_env(cls) -> "EvalSettings":
        env = os.environ
        headers = json.loads(env["UNIBOT_EVAL_HEADERS"]) if env.get("UNIBOT_EVAL_HEADERS") else {}
        return cls(
            base_url=env.get("UNIBOT_EVAL_BASE_URL", cls.base_url),
            timeout_s=float(env.get("UNIBOT_EVAL_TIMEOUT", cls.timeout_s)),
            headers={str(k): str(v) for k, v in headers.items()},
            tenant_id=env.get("UNIBOT_EVAL_TENANT", cls.tenant_id),
            repeats=int(env.get("UNIBOT_EVAL_REPEATS", cls.repeats)),
            concurrency=int(env.get("UNIBOT_EVAL_CONCURRENCY", cls.concurrency)),
            stream=env.get("UNIBOT_EVAL_STREAM", "true").lower() not in {"0", "false", "no"},
            keep_conversations=env.get("UNIBOT_EVAL_KEEP_CONVERSATIONS", "").lower() in {"1", "true", "yes"},
            judge=JudgeSettings(
                # The backend's own .env names (llm_model / llm_base_url / llm_api_key) are accepted as a fallback
                # so the judge can reuse the platform's OpenAI-compatible provider without extra configuration.
                model=env.get("EVAL_JUDGE_MODEL") or env.get("llm_model"),
                base_url=env.get("EVAL_JUDGE_BASE_URL") or env.get("llm_base_url"),
                api_key=env.get("EVAL_JUDGE_API_KEY") or env.get("llm_api_key") or env.get("OPENAI_API_KEY"),
            ),
        )
