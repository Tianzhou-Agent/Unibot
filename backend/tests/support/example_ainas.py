"""Load optional example packages without adding them to the host's import path."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[3] / "examples"


def example_server(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / name / "server" / "main.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def example_files(name: str) -> dict[str, bytes]:
    folder = ROOT / name / "package"
    return {path.relative_to(folder).as_posix(): path.read_bytes() for path in folder.rglob("*") if path.is_file()}
