from typing import TYPE_CHECKING, Any

from tianzhou_agent_platform.aina.document.models import (
    DocumentCreate,
    DocumentListResponse,
    DocumentRecord,
    DocumentRename,
    DocumentSummary,
)

if TYPE_CHECKING:
    from tianzhou_agent_platform.aina.document.service import DocumentService

__all__ = [
    "DocumentCreate",
    "DocumentListResponse",
    "DocumentRecord",
    "DocumentRename",
    "DocumentService",
    "DocumentSummary",
]


def __getattr__(name: str) -> Any:
    if name == "DocumentService":
        from tianzhou_agent_platform.aina.document.service import DocumentService

        return DocumentService
    raise AttributeError(name)
