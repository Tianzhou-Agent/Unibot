import { useEffect, useMemo, useState, type FormEvent } from "react";
import {
  Check,
  Code2,
  Columns2,
  Eye,
  FilePlus2,
  FileText,
  Loader2,
  Pencil,
  RefreshCw,
  Save,
  Trash2,
  X,
} from "lucide-react";
import { MarkdownContent } from "@/components/chat/MarkdownContent";
import { api, apiErrorMessage } from "@/lib/api";
import { classNames } from "@/lib/utils";
import type { DocumentListResponse, DocumentRecord, DocumentSummary } from "@/types";

const ACTOR = { user_id: "anonymous", tenant_id: "default" };
type EditorMode = "edit" | "split" | "preview";

export function DocumentWidget({ disabled = false }: { disabled?: boolean }) {
  const [items, setItems] = useState<DocumentSummary[]>([]);
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [content, setContent] = useState("");
  const [savedContent, setSavedContent] = useState("");
  const [newName, setNewName] = useState("");
  const [renameValue, setRenameValue] = useState("");
  const [renaming, setRenaming] = useState(false);
  const [pendingDelete, setPendingDelete] = useState(false);
  const [mode, setMode] = useState<EditorMode>("split");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const dirty = selectedName !== null && content !== savedContent;
  const selected = useMemo(
    () => items.find((item) => item.name === selectedName) ?? null,
    [items, selectedName],
  );

  useEffect(() => {
    void refreshDocuments();
  }, []);

  async function refreshDocuments(preferredName?: string | null) {
    setLoading(true);
    try {
      const response = await api.get<DocumentListResponse>(`/documents?${new URLSearchParams(ACTOR)}`);
      setItems(response.items);
      const target = preferredName && response.items.some((item) => item.name === preferredName)
        ? preferredName
        : response.items[0]?.name ?? null;
      if (target) await loadDocument(target, false);
      else {
        setSelectedName(null);
        setContent("");
        setSavedContent("");
      }
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
      const record = await api.get<DocumentRecord>(
        `/documents/${encodeURIComponent(name)}?${new URLSearchParams(ACTOR)}`,
      );
      setSelectedName(record.name);
      setContent(record.content);
      setSavedContent(record.content);
      setRenameValue(record.name);
      setRenaming(false);
      setPendingDelete(false);
      setError(null);
    } catch (loadError) {
      setError(apiErrorMessage(loadError));
    } finally {
      setLoading(false);
    }
  }

  async function createDocument(event: FormEvent) {
    event.preventDefault();
    const name = newName.trim();
    if (!name || disabled || saving) return;
    setSaving(true);
    try {
      const title = name.replace(/\.md$/i, "");
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

  async function saveDocument() {
    if (!selectedName || disabled || saving || !dirty) return;
    setSaving(true);
    try {
      const record = await api.put<DocumentRecord>(`/documents/${encodeURIComponent(selectedName)}`, {
        ...ACTOR,
        content,
      });
      setContent(record.content);
      setSavedContent(record.content);
      await refreshListOnly();
      setError(null);
    } catch (saveError) {
      setError(apiErrorMessage(saveError));
    } finally {
      setSaving(false);
    }
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
        `/documents/${encodeURIComponent(selectedName)}/rename`,
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
      await api.delete(`/documents/${encodeURIComponent(selectedName)}?${new URLSearchParams(ACTOR)}`);
      setPendingDelete(false);
      await refreshDocuments(null);
    } catch (deleteError) {
      setError(apiErrorMessage(deleteError));
    } finally {
      setSaving(false);
    }
  }

  async function refreshListOnly() {
    const response = await api.get<DocumentListResponse>(`/documents?${new URLSearchParams(ACTOR)}`);
    setItems(response.items);
  }

  return (
    <div className="grid h-full min-h-0 grid-rows-[minmax(180px,32%)_minmax(0,1fr)] overflow-hidden bg-white md:grid-cols-[220px_minmax(0,1fr)] md:grid-rows-1">
      <aside className="flex min-h-0 flex-col border-b border-line bg-app-soft md:border-b-0 md:border-r">
        <form onSubmit={createDocument} className="border-b border-line p-3">
          <label className="text-[10px] font-bold uppercase text-ink-muted">新建文档</label>
          <div className="mt-1.5 flex gap-1.5">
            <input
              value={newName}
              onChange={(event) => setNewName(event.target.value)}
              disabled={disabled || saving}
              placeholder="文件名.md"
              aria-label="新文档名称"
              className="input-soft min-w-0 flex-1 text-[11.5px]"
            />
            <button
              type="submit"
              disabled={disabled || saving || !newName.trim()}
              className="btn-primary h-9 w-9 shrink-0 p-0 disabled:opacity-50"
              aria-label="创建文档"
              title="创建文档"
            >
              <FilePlus2 className="h-4 w-4" />
            </button>
          </div>
        </form>

        <div className="flex items-center border-b border-line px-3 py-2">
          <span className="text-[10.5px] font-bold text-ink-muted">文档 {items.length}</span>
          <button
            type="button"
            onClick={() => void refreshDocuments(selectedName)}
            disabled={loading || saving}
            className="btn-ghost ml-auto h-7 w-7 p-0"
            aria-label="刷新文档列表"
            title="刷新"
          >
            <RefreshCw className={classNames("h-3.5 w-3.5", loading && "animate-spin")} />
          </button>
        </div>

        <div className="max-h-44 min-h-0 flex-1 overflow-y-auto p-2 md:max-h-none" aria-label="文档列表">
          {items.map((item) => (
            <button
              key={item.name}
              type="button"
              onClick={() => void loadDocument(item.name)}
              className={classNames(
                "mb-1 flex w-full items-start gap-2 rounded-md px-2.5 py-2 text-left transition",
                item.name === selectedName ? "bg-white text-accent shadow-sm" : "text-ink-muted hover:bg-white",
              )}
            >
              <FileText className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span className="min-w-0 flex-1">
                <strong className="block truncate text-[11.5px]">{item.name}</strong>
                <span className="mt-0.5 block text-[9px] text-ink-subtle">{formatBytes(item.size_bytes)}</span>
              </span>
            </button>
          ))}
          {!loading && !items.length ? (
            <p className="px-3 py-8 text-center text-[11px] text-ink-muted">暂无 Markdown 文档</p>
          ) : null}
        </div>
      </aside>

      <section className="flex min-h-0 min-w-0 flex-col">
        <header className="flex min-h-12 flex-wrap items-center gap-2 border-b border-line px-3 py-2">
          {renaming && selectedName ? (
            <div className="flex min-w-0 flex-1 items-center gap-1.5">
              <input
                value={renameValue}
                onChange={(event) => setRenameValue(event.target.value)}
                disabled={saving}
                aria-label="重命名文档"
                className="input-soft h-8 min-w-0 max-w-72 text-[11.5px]"
              />
              <IconButton label="确认重命名" onClick={() => void renameDocument()} disabled={!renameValue.trim() || saving}>
                <Check className="h-3.5 w-3.5" />
              </IconButton>
              <IconButton label="取消重命名" onClick={() => setRenaming(false)}>
                <X className="h-3.5 w-3.5" />
              </IconButton>
            </div>
          ) : (
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <h4 className="truncate text-[12.5px] font-extrabold text-ink">{selectedName ?? "选择文档"}</h4>
                {dirty ? <span className="rounded bg-warning-soft px-1.5 py-0.5 text-[9px] font-bold text-warning-deep">未保存</span> : null}
              </div>
              {selected?.modified_at ? (
                <p className="mt-0.5 text-[9px] text-ink-subtle">更新于 {formatDate(selected.modified_at)}</p>
              ) : null}
            </div>
          )}

          {!renaming && selectedName ? (
            <>
              <div className="flex h-8 items-center rounded-md border border-line bg-app-soft p-0.5" aria-label="编辑器视图">
                <ModeButton active={mode === "edit"} label="编辑" onClick={() => setMode("edit")}><Code2 className="h-3.5 w-3.5" /></ModeButton>
                <ModeButton active={mode === "split"} label="分栏" onClick={() => setMode("split")}><Columns2 className="h-3.5 w-3.5" /></ModeButton>
                <ModeButton active={mode === "preview"} label="预览" onClick={() => setMode("preview")}><Eye className="h-3.5 w-3.5" /></ModeButton>
              </div>
              <IconButton label="重命名" onClick={() => setRenaming(true)} disabled={saving}>
                <Pencil className="h-3.5 w-3.5" />
              </IconButton>
              <IconButton label="保存" onClick={() => void saveDocument()} disabled={!dirty || saving || disabled} primary>
                {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
              </IconButton>
              {pendingDelete ? (
                <div className="flex items-center gap-1">
                  <button type="button" onClick={() => setPendingDelete(false)} className="btn-outline h-8 px-2 text-[10px]">取消</button>
                  <button type="button" onClick={() => void deleteDocument()} className="btn-danger-outline h-8 px-2 text-[10px]">确认删除</button>
                </div>
              ) : (
                <IconButton label="删除" onClick={() => setPendingDelete(true)} disabled={saving || disabled} danger>
                  <Trash2 className="h-3.5 w-3.5" />
                </IconButton>
              )}
            </>
          ) : null}
        </header>

        {error ? (
          <div className="flex items-center gap-2 border-b border-danger-ring bg-danger-soft px-3 py-2 text-[10.5px] text-danger-deep">
            <span className="min-w-0 flex-1">{error}</span>
            <button type="button" onClick={() => setError(null)} aria-label="关闭错误"><X className="h-3.5 w-3.5" /></button>
          </div>
        ) : null}

        <div className="min-h-0 flex-1 bg-white">
          {loading && !selectedName ? (
            <div className="flex h-full items-center justify-center text-ink-muted"><Loader2 className="h-5 w-5 animate-spin" /></div>
          ) : null}
          {!loading && !selectedName ? (
            <div className="flex h-full min-h-72 flex-col items-center justify-center text-center text-ink-muted">
              <FileText className="h-8 w-8 text-ink-subtle" />
              <p className="mt-2 text-[11.5px] font-semibold">新建或选择一个 Markdown 文档</p>
            </div>
          ) : null}
          {selectedName ? (
            <div className={classNames("grid h-full min-h-0", mode === "split" && "grid-rows-2 lg:grid-cols-2 lg:grid-rows-1")}>
              {mode !== "preview" ? (
                <textarea
                  value={content}
                  onChange={(event) => setContent(event.target.value)}
                  disabled={disabled || saving}
                  spellCheck={false}
                  aria-label="Markdown 编辑器"
                  className={classNames(
                    "h-full min-h-0 w-full resize-none bg-white p-4 font-mono text-[12px] leading-6 text-ink outline-none",
                    mode === "split" && "border-b border-line lg:border-b-0 lg:border-r",
                  )}
                />
              ) : null}
              {mode !== "edit" ? (
                <div className="h-full min-h-0 overflow-y-auto bg-app-soft p-5" aria-label="Markdown 预览">
                  <MarkdownContent content={content || "_空文档_"} />
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
      </section>
    </div>
  );
}

function IconButton({
  label,
  onClick,
  disabled = false,
  primary = false,
  danger = false,
  children,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  primary?: boolean;
  danger?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      title={label}
      className={classNames(
        "flex h-8 w-8 shrink-0 items-center justify-center rounded-md border transition disabled:cursor-not-allowed disabled:opacity-40",
        primary && "border-accent bg-accent text-white hover:bg-accent-hover",
        danger && "border-danger-ring bg-white text-danger hover:bg-danger-soft",
        !primary && !danger && "border-line bg-white text-ink-muted hover:bg-app-soft hover:text-ink",
      )}
    >
      {children}
    </button>
  );
}

function ModeButton({ active, label, onClick, children }: { active: boolean; label: string; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      className={classNames(
        "flex h-6 w-7 items-center justify-center rounded text-ink-muted",
        active && "bg-white text-accent shadow-sm",
      )}
    >
      {children}
    </button>
  );
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  return `${(value / 1024).toFixed(value < 10 * 1024 ? 1 : 0)} KB`;
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}
