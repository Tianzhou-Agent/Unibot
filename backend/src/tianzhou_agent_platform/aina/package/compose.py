"""Building an AINA 2.0 package from its parts, for the Add app page (instead of a finished ``.aina.zip``).

The page sends ``aina.json`` as it was filled in, optionally the package it started from (whose other files, such as
widgets, are kept), each skill as a ``SKILL.md`` or a folder ``.zip``, and the canvas as a static bundle ``.zip``.
The result is an ordinary package: it is validated and registered exactly like an uploaded one.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any

import yaml  # type: ignore[import-untyped]

from tianzhou_agent_platform.aina.project import read_project_archive
from tianzhou_agent_platform.core.errors import PlatformError

CANVAS_FOLDER = "canvas"
CANVAS_ENTRY = f"{CANVAS_FOLDER}/index.html"
SKILL_ENTRY = "SKILL.md"


def compose_package(
    manifest: dict[str, Any],
    *,
    base: bytes | None = None,
    skills: Mapping[str, tuple[str, bytes]] | None = None,
    canvas_bundle: bytes | None = None,
) -> dict[str, bytes]:
    """The package files. ``skills`` maps a skill id to its upload (file name, bytes)."""
    files = read_project_archive(base)[0] if base else {}
    for skill_id, (filename, payload) in (skills or {}).items():
        folder = f"skills/{skill_id}"
        files = {path: content for path, content in files.items() if not path.startswith(f"{folder}/")}
        if filename.lower().endswith(".zip"):
            files.update({f"{folder}/{path}": content for path, content in _bundle(payload, SKILL_ENTRY).items()})
        else:
            files[f"{folder}/{SKILL_ENTRY}"] = payload
    for entry in manifest.get("skills") or []:
        _complete_skill(entry, files)
    if canvas_bundle is not None:
        files = {path: content for path, content in files.items() if not path.startswith(f"{CANVAS_FOLDER}/")}
        files.update(
            {f"{CANVAS_FOLDER}/{path}": content for path, content in _bundle(canvas_bundle, "index.html").items()}
        )
        canvas = manifest.setdefault("canvas", {})
        canvas.pop("url", None)
        canvas["entry"] = CANVAS_ENTRY
    files["aina.json"] = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode()
    return files


def _bundle(payload: bytes, entry: str) -> dict[str, bytes]:
    """The files of an uploaded folder ``.zip``, without the single top folder a zipped folder usually has."""
    files = read_project_archive(payload)[0]
    if entry not in files:
        tops = {PurePosixPath(path).parts[0] for path in files}
        if len(tops) == 1:
            top = f"{tops.pop()}/"
            files = {path[len(top) :]: content for path, content in files.items() if path.startswith(top)}
    if entry not in files:
        raise PlatformError(
            "INVALID_REQUEST",
            f"The uploaded .zip must contain {entry} at its root or in its one top folder",
            status_code=422,
            source="aina_package",
        )
    return {path: content for path, content in files.items() if not path.startswith(("__MACOSX/", ".")) and path}


def _complete_skill(entry: dict[str, Any], files: Mapping[str, bytes]) -> None:
    """A skill entry names only its id: its folder, name and description come from its ``SKILL.md`` frontmatter."""
    skill_id = str(entry.get("id") or "")
    entry.setdefault("path", f"skills/{skill_id}")
    if entry.get("name") and entry.get("description"):
        return
    text = files.get(f"{entry['path'].rstrip('/')}/{SKILL_ENTRY}", b"").decode("utf-8", errors="replace")
    metadata = _frontmatter(text)
    entry.setdefault("name", str(metadata.get("name") or skill_id))
    if metadata.get("description"):
        entry.setdefault("description", str(metadata["description"]).strip())


def _frontmatter(text: str) -> dict[str, Any]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    closing = next((index for index in range(1, len(lines)) if lines[index].strip() == "---"), None)
    if closing is None:
        return {}
    try:
        metadata = yaml.safe_load("\n".join(lines[1:closing])) or {}
    except yaml.YAMLError:
        return {}
    return metadata if isinstance(metadata, dict) else {}
