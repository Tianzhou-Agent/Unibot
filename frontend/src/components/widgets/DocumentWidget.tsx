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
  const [newName, setNewName] = useState("");
  const [newFolderName, setNewFolderName] = useState("");
  const [renameValue, setRenameValue] = useState("");
  const [renaming, setRenaming] = useState(false);
  const [pendingDelete, setPendingDelete] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
    if (guardDirty && dirty && !window.confirm("当前文档有未保存修改，确认放弃并切换文档吗？")) return;
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
      setRenameValue(name);
      setRenaming(false);
      setPendingDelete(false);
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

  async function createDocument(event: FormEvent) {
    event.preventDefault();
    const fileName = newName.trim();
    if (!fileName || disabled || saving) return;
    setSaving(true);
    try {
      const name = selectedFolder ? `${selectedFolder}/${fileName}` : fileName;
      const title = fileName.replace(/\.md$/i, "");
      const record = await api.post<DocumentRecord>("/documents", {
        ...ACTOR,
        name,
        content: `# ${title}\n\n`,
      });
      setNewName("");
      await refreshDocuments(record.name);
    } catch (createError) {
      setError(apiErrorMessage(createError));
    } finally {
      setSaving(false);
    }
  }

  async function createFolder(event: FormEvent) {
    event.preventDefault();
    const folderName = newFolderName.trim();
    if (!folderName || disabled || saving) return;
    const path = selectedFolder ? `${selectedFolder}/${folderName}` : folderName;
    setSaving(true);
    try {
      await api.post("/documents/folders", { ...ACTOR, path });
      setNewFolderName("");
      setSelectedFolder(path);
      setExpandedFolders((current) => new Set(current).add(path));
      await refreshTreeOnly();
      setError(null);
    } catch (createError) {
      setError(apiErrorMessage(createError));
    } finally {
      setSaving(false);
    }
  }

  async function renameFolder(path: string) {
    const nextPath = window.prompt("输入新的文件夹路径", path)?.trim();
    if (!nextPath || nextPath === path || disabled || saving) return;
    setSaving(true);
    try {
      await api.post(`/documents/folders/${documentApiPath(path)}/rename`, {
        ...ACTOR,
        new_path: nextPath,
      });
      const preferredDocument = selectedName?.startsWith(`${path}/`)
        ? `${nextPath}${selectedName.slice(path.length)}`
        : selectedName;
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
    if (!window.confirm(`删除空文件夹“${path}”？`) || disabled || saving) return;
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

  async function renameDocument() {
    const value = renameValue.trim();
    if (!selectedName || !value || disabled || saving) return;
    if (dirty) {
      setError("请先保存当前内容，再重命名文档。");
      return;
    }
    setSaving(true);
    try {
      const record = await api.post<DocumentRecord>(
        `/documents/${documentApiPath(selectedName)}/rename`,
        { ...ACTOR, new_name: value },
      );
      await refreshDocuments(record.name);
    } catch (renameError) {
      setError(apiErrorMessage(renameError));
    } finally {
      setSaving(false);
    }
  }

  async function deleteDocument() {
    if (!selectedName || disabled || saving) return;
    setSaving(true);
    try {
      await api.delete(`/documents/${documentApiPath(selectedName)}?${new URLSearchParams(ACTOR)}`);
      setPendingDelete(false);
      await refreshDocuments(null);
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
        <div className="border-b border-line p-2.5">
          <div className="flex items-center gap-2">
            <div className="min-w-0 flex-1"><h3 className="text-[11.5px] font-extrabold text-ink">文件树</h3>
              <p className="truncate text-[9px] text-ink-muted">{selectedFolder ? `当前位置 / ${selectedFolder}` : "根目录"}</p></div>
            {selectedFolder ? <><IconButton label="重命名文件夹" onClick={() => void renameFolder(selectedFolder)} disabled={saving}>
              <Pencil className="h-3.5 w-3.5" /></IconButton>
              <IconButton label="删除空文件夹" onClick={() => void deleteFolder(selectedFolder)} disabled={saving} danger>
                <Trash2 className="h-3.5 w-3.5" /></IconButton></> : null}
          </div>
        </div>
        <form onSubmit={createDocument} className="border-b border-line p-2.5">
          <label className="text-[9.5px] font-bold text-ink-muted">在当前位置新建文档</label>
          <div className="mt-1 flex gap-1.5">
            <input value={newName} onChange={(event) => setNewName(event.target.value)} disabled={disabled || saving}
              placeholder="文件名.md" aria-label="新文档名称" className="input-soft min-w-0 flex-1 text-[11.5px]" />
            <button type="submit" disabled={disabled || saving || !newName.trim()}
              className="btn-primary h-9 w-9 shrink-0 p-0 disabled:opacity-50" aria-label="创建文档" title="创建文档">
              <FilePlus2 className="h-4 w-4" />
            </button>
          </div>
        </form>
        <form onSubmit={createFolder} className="border-b border-line px-2.5 py-2">
          <div className="flex gap-1.5">
            <input value={newFolderName} onChange={(event) => setNewFolderName(event.target.value)} disabled={disabled || saving}
              placeholder="子文件夹名称" aria-label="新文件夹名称" className="input-soft min-w-0 flex-1 text-[11px]" />
            <button type="submit" disabled={disabled || saving || !newFolderName.trim()}
              className="btn-outline h-9 w-9 shrink-0 p-0 disabled:opacity-50" aria-label="创建文件夹" title="创建文件夹">
              <FolderPlus className="h-4 w-4" />
            </button>
          </div>
        </form>
        <div className="flex items-center border-b border-line px-2.5 py-1.5">
          <button type="button" onClick={() => setSelectedFolder("")}
            className={classNames("text-[10.5px] font-bold", selectedFolder ? "text-ink-muted" : "text-accent")}>全部文件 · {items.length}</button>
          <button type="button" onClick={() => void refreshDocuments(selectedName)} disabled={loading || saving}
            className="btn-ghost ml-auto h-7 w-7 p-0" aria-label="刷新文档列表" title="刷新">
            <RefreshCw className={classNames("h-3.5 w-3.5", loading && "animate-spin")} />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-1.5" aria-label="文档文件树">
          <DocumentTree node={documentTree} selectedName={selectedName} selectedFolder={selectedFolder}
            expanded={expandedFolders} onFolder={selectFolder} onDocument={(name) => void loadDocument(name)} />
          {!loading && !items.length ? <p className="px-2 py-6 text-center text-[11px] text-ink-muted">暂无 Markdown 文档</p> : null}
        </div>
      </aside>

      <section className="flex min-h-0 min-w-0 flex-col">
        <header className="flex min-h-12 flex-wrap items-center gap-2 border-b border-line px-3 py-2">
          {renaming && selectedName ? (
            <div className="flex min-w-0 flex-1 items-center gap-1.5">
              <input value={renameValue} onChange={(event) => setRenameValue(event.target.value)} disabled={saving}
                aria-label="重命名文档" className="input-soft h-8 min-w-0 max-w-72 text-[11.5px]" />
              <IconButton label="确认重命名" onClick={() => void renameDocument()} disabled={!renameValue.trim() || saving}><Check className="h-3.5 w-3.5" /></IconButton>
              <IconButton label="取消重命名" onClick={() => setRenaming(false)}><X className="h-3.5 w-3.5" /></IconButton>
            </div>
          ) : (
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <h4 className="truncate text-[12.5px] font-extrabold text-ink">{selectedName ?? "选择文档"}</h4>
                {dirty ? <span className="rounded bg-warning-soft px-1.5 py-0.5 text-[9px] font-bold text-warning-deep">未保存</span> : null}
              </div>
              <p className="mt-0.5 text-[9px] text-ink-subtle">{selected?.modified_at ? `更新于 ${formatDate(selected.modified_at)}` : "选择文档后开始编辑"}</p>
            </div>
          )}
          {!renaming && selectedName ? (
            <>
              <div className="flex h-8 items-center rounded-md border border-line bg-app-soft p-0.5" aria-label="文档工作模式">
                <ViewButton active={mode === "edit"} label="编辑" onClick={() => switchMode("edit")} />
                <ViewButton active={mode === "tasks"} label={`任务 ${tasks.length}`} onClick={() => switchMode("tasks")} />
              </div>
              <IconButton label="重命名" onClick={() => setRenaming(true)} disabled={saving}><Pencil className="h-3.5 w-3.5" /></IconButton>
              {pendingDelete ? (
                <div className="flex items-center gap-1"><button type="button" onClick={() => setPendingDelete(false)} className="btn-outline h-8 px-2 text-[10px]">取消</button>
                  <button type="button" onClick={() => void deleteDocument()} className="btn-danger-outline h-8 px-2 text-[10px]">确认删除</button></div>
              ) : <IconButton label="删除" onClick={() => setPendingDelete(true)} disabled={saving || disabled} danger><Trash2 className="h-3.5 w-3.5" /></IconButton>}
            </>
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

  return <div className={classNames("grid h-full min-h-0", showOutline && "grid-cols-[minmax(0,1fr)_190px]")}>
    <div className="flex min-h-0 min-w-0 flex-col">
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
    {showOutline ? <aside className="flex min-h-0 flex-col border-l border-line bg-app-soft">
      <div className="border-b border-line px-3 py-2"><h3 className="text-[11px] font-extrabold text-ink">章节导航</h3>
        <p className="text-[9px] text-ink-muted">选择章节后定位并限定保存范围</p></div>
      <div className="min-h-0 flex-1 overflow-y-auto p-1.5">
        {headings.map((heading) => <button key={heading.index} type="button" onClick={() => jumpToHeading(heading)}
          className={classNames("mb-0.5 flex w-full items-center gap-1.5 rounded px-2 py-1.5 text-left text-[10.5px]",
            heading.index === activeHeading?.index ? "bg-white text-accent shadow-sm" : "text-ink-muted hover:bg-white")}
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
  return <div className="grid h-full min-h-0 grid-cols-[210px_minmax(0,1fr)] grid-rows-1">
    <aside className="flex min-h-0 flex-col border-r border-line bg-app-soft">
      <div className="flex items-center gap-2 border-b border-line px-2.5 py-2">
        <div className="min-w-0 flex-1"><h3 className="text-[11.5px] font-extrabold text-ink">修改任务</h3><p className="text-[9px] text-ink-muted">草稿检视后合并</p></div>
        <button type="button" className="btn-primary h-7 px-2 text-[10px]" onClick={onCreateStart}><Plus className="h-3 w-3" />新建</button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-1.5">
        {tasks.map((task) => <button key={task.id} type="button" onClick={() => onOpenTask(task)}
          className={classNames("mb-1 flex w-full items-start gap-2 rounded-md px-2 py-2 text-left",
            activeTask?.id === task.id && !creating ? "bg-white text-accent shadow-sm" : "text-ink-muted hover:bg-white")}>
          <TaskIcon task={task} />
          <span className="min-w-0 flex-1"><strong className="block truncate text-[10.5px]">{task.title}</strong>
            <span className="block text-[8.5px]">{task.sections.length} 章 · {taskStatusLabel(task.status)}</span></span>
        </button>)}
        {!tasks.length ? <p className="px-2 py-6 text-center text-[10.5px] text-ink-muted">暂无任务</p> : null}
      </div>
      {activeTask && !creating ? <div className="max-h-[42%] overflow-y-auto border-t border-line p-1.5">
        <p className="px-2 py-1 text-[9px] font-bold uppercase text-ink-subtle">任务章节</p>
        {activeTask.sections.map((section) => <button key={section.id} type="button" onClick={() => onSection(section.id)}
          className={classNames("mb-0.5 flex w-full items-center gap-1.5 rounded px-2 py-1.5 text-left text-[10px]",
            activeSectionId === section.id ? "bg-white text-accent" : "text-ink-muted hover:bg-white")}>
          {section.ai_status === "queued" || section.ai_status === "running" ? <Loader2 className="h-3 w-3 shrink-0 animate-spin" />
            : section.ai_status === "failed" ? <X className="h-3 w-3 shrink-0 text-danger" /> : <Check className="h-3 w-3 shrink-0 text-success-deep" />}
          <span className="truncate">{section.heading}</span>
        </button>)}
      </div> : null}
    </aside>
    <section className="min-h-0 min-w-0 bg-white">
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
      <div className="min-w-0 flex-1"><div className="flex items-center gap-2"><h3 className="truncate text-[12px] font-extrabold text-ink">{task.title}</h3>
        <span className={statusBadge(task.status)}>{taskStatusLabel(task.status)}</span></div><p className="mt-0.5 truncate text-[9.5px] text-ink-muted">{task.description}</p></div>
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

function DocumentTree({ node, selectedName, selectedFolder, expanded, onFolder, onDocument }: {
  node: DocumentTreeNode;
  selectedName: string | null;
  selectedFolder: string;
  expanded: Set<string>;
  onFolder: (path: string) => void;
  onDocument: (name: string) => void;
}) {
  return <>
    {node.folders.map((folder) => {
      const open = expanded.has(folder.path);
      return <div key={folder.path}>
        <button type="button" onClick={() => onFolder(folder.path)}
          className={classNames("mb-0.5 flex w-full items-center gap-1 rounded px-1.5 py-1.5 text-left text-[10.5px]",
            selectedFolder === folder.path ? "bg-white text-accent shadow-sm" : "text-ink-muted hover:bg-white")}>
          {open ? <ChevronDown className="h-3 w-3 shrink-0" /> : <ChevronRight className="h-3 w-3 shrink-0" />}
          {open ? <FolderOpen className="h-3.5 w-3.5 shrink-0" /> : <Folder className="h-3.5 w-3.5 shrink-0" />}
          <span className="truncate font-semibold">{folder.name}</span>
        </button>
        {open ? <div className="ml-3 border-l border-line pl-1">
          <DocumentTree node={folder} selectedName={selectedName} selectedFolder={selectedFolder}
            expanded={expanded} onFolder={onFolder} onDocument={onDocument} />
        </div> : null}
      </div>;
    })}
    {node.documents.map((document) => <button key={document.name} type="button" onClick={() => onDocument(document.name)}
      className={classNames("mb-0.5 flex w-full items-center gap-1.5 rounded px-2 py-1.5 text-left",
        document.name === selectedName ? "bg-white text-accent shadow-sm" : "text-ink-muted hover:bg-white")}>
      <FileText className="h-3.5 w-3.5 shrink-0" />
      <span className="min-w-0 flex-1"><strong className="block truncate text-[10.5px]">{baseName(document.name)}</strong>
        <span className="block text-[8px] text-ink-subtle">{formatBytes(document.size_bytes)}</span></span>
    </button>)}
  </>;
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

function IconButton({ label, onClick, disabled = false, danger = false, children }: { label: string; onClick: () => void; disabled?: boolean; danger?: boolean; children: React.ReactNode }) {
  return <button type="button" onClick={onClick} disabled={disabled} aria-label={label} title={label}
    className={classNames("flex h-8 w-8 shrink-0 items-center justify-center rounded-md border transition disabled:cursor-not-allowed disabled:opacity-40",
      danger ? "border-danger-ring bg-white text-danger hover:bg-danger-soft" : "border-line bg-white text-ink-muted hover:bg-app-soft hover:text-ink")}>{children}</button>;
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
