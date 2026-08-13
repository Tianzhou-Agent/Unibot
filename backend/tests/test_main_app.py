from fastapi.testclient import TestClient

import pytest

from tianzhou_agent_platform.main import _resolve_obs_trace_status, create_app


def test_health() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    # OBS pipeline status is discoverable (design 15); disabled without storage
    assert body["obs"] == {"enabled": False}


@pytest.mark.asyncio
async def test_obs_resolver_canonicalizes_business_trace_id() -> None:
    requested: list[str] = []

    class FakeStore:
        async def get_trace(self, trace_id: str) -> dict[str, str] | None:
            requested.append(trace_id)
            return {"status": "approval_required"}

    legacy_trace_id = "trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    status = await _resolve_obs_trace_status(FakeStore(), legacy_trace_id)  # type: ignore[arg-type]
    assert status == "approval_required"
    assert requested == ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]
