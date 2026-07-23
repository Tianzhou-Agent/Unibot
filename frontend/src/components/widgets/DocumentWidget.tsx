import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import {
  ArrowLeft,
  Bot,
  Check,
  ChevronDown,
  ChevronRight,
  Code2,
  Columns2,
  Copy,
  Eye,
  FileCheck2,
  FilePlus2,
  FileText,
  Folder,
  FolderOpen,
  FolderPlus,
  GitMerge,
  Loader2,
  ListTree,
  MoreHorizontal,
  Pencil,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import { MarkdownContent } from "@/components/chat/MarkdownContent";
import { api, ApiError, apiErrorMessage } from "@/lib/api";
import { documentApiPath } from "@/lib/documentPaths";
import { classNames } from "@/lib/utils";
import type {
  DocumentDraftSection,
  DocumentEditTask,
  DocumentEditTaskListResponse,
  DocumentFolder,
  DocumentHeading,
  DocumentOutline,
  DocumentRecord,
  DocumentSectionsUpdateResult,
  DocumentSummary,
  DocumentTaskContext,
  DocumentTreeResponse,
} from "@/types";

const ACTOR = { user_id: "anonymous", tenant_id: "default" };
type DocumentMode = "edit" | "tasks";
type EditorMode = "edit" | "split" | "preview";

interface ConfirmRequest {
  title: string;
  message: string;
  confirmLabel: string;
  danger: boolean;
  resolve: (confirmed: boolean) => void;
}

export function DocumentWidget({ disabled = false, refreshToken, onTaskContextChange }: {
  disabled?: boolean;
  refreshToken?: string | null;
  onTaskContextChange?: (context: DocumentTaskContext | null) => void;
}) {
  const [items, setItems] = useState<DocumentSummary[]>([]);
  const [folders, setFolders] = useState<DocumentFolder[]>([]);
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [selectedFolder, setSelectedFolder] = useState("");
  const [expandedFolders, setExpandedFolders] = useState<Set<string>>(new Set());
  const [content, setContent] = useState("");
  const [savedContent, setSavedContent] = useState("");
  const [editHeadingIndex, setEditHeadingIndex] = useState<number | null>(null);
  const [outline, setOutline] = useState<DocumentOutline | null>(null);
  const [tasks, setTasks] = useState<DocumentEditTask[]>([]);
  const [activeTask, setActiveTask] = useState<DocumentEditTask | null>(null);
  const [activeSectionId, setActiveSectionId] = useState<string | null>(null);
  const [mode, setMode] = useState<DocumentMode>("edit");
  const [editorMode, setEditorMode] = useState<EditorMode>("split");
  const [creatingTask, setCreatingTask] = useState(false);
  const [taskDescription, setTaskDescription] = useState("");
  const [selectedHeadingIndexes, setSelectedHeadingIndexes] = useState<Set<number>>(new Set());
  const [draftContent, setDraftContent] = useState("");
  const [creatingEntry, setCreatingEntry] = useState<"document" | "folder" | null>(null);
  const [createValue, setCreateValue] = useState("");
  const [renamingItem, setRenamingItem] = useState<{ kind: "document" | "folder"; path: string } | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmRequest, setConfirmRequest] = useState<ConfirmRequest | null>(null);
  const appliedRefreshToken = useRef<string | null>(null);

  function requestConfirm(options: { title: string; message: string; confirmLabel?: string; danger?: boolean }): Promise<boolean> {
    return new Promise((resolve) => {
      setConfirmRequest({
        title: options.title,
        message: options.message,
        confirmLabel: options.confirmLabel ?? "确认",
        danger: options.danger ?? false,
        resolve,
      });
    });
  }

  function settleConfirm(confirmed: boolean) {
    if (!confirmRequest) return;
    confirmRequest.resolve(confirmed);
    setConfirmRequest(null);
  }

  const dirty = selectedName !== null && content !== savedContent;
  const selected = useMemo(
    () => items.find((item) => item.name === selectedName) ?? null,
    [items, selectedName],
  );
  const activeSection = useMemo(
    () => activeTask?.sections.find((item) => item.id === activeSectionId) ?? activeTask?.sections[0] ?? null,
    [activeSectionId, activeTask],
  );
  const hasPendingWork = useMemo(
    () => tasks.some((task) => taskPending(task)),
    [tasks],
  );
  const documentTree = useMemo(() => buildDocumentTree(folders, items), [folders, items]);

  useEffect(() => {
    void refreshDocuments();
  }, []);

  useEffect(() => {
    if (!selectedName || !hasPendingWork) return;
    const timer = window.setInterval(() => void refreshTasks(selectedName, activeTask?.id), 800);
    return () => window.clearInterval(timer);
  }, [activeSectionId, activeTask?.id, draftContent, hasPendingWork, selectedName]);

  useEffect(() => {
    if (!refreshToken || !selectedName || appliedRefreshToken.current === refreshToken) return;
    appliedRefreshToken.current = refreshToken;
    void syncFromConversation(selectedName);
  }, [refreshToken, selectedName]);

  useEffect(() => {
    if (!activeSection) {
      setDraftContent("");
      return;
    }
    setActiveSectionId(activeSection.id);
    setDraftContent(activeSection.draft_content);
  }, [activeSection?.id, activeSection?.draft_revision]);

  useEffect(() => {
    if (mode !== "tasks" || creatingTask || !selectedName || !activeTask || !activeSection) {
      onTaskContextChange?.(null);
      return;
    }
    onTaskContextChange?.({
      documentName: selectedName,
      taskId: activeTask.id,
      taskTitle: activeTask.title,
      taskStatus: activeTask.status,
      sectionId: activeSection.id,
      sectionHeading: activeSection.heading,
      draftRevision: activeSection.draft_revision,
    });
    return () => onTaskContextChange?.(null);
  }, [
    activeSection?.draft_revision,
    activeSection?.heading,
    activeSection?.id,
    activeSection?.review_status,
    activeTask?.id,
    activeTask?.status,
    activeTask?.title,
    creatingTask,
    mode,
    onTaskContextChange,
    selectedName,
  ]);

  async function refreshDocuments(preferredName?: string | null) {
    setLoading(true);
    try {
      const response = await api.get<DocumentTreeResponse>(`/documents/tree?${new URLSearchParams(ACTOR)}`);
      setItems(response.documents);
      setFolders(response.folders);
      const target = preferredName && response.documents.some((item) => item.name === preferredName)
        ? preferredName
        : response.documents[0]?.name ?? null;
      if (target) await loadDocument(target, false);
      else clearDocument();
      setError(null);
    } catch (loadError) {
      setError(apiErrorMessage(loadError));
    } finally {
      setLoading(false);
    }
  }

  async function loadDocument(name: string, guardDirty = true) {
    if (guardDirty && dirty && !(await requestConfirm({
      title: "放弃未保存修改？",
      message: "当前文档有未保存修改，切换文档将丢失这些修改。",
      confirmLabel: "放弃修改",
      danger: true,
    }))) return;
    setLoading(true);
    try {
      const actorQuery = new URLSearchParams(ACTOR);
      const path = documentApiPath(name);
      const [document, nextOutline, taskList] = await Promise.all([
        api.get<DocumentRecord>(`/documents/${path}?${actorQuery}`),
        api.get<DocumentOutline>(`/documents/${path}/outline?${actorQuery}`),
        api.get<DocumentEditTaskListResponse>(`/documents/${path}/edit-tasks?${actorQuery}`),
      ]);
      setSelectedName(name);
      setSelectedFolder(parentFolder(name));
      setExpandedFolders((current) => withParentFolders(current, name));
      setOutline(nextOutline);
      setEditHeadingIndex(editableDocumentHeadings(nextOutline)[0]?.index ?? null);
      setContent(document.content);
      setSavedContent(document.content);
      setTasks(taskList.items);
      setActiveTask(null);
      setActiveSectionId(null);
      setMode("edit");
      setCreatingTask(false);
      setRenamingItem(null);
      setError(null);
    } catch (loadError) {
      setError(apiErrorMessage(loadError));
    } finally {
      setLoading(false);
    }
  }

  async function selectEditorSection(index: number) {
    if (!outline?.headings.some((heading) => heading.index === index)) return;
    setEditHeadingIndex(index);
  }

  async function refreshTasks(name: string, activeTaskId?: string | null) {
    try {
      const response = await api.get<DocumentEditTaskListResponse>(
        `/documents/${documentApiPath(name)}/edit-tasks?${new URLSearchParams(ACTOR)}`,
      );
      setTasks(response.items);
      const taskId = activeTaskId ?? activeTask?.id;
      if (taskId) {
        const updated = response.items.find((item) => item.id === taskId);
        if (updated) {
          setActiveTask((current) => preserveUnsavedDraft(current, updated, activeSectionId, draftContent));
        }
      }
    } catch (loadError) {
      setError(apiErrorMessage(loadError));
    }
  }

  async function syncFromConversation(name: string) {
    try {
      const actorQuery = new URLSearchParams(ACTOR);
      const path = documentApiPath(name);
      const [tree, taskList, document, nextOutline] = await Promise.all([
        api.get<DocumentTreeResponse>(`/documents/tree?${actorQuery}`),
        api.get<DocumentEditTaskListResponse>(`/documents/${path}/edit-tasks?${actorQuery}`),
        dirty ? Promise.resolve(null) : api.get<DocumentRecord>(`/documents/${path}?${actorQuery}`),
        dirty ? Promise.resolve(null) : api.get<DocumentOutline>(`/documents/${path}/outline?${actorQuery}`),
      ]);
      setItems(tree.documents);
      setFolders(tree.folders);
      setTasks(taskList.items);
      setActiveTask((current) => {
        if (!current) return null;
        const updated = taskList.items.find((item) => item.id === current.id);
        return updated ? preserveUnsavedDraft(current, updated, activeSectionId, draftContent) : current;
      });
      if (document && nextOutline) {
        setContent(document.content);
        setSavedContent(document.content);
        setOutline(nextOutline);
        setEditHeadingIndex((current) => nextOutline.headings.some((heading) => heading.index === current)
          ? current
          : editableDocumentHeadings(nextOutline)[0]?.index ?? null);
      }
    } catch (loadError) {
      setError(apiErrorMessage(loadError));
    }
  }

  function clearDocument() {
    setSelectedName(null);
    setContent("");
    setSavedContent("");
    setEditHeadingIndex(null);
    setOutline(null);
    setTasks([]);
    setActiveTask(null);
  }

  function toggleCreateEntry(kind: "document" | "folder") {
    setCreatingEntry((current) => (current === kind ? null : kind));
    setCreateValue("");
  }

  async function submitCreate(event: FormEvent) {
    event.preventDefault();
    const value = createValue.trim();
    if (!value || disabled || saving || !creatingEntry) return;
    setSaving(true);
    try {
      if (creatingEntry === "document") {
        const name = selectedFolder ? `${selectedFolder}/${value}` : value;
        const title = value.replace(/\.md$/i, "");
        const record = await api.post<DocumentRecord>("/documents", {
          ...ACTOR,
          name,
          content: `# ${title}\n\n`,
        });
        setCreateValue("");
        setCreatingEntry(null);
        await refreshDocuments(record.name);
      } else {
        const path = selectedFolder ? `${selectedFolder}/${value}` : value;
        await api.post("/documents/folders", { ...ACTOR, path });
        setCreateValue("");
        setCreatingEntry(null);
        setSelectedFolder(path);
        setExpandedFolders((current) => new Set(current).add(path));
        await refreshTreeOnly();
      }
      setError(null);
    } catch (createError) {
      setError(apiErrorMessage(createError));
    } finally {
      setSaving(false);
    }
  }

  function startRename(item: { kind: "document" | "folder"; path: string }) {
    setRenamingItem(item);
    setRenameValue(baseName(item.path));
  }

  async function submitRename() {
    if (!renamingItem || disabled || saving) return;
    const value = renameValue.trim();
    if (!value) return;
    const parent = parentFolder(renamingItem.path);
    const nextPath = value.includes("/") ? value : parent ? `${parent}/${value}` : value;
    if (nextPath === renamingItem.path) {
      setRenamingItem(null);
      return;
    }
    if (renamingItem.kind === "document") await renameDocument(renamingItem.path, nextPath);
    else await renameFolder(renamingItem.path, nextPath);
  }

  async function renameDocument(name: string, nextName: string) {
    if (name === selectedName && dirty) {
      setError("请先保存当前内容，再重命名文档。");
      setRenamingItem(null);
      return;
    }
    setSaving(true);
    try {
      const record = await api.post<DocumentRecord>(
        `/documents/${documentApiPath(name)}/rename`,
        { ...ACTOR, new_name: nextName },
      );
      setRenamingItem(null);
      if (name === selectedName) await refreshDocuments(record.name);
      else await refreshTreeOnly();
      setError(null);
    } catch (renameError) {
      setError(apiErrorMessage(renameError));
    } finally {
      setSaving(false);
    }
  }

  async function renameFolder(path: string, nextPath: string) {
    setSaving(true);
    try {
      await api.post(`/documents/folders/${documentApiPath(path)}/rename`, {
        ...ACTOR,
        new_path: nextPath,
      });
      const preferredDocument = selectedName?.startsWith(`${path}/`)
        ? `${nextPath}${selectedName.slice(path.length)}`
        : selectedName;
      setRenamingItem(null);
      setSelectedFolder(nextPath);
      await refreshDocuments(preferredDocument);
      setError(null);
    } catch (renameError) {
      setError(apiErrorMessage(renameError));
    } finally {
      setSaving(false);
    }
  }

  async function deleteFolder(path: string) {
    if (disabled || saving) return;
    if (!(await requestConfirm({
      title: "删除文件夹",
      message: `删除空文件夹“${path}”？此操作不可撤销。`,
      confirmLabel: "删除",
      danger: true,
    }))) return;
    setSaving(true);
    try {
      await api.delete(`/documents/folders/${documentApiPath(path)}?${new URLSearchParams(ACTOR)}`);
      setSelectedFolder(parentFolder(path));
      await refreshTreeOnly();
      setError(null);
    } catch (deleteError) {
      setError(apiErrorMessage(deleteError));
    } finally {
      setSaving(false);
    }
  }

  function selectFolder(path: string) {
    setSelectedFolder(path);
    setExpandedFolders((current) => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }

  async function saveDocumentChanges() {
    if (!selectedName || !outline || disabled || saving || !dirty) return;
    setSaving(true);
    try {
      await api.put<DocumentSectionsUpdateResult>(
        `/documents/${documentApiPath(selectedName)}/section-changes`,
        {
          ...ACTOR,
          content,
          expected_revision: outline.revision,
        },
      );
      const path = documentApiPath(selectedName);
      const [document, nextOutline] = await Promise.all([
        api.get<DocumentRecord>(`/documents/${path}?${new URLSearchParams(ACTOR)}`),
        api.get<DocumentOutline>(`/documents/${path}/outline?${new URLSearchParams(ACTOR)}`),
      ]);
      setContent(document.content);
      setSavedContent(document.content);
      setOutline(nextOutline);
      setEditHeadingIndex(
        nextOutline.headings.some((item) => item.index === editHeadingIndex)
          ? editHeadingIndex
          : editableDocumentHeadings(nextOutline)[0]?.index ?? null,
      );
      await refreshListOnly();
      setError(null);
    } catch (saveError) {
      setError(apiErrorMessage(saveError));
    } finally {
      setSaving(false);
    }
  }

  function switchMode(nextMode: DocumentMode) {
    if (nextMode === "tasks" && dirty) {
      setError("请先保存当前编辑内容，再进入任务模式。");
      return;
    }
    setMode(nextMode);
    setCreatingTask(false);
  }

  async function createEditTask() {
    if (!selectedName || !outline || !taskDescription.trim() || !selectedHeadingIndexes.size) return;
    setSaving(true);
    try {
      const selectedHeadings = outline.headings.filter((heading) => selectedHeadingIndexes.has(heading.index));
      const task = await api.post<DocumentEditTask>(
        `/documents/${documentApiPath(selectedName)}/edit-tasks`,
        {
          ...ACTOR,
          description: taskDescription.trim(),
          sections: selectedHeadings.map((heading) => ({
            heading: heading.heading,
            occurrence: heading.occurrence,
          })),
        },
      );
      setTasks((current) => [task, ...current]);
      setActiveTask(task);
      setActiveSectionId(task.sections[0]?.id ?? null);
      setTaskDescription("");
      setSelectedHeadingIndexes(new Set());
      setCreatingTask(false);
      setMode("tasks");
      setError(null);
    } catch (createError) {
      setError(apiErrorMessage(createError));
    } finally {
      setSaving(false);
    }
  }

  function toggleHeading(heading: DocumentHeading) {
    setSelectedHeadingIndexes((current) => {
      const next = new Set(current);
      if (next.has(heading.index)) next.delete(heading.index);
      else next.add(heading.index);
      return next;
    });
  }

  function headingDisabled(heading: DocumentHeading): boolean {
    if (selectedHeadingIndexes.has(heading.index) || !outline) return false;
    return outline.headings.some((selectedHeading) => selectedHeadingIndexes.has(selectedHeading.index)
      && selectedHeading.line_start <= heading.line_end
      && heading.line_start <= selectedHeading.line_end);
  }

  function openTask(task: DocumentEditTask) {
    setActiveTask(task);
    setActiveSectionId(task.sections[0]?.id ?? null);
    setCreatingTask(false);
  }

  async function saveDraft() {
    if (!activeTask || !activeSection || draftContent === activeSection.draft_content) return;
    setSaving(true);
    try {
      const task = await api.patch<DocumentEditTask>(
        `/document-edit-tasks/${activeTask.id}/sections/${activeSection.id}`,
        {
          ...ACTOR,
          content: draftContent,
          expected_draft_revision: activeSection.draft_revision,
        },
      );
      replaceTask(task);
      setError(null);
    } catch (saveError) {
      setError(apiErrorMessage(saveError));
    } finally {
      setSaving(false);
    }
  }

  async function retryTask() {
    if (!activeTask) return;
    setSaving(true);
    try {
      replaceTask(await api.post<DocumentEditTask>(`/document-edit-tasks/${activeTask.id}/retry`, ACTOR));
      setError(null);
    } catch (retryError) {
      setError(apiErrorMessage(retryError));
    } finally {
      setSaving(false);
    }
  }

  async function abandonTask() {
    if (!activeTask) return;
    const mergedCount = activeTask.sections.filter((section) => section.review_status === "merged").length;
    if (!(await requestConfirm({
      title: "结束当前任务？",
      message: mergedCount
        ? `已合入的 ${mergedCount} 个章节会保留，其余未处理改动将放弃；任务随后进入历史记录。`
        : "所有未处理的章节草稿都将放弃，正式文档不会改变；任务随后归入失败记录。",
      confirmLabel: "结束并放弃剩余改动",
      danger: true,
    }))) return;
    setSaving(true);
    try {
      replaceTask(await api.post<DocumentEditTask>(
        `/document-edit-tasks/${activeTask.id}/abandon`,
        ACTOR,
      ));
      setError(null);
    } catch (abandonError) {
      setError(apiErrorMessage(abandonError));
    } finally {
      setSaving(false);
    }
  }

  async function deleteTask() {
    if (!activeTask) return;
    if (!(await requestConfirm({
      title: "删除当前任务？",
      message: "任务会从任务列表中移除。此操作仅适用于尚未产生任何章节合入记录的任务。",
      confirmLabel: "删除任务",
      danger: true,
    }))) return;
    setSaving(true);
    try {
      await api.delete(`/document-edit-tasks/${activeTask.id}?${new URLSearchParams(ACTOR)}`);
      setTasks((current) => current.filter((task) => task.id !== activeTask.id));
      setActiveTask(null);
      setActiveSectionId(null);
      setError(null);
    } catch (deleteError) {
      setError(apiErrorMessage(deleteError));
    } finally {
      setSaving(false);
    }
  }

  async function mergeSection() {
    if (!activeTask || !activeSection || !selectedName) return;
    if (!(await requestConfirm({
      title: `合入“${activeSection.heading}”章节？`,
      message: "只会将当前章节的草稿写入正式文档，任务中的其他章节仍保持待检视状态。",
      confirmLabel: "合入本章节",
    }))) return;
    setSaving(true);
    try {
      const task = await api.post<DocumentEditTask>(
        `/document-edit-tasks/${activeTask.id}/sections/${activeSection.id}/merge`,
        ACTOR,
      );
      replaceTask(task);
      const path = documentApiPath(selectedName);
      const [document, nextOutline] = await Promise.all([
        api.get<DocumentRecord>(`/documents/${path}?${new URLSearchParams(ACTOR)}`),
        api.get<DocumentOutline>(`/documents/${path}/outline?${new URLSearchParams(ACTOR)}`),
      ]);
      setContent(document.content);
      setSavedContent(document.content);
      setOutline(nextOutline);
      setEditHeadingIndex((current) =>
        nextOutline.headings.some((heading) => heading.index === current)
          ? current
          : editableDocumentHeadings(nextOutline)[0]?.index ?? null,
      );
      await refreshListOnly();
      setError(null);
    } catch (mergeError) {
      if (mergeError instanceof ApiError && mergeError.status === 409) setError(null);
      else setError(apiErrorMessage(mergeError));
      await refreshTasks(selectedName, activeTask.id);
    } finally {
      setSaving(false);
    }
  }

  async function abandonSection() {
    if (!activeTask || !activeSection) return;
    if (!(await requestConfirm({
      title: `放弃“${activeSection.heading}”章节草稿？`,
      message: "当前章节将标记为已放弃，原文不会被修改；任务中的其他章节不受影响。",
      confirmLabel: "放弃本章节",
      danger: true,
    }))) return;
    setSaving(true);
    try {
      replaceTask(await api.post<DocumentEditTask>(
        `/document-edit-tasks/${activeTask.id}/sections/${activeSection.id}/abandon`,
        ACTOR,
      ));
      setError(null);
    } catch (abandonError) {
      setError(apiErrorMessage(abandonError));
    } finally {
      setSaving(false);
    }
  }

  function replaceTask(task: DocumentEditTask) {
    setActiveTask(task);
    setTasks((current) => current.map((item) => item.id === task.id ? task : item));
  }

  async function deleteDocument(name: string) {
    if (disabled || saving) return;
    if (!(await requestConfirm({
      title: "删除文档",
      message: `删除文档“${name}”？此操作不可撤销。`,
      confirmLabel: "删除",
      danger: true,
    }))) return;
    setSaving(true);
    try {
      await api.delete(`/documents/${documentApiPath(name)}?${new URLSearchParams(ACTOR)}`);
      if (name === selectedName) await refreshDocuments(null);
      else await refreshTreeOnly();
      setError(null);
    } catch (deleteError) {
      setError(apiErrorMessage(deleteError));
    } finally {
      setSaving(false);
    }
  }

  async function refreshListOnly() {
    await refreshTreeOnly();
  }

  async function refreshTreeOnly() {
    const response = await api.get<DocumentTreeResponse>(`/documents/tree?${new URLSearchParams(ACTOR)}`);
    setItems(response.documents);
    setFolders(response.folders);
  }

  return (
    <div className="grid h-full min-h-0 grid-cols-[220px_minmax(0,1fr)] grid-rows-1 overflow-hidden bg-white">
      <aside className="flex min-h-0 flex-col border-r border-line bg-app-soft">
        <div className="flex items-center gap-1.5 border-b border-line px-2.5 py-2">
          <div className="min-w-0 flex-1"><h3 className="text-[11.5px] font-extrabold text-ink">文件树</h3>
            <p className="truncate text-[9px] text-ink-muted">{selectedFolder ? `当前位置 /${selectedFolder}` : "当前位置 /根目录"}</p></div>
          <button type="button" onClick={() => toggleCreateEntry("document")} disabled={disabled || saving}
            className={classNames("flex h-7 w-7 shrink-0 items-center justify-center rounded-md border transition disabled:opacity-40",
              creatingEntry === "document" ? "border-accent-ring bg-accent-soft text-accent" : "border-line bg-white text-ink-muted hover:bg-app-soft hover:text-ink")}
            aria-label="新建文档" title="在当前位置新建文档">
            <FilePlus2 className="h-3.5 w-3.5" />
          </button>
          <button type="button" onClick={() => toggleCreateEntry("folder")} disabled={disabled || saving}
            className={classNames("flex h-7 w-7 shrink-0 items-center justify-center rounded-md border transition disabled:opacity-40",
              creatingEntry === "folder" ? "border-accent-ring bg-accent-soft text-accent" : "border-line bg-white text-ink-muted hover:bg-app-soft hover:text-ink")}
            aria-label="新建文件夹" title="在当前位置新建文件夹">
            <FolderPlus className="h-3.5 w-3.5" />
          </button>
          <button type="button" onClick={() => void refreshDocuments(selectedName)} disabled={loading || saving}
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-line bg-white text-ink-muted transition hover:bg-app-soft hover:text-ink disabled:opacity-40"
            aria-label="刷新文档列表" title="刷新">
            <RefreshCw className={classNames("h-3.5 w-3.5", loading && "animate-spin")} />
          </button>
        </div>
        {creatingEntry ? (
          <form onSubmit={(event) => void submitCreate(event)} className="flex items-center gap-1.5 border-b border-line bg-white px-2 py-1.5">
            {creatingEntry === "document"
              ? <FileText className="h-3.5 w-3.5 shrink-0 text-ink-muted" />
              : <Folder className="h-3.5 w-3.5 shrink-0 text-ink-muted" />}
            <input autoFocus value={createValue} onChange={(event) => setCreateValue(event.target.value)} disabled={saving}
              placeholder={creatingEntry === "document" ? "文件名.md" : "文件夹名称"}
              aria-label={creatingEntry === "document" ? "新文档名称" : "新文件夹名称"}
              className="input-soft h-7 min-w-0 flex-1 px-1.5 text-[11px]" />
            <button type="submit" disabled={saving || !createValue.trim()} aria-label="确认创建" title="创建"
              className="flex h-6 w-6 shrink-0 items-center justify-center rounded bg-accent text-white transition hover:bg-accent-hover disabled:opacity-40">
              <Check className="h-3 w-3" />
            </button>
            <button type="button" onClick={() => setCreatingEntry(null)} aria-label="取消创建" title="取消"
              className="flex h-6 w-6 shrink-0 items-center justify-center rounded border border-line bg-white text-ink-muted transition hover:bg-app-soft">
              <X className="h-3 w-3" />
            </button>
          </form>
        ) : null}
        <div className="flex items-center border-b border-line px-2.5 py-1.5">
          <button type="button" onClick={() => setSelectedFolder("")}
            className={classNames("text-[10.5px] font-bold", selectedFolder ? "text-ink-muted hover:text-ink" : "text-accent")}>全部文件 · {items.length}</button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-1.5" aria-label="文档文件树">
          <DocumentTree node={documentTree} depth={0} selectedName={selectedName} selectedFolder={selectedFolder}
            expanded={expandedFolders} renaming={renamingItem} renameValue={renameValue}
            onFolder={selectFolder} onDocument={(name) => void loadDocument(name)}
            onRenameValue={setRenameValue} onRenameSubmit={() => void submitRename()}
            onRenameCancel={() => setRenamingItem(null)} onRenameStart={startRename}
            onDeleteDocument={(name) => void deleteDocument(name)} onDeleteFolder={(path) => void deleteFolder(path)} />
          {!loading && !items.length ? <p className="px-2 py-6 text-center text-[11px] text-ink-muted">暂无 Markdown 文档</p> : null}
        </div>
      </aside>

      <section className="flex min-h-0 min-w-0 flex-col">
        <header className="flex min-h-12 flex-wrap items-center gap-2 border-b border-line px-3 py-2">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h4 className="truncate text-[12.5px] font-extrabold text-ink">{selectedName ?? "选择文档"}</h4>
              {dirty ? <span className="rounded bg-warning-soft px-1.5 py-0.5 text-[9px] font-bold text-warning-deep">未保存</span> : null}
            </div>
            <p className="mt-0.5 text-[9px] text-ink-subtle">{selected?.modified_at ? `更新于 ${formatDate(selected.modified_at)}` : "选择文档后开始编辑"}</p>
          </div>
          {selectedName ? (
            <div className="flex h-8 items-center rounded-md border border-line bg-app-soft p-0.5" aria-label="文档工作模式">
              <ViewButton active={mode === "edit"} label="编辑" onClick={() => switchMode("edit")} />
              <ViewButton active={mode === "tasks"} label={`任务 ${tasks.length}`} onClick={() => switchMode("tasks")} />
            </div>
          ) : null}
        </header>

        {error ? <div className="flex items-center gap-2 border-b border-danger-ring bg-danger-soft px-3 py-2 text-[10.5px] text-danger-deep">
          <span className="min-w-0 flex-1">{error}</span><button type="button" onClick={() => setError(null)} aria-label="关闭错误"><X className="h-3.5 w-3.5" /></button>
        </div> : null}

        <div className="min-h-0 flex-1 bg-white">
          {loading && !selectedName ? <div className="flex h-full items-center justify-center text-ink-muted"><Loader2 className="h-5 w-5 animate-spin" /></div> : null}
          {!loading && !selectedName ? <EmptyDocument /> : null}
          {selectedName && mode === "edit" ? (
            <DocumentEditor content={content} dirty={dirty} saving={saving} disabled={disabled} mode={editorMode}
              outline={outline} headingIndex={editHeadingIndex} onHeading={(index) => void selectEditorSection(index)}
              onContent={setContent} onMode={setEditorMode} onSave={() => void saveDocumentChanges()} />
          ) : null}
          {selectedName && mode === "tasks" ? (
            <TaskWorkspace tasks={tasks} activeTask={activeTask} activeSection={activeSection}
              activeSectionId={activeSectionId} creating={creatingTask} outline={outline}
              description={taskDescription} selected={selectedHeadingIndexes} draftContent={draftContent}
              saving={saving} disabled={disabled}
              onCreateStart={() => setCreatingTask(true)} onCreateCancel={() => setCreatingTask(false)}
              onOpenTask={openTask} onSection={setActiveSectionId} onDescription={setTaskDescription}
              onToggle={toggleHeading} isHeadingDisabled={headingDisabled} onCreate={() => void createEditTask()}
              onDraft={setDraftContent} onSave={() => void saveDraft()} onRetry={() => void retryTask()}
              onMerge={() => void mergeSection()} onAbandon={() => void abandonSection()}
              onAbandonTask={() => void abandonTask()} onDeleteTask={() => void deleteTask()}
              onBack={() => { setActiveTask(null); setActiveSectionId(null); }} />
          ) : null}
        </div>
      </section>

      {confirmRequest ? <ConfirmDialog request={confirmRequest} onSettle={settleConfirm} /> : null}
    </div>
  );
}

function DocumentEditor({ content, dirty, saving, disabled, mode, outline, headingIndex, onHeading,
  onContent, onMode, onSave }: {
  content: string; dirty: boolean; saving: boolean; disabled: boolean; mode: EditorMode;
  outline: DocumentOutline | null; headingIndex: number | null; onHeading: (index: number) => void;
  onContent: (value: string) => void; onMode: (mode: EditorMode) => void; onSave: () => void;
}) {
  const headings = outline ? editableDocumentHeadings(outline) : [];
  const activeHeading = headings.find((heading) => heading.index === headingIndex) ?? headings[0];
  const editorRef = useRef<HTMLTextAreaElement | null>(null);
  const [showOutline, setShowOutline] = useState(false);

  function jumpToHeading(heading: DocumentHeading) {
    onHeading(heading.index);
    setShowOutline(false);
    if (!editorRef.current) return;
    const lineHeight = 24;
    editorRef.current.focus();
    editorRef.current.scrollTop = Math.max(0, (heading.line_start - 2) * lineHeight);
  }

  return <div className="relative h-full min-h-0">
    <div className="flex h-full min-h-0 min-w-0 flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-line bg-app-soft px-3 py-1.5">
        <button type="button" className={classNames("flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-line bg-white text-ink-muted hover:text-ink",
          showOutline && "border-accent-ring bg-accent-soft text-accent")}
          onClick={() => setShowOutline((current) => !current)} aria-label="章节" title="章节导航" aria-pressed={showOutline}>
          <ListTree className="h-3.5 w-3.5" />
        </button>
        <div className="flex h-7 items-center rounded-md border border-line bg-white p-0.5" aria-label="编辑器视图">
          <ModeButton active={mode === "edit"} label="仅编辑" onClick={() => onMode("edit")}><Code2 className="h-3.5 w-3.5" /></ModeButton>
          <ModeButton active={mode === "split"} label="分栏" onClick={() => onMode("split")}><Columns2 className="h-3.5 w-3.5" /></ModeButton>
          <ModeButton active={mode === "preview"} label="仅预览" onClick={() => onMode("preview")}><Eye className="h-3.5 w-3.5" /></ModeButton>
        </div>
        <button type="button" className="btn-primary ml-auto h-8 text-[10.5px]" onClick={onSave}
          disabled={disabled || saving || !dirty}>
          {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}保存文档
        </button>
      </div>
      <div className={classNames("grid min-h-0 flex-1", mode === "split" && "grid-rows-2 xl:grid-cols-2 xl:grid-rows-1")}>
        {mode !== "preview" ? <textarea ref={editorRef} value={content} onChange={(event) => onContent(event.target.value)}
          disabled={disabled || saving} spellCheck={false} aria-label="全文 Markdown 编辑器"
          className={classNames("h-full min-h-0 w-full resize-none bg-white p-3 font-mono text-[12px] leading-6 text-ink outline-none",
            mode === "split" && "border-b border-line xl:border-b-0 xl:border-r")} /> : null}
        {mode !== "edit" ? <div className="h-full min-h-0 overflow-y-auto bg-app-soft p-3" aria-label="全文 Markdown 预览">
          <MarkdownContent content={content || "_空文档_"} />
        </div> : null}
      </div>
    </div>
    {showOutline ? <aside className="absolute left-2 top-12 z-20 flex max-h-[calc(100%-3.5rem)] w-60 flex-col overflow-hidden rounded-lg border border-line bg-white shadow-xl">
      <div className="flex items-center gap-2 border-b border-line px-3 py-2">
        <div className="min-w-0 flex-1"><h3 className="text-[11px] font-extrabold text-ink">章节导航</h3>
          <p className="text-[9px] text-ink-muted">点击标题快速定位到对应内容</p></div>
        <button type="button" onClick={() => setShowOutline(false)} aria-label="关闭章节导航" title="关闭"
          className="flex h-5 w-5 shrink-0 items-center justify-center rounded text-ink-subtle hover:bg-app-soft hover:text-ink">
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-1.5">
        {headings.map((heading) => <button key={heading.index} type="button" onClick={() => jumpToHeading(heading)}
          className={classNames("mb-0.5 flex w-full items-center gap-1.5 rounded px-2 py-1.5 text-left text-[10.5px]",
            heading.index === activeHeading?.index ? "bg-accent-soft text-accent" : "text-ink-muted hover:bg-app-soft")}
          style={{ paddingLeft: `${8 + Math.max(0, heading.level - 1) * 10}px` }}>
          <span className="truncate">{heading.heading}</span><span className="ml-auto font-mono text-[8px] text-ink-subtle">H{heading.level}</span>
        </button>)}
        {!headings.length ? <p className="p-4 text-center text-[10px] text-ink-muted">没有可编辑章节</p> : null}
      </div>
    </aside> : null}
  </div>;
}

function TaskWorkspace({ tasks, activeTask, activeSection, activeSectionId, creating, outline, description, selected,
  draftContent, saving, disabled, onCreateStart, onCreateCancel, onOpenTask, onSection,
  onDescription, onToggle, isHeadingDisabled, onCreate, onDraft, onSave, onRetry, onMerge, onAbandon,
  onAbandonTask, onDeleteTask, onBack }: {
  tasks: DocumentEditTask[]; activeTask: DocumentEditTask | null; activeSection: DocumentDraftSection | null;
  activeSectionId: string | null; creating: boolean; outline: DocumentOutline | null; description: string;
  selected: Set<number>; draftContent: string; saving: boolean; disabled: boolean;
  onCreateStart: () => void; onCreateCancel: () => void; onOpenTask: (task: DocumentEditTask) => void;
  onSection: (id: string) => void; onDescription: (value: string) => void; onToggle: (heading: DocumentHeading) => void;
  isHeadingDisabled: (heading: DocumentHeading) => boolean; onCreate: () => void; onDraft: (value: string) => void;
  onSave: () => void; onRetry: () => void; onMerge: () => void; onAbandon: () => void;
  onAbandonTask: () => void; onDeleteTask: () => void; onBack: () => void;
}) {
  const [bucket, setBucket] = useState<DocumentTaskBucket>("active");
  const [copiedTaskId, setCopiedTaskId] = useState<string | null>(null);
  const taskGroups = useMemo(() => ({
    active: tasks.filter((task) => documentTaskBucket(task) === "active"),
    failed: tasks.filter((task) => documentTaskBucket(task) === "failed"),
    history: tasks.filter((task) => documentTaskBucket(task) === "history"),
  }), [tasks]);
  const visibleTasks = taskGroups[bucket];
  useEffect(() => {
    if (activeTask) setBucket(documentTaskBucket(activeTask));
  }, [activeTask]);
  async function copyTaskId(taskId: string) {
    let copied = false;
    try {
      await navigator.clipboard.writeText(taskId);
      copied = true;
    } catch {
      const textarea = document.createElement("textarea");
      textarea.value = taskId;
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.appendChild(textarea);
      textarea.select();
      copied = document.execCommand("copy");
      textarea.remove();
    }
    if (!copied) return;
    setCopiedTaskId(taskId);
    window.setTimeout(() => setCopiedTaskId((current) => current === taskId ? null : current), 1600);
  }
  return <div className="h-full min-h-0 bg-white">
    {creating ? <section className="h-full min-h-0">
      <TaskCreator outline={outline} description={description} selected={selected} saving={saving} disabled={disabled}
        onDescription={onDescription} onToggle={onToggle} isDisabled={isHeadingDisabled} onCancel={onCreateCancel} onCreate={onCreate} />
    </section> : null}
    {!creating && activeTask ? <TaskReview task={activeTask} section={activeSection} draftContent={draftContent}
      activeSectionId={activeSectionId} saving={saving} disabled={disabled} onSection={onSection} onDraft={onDraft}
      onSave={onSave} onRetry={onRetry} onMerge={onMerge} onAbandon={onAbandon}
      onAbandonTask={onAbandonTask} onDeleteTask={onDeleteTask} onBack={onBack} /> : null}
    {!creating && !activeTask ? <section className="flex h-full min-h-0 flex-col bg-app-soft">
      <header className="flex items-center gap-2 border-b border-line bg-white px-3 py-2">
        <div className="min-w-0 flex-1"><h3 className="text-[12px] font-extrabold text-ink">文档变更任务</h3>
          <p className="mt-0.5 text-[9px] text-ink-muted">跟踪进行中的改动、未合入任务和不可变更的合入历史</p></div>
        <button type="button" className="btn-primary h-8 text-[10.5px]" onClick={onCreateStart} aria-label="新建修改任务">
          <Plus className="h-3.5 w-3.5" />新建任务
        </button>
      </header>
      <nav className="flex items-center gap-1 border-b border-line bg-white px-3 py-1.5" aria-label="任务分区">
        {(["active", "failed", "history"] as const).map((item) => <button key={item} type="button"
          onClick={() => setBucket(item)} aria-pressed={bucket === item}
          className={classNames("flex h-7 items-center gap-1.5 rounded-md px-2.5 text-[10px] font-bold",
            bucket === item ? "bg-accent-soft text-accent" : "text-ink-muted hover:bg-app-soft hover:text-ink")}>
          {documentTaskBucketLabel(item)}
          <span className={classNames("rounded px-1 py-0.5 font-mono text-[8px]",
            bucket === item ? "bg-white/80" : "bg-app-soft")}>{taskGroups[item].length}</span>
        </button>)}
      </nav>
      <div className="min-h-0 flex-1 overflow-y-auto p-3" aria-label={bucket === "history" ? "合入历史" : "修改任务列表"}>
        {bucket === "history" ? groupDocumentTaskHistory(visibleTasks).map((group) => <section key={group.key} className="relative pb-4 pl-7 last:pb-0">
          <span aria-hidden="true" className="absolute bottom-0 left-[7px] top-3 w-px bg-line" />
          <span aria-hidden="true" className="absolute left-[3px] top-2.5 h-[9px] w-[9px] rounded-full border-2 border-ink-subtle bg-white" />
          <h4 className="mb-2 text-[10px] font-medium text-ink-muted">{group.label}的合入</h4>
          <div className="overflow-hidden rounded-lg border border-line bg-white">
            {group.tasks.map((task, index) => {
              return <div key={task.id}
                className={classNames("flex w-full items-center gap-3 px-3 py-3 text-left transition hover:bg-app-soft",
                  index > 0 && "border-t border-line")}>
                <span className="min-w-0 flex-1">
                  <strong className="block truncate text-[11px] text-ink">{task.title}</strong>
                  <span className="mt-0.5 block text-[8.5px] text-ink-subtle">
                    合入于 {formatTime(task.completed_at ?? task.merged_at ?? task.updated_at)}
                  </span>
                </span>
                <span className="shrink-0 font-mono text-[9px] text-ink-muted" title={task.id}>{shortTaskId(task.id)}</span>
                <button type="button" onClick={() => void copyTaskId(task.id)}
                  aria-label={`${copiedTaskId === task.id ? "已复制" : "复制"}任务 ID ${shortTaskId(task.id)}`}
                  className="flex h-6 w-6 shrink-0 items-center justify-center rounded text-ink-muted hover:bg-white hover:text-ink">
                  {copiedTaskId === task.id ? <Check className="h-3.5 w-3.5 text-success-deep" /> : <Copy className="h-3.5 w-3.5" />}
                </button>
                <button type="button" onClick={() => onOpenTask(task)} aria-label={`查看任务 ${task.title}`}
                  className="shrink-0 rounded border border-line px-1 py-0.5 font-mono text-[9px] text-ink-muted hover:border-accent-ring hover:text-accent">
                  {"<>"}
                </button>
              </div>;
            })}
          </div>
        </section>) : <div className="grid auto-rows-min grid-cols-1 gap-2">
          {visibleTasks.map((task) => <div key={task.id}
            className="flex items-start gap-2 rounded-lg border border-line bg-white p-3 text-left transition hover:border-accent-ring hover:shadow-sm">
            <TaskIcon task={task} />
            <span className="min-w-0 flex-1"><span className="flex items-start gap-2"><strong className="min-w-0 flex-1 truncate text-[11px] text-ink">{task.title}</strong>
              <span className={statusBadge(task.status)}>{taskStatusLabel(task.status)}</span></span>
              <span className="mt-1 block line-clamp-2 text-[9.5px] leading-4 text-ink-muted">{task.description}</span>
              <span className="mt-2 flex items-center gap-2 text-[8.5px] text-ink-subtle">
                <span className="font-mono" title={task.id}>{shortTaskId(task.id)}</span>
                <button type="button" onClick={() => void copyTaskId(task.id)}
                  aria-label={`${copiedTaskId === task.id ? "已复制" : "复制"}任务 ID ${shortTaskId(task.id)}`}
                  className="flex h-6 w-6 shrink-0 items-center justify-center rounded text-ink-muted hover:bg-app-soft hover:text-ink">
                  {copiedTaskId === task.id ? <Check className="h-3.5 w-3.5 text-success-deep" /> : <Copy className="h-3.5 w-3.5" />}
                </button>
                <span>
                已处理 {task.sections.filter((section) => section.review_status !== "pending").length}/{task.sections.length}
                {" · "}第 {task.attempt_count ?? 1} 次执行{" · "}{task.completed_at ? `结束于 ${formatDate(task.completed_at)}` : `更新于 ${formatDate(task.updated_at)}`}
                </span>
                <button type="button" onClick={() => onOpenTask(task)} aria-label={`查看任务 ${task.title}`}
                  className="ml-auto shrink-0 rounded border border-line px-1 py-0.5 font-mono text-[9px] text-ink-muted hover:border-accent-ring hover:text-accent">
                  {"<>"}
                </button>
              </span></span>
          </div>)}
        </div>}
        {!visibleTasks.length ? <div className="col-span-full flex min-h-56 flex-col items-center justify-center text-center text-ink-muted">
          <FileText className="h-7 w-7 text-ink-subtle" /><p className="mt-2 text-[11px] font-semibold">{documentTaskEmptyLabel(bucket)}</p>
          {bucket === "active" ? <button type="button" className="btn-primary mt-3 h-8 text-[10.5px]" onClick={onCreateStart}><Plus className="h-3.5 w-3.5" />创建修改任务</button> : null}
        </div> : null}
      </div>
    </section> : null}
  </div>;
}

function TaskCreator({ outline, description, selected, saving, disabled, onDescription, onToggle, isDisabled, onCancel, onCreate }: {
  outline: DocumentOutline | null; description: string; selected: Set<number>; saving: boolean; disabled: boolean;
  onDescription: (value: string) => void; onToggle: (heading: DocumentHeading) => void;
  isDisabled: (heading: DocumentHeading) => boolean; onCancel: () => void; onCreate: () => void;
}) {
  const headings = outline ? editableDocumentHeadings(outline) : [];
  return <div className="grid h-full min-h-0 grid-rows-[auto_auto_minmax(0,1fr)_auto] gap-2.5 p-3">
    <div className="flex items-center gap-2"><Sparkles className="h-4 w-4 text-accent" /><div><h3 className="text-[13px] font-extrabold text-ink">创建修改任务</h3>
      <p className="text-[9.5px] text-ink-muted">标题将根据描述自动生成</p></div></div>
    <div><label className="text-[10px] font-bold text-ink-muted">任务描述</label>
      <textarea value={description} onChange={(event) => onDescription(event.target.value)} rows={3} maxLength={20000}
        placeholder="描述希望 AI 如何修改所选章节…" className="input-soft mt-1 w-full resize-y p-2.5 text-[11.5px] leading-5" /></div>
    <div className="flex min-h-0 flex-col"><div className="flex items-center justify-between"><label className="text-[10px] font-bold text-ink-muted">选择章节（可多选，不可重叠）</label>
      <span className="text-[9.5px] text-ink-subtle">已选 {selected.size}</span></div>
      <div className="mt-1 min-h-0 flex-1 overflow-y-auto rounded-lg border border-line p-1">
        {headings.map((heading) => { const blocked = isDisabled(heading); return <label key={heading.index}
          className={classNames("flex items-center gap-2 rounded-md px-2 py-1.5 text-[11px]", blocked ? "cursor-not-allowed text-ink-subtle opacity-50" : "cursor-pointer text-ink hover:bg-app-soft")}
          style={{ paddingLeft: `${8 + Math.max(0, heading.level - 1) * 12}px` }}>
          <input type="checkbox" checked={selected.has(heading.index)} disabled={blocked} onChange={() => onToggle(heading)} />
          <span className="truncate">{heading.heading}</span><span className="ml-auto font-mono text-[8.5px] text-ink-subtle">H{heading.level}</span>
        </label>; })}
        {!headings.length ? <p className="p-5 text-center text-[11px] text-ink-muted">文档中没有可编辑章节</p> : null}
      </div></div>
    <div className="flex justify-end gap-2"><button type="button" className="btn-outline h-8 text-[10.5px]" onClick={onCancel}>取消</button>
      <button type="button" className="btn-primary h-8 text-[10.5px]" disabled={disabled || saving || !description.trim() || !selected.size} onClick={onCreate}>
        {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Bot className="h-3.5 w-3.5" />}创建并执行
      </button></div>
  </div>;
}

function TaskReview({ task, section, activeSectionId, draftContent, saving, disabled, onSection, onDraft,
  onSave, onRetry, onMerge, onAbandon, onAbandonTask, onDeleteTask, onBack }: {
  task: DocumentEditTask; section: DocumentDraftSection | null; activeSectionId: string | null; draftContent: string;
  saving: boolean; disabled: boolean; onSection: (id: string) => void; onDraft: (value: string) => void;
  onSave: () => void; onRetry: () => void; onMerge: () => void; onAbandon: () => void;
  onAbandonTask: () => void; onDeleteTask: () => void; onBack: () => void;
}) {
  const [reviewView, setReviewView] = useState<"diff" | "edit">("diff");
  const sectionBusy = section?.ai_status === "queued" || section?.ai_status === "running";
  const sectionPending = section?.review_status === "pending";
  const reviewableTask = task.status === "reviewing" || task.status === "failed";
  const editable = reviewableTask && sectionPending && !sectionBusy && section?.ai_status === "ready";
  const draftDirty = Boolean(section && draftContent !== section.draft_content);
  const mergeable = reviewableTask && sectionPending && section?.ai_status === "ready";
  const abandonable = sectionPending && (task.status === "reviewing" || task.status === "failed" || task.status === "conflict");
  const closed = isDocumentTaskHistory(task);
  const retryable = task.sections.some((item) => item.review_status === "pending" && item.ai_status === "failed")
    && task.status !== "conflict";
  const taskAbandonable = !closed && task.status !== "deleted"
    && task.sections.some((item) => item.review_status === "pending");
  const taskDeletable = !closed && task.status !== "deleted"
    && task.sections.every((item) => item.review_status !== "merged");
  const resolvedCount = task.sections.filter((item) => item.review_status !== "pending").length;
  const lineDiff = useMemo(() => buildLineDiff(section?.base_content ?? "", draftContent), [draftContent, section?.base_content]);
  useEffect(() => setReviewView("diff"), [section?.id]);
  return <div className="flex h-full min-h-0 flex-col">
    <header className="flex flex-wrap items-center gap-2 border-b border-line bg-app-soft px-3 py-2">
      <button type="button" onClick={onBack} aria-label="返回任务列表" title="返回任务列表"
        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-line bg-white text-ink-muted hover:text-ink">
        <ArrowLeft className="h-3.5 w-3.5" />
      </button>
      <div className="min-w-0 flex-1"><div className="flex items-center gap-2"><h3 className="truncate text-[11.5px] font-extrabold text-ink">{task.title}</h3>
        <span className={statusBadge(task.status)}>{taskStatusLabel(task.status)}</span></div>
        <p className="mt-0.5 truncate text-[9px] text-ink-muted" title={task.id}>
          任务 {shortTaskId(task.id)} · {task.description} · 已处理 {resolvedCount}/{task.sections.length} · 第 {task.attempt_count ?? 1} 次执行
        </p></div>
      {retryable ? <button type="button" className="btn-outline h-8 text-[10px]" onClick={onRetry} disabled={saving}>
        <RotateCcw className="h-3.5 w-3.5" />重试未完成</button> : null}
      {taskAbandonable ? <button type="button" className="btn-outline h-8 text-[10px]" onClick={onAbandonTask} disabled={saving || disabled}>
        <X className="h-3.5 w-3.5" />结束任务</button> : null}
      {taskDeletable ? <button type="button" className="btn-outline h-8 border-danger-ring px-2 text-[10px] text-danger-deep hover:bg-danger-soft" onClick={onDeleteTask} disabled={saving || disabled}
        aria-label="删除任务" title="仅未产生章节合入记录的任务可以删除">
        <Trash2 className="h-3.5 w-3.5" />删除</button> : null}
      {abandonable ? <button type="button" className="btn-outline h-8 border-danger-ring text-[10px] text-danger-deep hover:bg-danger-soft" onClick={onAbandon} disabled={saving || disabled}>
        <Trash2 className="h-3.5 w-3.5" />放弃本章节</button> : null}
      {editable && draftDirty ? <button type="button" className="btn-primary h-8 text-[10.5px]" onClick={onSave} disabled={saving || disabled}
        title="保存当前章节草稿">
        {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}保存草稿</button>
        : mergeable ? <button type="button" className="btn-primary h-8 text-[10.5px]" onClick={onMerge} disabled={saving || disabled}
        title="只合入当前章节">
        {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <GitMerge className="h-3.5 w-3.5" />}合入本章节</button> : null}
    </header>
    {closed ? <div className="border-b border-success-ring bg-success-soft px-3 py-2 text-[10px] text-success-deep">
      {task.status === "merged" ? "此任务的所有章节均已合入文档，记录已锁定。" : task.status === "completed" ? "此任务已部分合入、部分放弃，记录已锁定。" : "此任务的所有剩余改动均已放弃，记录已锁定。"}
      {task.completed_at ? ` 完成于 ${formatDate(task.completed_at)}。` : null}
    </div> : null}
    {task.status === "abandoned" ? <div className="border-b border-danger-ring bg-danger-soft px-3 py-2 text-[10px] text-danger-deep">
      此任务未产生任何合入，已归入失败记录。可以查看详情或删除记录。
    </div> : null}
    {task.error ? <p className="border-b border-danger-ring bg-danger-soft px-3 py-2 text-[10px] text-danger-deep">{task.error}</p> : null}
    <div className="grid min-h-0 flex-1 grid-cols-[145px_minmax(0,1fr)]">
      <aside className="min-h-0 overflow-y-auto border-r border-line bg-app-soft p-1.5" aria-label="任务章节">
        <p className="px-1.5 pb-1.5 pt-1 text-[9px] font-bold text-ink-subtle">任务章节 · {task.sections.length}</p>
        {task.sections.map((item) => <button key={item.id} type="button" onClick={() => onSection(item.id)}
          className={classNames("mb-1 flex w-full items-start gap-1.5 rounded-md px-2 py-2 text-left text-[10px] transition",
            activeSectionId === item.id ? "bg-white font-bold text-accent shadow-sm" : "text-ink-muted hover:bg-white")}>
          {item.review_status === "merged" ? <FileCheck2 className="mt-0.5 h-3 w-3 shrink-0 text-success-deep" />
            : item.review_status === "abandoned" ? <X className="mt-0.5 h-3 w-3 shrink-0 text-ink-subtle" />
              : item.ai_status === "queued" || item.ai_status === "running" ? <Loader2 className="mt-0.5 h-3 w-3 shrink-0 animate-spin" />
                : item.ai_status === "failed" ? <X className="mt-0.5 h-3 w-3 shrink-0 text-danger" /> : <Check className="mt-0.5 h-3 w-3 shrink-0 text-success-deep" />}
          <span className="min-w-0 flex-1 truncate">{item.heading}</span>
        </button>)}
      </aside>
      {section ? <div className="flex min-h-0 flex-col">
        <div className="flex items-center gap-2 border-b border-line bg-white px-3 py-1.5">
          <div className="flex rounded-md bg-app-soft p-0.5" role="group" aria-label="章节检视方式">
            <button type="button" onClick={() => setReviewView("diff")} aria-pressed={reviewView === "diff"}
              className={classNames("rounded px-2 py-1 text-[9.5px] font-bold", reviewView === "diff" ? "bg-white text-accent shadow-sm" : "text-ink-muted hover:text-ink")}>差异</button>
            <button type="button" onClick={() => setReviewView("edit")} aria-pressed={reviewView === "edit"}
              className={classNames("rounded px-2 py-1 text-[9.5px] font-bold", reviewView === "edit" ? "bg-white text-accent shadow-sm" : "text-ink-muted hover:text-ink")}>对照编辑</button>
          </div>
          <span className="min-w-0 flex-1 truncate text-[8.5px] text-ink-subtle">{section.heading} · 草稿版本 {section.draft_revision} · {section.updated_by}
            {section.result_revision ? ` · 文档 revision ${section.result_revision.slice(0, 10)}` : ""}</span>
          <span className="shrink-0 font-mono text-[9px] text-success-deep">+{lineDiff.additions}</span>
          <span className="shrink-0 font-mono text-[9px] text-danger-deep">-{lineDiff.deletions}</span>
        </div>
        <div className="min-h-0 flex-1">
          {reviewView === "diff" ? <SectionDiff diff={lineDiff} /> : <div className="grid h-full min-h-0 grid-rows-2">
            <div className="flex min-h-0 flex-col border-b border-line bg-app-soft">
              <div className="flex items-center justify-between border-b border-line px-3 py-1.5"><label className="text-[10px] font-bold text-ink-muted">原文快照</label><span className="truncate text-[8.5px] text-ink-subtle">{section.heading}</span></div>
              <pre className="min-h-0 flex-1 overflow-auto whitespace-pre p-3 font-mono text-[11px] leading-5 text-ink-muted">{section.base_content}</pre>
            </div>
            <div className="flex min-h-0 flex-col">
              <div className="flex items-center justify-between border-b border-line px-3 py-1.5"><label className="text-[10px] font-bold text-ink-muted">检视草稿</label><span className="text-[8.5px] text-ink-subtle">版本 {section.draft_revision} · {section.updated_by}</span></div>
              <textarea value={draftContent} onChange={(event) => onDraft(event.target.value)} disabled={!editable || saving} spellCheck={false}
                aria-label="章节草稿" className="min-h-[150px] flex-1 resize-none p-3 font-mono text-[11px] leading-5 text-ink outline-none disabled:bg-app-soft" />
            </div>
          </div>}
        </div>
        {section.ai_error ? <p className="border-t border-danger-ring bg-danger-soft px-3 py-1.5 text-[9.5px] text-danger-deep">{section.ai_error}</p> : null}
      </div> : <div className="flex h-full items-center justify-center text-[11px] text-ink-muted">选择章节进行检视</div>}
    </div>
  </div>;
}

type LineDiffKind = "context" | "addition" | "deletion";

interface LineDiffRow {
  kind: LineDiffKind;
  text: string;
  oldLine: number | null;
  newLine: number | null;
}

interface LineDiffResult {
  rows: LineDiffRow[];
  additions: number;
  deletions: number;
}

function SectionDiff({ diff }: { diff: LineDiffResult }) {
  return <div className="h-full min-h-0 overflow-auto bg-white font-mono text-[10.5px] leading-5" aria-label="章节差异">
    <div className="sticky top-0 z-10 grid min-w-max grid-cols-[42px_42px_22px_minmax(480px,1fr)] border-b border-line bg-app-soft text-[8.5px] text-ink-subtle">
      <span className="px-2 text-right">原</span><span className="border-l border-line px-2 text-right">新</span><span className="border-l border-line" />
      <span className="px-2">内容</span>
    </div>
    {diff.rows.map((row, index) => <div key={`${index}:${row.kind}`}
      className={classNames("grid min-w-max grid-cols-[42px_42px_22px_minmax(480px,1fr)]",
        row.kind === "addition" ? "bg-success-soft" : row.kind === "deletion" ? "bg-danger-soft" : "hover:bg-app-soft/60")}>
      <span className="select-none px-2 text-right text-ink-subtle">{row.oldLine ?? ""}</span>
      <span className="select-none border-l border-line/70 px-2 text-right text-ink-subtle">{row.newLine ?? ""}</span>
      <span className={classNames("select-none border-l border-line/70 text-center font-bold",
        row.kind === "addition" ? "text-success-deep" : row.kind === "deletion" ? "text-danger-deep" : "text-ink-subtle")}>
        {row.kind === "addition" ? "+" : row.kind === "deletion" ? "−" : " "}
      </span>
      <span className="whitespace-pre px-2 text-ink">{row.text || " "}</span>
    </div>)}
  </div>;
}

function buildLineDiff(original: string, draft: string): LineDiffResult {
  const oldLines = original.split("\n");
  const newLines = draft.split("\n");
  const lengths = Array.from({ length: oldLines.length + 1 }, () => new Uint32Array(newLines.length + 1));
  for (let oldIndex = oldLines.length - 1; oldIndex >= 0; oldIndex -= 1) {
    for (let newIndex = newLines.length - 1; newIndex >= 0; newIndex -= 1) {
      lengths[oldIndex][newIndex] = oldLines[oldIndex] === newLines[newIndex]
        ? lengths[oldIndex + 1][newIndex + 1] + 1
        : Math.max(lengths[oldIndex + 1][newIndex], lengths[oldIndex][newIndex + 1]);
    }
  }

  const rows: LineDiffRow[] = [];
  let oldIndex = 0;
  let newIndex = 0;
  let additions = 0;
  let deletions = 0;
  while (oldIndex < oldLines.length || newIndex < newLines.length) {
    if (oldIndex < oldLines.length && newIndex < newLines.length && oldLines[oldIndex] === newLines[newIndex]) {
      rows.push({ kind: "context", text: oldLines[oldIndex], oldLine: oldIndex + 1, newLine: newIndex + 1 });
      oldIndex += 1;
      newIndex += 1;
    } else if (oldIndex < oldLines.length
      && (newIndex >= newLines.length || lengths[oldIndex + 1][newIndex] >= lengths[oldIndex][newIndex + 1])) {
      rows.push({ kind: "deletion", text: oldLines[oldIndex], oldLine: oldIndex + 1, newLine: null });
      deletions += 1;
      oldIndex += 1;
    } else {
      rows.push({ kind: "addition", text: newLines[newIndex], oldLine: null, newLine: newIndex + 1 });
      additions += 1;
      newIndex += 1;
    }
  }
  return { rows, additions, deletions };
}

function TaskIcon({ task }: { task: DocumentEditTask }) {
  if (task.status === "merged" || task.status === "completed") return <FileCheck2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-success-deep" />;
  if (task.status === "failed" || task.status === "conflict" || task.status === "abandoned") return <X className="mt-0.5 h-3.5 w-3.5 shrink-0 text-danger" />;
  if (taskPending(task)) return <Loader2 className="mt-0.5 h-3.5 w-3.5 shrink-0 animate-spin text-accent" />;
  return <FileText className="mt-0.5 h-3.5 w-3.5 shrink-0" />;
}

function EmptyDocument() { return <div className="flex h-full min-h-72 flex-col items-center justify-center text-center text-ink-muted"><FileText className="h-8 w-8 text-ink-subtle" /><p className="mt-2 text-[11.5px] font-semibold">新建或选择一个 Markdown 文档</p></div>; }

interface DocumentTreeNode {
  path: string;
  name: string;
  folders: DocumentTreeNode[];
  documents: DocumentSummary[];
}

function DocumentTree({ node, depth, selectedName, selectedFolder, expanded, renaming, renameValue,
  onFolder, onDocument, onRenameValue, onRenameSubmit, onRenameCancel, onRenameStart, onDeleteDocument, onDeleteFolder }: {
  node: DocumentTreeNode;
  depth: number;
  selectedName: string | null;
  selectedFolder: string;
  expanded: Set<string>;
  renaming: { kind: "document" | "folder"; path: string } | null;
  renameValue: string;
  onFolder: (path: string) => void;
  onDocument: (name: string) => void;
  onRenameValue: (value: string) => void;
  onRenameSubmit: () => void;
  onRenameCancel: () => void;
  onRenameStart: (item: { kind: "document" | "folder"; path: string }) => void;
  onDeleteDocument: (name: string) => void;
  onDeleteFolder: (path: string) => void;
}) {
  const indent = { paddingLeft: `${6 + depth * 14}px` };
  const childProps = { selectedName, selectedFolder, expanded, renaming, renameValue,
    onFolder, onDocument, onRenameValue, onRenameSubmit, onRenameCancel, onRenameStart, onDeleteDocument, onDeleteFolder };
  return <>
    {node.folders.map((folder) => {
      const open = expanded.has(folder.path);
      const isRenaming = renaming?.kind === "folder" && renaming.path === folder.path;
      return <div key={folder.path}>
        <div role="button" tabIndex={0} aria-label={folder.name} onClick={() => onFolder(folder.path)}
          onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onFolder(folder.path); } }}
          style={indent}
          className={classNames("group mb-0.5 flex w-full cursor-pointer items-center gap-1 rounded py-1.5 pr-1 text-left text-[10.5px]",
            selectedFolder === folder.path ? "bg-white text-accent shadow-sm" : "text-ink-muted hover:bg-white")}>
          {open ? <ChevronDown className="h-3 w-3 shrink-0" /> : <ChevronRight className="h-3 w-3 shrink-0" />}
          {open ? <FolderOpen className="h-3.5 w-3.5 shrink-0" /> : <Folder className="h-3.5 w-3.5 shrink-0" />}
          {isRenaming
            ? <RenameInput value={renameValue} onValue={onRenameValue} onSubmit={onRenameSubmit} onCancel={onRenameCancel} />
            : <span className="truncate font-semibold">{folder.name}</span>}
          {!isRenaming ? <RowMenu items={[
            { label: "重命名", icon: <Pencil className="h-3 w-3" />, onSelect: () => onRenameStart({ kind: "folder", path: folder.path }) },
            { label: "删除", icon: <Trash2 className="h-3 w-3" />, danger: true, onSelect: () => onDeleteFolder(folder.path) },
          ]} /> : null}
        </div>
        {open ? <DocumentTree node={folder} depth={depth + 1} {...childProps} /> : null}
      </div>;
    })}
    {node.documents.map((document) => {
      const isRenaming = renaming?.kind === "document" && renaming.path === document.name;
      return <div key={document.name} role="button" tabIndex={0} aria-label={baseName(document.name)} onClick={() => onDocument(document.name)}
        onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onDocument(document.name); } }}
        style={indent}
        className={classNames("group mb-0.5 flex w-full cursor-pointer items-center gap-1.5 rounded py-1.5 pr-1 text-left",
          document.name === selectedName ? "bg-white text-accent shadow-sm" : "text-ink-muted hover:bg-white")}>
        <FileText className="h-3.5 w-3.5 shrink-0" />
        {isRenaming
          ? <RenameInput value={renameValue} onValue={onRenameValue} onSubmit={onRenameSubmit} onCancel={onRenameCancel} />
          : <span className="min-w-0 flex-1"><strong className="block truncate text-[10.5px]">{baseName(document.name)}</strong>
            <span className="block text-[8px] text-ink-subtle">{formatBytes(document.size_bytes)}</span></span>}
        {!isRenaming ? <RowMenu items={[
          { label: "重命名", icon: <Pencil className="h-3 w-3" />, onSelect: () => onRenameStart({ kind: "document", path: document.name }) },
          { label: "删除", icon: <Trash2 className="h-3 w-3" />, danger: true, onSelect: () => onDeleteDocument(document.name) },
        ]} /> : null}
      </div>;
    })}
  </>;
}

function RenameInput({ value, onValue, onSubmit, onCancel }: {
  value: string;
  onValue: (value: string) => void;
  onSubmit: () => void;
  onCancel: () => void;
}) {
  return <input autoFocus value={value} aria-label="重命名" spellCheck={false}
    onClick={(event) => event.stopPropagation()}
    onChange={(event) => onValue(event.target.value)}
    onKeyDown={(event) => {
      if (event.key === "Enter") { event.preventDefault(); onSubmit(); }
      if (event.key === "Escape") onCancel();
    }}
    className="input-soft h-6 min-w-0 flex-1 px-1.5 text-[10.5px]" />;
}

function RowMenu({ items }: {
  items: { label: string; icon: React.ReactNode; danger?: boolean; onSelect: () => void }[];
}) {
  const [open, setOpen] = useState(false);
  return <span className="relative ml-auto shrink-0">
    <button type="button" aria-label="更多操作" title="更多操作"
      onClick={(event) => { event.stopPropagation(); setOpen((current) => !current); }}
      className={classNames("flex h-5 w-5 items-center justify-center rounded text-ink-subtle hover:bg-app-soft hover:text-ink",
        open ? "opacity-100" : "opacity-0 focus:opacity-100 group-hover:opacity-100")}>
      <MoreHorizontal className="h-3.5 w-3.5" />
    </button>
    {open ? <>
      <span className="fixed inset-0 z-30 cursor-default" onClick={(event) => { event.stopPropagation(); setOpen(false); }} />
      <span className="absolute right-0 top-full z-40 mt-0.5 block w-24 overflow-hidden rounded-md border border-line bg-white py-0.5 shadow-lg">
        {items.map((item) => <button key={item.label} type="button"
          onClick={(event) => { event.stopPropagation(); setOpen(false); item.onSelect(); }}
          className={classNames("flex w-full items-center gap-1.5 px-2.5 py-1.5 text-left text-[10.5px]",
            item.danger ? "text-danger hover:bg-danger-soft" : "text-ink-muted hover:bg-app-soft hover:text-ink")}>
          {item.icon}{item.label}
        </button>)}
      </span>
    </> : null}
  </span>;
}

function buildDocumentTree(folders: DocumentFolder[], documents: DocumentSummary[]): DocumentTreeNode {
  const root: DocumentTreeNode = { path: "", name: "", folders: [], documents: [] };
  const nodes = new Map<string, DocumentTreeNode>([["", root]]);

  function ensureFolder(path: string): DocumentTreeNode {
    const existing = nodes.get(path);
    if (existing) return existing;
    const parentPath = parentFolder(path);
    const node = { path, name: baseName(path), folders: [], documents: [] };
    ensureFolder(parentPath).folders.push(node);
    nodes.set(path, node);
    return node;
  }

  folders.forEach((folder) => ensureFolder(folder.path));
  documents.forEach((document) => ensureFolder(parentFolder(document.name)).documents.push(document));
  nodes.forEach((node) => {
    node.folders.sort((left, right) => left.name.localeCompare(right.name, "zh-CN"));
    node.documents.sort((left, right) => baseName(left.name).localeCompare(baseName(right.name), "zh-CN"));
  });
  return root;
}

function parentFolder(path: string): string {
  const index = path.lastIndexOf("/");
  return index < 0 ? "" : path.slice(0, index);
}

function baseName(path: string): string {
  return path.slice(path.lastIndexOf("/") + 1);
}

function withParentFolders(current: Set<string>, path: string): Set<string> {
  const next = new Set(current);
  let folder = parentFolder(path);
  while (folder) {
    next.add(folder);
    folder = parentFolder(folder);
  }
  return next;
}

function ConfirmDialog({ request, onSettle }: {
  request: ConfirmRequest;
  onSettle: (confirmed: boolean) => void;
}) {
  useEffect(() => {
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") onSettle(false);
    }
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [onSettle]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/35 p-4"
      onMouseDown={(event) => { if (event.target === event.currentTarget) onSettle(false); }}>
      <section role="dialog" aria-modal="true" aria-label={request.title}
        className="w-full max-w-sm rounded-lg border border-line bg-white p-4 shadow-soft">
        <h2 className="text-[13px] font-extrabold text-ink">{request.title}</h2>
        <p className="mt-1.5 text-[11px] leading-5 text-ink-muted">{request.message}</p>
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" className="btn-outline h-8 text-[10.5px]" onClick={() => onSettle(false)}>取消</button>
          <button type="button" autoFocus onClick={() => onSettle(true)}
            className={classNames("h-8 text-[10.5px]", request.danger ? "btn-danger-outline" : "btn-primary")}>
            {request.confirmLabel}
          </button>
        </div>
      </section>
    </div>
  );
}

function ViewButton({ active, label, onClick }: { active: boolean; label: string; onClick: () => void }) {
  return <button type="button" onClick={onClick} className={classNames("h-6 rounded px-2.5 text-[10px] font-bold text-ink-muted", active && "bg-white text-accent shadow-sm")}>{label}</button>;
}

function ModeButton({ active, label, onClick, children }: { active: boolean; label: string; onClick: () => void; children: React.ReactNode }) {
  return <button type="button" onClick={onClick} aria-label={label} title={label}
    className={classNames("flex h-6 w-7 items-center justify-center rounded text-ink-muted", active && "bg-app-soft text-accent")}>{children}</button>;
}

function editableDocumentHeadings(outline: DocumentOutline): DocumentHeading[] {
  if (outline.headings.length <= 1) return outline.headings;
  return outline.headings.filter((heading) => !outline.headings.every((item) =>
    heading.line_start <= item.line_start && heading.line_end >= item.line_end));
}

function preserveUnsavedDraft(
  current: DocumentEditTask | null,
  updated: DocumentEditTask,
  activeSectionId: string | null,
  draftContent: string,
): DocumentEditTask {
  if (!current || current.id !== updated.id || !activeSectionId) return updated;
  const localSection = current.sections.find((item) => item.id === activeSectionId);
  if (!localSection || draftContent === localSection.draft_content) return updated;
  return {
    ...updated,
    sections: updated.sections.map((item) => item.id === activeSectionId ? localSection : item),
  };
}

type DocumentTaskBucket = "active" | "failed" | "history";

function isDocumentTaskHistory(task: DocumentEditTask): boolean {
  return task.status === "merged" || task.status === "completed";
}

function documentTaskBucket(task: DocumentEditTask): DocumentTaskBucket {
  if (task.status === "failed" || task.status === "abandoned" || task.status === "conflict") return "failed";
  if (isDocumentTaskHistory(task) || task.status === "deleted") return "history";
  return "active";
}

function groupDocumentTaskHistory(tasks: DocumentEditTask[]): Array<{ key: string; label: string; tasks: DocumentEditTask[] }> {
  const groups = new Map<string, { key: string; label: string; tasks: DocumentEditTask[] }>();
  const sorted = [...tasks].sort((left, right) =>
    new Date(right.completed_at ?? right.merged_at ?? right.updated_at).getTime()
    - new Date(left.completed_at ?? left.merged_at ?? left.updated_at).getTime());
  for (const task of sorted) {
    const completedAt = new Date(task.completed_at ?? task.merged_at ?? task.updated_at);
    const key = `${completedAt.getFullYear()}-${completedAt.getMonth() + 1}-${completedAt.getDate()}`;
    const group = groups.get(key) ?? {
      key,
      label: new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "long", day: "numeric" }).format(completedAt),
      tasks: [],
    };
    group.tasks.push(task);
    groups.set(key, group);
  }
  return [...groups.values()];
}

function shortTaskId(taskId: string): string {
  const normalized = taskId.startsWith("document_edit_") ? taskId.slice("document_edit_".length) : taskId;
  return normalized.slice(0, 8);
}

function documentTaskBucketLabel(bucket: DocumentTaskBucket): string {
  return { active: "进行中", failed: "失败", history: "历史" }[bucket];
}

function documentTaskEmptyLabel(bucket: DocumentTaskBucket): string {
  return {
    active: "没有进行中的任务",
    failed: "没有失败任务",
    history: "还没有任务历史",
  }[bucket];
}

function taskPending(task: DocumentEditTask): boolean { return task.status === "queued" || task.status === "running" || task.status === "merging" || task.sections.some((item) => item.ai_status === "queued" || item.ai_status === "running"); }
function taskStatusLabel(status: DocumentEditTask["status"]): string { return ({ queued: "排队中", running: "AI 修改中", reviewing: "待检视", merging: "章节合入中", merged: "全部已合入", completed: "部分已合入", abandoned: "未合入", conflict: "合入失败", failed: "执行失败", deleted: "已删除" })[status]; }
function statusBadge(status: DocumentEditTask["status"]): string { return classNames("rounded px-1.5 py-0.5 text-[8.5px] font-bold", status === "merged" || status === "completed" ? "bg-success-soft text-success-deep" : status === "conflict" || status === "failed" || status === "abandoned" ? "bg-danger-soft text-danger-deep" : status === "reviewing" ? "bg-warning-soft text-warning-deep" : status === "deleted" ? "bg-app-soft text-ink-muted" : "bg-accent-soft text-accent"); }
function formatBytes(value: number): string { return value < 1024 ? `${value} B` : `${(value / 1024).toFixed(value < 10 * 1024 ? 1 : 0)} KB`; }
function formatDate(value: string): string { return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value)); }
function formatTime(value: string): string { return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit" }).format(new Date(value)); }
