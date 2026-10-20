"""What an uploaded file becomes as a document, and how the document keeps the file it was made from."""

from __future__ import annotations

import asyncio
import io
import zipfile
from pathlib import Path

import docx
import openpyxl
import pytest

from tianzhou_agent_platform.aina.document.builtin import READ_DOCUMENT_TOOL_ID, invoke_document_tool
from tianzhou_agent_platform.aina.document.service import DocumentService
from tianzhou_agent_platform.conversations import conversion
from tianzhou_agent_platform.conversations.conversion import convert
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.store.nas.filesystem import NasStore

ACTOR = {"user_id": "anonymous", "tenant_id": "default"}


def pdf(*pages: str) -> bytes:
    """A PDF whose pages carry ``pages`` as text (an empty string is a page without a text layer)."""
    objects = ["<< /Type /Catalog /Pages 2 0 R >>", "", "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for text in pages:
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET" if text else ""
        objects.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        objects.append(
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> "
            f"/Contents {len(objects)} 0 R >>"
        )
        kids.append(f"{len(objects)} 0 R")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    return out + f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()


def word(*, heading: str, body: str, table: list[list[str]]) -> bytes:
    document = docx.Document()
    document.add_heading(heading, level=2)
    document.add_paragraph(body)
    grid = document.add_table(rows=len(table), cols=len(table[0]))
    for row, values in zip(grid.rows, table, strict=True):
        for cell, value in zip(row.cells, values, strict=True):
            cell.text = value
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def workbook(sheets: dict[str, list[list[object]]]) -> bytes:
    book = openpyxl.Workbook()
    book.remove(book.active)
    for title, rows in sheets.items():
        sheet = book.create_sheet(title)
        for row in rows:
            sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def refused(name: str, payload: bytes) -> PlatformError:
    with pytest.raises(PlatformError) as caught:
        convert(name, payload)
    return caught.value


def test_text_files_are_kept_as_text_and_data_and_code_are_fenced() -> None:
    notes = convert("notes.md", b"# Notes\n\nHello")
    log = convert("run.log", "started".encode("utf-16"))  # with a byte order mark
    table = convert("data.csv", b"a,b\n1,2\n")
    code = convert("tool.py", b'print("```")\n')

    assert (notes.markdown, notes.content_type, notes.is_document) == ("# Notes\n\nHello", "text/markdown", True)
    assert log.markdown == "started" and not log.is_document
    assert table.markdown == "```csv\na,b\n1,2\n```\n" and table.content_type == "text/csv"
    assert code.markdown == '````python\nprint("```")\n````\n'  # a longer fence than any in the code


def test_files_unibot_cannot_read_are_refused() -> None:
    image = refused("photo.png", b"\x89PNG\r\n")
    binary = refused("dump.txt", b"abc\x00def")
    latin1 = refused("old.txt", "café".encode("latin-1"))
    fake_pdf = refused("report.pdf", b"just text")
    fake_docx = refused("spec.docx", b"PK\x03\x04 not an archive")
    no_extension = refused("README", b"text")

    assert (image.status_code, image.code) == (415, "UNSUPPORTED_FILE_TYPE")
    assert no_extension.status_code == 415
    assert all(error.status_code == 422 for error in (binary, latin1, fake_pdf, fake_docx))
    assert "not UTF-8 text" in latin1.message


def test_a_pdf_becomes_one_section_per_page() -> None:
    converted = convert("report.pdf", pdf("Revenue grew", "", "Costs fell"))

    assert converted.content_type == "application/pdf" and not converted.is_document
    assert converted.markdown == "## Page 1\n\nRevenue grew\n\n## Page 2\n\n## Page 3\n\nCosts fell\n"


def test_a_scanned_pdf_without_a_text_layer_is_refused() -> None:
    error = refused("scan.pdf", pdf("", ""))

    assert error.status_code == 422 and "no text layer" in error.message


def test_a_docx_keeps_its_headings_and_tables() -> None:
    converted = convert(
        "spec.docx", word(heading="Scope", body="Only the API.", table=[["Name", "Owner"], ["API", "a|b"]])
    )

    assert converted.markdown == ("## Scope\n\nOnly the API.\n\n| Name | Owner |\n| --- | --- |\n| API | a\\|b |\n")


def test_an_xlsx_becomes_one_csv_section_per_sheet() -> None:
    converted = convert(
        "budget.xlsx",
        workbook({"Q1": [["Item", "Cost"], ["Rent", 1200], [None, None], ["Food", 300.5], [None]], "Empty": []}),
    )

    assert converted.markdown == (
        "## Sheet: Q1\n\n```csv\nItem,Cost\nRent,1200\n\nFood,300.5\n```\n\n## Sheet: Empty\n\n(empty)\n"
    )


def test_an_office_file_that_unpacks_to_too_much_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = word(heading="Big", body="x" * 5000, table=[["a"]])
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        unpacked = sum(item.file_size for item in archive.infolist())
    monkeypatch.setattr(conversion, "MAX_UNPACKED_BYTES", unpacked - 1)

    assert "unpacks to more than" in refused("big.docx", payload).message


def test_text_larger_than_a_document_may_hold_is_refused() -> None:
    error = refused("huge.txt", b"x" * (1024 * 1024 + 1))

    assert (error.status_code, error.code) == (413, "QUOTA_EXCEEDED")


def test_an_upload_is_stored_in_uploads_beside_its_original(tmp_path: Path) -> None:
    service = DocumentService(NasStore(tmp_path))

    async def scenario() -> tuple[list[str], list[str], bytes]:
        names = [
            await service.add_upload("report.pdf", "## Page 1\n", b"%PDF-1", **ACTOR),
            await service.add_upload("report.pdf", "## Page 1\n", b"%PDF-2", **ACTOR),
            await service.add_upload("notes.md", "# Notes", None, **ACTOR),
            await service.add_upload("a:b?<con>.txt", "text", b"text", **ACTOR),
            await service.add_upload("con.txt", "text", b"text", **ACTOR),
        ]
        listed = [item.name for item in await service.list_documents(**ACTOR)]
        return names, listed, await service.upload_original("Uploads/report (2).pdf.md", **ACTOR)

    names, listed, original = asyncio.run(scenario())

    assert names == [
        "Uploads/report.pdf.md",
        "Uploads/report (2).pdf.md",
        "Uploads/notes.md",
        "Uploads/a_b__con_.txt.md",
        "Uploads/con_.txt.md",
    ]
    assert listed == sorted(names, key=str.casefold)  # the originals are not documents
    assert original == b"%PDF-2"


def test_renaming_or_deleting_an_uploaded_document_carries_its_original(tmp_path: Path) -> None:
    service = DocumentService(NasStore(tmp_path))
    uploads = tmp_path / "documents" / "t-default" / "u-anonymous" / "Uploads"

    async def scenario() -> bytes:
        await service.add_upload("report.pdf", "## Page 1\n", b"%PDF-1", **ACTOR)
        await service.rename_document("Uploads/report.pdf.md", "Uploads/q3.pdf.md", **ACTOR)
        moved = await service.upload_original("Uploads/q3.pdf.md", **ACTOR)
        await service.delete_document("Uploads/q3.pdf.md", **ACTOR)
        return moved

    assert asyncio.run(scenario()) == b"%PDF-1"
    assert list(uploads.iterdir()) == []


def test_document_read_pages_through_lines(tmp_path: Path) -> None:
    service = DocumentService(NasStore(tmp_path))

    async def read(**arguments: object) -> dict[str, object]:
        result, _widgets = await invoke_document_tool(
            service, READ_DOCUMENT_TOOL_ID, {"name": "long.md", **arguments}, **ACTOR
        )
        return result["document"]

    async def scenario() -> list[dict[str, object]]:
        await service.create_document("long.md", "one\ntwo\nthree\nfour\n", **ACTOR)
        return [await read(), await read(offset=2, limit=2), await read(offset=4, limit=10), await read(offset=9)]

    whole, middle, tail, beyond = asyncio.run(scenario())

    assert whole["content"] == "one\ntwo\nthree\nfour\n" and "lines" not in whole
    assert (middle["content"], middle["lines"]) == ("two\nthree\n", {"start": 2, "end": 3, "total": 4})
    assert (tail["content"], tail["lines"]) == ("four\n", {"start": 4, "end": 4, "total": 4})
    assert (beyond["content"], beyond["lines"]) == ("", {"start": 9, "end": 8, "total": 4})
    with pytest.raises(PlatformError):
        asyncio.run(read(offset=0))
