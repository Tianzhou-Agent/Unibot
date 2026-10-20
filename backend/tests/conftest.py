from __future__ import annotations

import os
from pathlib import Path

import pytest
from deepeval.config.settings import dotenv_search_paths, read_dotenv_file

from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.store.settings import StorageSettings

_EVALS_ROOT = Path(__file__).parent / "evals"


@pytest.fixture(autouse=True)
def ignore_local_env_files(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a developer's backend/.env out of tests; only the live evals read it for their model settings."""
    if request.path.is_relative_to(_EVALS_ROOT):
        return
    for settings_class in (AgentSettings, StorageSettings):
        monkeypatch.setitem(settings_class.model_config, "env_file", None)
    # DeepEval's pytest plugin copies the working directory's dotenv files into os.environ when pytest starts.
    for path in dotenv_search_paths():
        for key, value in read_dotenv_file(path).items():
            if os.environ.get(key) == value:
                monkeypatch.delenv(key)
