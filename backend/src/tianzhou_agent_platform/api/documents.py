from fastapi import APIRouter, Query, Request, Response, status

from tianzhou_agent_platform.aina.document.models import (
    DocumentCreate,
    DocumentFolder,
    DocumentFolderCreate,
    DocumentFolderRename,
    DocumentListResponse,
    DocumentOutline,
    DocumentRecord,
    DocumentRename,
    DocumentSection,
    DocumentSectionsUpdate,
    DocumentSectionsUpdateResult,
    DocumentSectionUpdate,
    DocumentSectionUpdateResult,
    DocumentTreeResponse,
)
from tianzhou_agent_platform.api.dependencies import actor_scope, bind_actor, documents, repository


def create_document_router() -> APIRouter:
    router = APIRouter(prefix="/documents")

    @router.get("", response_model=DocumentListResponse)
    async def list_documents(
        request: Request,
        user_id: str = "anonymous",
        tenant_id: str = "default",
        workspace_id: str | None = None,
    ) -> DocumentListResponse:
        actor = actor_scope(request, user_id=user_id, tenant_id=tenant_id)
        workspace_storage_key = await _workspace_storage_key(
            request,
            workspace_id,
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
        )
        items = await documents(request).list_documents(
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            workspace_storage_key=workspace_storage_key,
        )
        return DocumentListResponse(items=items, total=len(items))

    @router.post("", response_model=DocumentRecord, status_code=status.HTTP_201_CREATED)
    async def create_document(payload: DocumentCreate, request: Request) -> DocumentRecord:
        scoped = bind_actor(request, payload)
        workspace_storage_key = await _workspace_storage_key(
            request,
            scoped.workspace_id,
            user_id=scoped.user_id,
            tenant_id=scoped.tenant_id,
        )
        return await documents(request).create_document(
            scoped.name,
            scoped.content,
            user_id=scoped.user_id,
            tenant_id=scoped.tenant_id,
            workspace_storage_key=workspace_storage_key,
        )

    @router.get("/tree", response_model=DocumentTreeResponse)
    async def get_document_tree(
        request: Request,
        user_id: str = "anonymous",
        tenant_id: str = "default",
        workspace_id: str | None = None,
    ) -> DocumentTreeResponse:
        actor = actor_scope(request, user_id=user_id, tenant_id=tenant_id)
        workspace_storage_key = await _workspace_storage_key(
            request,
            workspace_id,
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
        )
        service = documents(request)
        folders, document_items = await service.list_folders(
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            workspace_storage_key=workspace_storage_key,
        ), await service.list_documents(
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            workspace_storage_key=workspace_storage_key,
        )
        return DocumentTreeResponse(folders=folders, documents=document_items)

    @router.post("/folders", response_model=DocumentFolder, status_code=status.HTTP_201_CREATED)
    async def create_folder(payload: DocumentFolderCreate, request: Request) -> DocumentFolder:
        scoped = bind_actor(request, payload)
        workspace_storage_key = await _workspace_storage_key(
            request,
            scoped.workspace_id,
            user_id=scoped.user_id,
            tenant_id=scoped.tenant_id,
        )
        return await documents(request).create_folder(
            scoped.path,
            user_id=scoped.user_id,
            tenant_id=scoped.tenant_id,
            workspace_storage_key=workspace_storage_key,
        )

    @router.post("/folders/{path:path}/rename", response_model=DocumentFolder)
    async def rename_folder(
        path: str,
        payload: DocumentFolderRename,
        request: Request,
    ) -> DocumentFolder:
        scoped = bind_actor(request, payload)
        workspace_storage_key = await _workspace_storage_key(
            request,
            scoped.workspace_id,
            user_id=scoped.user_id,
            tenant_id=scoped.tenant_id,
        )
        return await documents(request).rename_folder(
            path,
            scoped.new_path,
            user_id=scoped.user_id,
            tenant_id=scoped.tenant_id,
            workspace_storage_key=workspace_storage_key,
        )

    @router.delete("/folders/{path:path}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_folder(
        path: str,
        request: Request,
        user_id: str = Query(default="anonymous"),
        tenant_id: str = Query(default="default"),
        workspace_id: str | None = Query(default=None),
    ) -> Response:
        actor = actor_scope(request, user_id=user_id, tenant_id=tenant_id)
        workspace_storage_key = await _workspace_storage_key(
            request,
            workspace_id,
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
        )
        await documents(request).delete_folder(
            path,
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            workspace_storage_key=workspace_storage_key,
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/{name:path}/outline", response_model=DocumentOutline)
    async def get_document_outline(
        name: str,
        request: Request,
        user_id: str = "anonymous",
        tenant_id: str = "default",
        workspace_id: str | None = None,
    ) -> DocumentOutline:
        actor = actor_scope(request, user_id=user_id, tenant_id=tenant_id)
        workspace_storage_key = await _workspace_storage_key(
            request,
            workspace_id,
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
        )
        return await documents(request).get_outline(
            name,
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            workspace_storage_key=workspace_storage_key,
        )

    @router.get("/{name:path}/sections", response_model=DocumentSection)
    async def get_document_section(
        name: str,
        request: Request,
        heading: str = Query(min_length=1),
        occurrence: int = Query(default=1, ge=1),
        user_id: str = "anonymous",
        tenant_id: str = "default",
        workspace_id: str | None = None,
    ) -> DocumentSection:
        actor = actor_scope(request, user_id=user_id, tenant_id=tenant_id)
        workspace_storage_key = await _workspace_storage_key(
            request,
            workspace_id,
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
        )
        return await documents(request).get_section(
            name,
            heading,
            occurrence,
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            workspace_storage_key=workspace_storage_key,
        )

    @router.get("/{name:path}", response_model=DocumentRecord)
    async def get_document(
        name: str,
        request: Request,
        user_id: str = "anonymous",
        tenant_id: str = "default",
        workspace_id: str | None = None,
    ) -> DocumentRecord:
        actor = actor_scope(request, user_id=user_id, tenant_id=tenant_id)
        workspace_storage_key = await _workspace_storage_key(
            request,
            workspace_id,
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
        )
        return await documents(request).get_document(
            name,
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            workspace_storage_key=workspace_storage_key,
        )

    @router.put("/{name:path}/sections", response_model=DocumentSectionUpdateResult)
    async def update_document_section(
        name: str,
        payload: DocumentSectionUpdate,
        request: Request,
    ) -> DocumentSectionUpdateResult:
        scoped = bind_actor(request, payload)
        workspace_storage_key = await _workspace_storage_key(
            request,
            scoped.workspace_id,
            user_id=scoped.user_id,
            tenant_id=scoped.tenant_id,
        )
        return await documents(request).update_section(
            name,
            scoped.heading,
            scoped.occurrence,
            scoped.section_content,
            scoped.expected_revision,
            user_id=scoped.user_id,
            tenant_id=scoped.tenant_id,
            workspace_storage_key=workspace_storage_key,
        )

    @router.put("/{name:path}/section-changes", response_model=DocumentSectionsUpdateResult)
    async def update_document_sections(
        name: str,
        payload: DocumentSectionsUpdate,
        request: Request,
    ) -> DocumentSectionsUpdateResult:
        scoped = bind_actor(request, payload)
        workspace_storage_key = await _workspace_storage_key(
            request,
            scoped.workspace_id,
            user_id=scoped.user_id,
            tenant_id=scoped.tenant_id,
        )
        return await documents(request).update_sections(
            name,
            scoped.content,
            scoped.expected_revision,
            user_id=scoped.user_id,
            tenant_id=scoped.tenant_id,
            workspace_storage_key=workspace_storage_key,
        )

    @router.post("/{name:path}/rename", response_model=DocumentRecord)
    async def rename_document(name: str, payload: DocumentRename, request: Request) -> DocumentRecord:
        scoped = bind_actor(request, payload)
        workspace_storage_key = await _workspace_storage_key(
            request,
            scoped.workspace_id,
            user_id=scoped.user_id,
            tenant_id=scoped.tenant_id,
        )
        return await documents(request).rename_document(
            name,
            scoped.new_name,
            user_id=scoped.user_id,
            tenant_id=scoped.tenant_id,
            workspace_storage_key=workspace_storage_key,
        )

    @router.delete("/{name:path}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_document(
        name: str,
        request: Request,
        user_id: str = Query(default="anonymous"),
        tenant_id: str = Query(default="default"),
        workspace_id: str | None = Query(default=None),
    ) -> Response:
        actor = actor_scope(request, user_id=user_id, tenant_id=tenant_id)
        workspace_storage_key = await _workspace_storage_key(
            request,
            workspace_id,
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
        )
        await documents(request).delete_document(
            name,
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            workspace_storage_key=workspace_storage_key,
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router


async def _workspace_storage_key(
    request: Request,
    workspace_id: str | None,
    *,
    user_id: str,
    tenant_id: str,
) -> str | None:
    if workspace_id is None:
        return None
    workspace = await repository(request).require_workspace_actor(
        workspace_id,
        user_id=user_id,
        tenant_id=tenant_id,
    )
    return workspace.storage_key
