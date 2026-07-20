from __future__ import annotations

from typing import Any

import pytest
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import HumanMessage

from tests.support.fake_llm import ScriptedLLM, assistant
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.core.langchain_adapter import LangChainChatModel, ModelRunContext
from tianzhou_agent_platform.core.observability import LangSmithObservability


class RecordingCallback(BaseCallbackHandler):
    def __init__(self) -> None:
        self.started = 0
        self.completed = 0

    def on_chat_model_start(self, *_: Any, **__: Any) -> None:
        self.started += 1

    def on_llm_end(self, *_: Any, **__: Any) -> None:
        self.completed += 1


@pytest.mark.asyncio
async def test_provider_adapter_emits_standard_langchain_model_callbacks() -> None:
    callback = RecordingCallback()
    client = ScriptedLLM([assistant("Hello from LangChain.")])
    model = LangChainChatModel(
        client=client,
        context=ModelRunContext(
            repository=object(),
            trace_id="trace_test",
            capabilities={},
            record_local_trace=False,
        ),
        default_model_name="test-model",
    )

    response = await model.ainvoke(
        [HumanMessage(content="Hello")],
        config={"callbacks": [callback]},
    )

    assert response.text == "Hello from LangChain."
    assert callback.started == 1
    assert callback.completed == 1
    assert client.calls[0]["messages"] == [{"role": "user", "content": "Hello"}]


def test_langsmith_client_applies_existing_trace_redaction(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        def close(self, timeout: float | None = None) -> None:
            captured["close_timeout"] = timeout

    monkeypatch.setattr("tianzhou_agent_platform.core.observability.Client", FakeClient)
    settings = AgentSettings(
        _env_file=None,
        langsmith_tracing=True,
        langsmith_api_key="langsmith-secret",
        langsmith_project="unibot-test",
    )

    observability = LangSmithObservability(settings)
    sanitized = captured["hide_inputs"](
        {
            "authorization": "Bearer provider-secret",
            "message": "password: customer-secret",
            "messages": [HumanMessage(content="api_key=message-secret")],
        }
    )

    assert sanitized == {
        "authorization": "[REDACTED]",
        "message": "password: [REDACTED]",
        "messages": [
            {
                "content": "api_key=[REDACTED]",
                "additional_kwargs": {},
                "response_metadata": {},
                "type": "human",
                "name": None,
                "id": None,
            }
        ],
    }
    assert captured["api_key"] == "langsmith-secret"
    assert observability.enabled is True
