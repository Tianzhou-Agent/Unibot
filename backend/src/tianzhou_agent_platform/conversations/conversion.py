"""What an uploaded file is to Superbot: its text, which the model reads.

Only files Superbot can read are accepted: UTF-8 text (Markdown, plain text, data and code files), PDF with a text
layer, DOCX, XLSX and PPTX. Everything else is refused, so the model is never handed a file it cannot see. The type is
decided from the file's name and bytes, never from what the browser said.
"""

from __future__ import annotations

import codecs
import csv
import io
import posixpath
import re
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from tianzhou_agent_platform.aina.document.file_types import content_type, extension, file_kind
from tianzhou_agent_platform.aina.document.service import MAX_DOCUMENT_BYTES
from tianzhou_agent_platform.core.errors import PlatformError

MAX_PDF_PAGES = 1000
# What a DOCX, XLSX or PPTX may unpack to: their XML is parsed in memory.
MAX_UNPACKED_BYTES = 50 * 1024 * 1024


@dataclass(frozen=True)
class Converted:
    content_type: str
    # A text file's own text, or the Markdown extracted from a PDF, DOCX, XLSX or PPTX.
    text: str
    binary: bool  # PDF, DOCX, XLSX or PPTX: stored as uploaded, read through ``text``


def convert(name: str, payload: bytes) -> Converted:
    """The text of the uploaded file ``name``; raises when Superbot cannot read the file."""
    kind = file_kind(name)
    if kind is None:
        raise unsupported(name)
    if kind == "binary":
        extract = {".pdf": _pdf, ".docx": _docx, ".xlsx": _xlsx, ".pptx": _pptx}[extension(name)]
        converted = Converted(content_type(name), extract(payload), binary=True)
    else:
        converted = Converted(content_type(name), _text(payload), binary=False)
    if len(converted.text.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise PlatformError(
            "QUOTA_EXCEEDED",
            f"The text of {name!r} is larger than the {MAX_DOCUMENT_BYTES // (1024 * 1024)} MiB a document may hold",
            status_code=413,
            source="attachments",
        )
    return converted


def unsupported(name: str) -> PlatformError:
    return PlatformError(
        "UNSUPPORTED_FILE_TYPE",
        f"{name!r} is not a file Superbot can read: attach text, Markdown, code, PDF, DOCX, XLSX or PPTX files",
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


_PPTX_NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}
_PPTX_RELATIONSHIP_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


def _pptx(payload: bytes) -> str:
    from xml.etree import ElementTree

    _office_archive(payload, "ppt/presentation.xml", "PPTX")
    slides: list[str] = []
    with _parsing("PPTX"), zipfile.ZipFile(io.BytesIO(payload)) as archive:
        presentation = ElementTree.fromstring(archive.read("ppt/presentation.xml"))
        relations = ElementTree.fromstring(archive.read("ppt/_rels/presentation.xml.rels"))
        targets = {
            item.get("Id"): item.get("Target") or "" for item in relations.iterfind("rel:Relationship", _PPTX_NS)
        }
        # In the order the deck shows them, not the order of the slide files' names.
        for slide_id in presentation.iterfind("p:sldIdLst/p:sldId", _PPTX_NS):
            part = posixpath.normpath(posixpath.join("ppt", targets[slide_id.get(_PPTX_RELATIONSHIP_ID)])).lstrip("/")
            slide = ElementTree.fromstring(archive.read(part))
            lines = (
                "".join(run.text or "" for run in paragraph.iterfind(".//a:t", _PPTX_NS)).strip()
                for paragraph in slide.iterfind(".//a:p", _PPTX_NS)
            )
            slides.append("\n".join(line for line in lines if line))
    # A slide of pictures alone is still a slide, so a deck without text reads as its slide headings.
    if not slides:
        raise _unreadable("contains no slides")
    return "\n\n".join(f"## Slide {number}\n\n{text}".rstrip() for number, text in enumerate(slides, start=1)) + "\n"
