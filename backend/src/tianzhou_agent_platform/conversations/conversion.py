"""What an uploaded file becomes: the Markdown document the model reads.

Only files Unibot can read are accepted: UTF-8 text (Markdown, plain text, data and code files), PDF with a text
layer, DOCX and XLSX. Everything else is refused, so the model is never handed a file it cannot see. The type is
decided from the file's name and bytes, never from what the browser said.
"""

from __future__ import annotations

import codecs
import csv
import io
import re
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import PurePosixPath

from tianzhou_agent_platform.aina.document.service import MAX_DOCUMENT_BYTES
from tianzhou_agent_platform.core.errors import PlatformError

MAX_PDF_PAGES = 1000
# What a DOCX or XLSX may unpack to: their XML is parsed in memory.
MAX_UNPACKED_BYTES = 50 * 1024 * 1024

PDF_TYPE = "application/pdf"
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Text kept as it is: the document is the text itself.
_PROSE = {".md", ".markdown", ".txt", ".text", ".log", ".rst"}
# Text shown in a fenced block of this language.
_FENCED = {
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
_TEXT_TYPES = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".json": "application/json",
    ".html": "text/html",
    ".htm": "text/html",
    ".xml": "application/xml",
}
ACCEPTED_EXTENSIONS = frozenset({*_PROSE, *_FENCED, ".pdf", ".docx", ".xlsx"})


@dataclass(frozen=True)
class Converted:
    content_type: str
    markdown: str
    is_document: bool  # see is_markdown


def convert(name: str, payload: bytes) -> Converted:
    """The document ``payload`` becomes; raises when Unibot cannot read the file."""
    extension = PurePosixPath(name).suffix.lower()
    if extension in _PROSE or extension in _FENCED:
        text = _text(payload)
        markdown = text if extension in _PROSE else _fenced(text, _FENCED[extension])
        converted = Converted(_TEXT_TYPES.get(extension, "text/plain"), markdown, is_document=is_markdown(name))
    elif extension == ".pdf":
        converted = Converted(PDF_TYPE, _pdf(payload), is_document=False)
    elif extension == ".docx":
        converted = Converted(DOCX_TYPE, _docx(payload), is_document=False)
    elif extension == ".xlsx":
        converted = Converted(XLSX_TYPE, _xlsx(payload), is_document=False)
    else:
        raise unsupported(name)
    if len(converted.markdown.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise PlatformError(
            "QUOTA_EXCEEDED",
            f"The text of {name!r} is larger than the {MAX_DOCUMENT_BYTES // (1024 * 1024)} MiB a document may hold",
            status_code=413,
            source="attachments",
        )
    return converted


def is_markdown(name: str) -> bool:
    """A Markdown upload is its own document; any other file is kept beside the document it became."""
    return PurePosixPath(name).suffix.lower() == ".md"


def unsupported(name: str) -> PlatformError:
    return PlatformError(
        "UNSUPPORTED_FILE_TYPE",
        f"{name!r} is not a file Unibot can read: attach text, Markdown, code, PDF, DOCX or XLSX files",
        status_code=415,
        source="attachments",
    )


def _unreadable(reason: str) -> PlatformError:
    return PlatformError("UNREADABLE_FILE", f"The file {reason}", status_code=422, source="attachments")


def _text(payload: bytes) -> str:
    try:
        if payload.startswith(codecs.BOM_UTF8):
            text = payload[len(codecs.BOM_UTF8) :].decode("utf-8")
        elif payload.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
            text = payload.decode("utf-16")
        else:
            text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _unreadable("is not UTF-8 text") from exc
    if "\x00" in text:
        raise _unreadable("is not text: it contains NUL characters")
    return text


def _fenced(text: str, language: str) -> str:
    longest = max((len(run) for run in re.findall(r"`{3,}", text)), default=2)
    fence = "`" * max(3, longest + 1)
    return f"{fence}{language}\n{text.rstrip(chr(10))}\n{fence}\n"


@contextmanager
def _parsing(kind: str) -> Iterator[None]:
    """A parser's own errors on a malformed file, as the file being unreadable."""
    try:
        yield
    except PlatformError:
        raise
    except Exception as exc:  # noqa: BLE001 - third-party parsers raise many types on untrusted input
        raise _unreadable(f"is not a readable {kind}") from exc


def _pdf(payload: bytes) -> str:
    from pypdf import PdfReader

    if not payload.startswith(b"%PDF-"):
        raise _unreadable("is not a PDF")
    with _parsing("PDF"):
        reader = PdfReader(io.BytesIO(payload))
        if reader.is_encrypted and not reader.decrypt(""):
            raise _unreadable("is password-protected")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise _unreadable(f"has more than {MAX_PDF_PAGES} pages")
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
    if not any(pages):
        raise _unreadable("has no text layer; a scanned PDF cannot be read")
    return "\n\n".join(f"## Page {number}\n\n{text}".rstrip() for number, text in enumerate(pages, start=1)) + "\n"


def _office_archive(payload: bytes, required_part: str, kind: str) -> None:
    """Refuse a file that is not the Office archive it claims to be, or that unpacks to too much."""
    if not payload.startswith(b"PK\x03\x04"):
        raise _unreadable(f"is not a {kind} file")
    with _parsing(kind), zipfile.ZipFile(io.BytesIO(payload)) as archive:
        if required_part not in archive.namelist():
            raise _unreadable(f"is not a {kind} file")
        if sum(item.file_size for item in archive.infolist()) > MAX_UNPACKED_BYTES:
            raise _unreadable(f"unpacks to more than {MAX_UNPACKED_BYTES // (1024 * 1024)} MiB")


def _docx(payload: bytes) -> str:
    import docx
    from docx.table import Table

    _office_archive(payload, "word/document.xml", "DOCX")
    blocks: list[str] = []
    with _parsing("DOCX"):
        for item in docx.Document(io.BytesIO(payload)).iter_inner_content():
            if isinstance(item, Table):
                table = _markdown_table([[cell.text for cell in row.cells] for row in item.rows])
                if table:
                    blocks.append(table)
                continue
            text = item.text.strip()
            if text:
                level = _heading_level(item.style.name if item.style is not None else "")
                blocks.append(f"{'#' * level} {text}" if level else text)
    if not blocks:
        raise _unreadable("contains no text")
    return "\n\n".join(blocks) + "\n"


def _heading_level(style: str) -> int:
    if style == "Title":
        return 1
    matched = re.fullmatch(r"Heading ([1-9])", style)
    return min(int(matched.group(1)), 6) if matched else 0


def _markdown_table(rows: list[list[str]]) -> str:
    cells = [[" ".join(cell.split()).replace("|", "\\|") for cell in row] for row in rows]
    cells = [row for row in cells if any(row)]
    if not cells:
        return ""
    width = max(len(row) for row in cells)
    padded = [row + [""] * (width - len(row)) for row in cells]
    lines = ["| " + " | ".join(row) + " |" for row in padded]
    lines.insert(1, "| " + " | ".join(["---"] * width) + " |")
    return "\n".join(lines)


def _xlsx(payload: bytes) -> str:
    import openpyxl

    _office_archive(payload, "xl/workbook.xml", "XLSX")
    sections: list[str] = []
    size = 0
    with _parsing("XLSX"):
        workbook = openpyxl.load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
        try:
            for sheet in workbook.worksheets:
                buffer = io.StringIO()
                writer = csv.writer(buffer, lineterminator="\n")
                blank_rows = 0  # written only when a row with values follows, so trailing blank rows are dropped
                for row in sheet.iter_rows(values_only=True):
                    values = ["" if value is None else str(value) for value in row]
                    while values and not values[-1]:
                        values.pop()
                    if not values:
                        blank_rows += 1
                        continue
                    buffer.write("\n" * blank_rows)
                    blank_rows = 0
                    writer.writerow(values)
                    if size + buffer.tell() > MAX_DOCUMENT_BYTES:
                        break  # convert() refuses the result as too large
                text = buffer.getvalue()
                size += len(text)
                sections.append(f"## Sheet: {sheet.title}\n\n" + (_fenced(text, "csv") if text else "(empty)\n"))
                if size > MAX_DOCUMENT_BYTES:
                    break
        finally:
            workbook.close()
    if size == 0:
        raise _unreadable("contains no values")
    return "\n".join(sections)
