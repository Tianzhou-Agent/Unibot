from datetime import datetime

from pydantic import Field

from tianzhou_agent_platform.core.base import StrictModel


class DocumentActor(StrictModel):
    user_id: str = "anonymous"
    tenant_id: str = "default"


class DocumentCreate(DocumentActor):
    name: str = Field(min_length=1, max_length=512)
    content: str = Field(default="", max_length=1_048_576)


class DocumentSectionUpdate(DocumentActor):
    heading: str = Field(min_length=1)
    occurrence: int = Field(default=1, ge=1)
    section_content: str = Field(max_length=1_048_576)
    expected_revision: str = Field(min_length=1)


class DocumentSectionsUpdate(DocumentActor):
    content: str = Field(max_length=1_048_576)
    expected_revision: str = Field(min_length=1)


class DocumentRename(DocumentActor):
    new_name: str = Field(min_length=1, max_length=512)


class DocumentFolderCreate(DocumentActor):
    path: str = Field(min_length=1, max_length=512)


class DocumentFolderRename(DocumentActor):
    new_path: str = Field(min_length=1, max_length=512)


class DocumentFolder(StrictModel):
    path: str
    name: str


class DocumentSummary(StrictModel):
    name: str
    size_bytes: int
    modified_at: datetime | None = None


class DocumentSearchResult(DocumentSummary):
    matched_in: list[str]
    excerpt: str | None = None


class DocumentRecord(DocumentSummary):
    content: str


class DocumentHeading(StrictModel):
    index: int
    heading: str
    level: int
    occurrence: int
    line_start: int
    line_end: int


class DocumentOutline(StrictModel):
    name: str
    size_bytes: int
    revision: str
    headings: list[DocumentHeading]


class DocumentSection(StrictModel):
    name: str
    heading: str
    level: int
    occurrence: int
    revision: str
    content: str


class DocumentSectionUpdateResult(StrictModel):
    name: str
    previous_heading: str
    heading: str
    level: int
    occurrence: int
    revision: str
    size_bytes: int
    modified_at: datetime | None = None


class DocumentSectionsUpdateResult(StrictModel):
    name: str
    revision: str
    updated_sections: list[str]
    size_bytes: int
    modified_at: datetime | None = None


class DocumentListResponse(StrictModel):
    items: list[DocumentSummary]
    total: int


class DocumentTreeResponse(StrictModel):
    folders: list[DocumentFolder]
    documents: list[DocumentSummary]
