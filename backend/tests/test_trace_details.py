from fastapi.testclient import TestClient
from pydantic import SecretStr

from tests.support.fake_llm import ScriptedLLM, assistant
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.core.trace_details import (
    REDACTED,
    redact_trace_data,
    sanitize_trace_data,
)
from tianzhou_agent_platform.main import create_app


def test_trace_groups_aina_tools_and_keeps_host_tools_standalone() -> None:
    settings = AgentSettings(  # type: ignore[call-arg]
        _env_file=None,
        llm_base_url="https://model.invalid/v1",
        llm_api_key=SecretStr("test-key"),
        llm_model="test-model",
    )
    with TestClient(
        create_app(settings=settings, llm=ScriptedLLM([assistant("Hello.")]))
    ) as client:
        response = client.post("/chat", json={"message": "Hello"})
        trace = client.get(f"/traces/{response.json()['trace_id']}").json()

    root_span = next(span for span in trace["spans"] if span["span_id"] == trace["root_span_id"])
    assert root_span["kind"] == "agent"
    assert root_span["parent_span_id"] is None
    assert root_span["status"] == "completed"
    assert root_span["duration_ms"] is not None
    assert root_span["input"] == {
        "message": "Hello",
        "requested_capability": None,
        "preferred_aina_id": None,
    }
    assert root_span["output"]["content"] == "Hello."
    model_span = next(span for span in trace["spans"] if span["kind"] == "model")
    assert model_span["parent_span_id"] == root_span["span_id"]
    assert model_span["status"] == "completed"
    assert model_span["attributes"]["input_tokens"] == 5
    assert model_span["attributes"]["output_tokens"] == 3
    assert model_span["input"]["messages"][-1] == {"role": "user", "content": "Hello"}
    assert model_span["output"] == {"role": "assistant", "content": "Hello."}

    discovery = next(event for event in trace["events"] if event["kind"] == "capability.discovery")["details"]
    graph = discovery["aina_graph"]
    assert graph["available_count"] == 4
    assert graph["counts"] == {"builtin_aina": 4, "remote_aina": 0}
    assert graph["excluded"] == []
    available = {item["id"]: item for item in graph["available"]}
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
    assert {item["id"] for item in scope["unibot-memory"]} == {"unibot-memory"}
    assert {item["id"] for item in discovery["model_scope"]["standalone"]} == {
        "describe_aina",
        "list_app",
        "open_aina",
        "request_clarification",
    }
    assert all(item["owner_aina_id"] is None for item in discovery["model_scope"]["standalone"])


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


def test_raw_trace_redaction_covers_private_signing_ssh_and_cloud_keys() -> None:
    pem = (
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        "private-material\n"
        "-----END OPENSSH PRIVATE KEY-----"
    )
    certificate = "-----BEGIN CERTIFICATE-----\npublic-material\n-----END CERTIFICATE-----"

    assert redact_trace_data(
        {
            "privateKey": "private-value",
            "signing_key": "signing-value",
            "sshPrivateKey": "ssh-value",
            "awsSecretAccessKey": "cloud-value",
            "safe_text": f"prefix {pem} suffix",
            "assignment_text": (
                "aws_secret_access_key=cloud-value private_key: private-value "
                "signing_key=signing-value ssh_private_key:ssh-value"
            ),
            "certificate": certificate,
        }
    ) == {
        "privateKey": REDACTED,
        "signing_key": REDACTED,
        "sshPrivateKey": REDACTED,
        "awsSecretAccessKey": REDACTED,
        "safe_text": f"prefix {REDACTED} suffix",
        "assignment_text": (
            f"aws_secret_access_key={REDACTED} private_key: {REDACTED} "
            f"signing_key={REDACTED} ssh_private_key:{REDACTED}"
        ),
        "certificate": certificate,
    }
