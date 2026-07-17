from fastapi import APIRouter, Query, Request, Response, status

from tianzhou_agent_platform.aina.document.models import (
    DocumentCreate,
    DocumentListResponse,
    DocumentOutline,
    DocumentRecord,
    DocumentRename,
    DocumentSection,
    DocumentUpdate,
)
from tianzhou_agent_platform.api.dependencies import documents


def create_document_router() -> APIRouter:
    router = APIRouter(prefix="/documents")

    @router.get("", response_model=DocumentListResponse)
    async def list_documents(
        request: Request,
        user_id: str = "anonymous",
        tenant_id: str = "default",
    ) -> DocumentListResponse:
        items = await documents(request).list_documents(user_id=user_id, tenant_id=tenant_id)
        return DocumentListResponse(items=items, total=len(items))

    @router.post("", response_model=DocumentRecord, status_code=status.HTTP_201_CREATED)
    async def create_document(payload: DocumentCreate, request: Request) -> DocumentRecord:
        return await documents(request).create_document(
            payload.name,
            payload.content,
            user_id=payload.user_id,
            tenant_id=payload.tenant_id,
        )

    @router.get("/{name}/outline", response_model=DocumentOutline)
    async def get_document_outline(
        name: str,
        request: Request,
        user_id: str = "anonymous",
        tenant_id: str = "default",
    ) -> DocumentOutline:
        return await documents(request).get_outline(name, user_id=user_id, tenant_id=tenant_id)

    @router.get("/{name}/sections", response_model=DocumentSection)
    async def get_document_section(
        name: str,
        request: Request,
        heading: str = Query(min_length=1),
        occurrence: int = Query(default=1, ge=1),
        user_id: str = "anonymous",
        tenant_id: str = "default",
    ) -> DocumentSection:
        return await documents(request).get_section(
            name,
            heading,
            occurrence,
            user_id=user_id,
            tenant_id=tenant_id,
        )

    @router.get("/{name}", response_model=DocumentRecord)
    async def get_document(
        name: str,
        request: Request,
        user_id: str = "anonymous",
        tenant_id: str = "default",
    ) -> DocumentRecord:
        return await documents(request).get_document(name, user_id=user_id, tenant_id=tenant_id)

    @router.put("/{name}", response_model=DocumentRecord)
    async def update_document(name: str, payload: DocumentUpdate, request: Request) -> DocumentRecord:
        return await documents(request).update_document(
            name,
            payload.content,
            user_id=payload.user_id,
            tenant_id=payload.tenant_id,
        )

    @router.post("/{name}/rename", response_model=DocumentRecord)
    async def rename_document(name: str, payload: DocumentRename, request: Request) -> DocumentRecord:
        return await documents(request).rename_document(
            name,
            payload.new_name,
            user_id=payload.user_id,
            tenant_id=payload.tenant_id,
        )

    @router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_document(
        name: str,
        request: Request,
        user_id: str = Query(default="anonymous"),
        tenant_id: str = Query(default="default"),
    ) -> Response:
        await documents(request).delete_document(name, user_id=user_id, tenant_id=tenant_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router
