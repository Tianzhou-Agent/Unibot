"""Validation of AINA Protocol 2.0 packages (design §5.4); the host and the ``aina validate`` CLI share it.

Validation never stops at the first problem: every rule violation is reported so a developer can fix a package in
one pass. Archive safety failures (traversal, symlinks, zip bombs) still raise immediately because the files
cannot be trusted at all.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from functools import cache
from importlib import resources
from pathlib import PurePosixPath
from typing import Any

import yaml  # type: ignore[import-untyped]
from jsonschema import Draft202012Validator, FormatChecker  # type: ignore[import-untyped]
from jsonschema.exceptions import SchemaError, ValidationError  # type: ignore[import-untyped]
from pydantic import Field

from tianzhou_agent_platform.aina.package.manifest import AinaPackageManifest
from tianzhou_agent_platform.aina.package.models import PackagedSkill
from tianzhou_agent_platform.aina.project import read_project_archive
from tianzhou_agent_platform.core.base import StrictModel
from tianzhou_agent_platform.core.errors import PlatformError

MANIFEST_NAME = "aina.json"
SKILL_ENTRY_NAMES = ("SKILL.md", "skill.md")
MAX_SKILL_ENTRY_CHARS = 20_000
MAX_SKILL_FILE_CHARS = 50_000
# Implementation limit: skill text is stored with the registered AINA and read on every scoped turn.
MAX_SKILL_TEXT_CHARS = 1_000_000
MAX_TOOL_DESCRIPTION_CHARS = 1_000
_EXECUTABLE_SUFFIXES = {
    ".bat",
    ".cjs",
    ".cmd",
    ".exe",
    ".js",
    ".mjs",
    ".pl",
    ".ps1",
    ".py",
    ".rb",
    ".sh",
    ".ts",
}


class PackageValidationReport(StrictModel):
    manifest: AinaPackageManifest | None = None
    files: list[str] = Field(default_factory=list)
    skills: list[PackagedSkill] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    archive_sha256: str | None = None

    @property
    def valid(self) -> bool:
        return not self.errors and self.manifest is not None


@cache
def manifest_schema() -> dict[str, Any]:
    """The published ``aina.schema.json`` (also at ``spec/aina/2.0/aina.schema.json``)."""
    text = resources.files("tianzhou_agent_platform.aina.package").joinpath("aina.schema.json").read_text("utf-8")
    schema: dict[str, Any] = json.loads(text)
    return schema


def validate_package_archive(payload: bytes) -> PackageValidationReport:
    files, _size = read_project_archive(payload)
    report = validate_package_files(files)
    report.archive_sha256 = hashlib.sha256(payload).hexdigest()
    return report


def require_valid_package(report: PackageValidationReport) -> AinaPackageManifest:
    if report.errors or report.manifest is None:
        errors = report.errors or ["aina.json could not be validated"]
        raise PlatformError(
            "INVALID_REQUEST",
            "Invalid AINA package: " + "; ".join(errors),
            status_code=422,
            source="aina_package",
            debug={"errors": errors},
        )
    return report.manifest


def validate_package_files(files: Mapping[str, bytes]) -> PackageValidationReport:
    report = PackageValidationReport(files=sorted(files))
    errors = report.errors
    for legacy in ("aina.yaml", "aina.yml"):
        if legacy in files:
            errors.append(f"{legacy}: YAML manifests are not accepted for protocol 2.0; use aina.json only")
    if MANIFEST_NAME not in files:
        errors.append("aina.json: the package must contain aina.json at its root")
        return report
    try:
        raw = json.loads(files[MANIFEST_NAME].decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        errors.append(f"aina.json: not valid UTF-8 JSON ({exc})")
        return report
    schema_errors = sorted(
        Draft202012Validator(manifest_schema(), format_checker=FormatChecker()).iter_errors(raw),
        key=lambda error: [str(part) for part in error.absolute_path],
    )
    for error in schema_errors:
        if not error.absolute_path and error.validator == "anyOf":
            errors.append("aina.json: an app needs at least one of tools (an MCP server), skills or a canvas")
            continue
        # ``tools.mcp`` is one server or a list: report what is wrong with the form that was used.
        error = _specific(error)
        location = "/".join(str(part) for part in error.absolute_path) or "(root)"
        errors.append(f"aina.json {location}: {error.message}")
    if schema_errors:
        return report
    manifest = AinaPackageManifest.model_validate(raw)
    report.manifest = manifest
    _check_references(manifest, files, errors)
    _check_tools(manifest, errors)
    _check_config(manifest, errors)
    report.skills = _load_skills(manifest, files, errors)
    report.warnings.extend(_model_text_warnings(manifest, report.skills))
    return report


def _specific(error: ValidationError) -> ValidationError:
    """The sub-error of a ``oneOf`` for the branch that matches the instance's type."""
    while error.validator == "oneOf" and error.context:
        same_type = [item for item in error.context if item.validator != "type"]
        if not same_type:
            break
        error = min(same_type, key=lambda item: len(item.relative_schema_path))
    return error


def check_mcp_tool_description(name: str, description: str) -> tuple[list[str], list[str]]:
    """(errors, warnings) for one MCP tool description from ``tools/list``."""
    errors: list[str] = []
    warnings: list[str] = []
    if len(description) > MAX_TOOL_DESCRIPTION_CHARS:
        errors.append(f"MCP tool {name!r}: description exceeds {MAX_TOOL_DESCRIPTION_CHARS} characters")
    if not description.isascii():
        warnings.append(f"MCP tool {name!r}: model-facing description should be English (non-ASCII text found)")
    return errors, warnings


def _check_references(manifest: AinaPackageManifest, files: Mapping[str, bytes], errors: list[str]) -> None:
    if manifest.aina.icon is not None and manifest.aina.icon not in files:
        errors.append(f"aina.icon: file {manifest.aina.icon!r} is missing from the package")
    if manifest.canvas is not None and manifest.canvas.entry is not None and manifest.canvas.entry not in files:
        errors.append(f"canvas.entry: file {manifest.canvas.entry!r} is missing from the package")
    widgets: dict[str, str] = {}
    for index, widget in enumerate(manifest.widgets):
        if widget.id in widgets:
            errors.append(f"widgets/{index}/id: widget id {widget.id!r} is duplicated")
        widgets[widget.id] = widget.purpose
        if widget.entry not in files:
            errors.append(f"widgets/{index}/entry: file {widget.entry!r} is missing from the package")
    for name, tool in manifest.exposed().items():
        for field, purpose in (("confirm_widget", "confirmation"), ("result_widget", "result")):
            widget_id = getattr(tool, field)
            if widget_id is None:
                continue
            if widget_id not in widgets:
                errors.append(f"tools.expose.{name}.{field}: widget {widget_id!r} is not declared in widgets")
            elif widgets[widget_id] != purpose:
                errors.append(f"tools.expose.{name}.{field}: widget {widget_id!r} must have purpose {purpose!r}")


def _check_tools(manifest: AinaPackageManifest, errors: list[str]) -> None:
    canvas_tools = manifest.canvas.tools if manifest.canvas is not None else []
    _check_servers(manifest, errors)
    seen: set[str] = set(manifest.exposed())
    for index, tool in enumerate(canvas_tools):
        if tool.name in seen:
            errors.append(f"canvas.tools/{index}/name: tool name {tool.name!r} is not unique across the package")
        seen.add(tool.name)
        for field, schema in (("inputSchema", tool.input_schema), ("outputSchema", tool.output_schema)):
            if schema is None:
                continue
            try:
                Draft202012Validator.check_schema(schema)
            except SchemaError as exc:
                errors.append(f"canvas.tools/{index}/{field}: not a valid JSON Schema ({exc.message})")
    skill_ids: set[str] = set()
    for index, skill in enumerate(manifest.skills):
        if skill.id in skill_ids:
            errors.append(f"skills/{index}/id: skill id {skill.id!r} is duplicated")
        skill_ids.add(skill.id)
        for name in skill.tools:
            if name not in seen:
                errors.append(f"skills/{index}/tools: {name!r} is neither an exposed tool nor a canvas tool")
    scopes = [item.scope for item in manifest.permissions]
    for scope in sorted({scope for scope in scopes if scopes.count(scope) > 1}):
        errors.append(f"permissions: scope {scope!r} is declared more than once")


def _check_servers(manifest: AinaPackageManifest, errors: list[str]) -> None:
    """Several MCP servers: unique ids, every exposed tool names its server, and at most one uses OAuth."""
    servers = manifest.servers()
    ids = [server.id for server in servers]
    for server_id in sorted({server_id for server_id in ids if ids.count(server_id) > 1}):
        errors.append(f"tools.mcp: server id {server_id!r} is duplicated")
    for name, tool in manifest.exposed().items():
        if tool.server is None and len(servers) > 1:
            errors.append(f"tools.expose.{name}.server: name the server that provides the tool ({', '.join(ids)})")
        elif tool.server is not None and tool.server not in ids:
            errors.append(f"tools.expose.{name}.server: no MCP server has the id {tool.server!r}")
    if sum(server.auth.type == "oauth2" for server in servers) > 1:
        errors.append("tools.mcp: at most one MCP server of an app may use OAuth")


def _check_config(manifest: AinaPackageManifest, errors: list[str]) -> None:
    if manifest.config is not None:
        schema = manifest.config.json_schema
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            errors.append(f"config.schema: not a valid JSON Schema ({exc.message})")
        if schema.get("type") != "object":
            errors.append("config.schema: the configuration schema must have type 'object'")
    for server in manifest.servers():
        secret = server.auth.secret
        if secret is not None and secret not in manifest.secret_config_fields():
            errors.append(f'tools.mcp.auth.secret: {secret!r} must be a config.schema property with "x-secret": true')


def _load_skills(
    manifest: AinaPackageManifest,
    files: Mapping[str, bytes],
    errors: list[str],
) -> list[PackagedSkill]:
    skills: list[PackagedSkill] = []
    total = 0
    for index, declared in enumerate(manifest.skills):
        label = f"skills/{index} ({declared.id})"
        folder = declared.path.rstrip("/")
        prefix = f"{folder}/"
        members = {name[len(prefix) :]: content for name, content in files.items() if name.startswith(prefix)}
        if not members:
            errors.append(f"{label}: skill folder {folder!r} is missing from the package")
            continue
        entries = [name for name in SKILL_ENTRY_NAMES if name in members]
        if len(entries) != 1:
            errors.append(f"{label}: skill folder {folder!r} must contain exactly one of SKILL.md or skill.md")
            continue
        texts: dict[str, str] = {}
        for relative, content in sorted(members.items()):
            if PurePosixPath(relative).suffix.lower() in _EXECUTABLE_SUFFIXES:
                errors.append(f"{label}: {relative!r} looks executable; skill folders may contain only text")
                continue
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                errors.append(f"{label}: {relative!r} is not UTF-8 text; skill folders may contain only text")
                continue
            limit = MAX_SKILL_ENTRY_CHARS if relative == entries[0] else MAX_SKILL_FILE_CHARS
            if len(text) > limit:
                errors.append(f"{label}: {relative!r} exceeds {limit} characters")
                continue
            texts[relative] = text
            total += len(text)
        entry_text = texts.pop(entries[0], None)
        if entry_text is None:
            continue
        body = _strip_frontmatter(entry_text, declared.id, declared.name, declared.description, label, errors)
        if not body.strip():
            errors.append(f"{label}: {entries[0]} has no workflow text")
        skills.append(PackagedSkill(id=declared.id, entry=entries[0], body=body, files=texts))
    if total > MAX_SKILL_TEXT_CHARS:
        errors.append(f"skills: skill folders hold more than {MAX_SKILL_TEXT_CHARS} characters of text in total")
    return skills


def _strip_frontmatter(
    text: str,
    skill_id: str,
    name: str,
    description: str,
    label: str,
    errors: list[str],
) -> str:
    """The skill body without an Agent Skills frontmatter block, whose metadata must agree with aina.json."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return text
    closing = next((index for index in range(1, len(lines)) if lines[index].strip() == "---"), None)
    if closing is None:
        errors.append(f"{label}: frontmatter block is not closed with '---'")
        return text
    try:
        metadata = yaml.safe_load("".join(lines[1:closing])) or {}
    except yaml.YAMLError as exc:
        errors.append(f"{label}: frontmatter is not valid YAML ({exc})")
        return text
    if not isinstance(metadata, dict):
        errors.append(f"{label}: frontmatter must be a YAML mapping")
        return text
    # Agent Skills folders name the skill by its slug, so the frontmatter name may match the id or the name.
    if "name" in metadata and str(metadata["name"]).strip() not in {skill_id, name}:
        errors.append(f"{label}: frontmatter name {metadata['name']!r} does not match aina.json")
    if "description" in metadata and str(metadata["description"]).strip() != description.strip():
        errors.append(f"{label}: frontmatter description does not match aina.json")
    return "".join(lines[closing + 1 :]).lstrip("\n")


def _model_text_warnings(manifest: AinaPackageManifest, skills: list[PackagedSkill]) -> list[str]:
    texts: list[tuple[str, str]] = [("aina.description", manifest.aina.description)]
    texts += [(f"skills.{skill.id}.description", skill.description) for skill in manifest.skills]
    texts += [(f"skills.{skill.id} body", skill.body) for skill in skills]
    if manifest.canvas is not None:
        texts += [(f"canvas.tools.{tool.name}.description", tool.description) for tool in manifest.canvas.tools]
    return [
        f"{label}: model-facing text should be English (non-ASCII text found)"
        for label, text in texts
        if not text.isascii()
    ]
