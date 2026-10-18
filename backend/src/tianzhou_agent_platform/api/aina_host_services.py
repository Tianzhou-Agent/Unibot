"""Host API services for AINA MCP servers (design §8): the user's documents, memory and model.

Every route is authorized by the context token (see ``aina_host``) and one scope. Documents are read in the workspace
of the invocation that minted the token, like the built-in document tools; memory is the user's own. Responses
never carry the host's raw user, tenant or workspace ids.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, Query, Request, Response, status
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from pydantic import Field, field_validator

from tianzhou_agent_platform.aina.document.models import DocumentOutline
from tianzhou_agent_platform.aina.memory.models import MemoryCategory, MemoryCreate, MemoryUpdate
from tianzhou_agent_platform.api.aina_host import HOST_API_PREFIX, HostCaller, host_caller, require_scope
from tianzhou_agent_platform.api.dependencies import chat_service, documents, model_provider_service, repository
from tianzhou_agent_platform.core.base import StrictModel
from tianzhou_agent_platform.core.errors import PlatformError, not_found
from tianzhou_agent_platform.model_providers.models import use_model_runtime
from tianzhou_agent_platform.store.errors import StorageNotFoundError

MAX_LLM_MESSAGES = 50
MAX_LLM_CHARS = 200_000
MAX_LLM_OUTPUT_TOKENS = 4_096


class HostDocument(StrictModel):
    name: str
    size_bytes: int
    modified_at: datetime | None = None
    content: str | None = None
    excerpt: str | None = None


class HostDocumentList(StrictModel):
    items: list[HostDocument]


class DocumentContent(StrictModel):
    content: str = Field(max_length=1_000_000)


class HostMemory(StrictModel):
    id: str
    content: str
    category: MemoryCategory
    created_at: datetime
    updated_at: datetime


class HostMemoryList(StrictModel):
    items: list[HostMemory]


class MemoryInput(StrictModel):
    content: str = Field(min_length=1, max_length=1000)
    category: MemoryCategory = "fact"

    @field_validator("content")
    @classmethod
    def safe_content(cls, value: str) -> str:
        """The memory store's own content rules (normalization, unsafe instruction markers), as a 422."""
        return MemoryCreate.validate_content(value)


class MemoryChange(StrictModel):
    content: str | None = Field(default=None, min_length=1, max_length=1000)
    category: MemoryCategory | None = None

    @field_validator("content")
    @classmethod
    def safe_content(cls, value: str | None) -> str | None:
        return MemoryUpdate.validate_content(value)


class ChatMessage(StrictModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(max_length=50_000)


class LlmChatRequest(StrictModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=MAX_LLM_MESSAGES)
    max_tokens: int | None = Field(default=None, ge=1, le=MAX_LLM_OUTPUT_TOKENS)


class ChatUsage(StrictModel):
    input_tokens: int = 0
    output_tokens: int = 0


class ChatReply(StrictModel):
    content: str
    usage: ChatUsage


def create_aina_host_services_router() -> APIRouter:
    router = APIRouter(tags=["aina-host"])

    # --- documents.read / documents.write -----------------------------------------------------------------------

    async def workspace_key(request: Request, caller: HostCaller) -> str | None:
        workspace_id = caller.token.reference.workspace_id
        if workspace_id is None:
            return None
        workspace = await repository(request).require_workspace_actor(
            workspace_id, user_id=caller.installation.user_id, tenant_id=caller.installation.tenant_id
        )
        return workspace.storage_key

    def actor(caller: HostCaller) -> dict[str, str]:
        return {"user_id": caller.installation.user_id, "tenant_id": caller.installation.tenant_id}

    @router.get(f"{HOST_API_PREFIX}/documents", response_model=HostDocumentList)
    async def list_documents(
        request: Request,
        query: str | None = Query(default=None, max_length=200),
        caller: HostCaller = Depends(host_caller),
    ) -> HostDocumentList:
        require_scope(caller, "documents.read", "documents.list")
        key = await workspace_key(request, caller)
        if query and query.strip():
            found = await documents(request).search_documents(
                query,
                user_id=caller.installation.user_id,
                tenant_id=caller.installation.tenant_id,
                workspace_storage_key=key,
            )
            return HostDocumentList(
                items=[
                    HostDocument(name=i.name, size_bytes=i.size_bytes, modified_at=i.modified_at, excerpt=i.excerpt)
                    for i in found
                ]
            )
        listed = await documents(request).list_documents(**actor(caller), workspace_storage_key=key)
        return HostDocumentList(
            items=[HostDocument(name=i.name, size_bytes=i.size_bytes, modified_at=i.modified_at) for i in listed]
        )

    @router.get(f"{HOST_API_PREFIX}/documents/{{name:path}}/outline", response_model=DocumentOutline)
    async def document_outline(name: str, request: Request, caller: HostCaller = Depends(host_caller)) -> Any:
        require_scope(caller, "documents.read", "documents.outline")
        key = await workspace_key(request, caller)
        return await documents(request).get_outline(name, **actor(caller), workspace_storage_key=key)

    @router.get(f"{HOST_API_PREFIX}/documents/{{name:path}}", response_model=HostDocument)
    async def get_document(name: str, request: Request, caller: HostCaller = Depends(host_caller)) -> HostDocument:
        require_scope(caller, "documents.read", "documents.get")
        key = await workspace_key(request, caller)
        record = await documents(request).get_document(name, **actor(caller), workspace_storage_key=key)
        return HostDocument(
            name=record.name, size_bytes=record.size_bytes, modified_at=record.modified_at, content=record.content
        )

    @router.put(f"{HOST_API_PREFIX}/documents/{{name:path}}", response_model=HostDocument)
    async def put_document(
        name: str, payload: DocumentContent, request: Request, caller: HostCaller = Depends(host_caller)
    ) -> HostDocument:
        """Create the document, or replace its content."""
        require_scope(caller, "documents.write", "documents.put")
        key = await workspace_key(request, caller)
        service = documents(request)
        try:
            record = await service.update_document(name, payload.content, **actor(caller), workspace_storage_key=key)
        except StorageNotFoundError:
            record = await service.create_document(name, payload.content, **actor(caller), workspace_storage_key=key)
        return HostDocument(name=record.name, size_bytes=record.size_bytes, modified_at=record.modified_at)

    @router.delete(f"{HOST_API_PREFIX}/documents/{{name:path}}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_document(name: str, request: Request, caller: HostCaller = Depends(host_caller)) -> Response:
        require_scope(caller, "documents.write", "documents.delete")
        key = await workspace_key(request, caller)
        if not await documents(request).delete_document(name, **actor(caller), workspace_storage_key=key):
            raise not_found("Document", name)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # --- memory.read / memory.write -----------------------------------------------------------------------------

    def host_memory(record: Any) -> HostMemory:
        return HostMemory(
            id=record.id,
            content=record.content,
            category=record.category,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    async def own_memory(request: Request, caller: HostCaller, memory_id: str) -> None:
        """Only the user's own memories can be named; another user's id is simply not found."""
        try:
            await repository(request).get_memory(memory_id, **actor(caller))
        except PlatformError as exc:
            if exc.code in {"PERMISSION_DENIED", "RESOURCE_NOT_FOUND"}:
                raise not_found("Memory", memory_id) from exc
            raise

    @router.get(f"{HOST_API_PREFIX}/memory/search", response_model=HostMemoryList)
    async def search_memory(
        request: Request,
        query: str = Query(default="", max_length=500),
        limit: int = Query(default=8, ge=1, le=50),
        caller: HostCaller = Depends(host_caller),
    ) -> HostMemoryList:
        require_scope(caller, "memory.read", "memory.search")
        found = await repository(request).search_memories(query, **actor(caller), limit=limit)
        return HostMemoryList(items=[host_memory(item) for item in found])

    @router.post(f"{HOST_API_PREFIX}/memory", response_model=HostMemory, status_code=status.HTTP_201_CREATED)
    async def create_memory(
        payload: MemoryInput, request: Request, caller: HostCaller = Depends(host_caller)
    ) -> HostMemory:
        require_scope(caller, "memory.write", "memory.create")
        record = await repository(request).create_memory(
            MemoryCreate(
                content=payload.content,
                category=payload.category,
                **actor(caller),
                source_conversation_id=caller.token.reference.conversation_id,
                metadata={"source_aina": caller.installation.aina_id},
            )
        )
        return host_memory(record)

    @router.patch(f"{HOST_API_PREFIX}/memory/{{memory_id}}", response_model=HostMemory)
    async def update_memory(
        memory_id: str, payload: MemoryChange, request: Request, caller: HostCaller = Depends(host_caller)
    ) -> HostMemory:
        require_scope(caller, "memory.write", "memory.update")
        await own_memory(request, caller, memory_id)
        record = await repository(request).update_memory(
            memory_id, MemoryUpdate(content=payload.content, category=payload.category, **actor(caller))
        )
        return host_memory(record)

    @router.delete(f"{HOST_API_PREFIX}/memory/{{memory_id}}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_memory(memory_id: str, request: Request, caller: HostCaller = Depends(host_caller)) -> Response:
        require_scope(caller, "memory.write", "memory.delete")
        await own_memory(request, caller, memory_id)
        await repository(request).remove_memory(memory_id, **actor(caller))
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # --- llm ------------------------------------------------------------------------------------------------------

    @router.post(f"{HOST_API_PREFIX}/llm/chat", response_model=ChatReply)
    async def llm_chat(
        request: Request, payload: LlmChatRequest = Body(...), caller: HostCaller = Depends(host_caller)
    ) -> ChatReply:
        """The user's own configured model, without tools (design §8 ``llm``)."""
        require_scope(caller, "llm", "llm.chat")
        if sum(len(message.content) for message in payload.messages) > MAX_LLM_CHARS:
            raise PlatformError(
                "INVALID_REQUEST", f"Messages exceed {MAX_LLM_CHARS} characters", status_code=413, source="aina_host"
            )
        runtime = await model_provider_service(request).default_runtime(**actor(caller))
        with use_model_runtime(runtime):
            model = chat_service(request).runner.chat_model()
        if payload.max_tokens is not None:
            model = model.bind(max_tokens=payload.max_tokens)  # type: ignore[assignment]
        kinds: dict[str, type[BaseMessage]] = {"system": SystemMessage, "user": HumanMessage, "assistant": AIMessage}
        reply = await model.ainvoke([kinds[message.role](content=message.content) for message in payload.messages])
        usage = getattr(reply, "usage_metadata", None) or {}
        content = reply.content if isinstance(reply.content, str) else str(reply.content)
        return ChatReply(
            content=content,
            usage=ChatUsage(
                input_tokens=int(usage.get("input_tokens") or 0), output_tokens=int(usage.get("output_tokens") or 0)
            ),
        )

    return router
