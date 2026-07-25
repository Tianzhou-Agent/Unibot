from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from fastapi.testclient import TestClient


def load_sandboxd(monkeypatch, tmp_path, *, output_limit: int = 1_000_000):
    monkeypatch.setenv("SANDBOX_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("SANDBOX_OUTPUT_LIMIT_BYTES", str(output_limit))
    source = Path(__file__).resolve().parents[2] / "sandbox" / "sandboxd" / "app.py"
    spec = importlib.util.spec_from_file_location("test_sandboxd_app", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_sandboxd_executes_in_persistent_workspace(monkeypatch, tmp_path) -> None:
    module = load_sandboxd(monkeypatch, tmp_path)
    with TestClient(module.app) as client:
        first = client.post(
            "/exec",
            json={
                "language": "python",
                "script": "from pathlib import Path; Path('value').write_text('persisted'); print('created')",
            },
        )
        second = client.post(
            "/exec",
            json={
                "language": "python",
                "script": "from pathlib import Path; print(Path('value').read_text())",
            },
        )
    assert first.status_code == 200
    assert first.json()["stdout"].strip() == "created"
    assert second.json()["stdout"].strip() == "persisted"


def test_sandboxd_rejects_workspace_escape(monkeypatch, tmp_path) -> None:
    module = load_sandboxd(monkeypatch, tmp_path)
    with TestClient(module.app) as client:
        response = client.post(
            "/exec",
            json={
                "language": "python",
                "script": "print('unsafe')",
                "working_directory": "../outside",
            },
        )
    assert response.status_code == 422


def test_sandboxd_terminates_timed_out_process(monkeypatch, tmp_path) -> None:
    module = load_sandboxd(monkeypatch, tmp_path)
    with TestClient(module.app) as client:
        response = client.post(
            "/exec",
            json={
                "language": "python",
                "script": "import time; time.sleep(5)",
                "timeout_seconds": 1,
            },
        )
    assert response.status_code == 200
    assert response.json()["status"] == "timed_out"
    assert response.json()["exit_code"] is None


def test_sandboxd_drains_but_bounds_large_output(monkeypatch, tmp_path) -> None:
    module = load_sandboxd(monkeypatch, tmp_path, output_limit=128)
    with TestClient(module.app) as client:
        response = client.post(
            "/exec",
            json={"language": "python", "script": "print('x' * 4096, end='')"},
        )
    assert response.status_code == 200
    assert response.json()["truncated"] is True
    assert len(response.json()["stdout"].encode()) == 128
