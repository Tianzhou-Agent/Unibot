from fastapi import APIRouter, Query, Request, Response, status

from tianzhou_agent_platform.aina.document.models import (
    DocumentCreate,
    DocumentListResponse,
    DocumentRecord,
    DocumentRename,
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
