import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import {
  Bot,
  Check,
  ChevronDown,
  ChevronRight,
  Code2,
  Columns2,
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
import { api, apiErrorMessage } from "@/lib/api";
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
  DocumentSectionUpdateResult,
  DocumentSummary,
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

export function DocumentWidget({ disabled = false }: { disabled?: boolean }) {
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
  const [aiInstruction, setAiInstruction] = useState("");
  const [creatingEntry, setCreatingEntry] = useState<"document" | "folder" | null>(null);
  const [createValue, setCreateValue] = useState("");
  const [renamingItem, setRenamingItem] = useState<{ kind: "document" | "folder"; path: string } | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmRequest, setConfirmRequest] = useState<ConfirmRequest | null>(null);

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
  }, [activeTask?.id, hasPendingWork, selectedName]);

  useEffect(() => {
    if (!activeSection) {
      setDraftContent("");
      return;
    }
    setActiveSectionId(activeSection.id);
    setDraftContent(activeSection.draft_content);
  }, [activeSection?.id, activeSection?.draft_revision]);

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
      setActiveTask(taskList.items[0] ?? null);
      setActiveSectionId(taskList.items[0]?.sections[0]?.id ?? null);
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
        if (updated) setActiveTask(updated);
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

  async function saveSection() {
    const heading = outline?.headings.find((item) => item.index === editHeadingIndex);
    if (!selectedName || !outline || !heading || disabled || saving || !dirty) return;
    const sectionContent = changedSectionContent(savedContent, content, heading);
    if (sectionContent === null) {
      setError(`只能保存“${heading.heading}”章节，请撤销对其他章节或章节外内容的修改。`);
      return;
    }
    setSaving(true);
    try {
      const result = await api.put<DocumentSectionUpdateResult>(
        `/documents/${documentApiPath(selectedName)}/sections`,
        {
        ...ACTOR,
          heading: heading.heading,
          occurrence: heading.occurrence,
          section_content: sectionContent,
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
      const updatedHeading = nextOutline.headings.find(
        (item) => item.heading === result.heading && item.occurrence === result.occurrence,
      );
      setEditHeadingIndex(updatedHeading?.index ?? editableDocumentHeadings(nextOutline)[0]?.index ?? null);
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
    if (nextMode === "tasks" && !activeTask && tasks.length) {
      setActiveTask(tasks[0]);
      setActiveSectionId(tasks[0].sections[0]?.id ?? null);
    }
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

  async function reviseWithAi() {
    if (!activeTask || !activeSection || !aiInstruction.trim()) return;
    setSaving(true);
    try {
      const task = await api.post<DocumentEditTask>(
        `/document-edit-tasks/${activeTask.id}/sections/${activeSection.id}/ai-revise`,
        {
          ...ACTOR,
          instruction: aiInstruction.trim(),
          expected_draft_revision: activeSection.draft_revision,
        },
      );
      setAiInstruction("");
      replaceTask(task);
      setError(null);
    } catch (revisionError) {
      setError(apiErrorMessage(revisionError));
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

  async function mergeTask() {
    if (!activeTask || !selectedName) return;
    setSaving(true);
    try {
      const task = await api.post<DocumentEditTask>(`/document-edit-tasks/${activeTask.id}/merge`, ACTOR);
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
      setError(apiErrorMessage(mergeError));
      await refreshTasks(selectedName, activeTask.id);
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
              onContent={setContent} onMode={setEditorMode} onSave={() => void saveSection()} />
          ) : null}
          {selectedName && mode === "tasks" ? (
            <TaskWorkspace tasks={tasks} activeTask={activeTask} activeSection={activeSection}
              activeSectionId={activeSectionId} creating={creatingTask} outline={outline}
              description={taskDescription} selected={selectedHeadingIndexes} draftContent={draftContent}
              aiInstruction={aiInstruction} saving={saving} disabled={disabled}
              onCreateStart={() => setCreatingTask(true)} onCreateCancel={() => setCreatingTask(false)}
              onOpenTask={openTask} onSection={setActiveSectionId} onDescription={setTaskDescription}
              onToggle={toggleHeading} isHeadingDisabled={headingDisabled} onCreate={() => void createEditTask()}
              onDraft={setDraftContent} onInstruction={setAiInstruction} onSave={() => void saveDraft()}
              onRevise={() => void reviseWithAi()} onRetry={() => void retryTask()} onMerge={() => void mergeTask()} />
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
        <div className="min-w-0 flex-1"><h3 className="truncate text-[11.5px] font-extrabold text-ink">全文编辑</h3>
          <p className="truncate text-[9px] text-ink-muted">全文可见；当前保存单元：{activeHeading?.heading ?? "无可编辑章节"}</p></div>
        <div className="flex h-7 items-center rounded-md border border-line bg-white p-0.5" aria-label="编辑器视图">
          <ModeButton active={mode === "edit"} label="仅编辑" onClick={() => onMode("edit")}><Code2 className="h-3.5 w-3.5" /></ModeButton>
          <ModeButton active={mode === "split"} label="分栏" onClick={() => onMode("split")}><Columns2 className="h-3.5 w-3.5" /></ModeButton>
          <ModeButton active={mode === "preview"} label="仅预览" onClick={() => onMode("preview")}><Eye className="h-3.5 w-3.5" /></ModeButton>
        </div>
        <button type="button" className={classNames("btn-outline h-8 px-2 text-[10px]", showOutline && "border-accent-ring bg-accent-soft text-accent")}
          onClick={() => setShowOutline((current) => !current)} aria-pressed={showOutline}>
          <ListTree className="h-3.5 w-3.5" />章节
        </button>
        <button type="button" className="btn-primary h-8 text-[10.5px]" onClick={onSave}
          disabled={disabled || saving || !dirty || !activeHeading}>
          {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}保存当前章节
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
          <p className="text-[9px] text-ink-muted">选择章节后定位并限定保存范围</p></div>
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
  draftContent, aiInstruction, saving, disabled, onCreateStart, onCreateCancel, onOpenTask, onSection,
  onDescription, onToggle, isHeadingDisabled, onCreate, onDraft, onInstruction, onSave, onRevise, onRetry, onMerge }: {
  tasks: DocumentEditTask[]; activeTask: DocumentEditTask | null; activeSection: DocumentDraftSection | null;
  activeSectionId: string | null; creating: boolean; outline: DocumentOutline | null; description: string;
  selected: Set<number>; draftContent: string; aiInstruction: string; saving: boolean; disabled: boolean;
  onCreateStart: () => void; onCreateCancel: () => void; onOpenTask: (task: DocumentEditTask) => void;
  onSection: (id: string) => void; onDescription: (value: string) => void; onToggle: (heading: DocumentHeading) => void;
  isHeadingDisabled: (heading: DocumentHeading) => boolean; onCreate: () => void; onDraft: (value: string) => void;
  onInstruction: (value: string) => void; onSave: () => void; onRevise: () => void; onRetry: () => void; onMerge: () => void;
}) {
  return <div className="flex h-full min-h-0 flex-col">
    <header className="flex flex-wrap items-center gap-2 border-b border-line bg-app-soft px-3 py-2">
      <h3 className="shrink-0 text-[11.5px] font-extrabold text-ink">修改任务</h3>
      <TaskSelect tasks={tasks} activeTask={creating ? null : activeTask} onOpenTask={onOpenTask} />
      {activeTask && !creating ? <span className={statusBadge(activeTask.status)}>{taskStatusLabel(activeTask.status)}</span> : null}
      <button type="button" className="btn-primary ml-auto h-8 text-[10.5px]" onClick={onCreateStart}>
        <Plus className="h-3.5 w-3.5" />新建任务
      </button>
    </header>
    {activeTask && !creating ? (
      <div className="flex items-center gap-1.5 overflow-x-auto border-b border-line bg-white px-3 py-2" aria-label="任务章节">
        {activeTask.sections.map((section) => (
          <button key={section.id} type="button" onClick={() => onSection(section.id)}
            className={classNames("flex h-7 shrink-0 items-center gap-1.5 rounded-full border px-2.5 text-[10px] font-semibold transition",
              activeSectionId === section.id ? "border-accent-ring bg-accent-soft text-accent" : "border-line bg-white text-ink-muted hover:bg-app-soft")}>
            {section.ai_status === "queued" || section.ai_status === "running" ? <Loader2 className="h-3 w-3 animate-spin" />
              : section.ai_status === "failed" ? <X className="h-3 w-3 text-danger" /> : <Check className="h-3 w-3 text-success-deep" />}
            <span className="max-w-44 truncate">{section.heading}</span>
          </button>
        ))}
      </div>
    ) : null}
    <section className="min-h-0 flex-1 bg-white">
      {creating ? <TaskCreator outline={outline} description={description} selected={selected} saving={saving} disabled={disabled}
        onDescription={onDescription} onToggle={onToggle} isDisabled={isHeadingDisabled} onCancel={onCreateCancel} onCreate={onCreate} /> : null}
      {!creating && activeTask ? <TaskReview task={activeTask} section={activeSection} draftContent={draftContent}
        aiInstruction={aiInstruction} saving={saving} disabled={disabled} onDraft={onDraft} onInstruction={onInstruction}
        onSave={onSave} onRevise={onRevise} onRetry={onRetry} onMerge={onMerge} /> : null}
      {!creating && !activeTask ? <div className="flex h-full flex-col items-center justify-center text-center text-ink-muted">
        <FileText className="h-7 w-7 text-ink-subtle" /><p className="mt-2 text-[11px] font-semibold">选择任务或新建修改任务</p>
        <button type="button" className="btn-primary mt-3 h-8 text-[10.5px]" onClick={onCreateStart}><Plus className="h-3.5 w-3.5" />新建任务</button>
      </div> : null}
    </section>
  </div>;
}

function TaskSelect({ tasks, activeTask, onOpenTask }: {
  tasks: DocumentEditTask[];
  activeTask: DocumentEditTask | null;
  onOpenTask: (task: DocumentEditTask) => void;
}) {
  const [open, setOpen] = useState(false);
  return <span className="relative min-w-0">
    <button type="button" onClick={() => setOpen((current) => !current)} disabled={!tasks.length}
      className="flex h-8 min-w-0 items-center gap-1.5 rounded-md border border-line bg-white px-2.5 text-[11px] font-semibold text-ink transition hover:border-line-strong disabled:cursor-not-allowed disabled:opacity-50"
      aria-label="选择任务" aria-expanded={open}>
      {activeTask ? <><TaskIcon task={activeTask} /><span className="max-w-56 truncate">{activeTask.title}</span></>
        : <span className="text-ink-muted">{tasks.length ? "选择任务" : "暂无任务"}</span>}
      <ChevronDown className={classNames("h-3.5 w-3.5 shrink-0 text-ink-subtle transition", open && "rotate-180")} />
    </button>
    {open ? <>
      <span className="fixed inset-0 z-30 cursor-default" onClick={() => setOpen(false)} />
      <span className="absolute left-0 top-full z-40 mt-1 block max-h-72 w-72 overflow-y-auto rounded-lg border border-line bg-white p-1 shadow-xl">
        {tasks.map((task) => <button key={task.id} type="button" onClick={() => { onOpenTask(task); setOpen(false); }}
          className={classNames("mb-0.5 flex w-full items-start gap-2 rounded-md px-2 py-2 text-left",
            activeTask?.id === task.id ? "bg-accent-soft text-accent" : "text-ink-muted hover:bg-app-soft")}>
          <TaskIcon task={task} />
          <span className="min-w-0 flex-1"><strong className="block truncate text-[10.5px]">{task.title}</strong>
            <span className="block text-[8.5px]">{task.sections.length} 章 · {taskStatusLabel(task.status)}</span></span>
        </button>)}
      </span>
    </> : null}
  </span>;
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

function TaskReview({ task, section, draftContent, aiInstruction, saving, disabled, onDraft, onInstruction, onSave, onRevise, onRetry, onMerge }: {
  task: DocumentEditTask; section: DocumentDraftSection | null; draftContent: string; aiInstruction: string;
  saving: boolean; disabled: boolean; onDraft: (value: string) => void; onInstruction: (value: string) => void;
  onSave: () => void; onRevise: () => void; onRetry: () => void; onMerge: () => void;
}) {
  const sectionBusy = section?.ai_status === "queued" || section?.ai_status === "running";
  const editable = task.status === "reviewing" && !sectionBusy;
  const mergeable = task.status === "reviewing" && task.sections.every((item) => item.ai_status === "ready");
  return <div className="flex h-full min-h-0 flex-col">
    <header className="flex flex-wrap items-center gap-2 border-b border-line bg-app-soft px-3 py-2">
      <div className="min-w-0 flex-1"><h3 className="truncate text-[11px] font-bold text-ink">{task.description}</h3>
        <p className="mt-0.5 text-[9px] text-ink-muted">{section ? `当前章节：${section.heading}` : "选择章节进行检视"}</p></div>
      {task.sections.some((item) => item.ai_status === "failed") && task.status !== "conflict" ? <button type="button" className="btn-outline h-8 text-[10px]" onClick={onRetry} disabled={saving}>
        <RotateCcw className="h-3.5 w-3.5" />重试</button> : null}
      {mergeable ? <button type="button" className="btn-primary h-8 text-[10.5px]" onClick={onMerge} disabled={saving || disabled}>
        {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <GitMerge className="h-3.5 w-3.5" />}合并全部</button> : null}
    </header>
    {task.error ? <p className="border-b border-danger-ring bg-danger-soft px-3 py-2 text-[10px] text-danger-deep">{task.error}</p> : null}
    {section ? <div className="grid min-h-0 flex-1 grid-rows-2">
      <div className="flex min-h-0 flex-col border-b border-line bg-app-soft">
        <div className="flex items-center justify-between border-b border-line px-3 py-1.5"><label className="text-[10px] font-bold text-ink-muted">原文快照</label><span className="text-[8.5px] text-ink-subtle">{section.heading}</span></div>
        <pre className="min-h-0 flex-1 overflow-y-auto whitespace-pre-wrap p-3 font-mono text-[11px] leading-5 text-ink-muted">{section.base_content}</pre>
      </div>
      <div className="flex min-h-0 flex-col">
        <div className="flex items-center justify-between border-b border-line px-3 py-1.5"><label className="text-[10px] font-bold text-ink-muted">检视草稿</label><span className="text-[8.5px] text-ink-subtle">版本 {section.draft_revision} · {section.updated_by}</span></div>
        <textarea value={draftContent} onChange={(event) => onDraft(event.target.value)} disabled={!editable || saving} spellCheck={false}
          aria-label="章节草稿" className="min-h-[180px] flex-1 resize-none p-3 font-mono text-[11px] leading-5 text-ink outline-none disabled:bg-app-soft" />
        {section.ai_error ? <p className="border-t border-danger-ring bg-danger-soft px-3 py-1.5 text-[9.5px] text-danger-deep">{section.ai_error}</p> : null}
        <div className="border-t border-line p-2.5">
          <div className="flex flex-wrap gap-2"><input value={aiInstruction} onChange={(event) => onInstruction(event.target.value)} disabled={sectionBusy || saving}
            placeholder="让 AI 继续修改当前章节…" aria-label="AI 修改要求" className="input-soft min-w-[180px] flex-1 text-[10.5px]" />
            <button type="button" className="btn-outline h-8 text-[10px]" disabled={!editable || saving || draftContent === section.draft_content} onClick={onSave}><Save className="h-3.5 w-3.5" />保存草稿</button>
            <button type="button" className="btn-primary h-8 text-[10px]" disabled={disabled || saving || sectionBusy || !aiInstruction.trim()} onClick={onRevise}>
              {sectionBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}AI 修改</button></div>
        </div>
      </div>
    </div> : <div className="flex h-full items-center justify-center text-[11px] text-ink-muted">选择任务章节进行检视</div>}
  </div>;
}

function TaskIcon({ task }: { task: DocumentEditTask }) {
  if (task.status === "merged") return <FileCheck2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-success-deep" />;
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
        <div role="button" tabIndex={0} onClick={() => onFolder(folder.path)}
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
      return <div key={document.name} role="button" tabIndex={0} onClick={() => onDocument(document.name)}
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

function changedSectionContent(original: string, updated: string, heading: DocumentHeading): string | null {
  const lines = original.match(/[^\n]*\n|[^\n]+$/g) ?? [];
  const prefix = lines.slice(0, heading.line_start - 1).join("");
  const suffix = lines.slice(heading.line_end).join("");
  if (!updated.startsWith(prefix) || !updated.endsWith(suffix) || updated.length < prefix.length + suffix.length) {
    return null;
  }
  return updated.slice(prefix.length, updated.length - suffix.length);
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

function taskPending(task: DocumentEditTask): boolean { return task.status === "queued" || task.status === "running" || task.status === "merging" || task.sections.some((item) => item.ai_status === "queued" || item.ai_status === "running"); }
function taskStatusLabel(status: DocumentEditTask["status"]): string { return ({ queued: "排队中", running: "AI 修改中", reviewing: "待检视", merging: "合并中", merged: "已合并", conflict: "存在冲突", failed: "执行失败" })[status]; }
function statusBadge(status: DocumentEditTask["status"]): string { return classNames("rounded px-1.5 py-0.5 text-[8.5px] font-bold", status === "merged" ? "bg-success-soft text-success-deep" : status === "conflict" || status === "failed" ? "bg-danger-soft text-danger-deep" : status === "reviewing" ? "bg-warning-soft text-warning-deep" : "bg-accent-soft text-accent"); }
function formatBytes(value: number): string { return value < 1024 ? `${value} B` : `${(value / 1024).toFixed(value < 10 * 1024 ? 1 : 0)} KB`; }
function formatDate(value: string): string { return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value)); }
