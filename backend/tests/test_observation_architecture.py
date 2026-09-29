import ast
from pathlib import Path


def test_agent_runtime_has_no_observability_dependency() -> None:
    runtime = Path(__file__).parents[1] / "src" / "tianzhou_agent_platform" / "core" / "agent_runtime"

    offenders: list[str] = []
    for path in runtime.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and "observ" in (node.module or ""):
                offenders.append(f"{path.name}: {node.module}")
            elif isinstance(node, ast.Import):
                offenders.extend(f"{path.name}: {alias.name}" for alias in node.names if "observ" in alias.name)
            elif isinstance(node, (ast.Name, ast.Attribute)):
                name = node.id if isinstance(node, ast.Name) else node.attr
                if name in {"record_event", "emit", "ObservedLLMClient", "ObservedCapabilityGateway"}:
                    offenders.append(f"{path.name}: {name}")

    assert not offenders, offenders
