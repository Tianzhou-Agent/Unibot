from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable

import httpx
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from tests.support.fake_llm import ScriptedLLM, assistant, call_first_tool  # noqa: E402
from tianzhou_agent_platform.config import AgentSettings  # noqa: E402
from tianzhou_agent_platform.main import create_app  # noqa: E402


Case = tuple[str, str, Callable[[], dict[str, Any]]]


def settings() -> AgentSettings:
    return AgentSettings(
        _env_file=None,
        llm_base_url="https://model.invalid/v1",
        llm_api_key="e2e-key",
        llm_model="e2e-model",
    )


def expect_status(response: httpx.Response, expected: int) -> Any:
    if response.status_code != expected:
        raise AssertionError(f"expected HTTP {expected}, got {response.status_code}: {response.text}")
    return response.json() if response.content else None


def case_health_and_summary() -> dict[str, Any]:
    with TestClient(create_app(settings=settings(), llm=ScriptedLLM([]))) as client:
        health = expect_status(client.get("/health"), 200)
        summary = expect_status(client.get("/admin/summary"), 200)

    assert health == {"status": "ok"}
    assert summary["ainas"] >= 1
    assert all(summary[key] == 0 for key in ("conversations", "tools", "skills", "installations", "traces", "memories"))
    return {"health": health["status"], "builtin_ainas": summary["ainas"]}


def case_conversation_lifecycle() -> dict[str, Any]:
    with TestClient(create_app(settings=settings(), llm=ScriptedLLM([]))) as client:
        created = expect_status(client.post("/conversations", json={"title": "E2E Roadmap"}), 201)
        updated = expect_status(
            client.patch(f"/conversations/{created['id']}", json={"title": "Release Roadmap", "category": "work"}),
            200,
        )
        filtered = expect_status(client.get("/conversations", params={"category": "work"}), 200)
        expect_status(client.delete(f"/conversations/{created['id']}"), 204)
        expect_status(client.get(f"/conversations/{created['id']}"), 404)
        restored = expect_status(client.post(f"/conversations/{created['id']}/restore"), 200)

    assert updated["title"] == "Release Roadmap"
    assert updated["category"] == "work"
    assert [item["id"] for item in filtered] == [created["id"]]
    assert restored["status"] == "active"
    return {"conversation_id": created["id"], "restored_status": restored["status"]}


def case_memory_lifecycle() -> dict[str, Any]:
    with TestClient(create_app(settings=settings(), llm=ScriptedLLM([]))) as client:
        created = expect_status(
            client.post("/memories", json={"content": "E2E prefers concise Chinese replies", "category": "preference"}),
            201,
        )
        searched = expect_status(client.get("/memories", params={"q": "concise Chinese"}), 200)
        updated = expect_status(
            client.patch(f"/memories/{created['id']}", json={"content": "E2E prefers concise bilingual replies"}),
            200,
        )
        stats = expect_status(client.get("/memories/stats"), 200)
        expect_status(client.delete(f"/memories/{created['id']}"), 204)
        after_delete = expect_status(client.get("/memories"), 200)

    assert searched["total"] == 1
    assert updated["content"].endswith("bilingual replies")
    assert stats["preference"] == 1 and stats["total"] == 1
    assert after_delete["total"] == 0
    return {"memory_id": created["id"], "category_count": stats["preference"]}


def case_chat_context_stream_and_trace() -> dict[str, Any]:
    llm = ScriptedLLM([assistant("first answer"), assistant("second answer"), assistant("streamed answer")])
    with TestClient(create_app(settings=settings(), llm=llm)) as client:
        first = expect_status(client.post("/chat", json={"message": "first question"}), 200)
        second = expect_status(
            client.post("/chat", json={"message": "follow up", "conversation_id": first["conversation_id"]}),
            200,
        )
        trace = expect_status(client.get(f"/traces/{second['trace_id']}"), 200)
        with client.stream("POST", "/chat/stream", json={"message": "stream this"}) as response:
            stream_body = "".join(response.iter_text())
            assert response.status_code == 200

    assert first["content"] == "first answer"
    assert second["content"] == "second answer"
    assert any(message.get("content") == "first answer" for message in llm.calls[1]["messages"])
    assert trace["status"] == "completed"
    assert "event: message.delta" in stream_body
    assert "streamed answer" in stream_body
    assert "event: message.completed" in stream_body
    return {"conversation_id": first["conversation_id"], "trace_events": len(trace["events"])}


def case_remote_tool_and_trace() -> dict[str, Any]:
    captured: list[dict[str, Any]] = []

    async def remote(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"result": 42})

    llm = ScriptedLLM([call_first_tool(arguments='{"a": 17, "b": 25}'), assistant("The result is 42.")])
    capability_client = httpx.AsyncClient(transport=httpx.MockTransport(remote))
    with TestClient(
        create_app(settings=settings(), llm=llm, capability_http_client=capability_client)
    ) as client:
        expect_status(
            client.post(
                "/tools",
                json={
                    "tool_id": "e2e.add",
                    "name": "E2E addition",
                    "description": "Add two integers for an end-to-end test.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                        "required": ["a", "b"],
                        "additionalProperties": False,
                    },
                    "output_schema": {
                        "type": "object",
                        "properties": {"result": {"type": "integer"}},
                        "required": ["result"],
                    },
                    "endpoint": "https://tool.invalid/add",
                },
            ),
            201,
        )
        chat = expect_status(
            client.post("/chat", json={"message": "What is 17 + 25?", "capability": "tool:e2e.add"}),
            200,
        )
        trace = expect_status(client.get(f"/traces/{chat['trace_id']}"), 200)

    assert chat["content"] == "The result is 42."
    assert captured[0]["arguments"] == {"a": 17, "b": 25}
    assert any(event["kind"] == "tool.completed" for event in trace["events"])
    return {"result": 42, "trace_id": chat["trace_id"]}


def case_high_risk_approval() -> dict[str, Any]:
    calls = 0

    async def remote(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"sent": True})

    llm = ScriptedLLM(
        [call_first_tool(arguments='{"recipient": "qa@example.com"}'), assistant("Sent after approval.")]
    )
    capability_client = httpx.AsyncClient(transport=httpx.MockTransport(remote))
    with TestClient(
        create_app(settings=settings(), llm=llm, capability_http_client=capability_client)
    ) as client:
        expect_status(
            client.post(
                "/tools",
                json={
                    "tool_id": "e2e.send",
                    "name": "E2E send",
                    "description": "Send a test message.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"recipient": {"type": "string"}},
                        "required": ["recipient"],
                    },
                    "endpoint": "https://tool.invalid/send",
                    "side_effect_level": "high",
                },
            ),
            201,
        )
        pending = expect_status(client.post("/chat", json={"message": "Send the message"}), 200)
        assert calls == 0
        approval_id = pending["approval"]["id"]
        confirmed = expect_status(client.post(f"/approvals/{approval_id}/confirm", json={}), 200)

    assert pending["status"] == "approval_required"
    assert confirmed["status"] == "completed"
    assert confirmed["content"] == "Sent after approval."
    assert calls == 1
    return {"approval_id": approval_id, "remote_calls_before_confirmation": 0, "remote_calls_after": calls}


CASES: list[Case] = [
    ("BE-E2E-001", "健康检查与管理摘要", case_health_and_summary),
    ("BE-E2E-002", "会话创建、分类、删除与恢复", case_conversation_lifecycle),
    ("BE-E2E-003", "长期记忆增删改查与统计", case_memory_lifecycle),
    ("BE-E2E-004", "多轮对话、SSE 与 Trace", case_chat_context_stream_and_trace),
    ("BE-E2E-005", "远程 Tool 调用与 Trace", case_remote_tool_and_trace),
    ("BE-E2E-006", "高风险 Tool 授权门禁", case_high_risk_approval),
]


def run_cases() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for case_id, title, case in CASES:
        started = time.perf_counter()
        try:
            details = case()
            status = "PASS"
            error = None
        except Exception as exc:  # noqa: BLE001 - runner must record every case failure
            details = {}
            status = "FAIL"
            error = f"{type(exc).__name__}: {exc}"
            traceback.print_exc()
        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        result = {
            "id": case_id,
            "title": title,
            "status": status,
            "duration_ms": duration_ms,
            "details": details,
            "error": error,
        }
        results.append(result)
        print(f"[{status}] {case_id} {title} ({duration_ms:.1f} ms)")
    return results


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Run deterministic backend end-to-end API cases.")
    parser.add_argument("--json-output", type=Path, help="Optional path for machine-readable results.")
    args = parser.parse_args()

    results = run_cases()
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    passed = sum(result["status"] == "PASS" for result in results)
    print(f"Backend E2E: {passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
