from fastapi.testclient import TestClient
from pydantic import SecretStr

from tests.support.fake_llm import ScriptedLLM, assistant
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.core.trace_details import REDACTED, sanitize_trace_data
from tianzhou_agent_platform.main import create_app


def test_trace_groups_builtin_capabilities_under_their_ainas() -> None:
    settings = AgentSettings(  # type: ignore[call-arg]
        _env_file=None,
        llm_base_url="https://model.invalid/v1",
        llm_api_key=SecretStr("test-key"),
        llm_model="test-model",
    )
    with TestClient(
        create_app(settings=settings, llm=ScriptedLLM([assistant("NO_AINA_MATCH"), assistant("Hello.")]))
    ) as client:
        response = client.post("/chat", json={"message": "Hello"})
        trace = client.get(f"/traces/{response.json()['trace_id']}").json()

    discovery = next(event for event in trace["events"] if event["kind"] == "capability.discovery")["details"]
    graph = discovery["aina_graph"]
    assert graph["available_count"] == 4
    assert graph["counts"] == {"builtin_aina": 4, "remote_aina": 0}
    assert graph["excluded"] == []
    available = {item["id"]: item for item in graph["available"]}
    assert {item["id"] for item in available["unibot-assistant"]["capabilities"]["tools"]} == {
        "describe_aina",
        "list_app",
        "open_aina",
        "request_clarification",
    }
    assert {item["id"] for item in available["unibot-memory"]["capabilities"]["tools"]} == {
        "memory.remember",
        "memory.recall",
        "memory.update",
        "memory.forget",
    }
    assert {item["id"] for item in available["unibot-code-runner"]["capabilities"]["tools"]} == {
        "sandbox.run_python",
        "sandbox.run_bash",
        "sandbox.run_node",
    }
    scope = {item["aina_id"]: item["capabilities"] for item in discovery["model_scope"]["by_aina"]}
    assert {item["id"] for item in scope["unibot-assistant"]} == {
        "describe_aina",
        "list_app",
        "open_aina",
        "request_clarification",
    }
    assert {item["id"] for item in scope["unibot-memory"]} == {
        "memory.remember",
        "memory.recall",
        "memory.update",
        "memory.forget",
    }


def test_trace_sanitizer_redacts_nested_credentials_and_inline_secrets() -> None:
    value = {
        "authorization": "Bearer header-secret",
        "nested": {
            "apiKey": "api-secret",
            "safe": "Bearer inline-secret",
        },
        "messages": [
            "password: english-secret",
            "密码是中文秘密",
            "provider key sk-1234567890abcdef",
        ],
        "input_tokens": 17,
    }

    assert sanitize_trace_data(value) == {
        "authorization": REDACTED,
        "nested": {
            "apiKey": REDACTED,
            "safe": "Bearer [REDACTED]",
        },
        "messages": [
            "password: [REDACTED]",
            "密码是[REDACTED]",
            "provider key [REDACTED]",
        ],
        "input_tokens": 17,
    }


def test_trace_sanitizer_bounds_large_values() -> None:
    sanitized = sanitize_trace_data({"content": "x" * 5_000, "items": list(range(60))})

    assert sanitized["content"].endswith("[TRUNCATED 1000 CHARS]")
    assert sanitized["items"][-1] == "[TRUNCATED 10 ITEMS]"
