"""The kinds of file a user's file tree holds, by extension.

Markdown documents have sections (outline, section edits, edit tasks). Other text files (plain text, data and code)
are edited as a whole. PDF, DOCX and XLSX files are stored as uploaded and read through the text extracted from them,
kept hidden beside the file; they cannot be edited.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Literal

FileKind = Literal["markdown", "text", "binary"]

MARKDOWN_EXTENSIONS = frozenset({".md"})
# Text files and the language their content is written in (for fencing and syntax highlighting); "" is plain text.
TEXT_LANGUAGES = {
    ".markdown": "",
    ".txt": "",
    ".text": "",
    ".log": "",
    ".rst": "",
    ".csv": "csv",
    ".tsv": "tsv",
    ".json": "json",
    ".jsonl": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".xml": "xml",
    ".html": "html",
    ".htm": "html",
    ".toml": "toml",
    ".ini": "ini",
    ".cfg": "ini",
    ".conf": "",
    ".sql": "sql",
    ".py": "python",
    ".ipynb": "json",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".jsx": "jsx",
    ".vue": "vue",
    ".svelte": "svelte",
    ".css": "css",
    ".scss": "scss",
    ".java": "java",
    ".kt": "kotlin",
    ".scala": "scala",
    ".go": "go",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".dart": "dart",
    ".lua": "lua",
    ".r": "r",
    ".sh": "bash",
    ".bash": "bash",
    ".ps1": "powershell",
    ".bat": "bat",
    ".diff": "diff",
    ".patch": "diff",
}
BINARY_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
TEXT_TYPES = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".json": "application/json",
    ".html": "text/html",
    ".htm": "text/html",
    ".xml": "application/xml",
}
READABLE_EXTENSIONS = frozenset({*MARKDOWN_EXTENSIONS, *TEXT_LANGUAGES, *BINARY_TYPES})


def extension(name: str) -> str:
    return PurePosixPath(name).suffix.lower()


def file_kind(name: str) -> FileKind | None:
    """What a file of this name is, or None for a type the file tree does not hold."""
    suffix = extension(name)
    if suffix in MARKDOWN_EXTENSIONS:
        return "markdown"
    if suffix in TEXT_LANGUAGES:
        return "text"
    if suffix in BINARY_TYPES:
        return "binary"
    return None


def content_type(name: str) -> str:
    suffix = extension(name)
    return BINARY_TYPES.get(suffix) or TEXT_TYPES.get(suffix) or "text/plain"
