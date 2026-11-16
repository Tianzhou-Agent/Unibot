"""Modules that take a LeaseStore must import in a fresh interpreter, whatever was imported before."""

from __future__ import annotations

import subprocess
import sys

import pytest

MODULES = [
    "tianzhou_agent_platform.core.leases",
    "tianzhou_agent_platform.aina.scheduler",
    "tianzhou_agent_platform.sandbox.service",
    "tianzhou_agent_platform.aina.document.task_service",
    "tianzhou_agent_platform.main",
]


@pytest.mark.parametrize("module", MODULES)
def test_module_imports_in_a_fresh_interpreter(module: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr[-1500:]
