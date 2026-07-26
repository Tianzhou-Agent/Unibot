from __future__ import annotations

import hashlib
import io
import json
import stat
import zipfile
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Literal

import yaml  # type: ignore[import-untyped]
from pydantic import Field, ValidationError

from tianzhou_agent_platform.aina.protocol.models import AinaManifest
from tianzhou_agent_platform.core.base import StrictModel
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.core.schema import validate_schema

AINA_PROJECT_FORMAT_VERSION: Literal["1.0"] = "1.0"
AINA_PROJECT_MANIFEST_NAMES = ("aina.yaml", "aina.yml", "aina.json")
MAX_PROJECT_ARCHIVE_BYTES = 20 * 1024 * 1024
MAX_PROJECT_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
MAX_PROJECT_FILE_BYTES = 5 * 1024 * 1024
MAX_PROJECT_FILES = 500


class AinaProjectScaffoldRequest(StrictModel):
    aina_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9._-]+$")
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=2000)
    version: str = Field(default="0.1.0", pattern=r"^\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?$")
    publisher_id: str = Field(default="local", min_length=1, max_length=160)
    publisher_name: str = Field(default="Local developer", min_length=1, max_length=160)
    language: Literal["python", "node"] = "python"


class AinaProjectValidationReport(StrictModel):
    format_version: Literal["1.0"] = AINA_PROJECT_FORMAT_VERSION
    archive_sha256: str
    size_bytes: int
    uncompressed_size_bytes: int
    file_count: int
    files: list[str]
    manifest_path: str
    manifest: AinaManifest
    ready_for_registration: bool
    warnings: list[str] = Field(default_factory=list)


def scaffold_project_archive(request: AinaProjectScaffoldRequest) -> bytes:
    entrypoint = "src/main.py:invoke" if request.language == "python" else "src/index.mjs:invoke"
    dependency_file = "requirements.txt" if request.language == "python" else "package.json"
    manifest = {
        "protocol_version": "1.0",
        "aina": {
            "id": request.aina_id,
            "name": request.name,
            "version": request.version,
            "description": request.description,
            "publisher": {"id": request.publisher_id, "name": request.publisher_name},
        },
        "runtime": {
            "type": "managed",
            "language": request.language,
            "entrypoint": entrypoint,
            "dependency_file": dependency_file,
        },
        "capabilities": {"skills": [], "tools": [], "ui": [], "events": []},
        "permissions": [],
        "authentication": {"type": "none"},
    }
    files: dict[str, str] = {
        "aina.yaml": yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True),
        "README.md": _scaffold_readme(request),
    }
    if request.language == "python":
        files["src/main.py"] = _PYTHON_HANDLER
        files["requirements.txt"] = ""
    else:
        files["src/index.mjs"] = _NODE_HANDLER
        files["package.json"] = json.dumps(
            {"name": request.aina_id, "version": request.version, "private": True, "type": "module"},
            ensure_ascii=False,
            indent=2,
        ) + "\n"
    archive = build_project_archive(files)
    validate_project_archive(archive)
    return archive


def build_project_archive(files: Mapping[str, str | bytes]) -> bytes:
    if not files:
        raise _invalid_project("AINA project must contain at least one file")
    if len(files) > MAX_PROJECT_FILES:
        raise _invalid_project(f"AINA project cannot contain more than {MAX_PROJECT_FILES} files")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for raw_name, raw_content in sorted(files.items()):
            name = _validate_archive_path(raw_name)
            content = raw_content.encode("utf-8") if isinstance(raw_content, str) else raw_content
            if len(content) > MAX_PROJECT_FILE_BYTES:
                raise _invalid_project(f"AINA project file {name!r} is too large")
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)
    payload = buffer.getvalue()
    if len(payload) > MAX_PROJECT_ARCHIVE_BYTES:
        raise _invalid_project("AINA project archive is too large")
    return payload


def validate_project_archive(payload: bytes) -> AinaProjectValidationReport:
    if not payload:
        raise _invalid_project("AINA project archive is empty")
    if len(payload) > MAX_PROJECT_ARCHIVE_BYTES:
        raise _invalid_project("AINA project archive is too large")
    files: dict[str, bytes] = {}
    uncompressed_size = 0
    try:
        with zipfile.ZipFile(io.BytesIO(payload), mode="r") as archive:
            infos = [info for info in archive.infolist() if not info.is_dir()]
            if len(infos) > MAX_PROJECT_FILES:
                raise _invalid_project(f"AINA project cannot contain more than {MAX_PROJECT_FILES} files")
            for info in infos:
                name = _validate_archive_path(info.filename)
                if name in files:
                    raise _invalid_project(f"AINA project contains duplicate file {name!r}")
                if info.flag_bits & 0x1:
                    raise _invalid_project(f"Encrypted AINA project file {name!r} is not supported")
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise _invalid_project(f"AINA project cannot contain symbolic link {name!r}")
                if info.file_size > MAX_PROJECT_FILE_BYTES:
                    raise _invalid_project(f"AINA project file {name!r} is too large")
                uncompressed_size += info.file_size
                if uncompressed_size > MAX_PROJECT_UNCOMPRESSED_BYTES:
                    raise _invalid_project("AINA project expands beyond the allowed size")
                files[name] = archive.read(info)
    except zipfile.BadZipFile as exc:
        raise _invalid_project("AINA project must be a valid ZIP archive") from exc

    manifest_paths = [name for name in AINA_PROJECT_MANIFEST_NAMES if name in files]
    if len(manifest_paths) != 1:
        raise _invalid_project("AINA project must contain exactly one root aina.yaml, aina.yml, or aina.json")
    manifest_path = manifest_paths[0]
    manifest = _load_manifest(files[manifest_path], manifest_path)
    if manifest.runtime.type == "builtin":
        raise _invalid_project("Built-in AINA runtimes cannot be packaged by users")
    _validate_capabilities(manifest)

    warnings: list[str] = []
    if manifest.runtime.type == "managed":
        entrypoint_path = manifest.runtime.entrypoint.partition(":")[0]
        if entrypoint_path not in files:
            raise _invalid_project(f"Managed runtime entrypoint {entrypoint_path!r} is missing from the package")
        dependency_file = manifest.runtime.dependency_file
        if dependency_file is not None and dependency_file not in files:
            raise _invalid_project(f"Managed runtime dependency file {dependency_file!r} is missing from the package")
        warnings.append("Managed AINA projects require deployment before they can be installed or invoked.")
    elif not any(name.startswith("src/") for name in files):
        warnings.append("Remote AINA package does not include source files.")

    return AinaProjectValidationReport(
        archive_sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
        uncompressed_size_bytes=uncompressed_size,
        file_count=len(files),
        files=sorted(files),
        manifest_path=manifest_path,
        manifest=manifest,
        ready_for_registration=manifest.runtime.type == "remote",
        warnings=warnings,
    )


def _load_manifest(content: bytes, path: str) -> AinaManifest:
    if len(content) > 1024 * 1024:
        raise _invalid_project("AINA manifest cannot exceed 1 MiB")
    try:
        raw = yaml.safe_load(content.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise _invalid_project(f"AINA manifest {path!r} is not valid UTF-8 YAML or JSON") from exc
    if not isinstance(raw, dict):
        raise _invalid_project("AINA manifest root must be an object")
    try:
        return AinaManifest.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors(include_url=False)[0]
        location = ".".join(str(item) for item in first.get("loc", ())) or "manifest"
        raise _invalid_project(f"AINA manifest {location}: {first['msg']}") from exc


def _validate_capabilities(manifest: AinaManifest) -> None:
    capability_ids: set[str] = set()
    for capability in [*manifest.capabilities.skills, *manifest.capabilities.tools]:
        if capability.id in capability_ids:
            raise _invalid_project(f"AINA capability id {capability.id!r} is duplicated")
        capability_ids.add(capability.id)
        try:
            validate_schema(capability.input_schema, label=f"capability {capability.id} input_schema")
        except PlatformError as exc:
            raise _invalid_project(exc.message) from exc
    ui_ids: set[str] = set()
    for ui_capability in manifest.capabilities.ui:
        if ui_capability.id in ui_ids:
            raise _invalid_project(f"AINA UI capability id {ui_capability.id!r} is duplicated")
        ui_ids.add(ui_capability.id)


def _validate_archive_path(raw_name: str) -> str:
    if not raw_name or "\\" in raw_name or "\x00" in raw_name:
        raise _invalid_project("AINA project file paths must use non-empty POSIX paths")
    path = PurePosixPath(raw_name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise _invalid_project(f"AINA project file {raw_name!r} escapes the project root")
    return path.as_posix()


def _invalid_project(message: str) -> PlatformError:
    return PlatformError("INVALID_REQUEST", message, status_code=422, source="aina_project")


def _scaffold_readme(request: AinaProjectScaffoldRequest) -> str:
    return (
        f"# {request.name}\n\n"
        f"AINA project `{request.aina_id}` generated for the Unibot managed runtime.\n\n"
        "- Declare skills, tools, UI, and permissions in `aina.yaml`.\n"
        "- Implement the configured `invoke` handler under `src/`.\n"
        "- The handler receives an AINA Protocol 1.0 request object and returns a Protocol 1.0 response object.\n"
    )


_PYTHON_HANDLER = '''from __future__ import annotations

from typing import Any


async def invoke(request: dict[str, Any]) -> dict[str, Any]:
    """Handle one request from Unibot through AINA Protocol 1.0."""
    user_input = request.get("input", {})
    return {
        "request_id": request["request_id"],
        "status": "completed",
        "outputs": [{"type": "text", "content": f"Received: {user_input}"}],
        "usage": {"input_tokens": 0, "output_tokens": 0},
        "trace_id": request["trace"]["trace_id"],
    }
'''

_NODE_HANDLER = '''export async function invoke(request) {
  const userInput = request.input ?? {};
  return {
    request_id: request.request_id,
    status: "completed",
    outputs: [{ type: "text", content: `Received: ${JSON.stringify(userInput)}` }],
    usage: { input_tokens: 0, output_tokens: 0 },
    trace_id: request.trace.trace_id,
  };
}
'''
