from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from tianzhou_agent_platform.aina.document.service import (
    MAX_DOCUMENT_BYTES,
    DocumentService,
    _document_revision,
    _preferred_newline,
    _validate_section_replacement,
)
from tianzhou_agent_platform.aina.document.task_models import (
    DocumentDraftSection,
    DocumentEditTask,
    DocumentEditTaskCreate,
    DocumentEditTaskStatus,
)
from tianzhou_agent_platform.core.errors import PlatformError, conflict, not_found
from tianzhou_agent_platform.core.llm import LLMClient
from tianzhou_agent_platform.core.model_settings import use_model_runtime
from tianzhou_agent_platform.core.repository import InMemoryRepository
from tianzhou_agent_platform.store.errors import StorageValidationError

_SUBMIT_DRAFT_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_document_section_draft",
        "description": "Submit the complete revised Markdown for the selected section.",
        "parameters": {
            "type": "object",
            "properties": {
                "section_content": {
                    "type": "string",
                    "description": "The complete section, starting with its Markdown heading.",
                }
            },
            "required": ["section_content"],
            "additionalProperties": False,
        },
    },
}


class DocumentEditTaskService:
    def __init__(
        self,
        documents: DocumentService,
        repository: InMemoryRepository,
        llm: LLMClient,
    ) -> None:
        self.documents = documents
        self.repository = repository
        self.llm = llm
        self._merge_locks: dict[str, asyncio.Lock] = {}

    async def create_task(self, name: str, data: DocumentEditTaskCreate) -> DocumentEditTask:
        description = data.description.strip()
        workspace_storage_key = await self._workspace_storage_key(
            data.workspace_id,
            user_id=data.user_id,
            tenant_id=data.tenant_id,
        )
        outline = await self.documents.get_outline(
            name,
            user_id=data.user_id,
            tenant_id=data.tenant_id,
            workspace_storage_key=workspace_storage_key,
        )
        available = {(item.heading, item.occurrence): item for item in outline.headings}
        selected = []
        seen: set[tuple[str, int]] = set()
        for requested in data.sections:
            key = (requested.heading.strip(), requested.occurrence)
            if key in seen:
                raise conflict("A document section cannot be selected more than once")
            seen.add(key)
            heading = available.get(key)
            if heading is None:
                raise PlatformError(
                    "INVALID_REQUEST",
                    f"Document section {key[0]!r} occurrence {key[1]} was not found",
                    status_code=422,
                )
            if len(outline.headings) > 1 and all(
                heading.line_start <= item.line_start and heading.line_end >= item.line_end
                for item in outline.headings
            ):
                raise PlatformError(
                    "INVALID_REQUEST",
                    "The document root cannot be selected. Choose one or more child sections.",
                    status_code=422,
                )
            selected.append(heading)
        ordered = sorted(selected, key=lambda item: item.line_start)
        for previous, following in zip(ordered, ordered[1:]):
            if following.line_start <= previous.line_end:
                raise conflict("Selected document sections must not overlap")

        draft_sections: list[DocumentDraftSection] = []
        for heading in selected:
            section = await self.documents.get_section(
                outline.name,
                heading.heading,
                heading.occurrence,
                user_id=data.user_id,
                tenant_id=data.tenant_id,
                workspace_storage_key=workspace_storage_key,
            )
            if section.revision != outline.revision:
                raise conflict("The document changed while the task was being created. Please try again.")
            draft_sections.append(
                DocumentDraftSection(
                    heading=section.heading,
                    occurrence=section.occurrence,
                    level=section.level,
                    base_content=section.content,
                    draft_content=section.content,
                )
            )
        task = DocumentEditTask(
            document_name=outline.name,
            title=_task_title(description),
            description=description,
            base_revision=outline.revision,
            user_id=data.user_id,
            tenant_id=data.tenant_id,
            workspace_id=data.workspace_id,
            sections=draft_sections,
        )
        return await self.repository.create_document_edit_task(task)

    async def get_task(self, task_id: str, *, user_id: str, tenant_id: str) -> DocumentEditTask:
        return await self.repository.get_document_edit_task(
            task_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )

    async def list_tasks(
        self,
        name: str,
        *,
        user_id: str,
        tenant_id: str,
        workspace_id: str | None = None,
    ) -> list[DocumentEditTask]:
        workspace_storage_key = await self._workspace_storage_key(
            workspace_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        document = await self.documents.get_document(
            name,
            user_id=user_id,
            tenant_id=tenant_id,
            workspace_storage_key=workspace_storage_key,
        )
        tasks = await self.repository.list_document_edit_tasks(
            user_id=user_id,
            tenant_id=tenant_id,
            document_name=document.name,
        )
        return [
            task
            for task in tasks
            if task.status != "deleted" and task.workspace_id == workspace_id
        ]

    async def update_draft(
        self,
        task_id: str,
        section_id: str,
        content: str,
        expected_draft_revision: int,
        *,
        user_id: str,
        tenant_id: str,
    ) -> DocumentEditTask:
        task = await self.get_task(task_id, user_id=user_id, tenant_id=tenant_id)
        self._require_reviewing(task)
        section = _find_draft_section(task, section_id)
        _require_pending_section(section)
        if section.draft_revision != expected_draft_revision:
            raise conflict("The draft changed. Reload it before saving your edit.")
        normalized, _ = _validate_section_replacement(
            content,
            target_level=section.level,
            newline=_preferred_newline(section.base_content),
        )
        _validate_draft_size(normalized)
        updated_section = section.model_copy(
            update={
                "draft_content": normalized,
                "draft_revision": section.draft_revision + 1,
                "ai_status": "ready",
                "ai_instruction": None,
                "ai_error": None,
                "updated_by": "user",
            }
        )
        updated = _replace_draft_section(task, updated_section)
        return await self.repository.put_document_edit_task(updated, expected_version=task.version)

    async def request_ai_revision(
        self,
        task_id: str,
        section_id: str,
        instruction: str,
        expected_draft_revision: int,
        *,
        user_id: str,
        tenant_id: str,
    ) -> DocumentEditTask:
        task = await self.get_task(task_id, user_id=user_id, tenant_id=tenant_id)
        self._require_reviewing(task)
        section = _find_draft_section(task, section_id)
        _require_pending_section(section)
        if section.draft_revision != expected_draft_revision:
            raise conflict("The draft changed. Reload it before requesting another AI revision.")
        if section.ai_status in {"queued", "running"}:
            raise conflict("This section already has an AI revision in progress")
        updated_section = section.model_copy(
            update={
                "ai_status": "queued",
                "ai_instruction": instruction.strip(),
                "ai_base_revision": section.draft_revision,
                "ai_error": None,
            }
        )
        updated = _replace_draft_section(task, updated_section)
        if task.status == "failed":
            updated = updated.model_copy(update={"status": "queued", "error": None})
        return await self.repository.put_document_edit_task(updated, expected_version=task.version)

    async def retry_failed(
        self,
        task_id: str,
        *,
        user_id: str,
        tenant_id: str,
    ) -> DocumentEditTask:
        task = await self.get_task(task_id, user_id=user_id, tenant_id=tenant_id)
        if task.status not in {"reviewing", "failed"}:
            raise conflict("Only a failed or reviewable task can be retried")
        sections = [
            (
                item.model_copy(
                    update={
                        "ai_status": "queued",
                        "ai_base_revision": item.draft_revision,
                        "ai_error": None,
                    }
                )
                if item.ai_status == "failed" and item.review_status == "pending"
                else item
            )
            for item in task.sections
        ]
        if not any(item.ai_status == "queued" for item in sections):
            raise conflict("This task has no failed sections to retry")
        updated = task.model_copy(
            update={
                "status": "queued",
                "sections": sections,
                "attempt_count": task.attempt_count + 1,
                "error": None,
            },
            deep=True,
        )
        return await self.repository.put_document_edit_task(updated, expected_version=task.version)

    async def merge_task(self, task_id: str, *, user_id: str, tenant_id: str) -> DocumentEditTask:
        task = await self.get_task(task_id, user_id=user_id, tenant_id=tenant_id)
        workspace_storage_key = await self._workspace_storage_key(
            task.workspace_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        self._require_reviewing(task)
        pending_sections = [item for item in task.sections if item.review_status == "pending"]
        if not pending_sections:
            raise conflict("This task has no pending sections to merge")
        if any(item.ai_status != "ready" for item in pending_sections):
            raise conflict("Every section must be ready before the task can be merged")
        merging = await self.repository.put_document_edit_task(
            task.model_copy(update={"status": "merging", "error": None}),
            expected_version=task.version,
        )
        lock_key = f"{tenant_id}:{user_id}:{task.workspace_id or 'standalone'}:{merging.document_name}"
        acquired = await self._acquire_merge_lock(lock_key)
        if not acquired:
            latest = await self.get_task(task_id, user_id=user_id, tenant_id=tenant_id)
            await self.repository.put_document_edit_task(
                latest.model_copy(update={"status": "reviewing"}),
                expected_version=latest.version,
            )
            raise conflict("Another merge is already updating this document")
        try:
            document = await self.documents.merge_sections(
                merging.document_name,
                [(item.heading, item.occurrence, item.draft_content) for item in pending_sections],
                merging.base_revision,
                user_id=user_id,
                tenant_id=tenant_id,
                workspace_storage_key=workspace_storage_key,
            )
        except StorageValidationError as exc:
            latest = await self.get_task(task_id, user_id=user_id, tenant_id=tenant_id)
            await self.repository.put_document_edit_task(
                latest.model_copy(update={"status": "conflict", "error": str(exc)}),
                expected_version=latest.version,
            )
            raise conflict(str(exc)) from exc
        except Exception as exc:
            latest = await self.get_task(task_id, user_id=user_id, tenant_id=tenant_id)
            await self.repository.put_document_edit_task(
                latest.model_copy(update={"status": "failed", "error": str(exc)}),
                expected_version=latest.version,
            )
            raise
        finally:
            await self._release_merge_lock(lock_key)
        latest = await self.get_task(task_id, user_id=user_id, tenant_id=tenant_id)
        resolved_at = datetime.now(UTC)
        sections = [
            item.model_copy(
                update={
                    "review_status": "merged",
                    "resolved_at": resolved_at,
                    "result_revision": _document_revision(document.content),
                }
            )
            if item.review_status == "pending"
            else item
            for item in latest.sections
        ]
        status = _resolved_task_status(sections)
        return await self.repository.put_document_edit_task(
            latest.model_copy(
                update={
                    "status": status,
                    "sections": sections,
                    "base_revision": _document_revision(document.content),
                    "merged_at": resolved_at,
                    "completed_at": resolved_at if _is_terminal_status(status) else None,
                    "error": None,
                }
            ),
            expected_version=latest.version,
        )

    async def merge_section(
        self,
        task_id: str,
        section_id: str,
        *,
        user_id: str,
        tenant_id: str,
    ) -> DocumentEditTask:
        task = await self.get_task(task_id, user_id=user_id, tenant_id=tenant_id)
        workspace_storage_key = await self._workspace_storage_key(
            task.workspace_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        self._require_reviewing(task)
        section = _find_draft_section(task, section_id)
        _require_pending_section(section)
        if section.ai_status != "ready":
            raise conflict("Only a ready section can be merged")
        merging = await self.repository.put_document_edit_task(
            task.model_copy(update={"status": "merging", "error": None}),
            expected_version=task.version,
        )
        lock_key = f"{tenant_id}:{user_id}:{task.workspace_id or 'standalone'}:{merging.document_name}"
        acquired = await self._acquire_merge_lock(lock_key)
        if not acquired:
            latest = await self.get_task(task_id, user_id=user_id, tenant_id=tenant_id)
            await self.repository.put_document_edit_task(
                latest.model_copy(update={"status": "reviewing"}),
                expected_version=latest.version,
            )
            raise conflict("Another merge is already updating this document")
        try:
            document = await self.documents.merge_sections(
                merging.document_name,
                [(section.heading, section.occurrence, section.draft_content)],
                merging.base_revision,
                user_id=user_id,
                tenant_id=tenant_id,
                workspace_storage_key=workspace_storage_key,
            )
        except StorageValidationError as exc:
            latest = await self.get_task(task_id, user_id=user_id, tenant_id=tenant_id)
            await self.repository.put_document_edit_task(
                latest.model_copy(update={"status": "conflict", "error": str(exc)}),
                expected_version=latest.version,
            )
            raise conflict(str(exc)) from exc
        except Exception as exc:
            latest = await self.get_task(task_id, user_id=user_id, tenant_id=tenant_id)
            await self.repository.put_document_edit_task(
                latest.model_copy(update={"status": "failed", "error": str(exc)}),
                expected_version=latest.version,
            )
            raise
        finally:
            await self._release_merge_lock(lock_key)
        latest = await self.get_task(task_id, user_id=user_id, tenant_id=tenant_id)
        resolved_at = datetime.now(UTC)
        resolved = _find_draft_section(latest, section_id).model_copy(
            update={
                "review_status": "merged",
                "resolved_at": resolved_at,
                "result_revision": _document_revision(document.content),
            }
        )
        updated = _replace_draft_section(latest, resolved)
        sections = updated.sections
        status = _resolved_task_status(sections)
        return await self.repository.put_document_edit_task(
            updated.model_copy(
                update={
                    "status": status,
                    "base_revision": _document_revision(document.content),
                    "merged_at": resolved_at if not _has_pending_sections(sections) else None,
                    "completed_at": resolved_at if _is_terminal_status(status) else None,
                    "error": None,
                }
            ),
            expected_version=latest.version,
        )

    async def abandon_section(
        self,
        task_id: str,
        section_id: str,
        *,
        user_id: str,
        tenant_id: str,
    ) -> DocumentEditTask:
        task = await self.get_task(task_id, user_id=user_id, tenant_id=tenant_id)
        if task.status not in {"reviewing", "failed", "conflict"}:
            raise conflict("Only a reviewable, failed, or conflicted section can be abandoned")
        section = _find_draft_section(task, section_id)
        _require_pending_section(section)
        resolved_at = datetime.now(UTC)
        updated = _replace_draft_section(
            task,
            section.model_copy(update={"review_status": "abandoned", "resolved_at": resolved_at}),
        )
        sections = updated.sections
        status = _resolved_task_status(sections, pending_status=task.status)
        return await self.repository.put_document_edit_task(
            updated.model_copy(
                update={
                    "status": status,
                    "abandoned_at": resolved_at if status == "abandoned" else None,
                    "merged_at": resolved_at if status == "completed" else task.merged_at,
                    "completed_at": resolved_at if _is_terminal_status(status) else None,
                    "error": None if not _has_pending_sections(sections) else task.error,
                }
            ),
            expected_version=task.version,
        )

    async def abandon_task(
        self,
        task_id: str,
        *,
        user_id: str,
        tenant_id: str,
    ) -> DocumentEditTask:
        task = await self.get_task(task_id, user_id=user_id, tenant_id=tenant_id)
        if _is_terminal_status(task.status) or task.status == "deleted":
            raise conflict("A completed or deleted task cannot be abandoned")
        if not _has_pending_sections(task.sections):
            raise conflict("This task has no pending sections to abandon")
        resolved_at = datetime.now(UTC)
        sections = [
            (
                item.model_copy(
                    update={
                        "ai_status": (
                            "cancelled"
                            if item.ai_status in {"queued", "running"}
                            else item.ai_status
                        ),
                        "review_status": "abandoned",
                        "resolved_at": resolved_at,
                    }
                )
                if item.review_status == "pending"
                else item
            )
            for item in task.sections
        ]
        status = _resolved_task_status(sections)
        return await self.repository.put_document_edit_task(
            task.model_copy(
                update={
                    "status": status,
                    "sections": sections,
                    "abandoned_at": resolved_at,
                    "completed_at": resolved_at,
                    "error": None,
                },
                deep=True,
            ),
            expected_version=task.version,
        )

    async def delete_task(
        self,
        task_id: str,
        *,
        user_id: str,
        tenant_id: str,
    ) -> None:
        task = await self.get_task(task_id, user_id=user_id, tenant_id=tenant_id)
        if _is_terminal_status(task.status):
            raise conflict("Completed tasks are immutable history and cannot be deleted")
        if any(item.review_status == "merged" for item in task.sections):
            raise conflict("A task with merged sections cannot be deleted")
        if task.status == "deleted":
            return
        deleted_at = datetime.now(UTC)
        sections = [
            (
                item.model_copy(update={"ai_status": "cancelled"})
                if item.review_status == "pending" and item.ai_status in {"queued", "running"}
                else item
            )
            for item in task.sections
        ]
        await self.repository.put_document_edit_task(
            task.model_copy(
                update={
                    "status": "deleted",
                    "sections": sections,
                    "deleted_at": deleted_at,
                    "error": None,
                },
                deep=True,
            ),
            expected_version=task.version,
        )

    async def _acquire_merge_lock(self, key: str) -> bool:
        stores = getattr(self.repository, "stores", None)
        if stores is not None:
            result = await stores.redis.set_if_absent(
                "document-merge",
                key,
                {"acquired_at": datetime.now(UTC).isoformat()},
                ttl_seconds=15 * 60,
            )
            return bool(result.written)
        lock = self._merge_locks.setdefault(key, asyncio.Lock())
        if lock.locked():
            return False
        await lock.acquire()
        return True

    async def _release_merge_lock(self, key: str) -> None:
        stores = getattr(self.repository, "stores", None)
        if stores is not None:
            await stores.redis.delete("document-merge", key)
            return
        lock = self._merge_locks.get(key)
        if lock is not None and lock.locked():
            lock.release()

    async def _workspace_storage_key(
        self,
        workspace_id: str | None,
        *,
        user_id: str,
        tenant_id: str,
    ) -> str | None:
        if workspace_id is None:
            return None
        workspace = await self.repository.require_workspace_actor(
            workspace_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        return workspace.storage_key

    @staticmethod
    def _require_reviewing(task: DocumentEditTask) -> None:
        if task.status not in {"reviewing", "failed"}:
            raise conflict("Only a reviewable or failed task can be edited or merged")


class DocumentEditWorker:
    def __init__(self, service: DocumentEditTaskService, *, poll_seconds: float = 0.25) -> None:
        self.service = service
        self.poll_seconds = poll_seconds
        self._stop = asyncio.Event()
        self._local_claims: set[str] = set()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        while not self._stop.is_set():
            await self.tick()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_seconds)
            except TimeoutError:
                pass

    async def tick(self) -> None:
        tasks = await self.service.repository.list_document_edit_tasks()
        for task in tasks:
            if (
                task.status == "running" or any(item.ai_status == "running" for item in task.sections)
            ) and not await self._claim_exists(task.id):
                sections = [
                    (
                        item.model_copy(update={"ai_status": "queued", "ai_base_revision": item.draft_revision})
                        if item.ai_status == "running"
                        else item
                    )
                    for item in task.sections
                ]
                recovered_status = "queued" if any(item.ai_status == "queued" for item in sections) else "reviewing"
                task = await self.service.repository.put_document_edit_task(
                    task.model_copy(update={"status": recovered_status, "sections": sections}, deep=True),
                    expected_version=task.version,
                )
            if task.status not in {"queued", "reviewing"}:
                continue
            if not any(item.ai_status == "queued" for item in task.sections):
                continue
            if not await self._claim(task.id):
                continue
            try:
                await self._execute(task)
            finally:
                await self._release(task.id)

    async def _execute(self, task: DocumentEditTask) -> None:
        try:
            if task.status == "queued":
                task = await self.service.repository.put_document_edit_task(
                    task.model_copy(update={"status": "running", "error": None}),
                    expected_version=task.version,
                )
            for item in task.sections:
                latest = await self.service.get_task(
                    task.id,
                    user_id=task.user_id,
                    tenant_id=task.tenant_id,
                )
                section = _find_draft_section(latest, item.id)
                if section.ai_status != "queued":
                    continue
                await self._generate(latest, section)
            latest = await self.service.get_task(
                task.id,
                user_id=task.user_id,
                tenant_id=task.tenant_id,
            )
            if latest.status == "running":
                next_status: DocumentEditTaskStatus = (
                    "failed"
                    if any(
                        item.review_status == "pending" and item.ai_status == "failed"
                        for item in latest.sections
                    )
                    else "reviewing"
                )
                await self.service.repository.put_document_edit_task(
                    latest.model_copy(update={"status": next_status}),
                    expected_version=latest.version,
                )
        except Exception as exc:
            latest = await self.service.get_task(
                task.id,
                user_id=task.user_id,
                tenant_id=task.tenant_id,
            )
            if latest.status == "running":
                await self.service.repository.put_document_edit_task(
                    latest.model_copy(update={"status": "failed", "error": str(exc)}),
                    expected_version=latest.version,
                )

    async def _generate(self, task: DocumentEditTask, section: DocumentDraftSection) -> None:
        running_section = section.model_copy(update={"ai_status": "running", "ai_error": None})
        running = await self.service.repository.put_document_edit_task(
            _replace_draft_section(task, running_section),
            expected_version=task.version,
        )
        instruction = section.ai_instruction or task.description
        try:
            content = await self._complete_section(running, running_section, instruction)
            normalized, _ = _validate_section_replacement(
                content,
                target_level=section.level,
                newline=_preferred_newline(section.base_content),
            )
            _validate_draft_size(normalized)
        except Exception as exc:
            latest = await self.service.get_task(
                task.id,
                user_id=task.user_id,
                tenant_id=task.tenant_id,
            )
            current = _find_draft_section(latest, section.id)
            if current.ai_status == "running" and current.draft_revision == section.ai_base_revision:
                failed = current.model_copy(update={"ai_status": "failed", "ai_error": str(exc)})
                await self.service.repository.put_document_edit_task(
                    _replace_draft_section(latest, failed),
                    expected_version=latest.version,
                )
            return

        latest = await self.service.get_task(
            task.id,
            user_id=task.user_id,
            tenant_id=task.tenant_id,
        )
        current = _find_draft_section(latest, section.id)
        if current.ai_status != "running" or current.draft_revision != section.ai_base_revision:
            return
        ready = current.model_copy(
            update={
                "draft_content": normalized,
                "draft_revision": current.draft_revision + 1,
                "ai_status": "ready",
                "ai_instruction": None,
                "ai_error": None,
                "updated_by": "ai",
            }
        )
        await self.service.repository.put_document_edit_task(
            _replace_draft_section(latest, ready),
            expected_version=latest.version,
        )

    async def _complete_section(
        self,
        task: DocumentEditTask,
        section: DocumentDraftSection,
        instruction: str,
    ) -> str:
        runtime_model = await self.service.repository.get_default_model_runtime(
            user_id=task.user_id,
            tenant_id=task.tenant_id,
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "Revise exactly one Markdown section. Return the complete section through the provided "
                    "function. Keep the first heading at the same Markdown level. Do not add a peer or parent "
                    "heading. Do not discuss the change."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Document: {task.document_name}\n"
                    f"Task: {task.description}\n"
                    f"Current instruction: {instruction}\n\n"
                    f"Current section:\n{section.draft_content}"
                ),
            },
        ]
        with use_model_runtime(runtime_model):
            result = await self.service.llm.complete(
                messages=messages,
                tools=[_SUBMIT_DRAFT_TOOL],
                tool_choice={
                    "type": "function",
                    "function": {"name": "submit_document_section_draft"},
                },
                context_type="document_edit_task",
                context_id=task.id,
            )
        calls = result.message.get("tool_calls") or []
        if len(calls) != 1:
            raise ValueError("The model did not submit a document section draft")
        function = calls[0].get("function") or {}
        if function.get("name") != "submit_document_section_draft":
            raise ValueError("The model returned an unexpected draft function")
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("The model returned invalid draft arguments") from exc
        content = arguments.get("section_content")
        if not isinstance(content, str):
            raise ValueError("The model draft did not contain section_content")
        return content

    async def _claim(self, task_id: str) -> bool:
        stores = getattr(self.service.repository, "stores", None)
        if stores is not None:
            result = await stores.redis.set_if_absent(
                "document-edit-worker",
                task_id,
                {"claimed_at": datetime.now(UTC).isoformat()},
                ttl_seconds=15 * 60,
            )
            return bool(result.written)
        if task_id in self._local_claims:
            return False
        self._local_claims.add(task_id)
        return True

    async def _release(self, task_id: str) -> None:
        stores = getattr(self.service.repository, "stores", None)
        if stores is not None:
            await stores.redis.delete("document-edit-worker", task_id)
        self._local_claims.discard(task_id)

    async def _claim_exists(self, task_id: str) -> bool:
        stores = getattr(self.service.repository, "stores", None)
        if stores is not None:
            return bool(await stores.redis.exists("document-edit-worker", task_id))
        return task_id in self._local_claims


def _task_title(description: str) -> str:
    normalized = " ".join(description.split())
    return normalized if len(normalized) <= 30 else f"{normalized[:30].rstrip()}…"


def _find_draft_section(task: DocumentEditTask, section_id: str) -> DocumentDraftSection:
    section = next((item for item in task.sections if item.id == section_id), None)
    if section is None:
        raise not_found("Document draft section", section_id)
    return section


def _replace_draft_section(task: DocumentEditTask, section: DocumentDraftSection) -> DocumentEditTask:
    return task.model_copy(
        update={
            "sections": [section if item.id == section.id else item for item in task.sections],
        },
        deep=True,
    )


def _require_pending_section(section: DocumentDraftSection) -> None:
    if section.review_status != "pending":
        raise conflict("This section has already been resolved")


def _has_pending_sections(sections: list[DocumentDraftSection]) -> bool:
    return any(item.review_status == "pending" for item in sections)


def _resolved_task_status(
    sections: list[DocumentDraftSection],
    *,
    pending_status: DocumentEditTaskStatus = "reviewing",
) -> DocumentEditTaskStatus:
    if _has_pending_sections(sections):
        if pending_status == "reviewing" and any(
            item.review_status == "pending" and item.ai_status == "failed"
            for item in sections
        ):
            return "failed"
        return pending_status
    resolved = {item.review_status for item in sections}
    if resolved == {"merged"}:
        return "merged"
    if resolved == {"abandoned"}:
        return "abandoned"
    return "completed"


def _is_terminal_status(status: DocumentEditTaskStatus) -> bool:
    return status in {"merged", "completed"}


def _validate_draft_size(content: str) -> None:
    if len(content.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise StorageValidationError("Document section draft exceeds the 1 MiB size limit")
