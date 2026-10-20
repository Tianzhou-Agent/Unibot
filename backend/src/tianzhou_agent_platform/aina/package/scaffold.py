"""``aina init``: a new AINA Protocol 2.0 project (design §12), from the template folder next to this module.

The project holds ``package/`` (what Superbot installs) and ``server/`` (the MCP server the developer runs). The
template is a small Notes app that uses every part of the protocol: tools with each effect, a skill, a canvas with a
canvas tool, a result widget and Host API storage.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field

from tianzhou_agent_platform.aina.package.validation import validate_package_files
from tianzhou_agent_platform.aina.project import build_project_archive
from tianzhou_agent_platform.core.base import StrictModel
from tianzhou_agent_platform.core.errors import PlatformError

TEMPLATE = Path(__file__).with_name("template")


class PackageScaffoldRequest(StrictModel):
    aina_id: str = Field(max_length=160, pattern=r"^[a-z0-9]+(\.[a-z0-9-]+)+$")
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=2000)
    version: str = Field(default="0.1.0", pattern=r"^\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?$")
    publisher_id: str = Field(default="local", min_length=1, max_length=160)
    publisher_name: str = Field(default="Local developer", min_length=1, max_length=160)
    mcp_url: str = Field(default="http://127.0.0.1:8770/mcp", max_length=2048)


def scaffold_package(request: PackageScaffoldRequest) -> dict[str, bytes]:
    """The new project's files by relative path; its ``package/`` is checked with the host's own validator."""
    port = urlsplit(request.mcp_url).port or 8770
    text = {
        "__NAME__": request.name,
        "__MCP_URL__": request.mcp_url,
        "__SERVER_NAME__": json.dumps(request.aina_id),
        "__SERVER_VERSION__": json.dumps(request.version),
        "__PORT__": str(port),
    }
    files: dict[str, bytes] = {}
    for path in sorted(TEMPLATE.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        name = path.relative_to(TEMPLATE).as_posix()
        if name == "package/aina.json":
            files[name] = _manifest(request, path)
        elif name in {"README.md", "server/main.py"}:
            content = path.read_text(encoding="utf-8")
            for placeholder, value in text.items():
                content = content.replace(placeholder, value)
            files[name] = content.encode()
        else:
            files[name] = path.read_bytes()
    package = {name.removeprefix("package/"): data for name, data in files.items() if name.startswith("package/")}
    report = validate_package_files(package)
    if report.errors:
        raise PlatformError("INVALID_REQUEST", "; ".join(report.errors), status_code=422, source="aina_package")
    return files


def scaffold_archive(request: PackageScaffoldRequest) -> bytes:
    return build_project_archive(scaffold_package(request))


def _manifest(request: PackageScaffoldRequest, path: Path) -> bytes:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["aina"].update(
        id=request.aina_id,
        version=request.version,
        name=request.name,
        description=request.description,
        publisher={"id": request.publisher_id, "name": request.publisher_name},
    )
    manifest["tools"]["mcp"]["url"] = request.mcp_url
    manifest["canvas"]["title"] = request.name
    return (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
