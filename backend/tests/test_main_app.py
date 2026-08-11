from fastapi.testclient import TestClient

from tianzhou_agent_platform.main import create_app


def test_health() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    # OBS pipeline status is discoverable (design 15); disabled without storage
    assert body["obs"] == {"enabled": False}
