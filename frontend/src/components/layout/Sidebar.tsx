import { createPortal } from "react-dom";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Activity, Check, ChevronDown, ChevronRight, Folder, FolderOpen, LayoutGrid, LogOut, MessageSquarePlus, MoreHorizontal, Pencil, Plus, Search, Settings as SettingsIcon, ShieldCheck, Trash2, UserRound, X } from "lucide-react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { api, apiErrorMessage } from "@/lib/api";
import { classNames, timeAgo } from "@/lib/utils";
import type { ConversationRecord } from "@/types";
import { useMockSession } from "@/lib/mockSession";
import { useAuth } from "@/lib/auth";
import { useWorkspace, workspaceChatPath, workspaceHomePath } from "@/lib/workspace";

export const CONVERSATIONS_CHANGED_EVENT = "unibot:conversations-changed";

export function notifyConversationsChanged() {
  window.dispatchEvent(new Event(CONVERSATIONS_CHANGED_EVENT));
}

export function Sidebar() {
  const { profile } = useMockSession();
  const { workspaces, activeWorkspaceId, loading: workspacesLoading, error: workspacesError, createWorkspace } = useWorkspace();
  const [conversations, setConversations] = useState<ConversationRecord[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [expandedWorkspaceIds, setExpandedWorkspaceIds] = useState<Set<string>>(new Set());
  const [workspaceDialogOpen, setWorkspaceDialogOpen] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();

  const load = useCallback(async () => {
    try {
      const records = await api.get<ConversationRecord[]>(
        `/conversations?user_id=${encodeURIComponent(profile.actorUserId)}&tenant_id=${encodeURIComponent(profile.tenantId)}`,
      );
      setConversations(records);
      setError(null);
    } catch (loadError) {
      setError(apiErrorMessage(loadError));
    } finally {
      setLoading(false);
    }
  }, [profile.actorUserId, profile.tenantId]);

  useEffect(() => {
    void load();
    const refresh = () => void load();
    window.addEventListener(CONVERSATIONS_CHANGED_EVENT, refresh);
    return () => window.removeEventListener(CONVERSATIONS_CHANGED_EVENT, refresh);
  }, [load]);

  const filtered = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return conversations;
    return conversations.filter((conversation) => {
      const preview = conversation.messages.at(-1)?.content ?? "";
      return `${conversation.title} ${preview}`.toLowerCase().includes(normalized);
    });
  }, [conversations, query]);

  const conversationsByWorkspace = useMemo(() => {
    const grouped = new Map<string, ConversationRecord[]>();
    for (const workspace of workspaces) grouped.set(workspace.id, []);
    for (const conversation of filtered) {
      if (!conversation.workspace_id || !grouped.has(conversation.workspace_id)) continue;
      grouped.get(conversation.workspace_id)?.push(conversation);
    }
    return grouped;
  }, [filtered, workspaces]);

  const independentConversations = useMemo(
    () => filtered.filter((conversation) => !conversation.workspace_id),
    [filtered],
  );

  useEffect(() => {
    const preferred = activeWorkspaceId ?? workspaces[0]?.id;
    if (!preferred) return;
    setExpandedWorkspaceIds((current) => {
      if (current.has(preferred)) return current;
      const next = new Set(current);
      next.add(preferred);
      return next;
    });
  }, [activeWorkspaceId, workspaces]);

  function startConversation(workspaceId: string | null = null) {
    const target = workspaceChatPath(workspaceId);
    if (location.pathname === target) {
      window.dispatchEvent(new Event("unibot:new-conversation"));
    } else {
      navigate(target);
    }
  }

  async function deleteConversation(conversation: ConversationRecord) {
    setDeleting(true);
    try {
      await api.delete(`/conversations/${conversation.id}`);
      setPendingDelete(null);
      if (location.pathname === workspaceChatPath(conversation.workspace_id, conversation.id)) {
        navigate(workspaceChatPath(conversation.workspace_id));
      }
      await load();
    } catch (deleteError) {
      setError(apiErrorMessage(deleteError));
    } finally {
      setDeleting(false);
    }
  }

  return (
    <aside className="relative z-20 w-16 md:w-[220px] shrink-0 h-full bg-sidebar-bg text-ink-onDark flex flex-col dark-scroll">
      <div className="flex flex-col items-center gap-2 px-3 pt-4 pb-3 md:flex-row md:justify-between md:px-4">
        <Brand />
        <NewConversationMenu
          onNewConversation={() => startConversation(null)}
          onNewWorkspace={() => setWorkspaceDialogOpen(true)}
        />
      </div>

      <div className="hidden px-4 pb-2 md:block">
        <label className="flex items-center gap-2 h-9 rounded-lg px-2.5 bg-sidebar-bg border border-sidebar-border focus-within:border-accent">
          <Search className="w-3.5 h-3.5 text-ink-onDarkMuted" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            type="search"
            placeholder="搜索对话"
            aria-label="搜索对话"
            className="flex-1 min-w-0 bg-transparent text-[12.5px] placeholder:text-ink-onDarkMuted/70 text-ink-inverse outline-none"
          />
        </label>
      </div>

      <nav className="hidden md:flex flex-col flex-1 min-h-0 px-3 pb-4" aria-label="对话列表">
        <div className="flex-1 min-h-0 overflow-y-auto space-y-4">
          {loading ? <SkeletonList /> : null}
          {!loading && error ? (
            <div className="rounded-lg border border-danger/30 bg-danger/10 p-3 text-[11.5px] text-red-200">
              <p>无法连接后端</p>
              <button type="button" onClick={() => void load()} className="mt-2 font-bold underline">
                重试
              </button>
            </div>
          ) : null}
          {!loading && !error && filtered.length === 0 && workspaces.length === 0 ? (
            <div className="px-2 py-8 text-center text-[11.5px] text-ink-onDarkMuted">
              {query ? "没有匹配的对话" : "还没有对话"}
            </div>
          ) : null}
          <section aria-label="工作区列表">
            <div className="mb-1.5 flex items-center gap-2 px-1.5 text-[10px] font-bold uppercase tracking-[0.12em] text-ink-onDarkMuted">
              <span>工作区</span>
              <span className="rounded-full bg-white/5 px-1.5 py-0.5">{workspaces.length}</span>
              <button
                type="button"
                onClick={() => setWorkspaceDialogOpen(true)}
                className="ml-auto flex h-6 w-6 items-center justify-center rounded-md text-ink-onDarkMuted transition hover:bg-white/10 hover:text-white"
                aria-label="创建工作区"
                title="创建工作区"
              >
                <Plus className="h-3.5 w-3.5" />
              </button>
            </div>
            {workspacesLoading ? <div className="mx-1 h-9 animate-pulse rounded-lg bg-white/5" /> : null}
            {!workspacesLoading && workspacesError ? <p className="px-1.5 py-2 text-[10px] text-red-200">{workspacesError}</p> : null}
            <div className="space-y-1.5">
              {workspaces.map((workspace) => (
                <WorkspaceConversationGroup
                  key={workspace.id}
                  workspaceId={workspace.id}
                  name={workspace.name}
                  conversations={conversationsByWorkspace.get(workspace.id) ?? []}
                  expanded={query ? true : expandedWorkspaceIds.has(workspace.id)}
                  onToggle={() => setExpandedWorkspaceIds((current) => {
                    const next = new Set(current);
                    if (next.has(workspace.id)) next.delete(workspace.id);
                    else next.add(workspace.id);
                    return next;
                  })}
                  onNewConversation={() => startConversation(workspace.id)}
                  pendingDelete={pendingDelete}
                  deleting={deleting}
                  onRequestDelete={setPendingDelete}
                  onCancelDelete={() => setPendingDelete(null)}
                  onConfirmDelete={(conversation) => void deleteConversation(conversation)}
                />
              ))}
              {!workspacesLoading && !workspacesError && !workspaces.length ? (
                <button type="button" onClick={() => setWorkspaceDialogOpen(true)} className="w-full rounded-lg border border-dashed border-sidebar-border px-3 py-3 text-left text-[10.5px] text-ink-onDarkMuted hover:bg-sidebar-hover">
                  创建第一个工作区
                </button>
              ) : null}
            </div>
          </section>

          <section aria-label="独立对话">
            <div className="mb-1.5 flex items-center gap-2 px-1.5 text-[10px] font-bold uppercase tracking-[0.12em] text-ink-onDarkMuted">
              <span>独立对话</span>
              <span className="rounded-full bg-white/5 px-1.5 py-0.5">{independentConversations.length}</span>
              <button type="button" onClick={() => startConversation(null)} className="ml-auto flex h-6 w-6 items-center justify-center rounded-md text-ink-onDarkMuted transition hover:bg-white/10 hover:text-white" aria-label="新建独立对话" title="新建独立对话">
                <Plus className="h-3.5 w-3.5" />
              </button>
            </div>
            <div className="space-y-1.5">
              {independentConversations.map((conversation) => (
                <ConversationLink
                  key={conversation.id}
                  conversation={conversation}
                  confirmingDelete={pendingDelete === conversation.id}
                  deleting={deleting}
                  onRequestDelete={() => setPendingDelete(conversation.id)}
                  onCancelDelete={() => setPendingDelete(null)}
                  onConfirmDelete={() => void deleteConversation(conversation)}
                />
              ))}
              {!loading && !independentConversations.length ? <p className="px-2 py-2 text-[10px] text-ink-onDarkMuted/70">暂无独立对话</p> : null}
            </div>
          </section>
        </div>
      </nav>

      <UserAccount />
      <FooterUtility />
      {workspaceDialogOpen ? (
        <WorkspaceCreateDialog
          onClose={() => setWorkspaceDialogOpen(false)}
          onCreate={async (input) => {
            const workspace = await createWorkspace(input);
            setExpandedWorkspaceIds((current) => new Set(current).add(workspace.id));
            setWorkspaceDialogOpen(false);
            navigate(workspaceHomePath(workspace.id));
          }}
        />
      ) : null}
    </aside>
  );
}

function Brand() {
  return (
    <NavLink to="/chat" className="flex items-center justify-center gap-2.5 md:justify-start" aria-label="Unibot 首页">
      <img src="/unibot-icon-v2.png" alt="" className="h-8 w-8 rounded-lg shadow-soft" />
      <div className="hidden md:block">
        <div className="text-ink-inverse text-[15px] font-extrabold tracking-tight">Unibot</div>
        <div className="text-[10px] text-ink-onDarkMuted">智能体运行平台</div>
      </div>
    </NavLink>
  );
}

function NewConversationMenu({
  onNewConversation,
  onNewWorkspace,
}: {
  onNewConversation: () => void;
  onNewWorkspace: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [menuPos, setMenuPos] = useState<{ top: number; left: number } | null>(null);
  const btnRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!open) return;
    function onEscape(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("keydown", onEscape);
    return () => document.removeEventListener("keydown", onEscape);
  }, [open]);

  function toggleMenu() {
    if (!open) {
      const rect = btnRef.current?.getBoundingClientRect();
      if (rect) setMenuPos({ top: rect.bottom + 2, left: rect.right + 2 });
    }
    setOpen((c) => !c);
  }

  return (
    <div className="relative">
      <button
        ref={btnRef}
        type="button"
        onClick={toggleMenu}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="新建"
        title="新建"
        className="flex h-8 w-8 items-center justify-center rounded-lg border border-sidebar-border text-ink-onDark transition-colors hover:border-accent hover:bg-sidebar-hover hover:text-white"
      >
        <Plus className="h-4 w-4" />
      </button>
      {open && menuPos
        ? createPortal(
            <>
              <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
              <div
                role="menu"
                aria-label="新建菜单"
                className="fixed z-50 w-36 overflow-hidden rounded-lg border border-line bg-white py-1 shadow-card"
                style={{ top: menuPos.top, left: menuPos.left }}
              >
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setOpen(false);
                    onNewConversation();
                  }}
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-[12px] font-semibold text-ink transition-colors hover:bg-app-soft"
                >
                  <MessageSquarePlus className="h-3.5 w-3.5 text-ink-muted" />
                  新建独立对话
                </button>
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setOpen(false);
                    onNewWorkspace();
                  }}
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-[12px] font-semibold text-ink transition-colors hover:bg-app-soft"
                >
                  <Folder className="h-3.5 w-3.5 text-ink-muted" />
                  新建工作区
                </button>
              </div>
            </>,
            document.body,
          )
        : null}
    </div>
  );
}

function WorkspaceConversationGroup({
  workspaceId,
  name,
  conversations,
  expanded,
  onToggle,
  onNewConversation,
  pendingDelete,
  deleting,
  onRequestDelete,
  onCancelDelete,
  onConfirmDelete,
}: {
  workspaceId: string;
  name: string;
  conversations: ConversationRecord[];
  expanded: boolean;
  onToggle: () => void;
  onNewConversation: () => void;
  pendingDelete: string | null;
  deleting: boolean;
  onRequestDelete: (conversationId: string) => void;
  onCancelDelete: () => void;
  onConfirmDelete: (conversation: ConversationRecord) => void;
}) {
  const location = useLocation();
  const active = location.pathname.startsWith(`${workspaceHomePath(workspaceId)}/`) || location.pathname === workspaceHomePath(workspaceId);
  return (
    <div className="rounded-lg border border-sidebar-border/80 bg-sidebar-bg">
      <div className={classNames("group flex h-9 items-center gap-1 rounded-lg px-1.5", active && "bg-sidebar-hover")}>
        <button type="button" onClick={onToggle} className="flex h-7 w-6 shrink-0 items-center justify-center rounded text-ink-onDarkMuted hover:bg-white/10 hover:text-white" aria-label={`${expanded ? "收起" : "展开"}${name}`}>
          {expanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
        </button>
        <NavLink to={workspaceHomePath(workspaceId)} className="flex min-w-0 flex-1 items-center gap-1.5 text-[11.5px] font-bold text-ink-onDark" title={name}>
          {active ? <FolderOpen className="h-3.5 w-3.5 shrink-0 text-blue-300" /> : <Folder className="h-3.5 w-3.5 shrink-0 text-ink-onDarkMuted" />}
          <span className="truncate">{name}</span>
          <span className="ml-auto shrink-0 rounded-full bg-white/5 px-1.5 py-0.5 text-[9px] font-normal text-ink-onDarkMuted">{conversations.length}</span>
        </NavLink>
        <button type="button" onClick={onNewConversation} className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-ink-onDarkMuted opacity-0 transition hover:bg-white/10 hover:text-white group-hover:opacity-100 focus-visible:opacity-100" aria-label={`在 ${name} 新建对话`} title="新建对话">
          <Plus className="h-3.5 w-3.5" />
        </button>
      </div>
      {expanded ? (
        <div className="space-y-1 border-t border-sidebar-border/70 px-1.5 py-1.5">
          {conversations.map((conversation) => (
            <ConversationLink
              key={conversation.id}
              conversation={conversation}
              confirmingDelete={pendingDelete === conversation.id}
              deleting={deleting}
              compact
              onRequestDelete={() => onRequestDelete(conversation.id)}
              onCancelDelete={onCancelDelete}
              onConfirmDelete={() => onConfirmDelete(conversation)}
            />
          ))}
          {!conversations.length ? <p className="px-2 py-1.5 text-[9.5px] text-ink-onDarkMuted/70">暂无对话</p> : null}
        </div>
      ) : null}
    </div>
  );
}

function WorkspaceCreateDialog({
  onClose,
  onCreate,
}: {
  onClose: () => void;
  onCreate: (input: { name: string; description?: string }) => Promise<void>;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const onEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !saving) onClose();
    };
    document.addEventListener("keydown", onEscape);
    return () => document.removeEventListener("keydown", onEscape);
  }, [onClose, saving]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!name.trim() || saving) return;
    setSaving(true);
    setError(null);
    try {
      await onCreate({ name: name.trim(), description: description.trim() || undefined });
    } catch (createError) {
      setError(apiErrorMessage(createError));
      setSaving(false);
    }
  }

  return createPortal(
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-slate-950/45 p-4" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !saving) onClose(); }}>
      <form onSubmit={(event) => void submit(event)} role="dialog" aria-modal="true" aria-label="创建工作区" className="w-full max-w-md rounded-xl border border-line bg-white p-5 text-ink shadow-xl">
        <div className="flex items-start gap-3">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-accent-soft text-accent"><FolderPlusIcon /></span>
          <div className="min-w-0 flex-1">
            <h2 className="text-[15px] font-extrabold">创建工作区</h2>
            <p className="mt-1 text-[11.5px] text-ink-muted">工作区内的对话和产物会共享同一份 NAS 存储空间。</p>
          </div>
          <button type="button" onClick={onClose} disabled={saving} className="flex h-8 w-8 items-center justify-center rounded-lg text-ink-muted hover:bg-app-soft hover:text-ink" aria-label="关闭创建工作区"><X className="h-4 w-4" /></button>
        </div>
        <label className="mt-4 block text-[11.5px] font-bold text-ink">名称
          <input value={name} onChange={(event) => setName(event.target.value)} autoFocus required maxLength={160} placeholder="例如：产品发布计划" className="input-soft mt-1.5" />
        </label>
        <label className="mt-3 block text-[11.5px] font-bold text-ink">描述
          <textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={3} maxLength={2000} placeholder="说明这个工作区要完成的目标（可选）" className="input-soft mt-1.5 resize-none" />
        </label>
        {error ? <p className="mt-3 rounded-lg border border-danger-ring bg-danger-soft px-3 py-2 text-[11.5px] text-danger-deep">{error}</p> : null}
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" onClick={onClose} disabled={saving} className="btn-outline">取消</button>
          <button type="submit" disabled={saving || !name.trim()} className="btn-primary disabled:opacity-50">{saving ? "创建中…" : "创建"}</button>
        </div>
      </form>
    </div>,
    document.body,
  );
}

function FolderPlusIcon() {
  return <Plus className="h-5 w-5" />;
}

function ConversationLink({
  conversation,
  confirmingDelete,
  deleting,
  onRequestDelete,
  onCancelDelete,
  onConfirmDelete,
  compact = false,
}: {
  conversation: ConversationRecord;
  confirmingDelete: boolean;
  deleting: boolean;
  onRequestDelete: () => void;
  onCancelDelete: () => void;
  onConfirmDelete: () => void;
  compact?: boolean;
}) {
  const preview = conversation.messages.at(-1)?.content || "等待第一条消息";
  const title = conversation.title === "New conversation" ? "新对话" : conversation.title;
  const [menuOpen, setMenuOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [titleDraft, setTitleDraft] = useState(title);
  const menuBtnRef = useRef<HTMLButtonElement | null>(null);
  const [menuPos, setMenuPos] = useState<{ top: number; left: number } | null>(null);

  function openMenu(e: React.MouseEvent) {
    e.preventDefault();
    const rect = menuBtnRef.current?.getBoundingClientRect();
    if (rect) setMenuPos({ top: rect.bottom + 2, left: rect.right + 2 });
    setMenuOpen(true);
  }

  async function saveTitle() {
    if (!titleDraft.trim()) return;
    try {
      await api.patch(`/conversations/${conversation.id}`, { title: titleDraft.trim() });
      setRenaming(false);
      notifyConversationsChanged();
    } catch {
      // ignore
    }
  }

  return (
    <div
      className="relative group"
      data-testid={`conversation-row-${conversation.id}`}
    >
      <NavLink
        to={workspaceChatPath(conversation.workspace_id, conversation.id)}
        className={({ isActive }) => classNames(
          classNames("block rounded-lg border pr-10 transition-colors", compact ? "px-2 py-2" : "px-3 py-2.5"),
          isActive ? "bg-sidebar-active border-transparent" : "bg-sidebar-bg border-sidebar-border hover:bg-sidebar-hover",
        )}
      >
        {({ isActive }) => (
          <>
            <div className="flex items-center gap-1.5">
              <span className={classNames("h-1.5 w-1.5 shrink-0 rounded-full", conversation.run_status === "running" ? "animate-pulse bg-warning" : isActive ? "bg-white" : "bg-success")} />
              <div className="min-w-0 flex-1 truncate text-[12.5px] font-semibold text-ink-onDark">{renaming ? "" : title}</div>
              <span className={classNames("shrink-0 text-[10px] transition-opacity group-hover:opacity-0 group-focus-within:opacity-0", isActive ? "text-white/70" : "text-ink-onDarkMuted")}>
                {timeAgo(conversation.updated_at)}
              </span>
            </div>
            {renaming ? (
              <div className="mt-1 flex items-center gap-1" onClick={(e) => e.preventDefault()}>
                <input
                  value={titleDraft}
                  aria-label="对话标题"
                  onChange={(e) => setTitleDraft(e.target.value)}
                  className="flex-1 min-w-0 rounded bg-sidebar-surface border border-sidebar-border px-2 py-0.5 text-[12px] text-ink-onDark outline-none focus:border-accent"
                  autoFocus
                  onKeyDown={(e) => {
                    if (e.key === "Enter") { e.preventDefault(); void saveTitle(); }
                    if (e.key === "Escape") { e.preventDefault(); setRenaming(false); }
                  }}
                />
                <button type="button" onClick={() => void saveTitle()} className="rounded p-0.5 text-success hover:bg-white/10"><Check className="h-3.5 w-3.5" /></button>
                <button type="button" onClick={() => setRenaming(false)} className="rounded p-0.5 text-ink-onDarkMuted hover:bg-white/10"><X className="h-3.5 w-3.5" /></button>
              </div>
            ) : (
              <div className={classNames("mt-1 truncate text-[10.5px]", isActive ? "text-white/75" : "text-ink-onDarkMuted/70")}>{preview}</div>
            )}
          </>
        )}
      </NavLink>

      <button
        ref={menuBtnRef}
        type="button"
        onClick={openMenu}
        className={classNames(
          "absolute right-2 top-1/2 z-10 flex h-7 w-7 -translate-y-1/2 items-center justify-center rounded-md text-ink-onDarkMuted transition-[color,background-color,opacity] hover:bg-white/10 hover:text-white focus-visible:pointer-events-auto focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-ring group-hover:pointer-events-auto group-hover:opacity-100 group-focus-within:pointer-events-auto group-focus-within:opacity-100",
          menuOpen ? "pointer-events-auto opacity-100" : "pointer-events-none opacity-0",
        )}
        aria-label="更多操作"
        aria-haspopup="menu"
        aria-expanded={menuOpen}
      >
        <MoreHorizontal className="h-4 w-4" />
      </button>

      {confirmingDelete ? (
        <div className="mt-1 flex items-center gap-1 rounded-lg border border-danger/30 bg-danger/10 px-2 py-1.5 text-[10px] text-red-100">
          <span>确认删除？</span><span className="flex-1" />
          <button type="button" onClick={onCancelDelete} disabled={deleting} aria-label="取消删除"><X className="h-3.5 w-3.5" /></button>
          <button type="button" onClick={onConfirmDelete} disabled={deleting} aria-label={`确认删除 ${title}`}><Check className="h-3.5 w-3.5" /></button>
        </div>
      ) : null}

      {menuOpen && menuPos
        ? createPortal(
            <>
              <div className="fixed inset-0 z-40" onClick={() => setMenuOpen(false)} />
              <div
                role="menu"
                aria-label={`${title} 对话操作`}
                className="fixed z-50 w-32 overflow-hidden rounded-lg border border-line bg-white py-1 shadow-card"
                style={{ top: menuPos.top, left: menuPos.left }}
              >
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => { setMenuOpen(false); setRenaming(true); setTitleDraft(title); }}
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-[12px] text-ink hover:bg-app-soft"
                >
                  <Pencil className="h-3.5 w-3.5" />重命名
                </button>
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => { setMenuOpen(false); onRequestDelete(); }}
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-[12px] text-danger hover:bg-danger-soft"
                >
                  <Trash2 className="h-3.5 w-3.5" />删除
                </button>
              </div>
            </>,
            document.body,
          )
        : null}
    </div>
  );
}

function SkeletonList() {
  return (
    <div className="space-y-2">
      {[0, 1, 2].map((item) => (
        <div key={item} className="rounded-lg p-3 border border-sidebar-border">
          <div className="h-3 w-28 rounded bg-white/5" />
          <div className="mt-2 h-2.5 w-36 rounded bg-white/5" />
        </div>
      ))}
    </div>
  );
}

function FooterUtility() {
  const { profile, isAdmin, toggleRole } = useMockSession();
  const { user, config } = useAuth();
  const canAccessAdmin = config.auth_required ? Boolean(user?.is_admin) : true;
  return (
    <div className="border-t border-sidebar-border p-2 md:p-3">
      {!config.auth_required ? (
        <button
          type="button"
          onClick={toggleRole}
          aria-label={`切换身份，当前${profile.roleLabel}`}
          className="mb-2 flex h-10 w-full items-center justify-center gap-2 rounded-lg border border-sidebar-border text-ink-onDark transition-colors hover:bg-sidebar-hover md:justify-start md:px-2"
        >
          <span className="flex h-6 w-6 items-center justify-center rounded-md bg-white/10 text-[10px] font-bold">{profile.initials}</span>
          <span className="hidden min-w-0 flex-1 text-left md:block"><span className="block truncate text-[10.5px] font-semibold">{profile.name}</span><span className="block truncate text-[9px] text-ink-onDarkMuted">Mock · {profile.roleLabel}</span></span>
          {isAdmin ? <ShieldCheck className="hidden h-3.5 w-3.5 text-blue-300 md:block" /> : <UserRound className="hidden h-3.5 w-3.5 text-ink-onDarkMuted md:block" />}
        </button>
      ) : null}
      <div className="grid grid-cols-1 gap-2 md:grid-cols-4">
        <FooterButton to="/apps" label="应用" icon={<LayoutGrid className="h-4 w-4" />} />
        <FooterButton to="/settings" label="设置" icon={<SettingsIcon className="h-4 w-4" />} />
        <FooterButton to="/obs" label="OBS" icon={<Activity className="h-4 w-4" />} />
        {canAccessAdmin ? <FooterButton to="/admin/observability" label="管理" icon={<ShieldCheck className="h-4 w-4" />} /> : null}
      </div>
    </div>
  );
}

function UserAccount() {
  const { user, config, logout } = useAuth();
  const navigate = useNavigate();
  if (!user) return null;
  return (
    <div className="border-t border-sidebar-border px-2 py-2 md:px-3">
      <div className="flex items-center justify-center gap-2 rounded-lg px-1.5 py-1.5 md:justify-start">
        {user.avatar_url ? (
          <img src={user.avatar_url} alt="" className="h-8 w-8 shrink-0 rounded-full object-cover" referrerPolicy="no-referrer" />
        ) : (
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-white/10 text-ink-onDark">
            <UserRound className="h-4 w-4" />
          </span>
        )}
        <div className="hidden min-w-0 flex-1 md:block">
          <div className="truncate text-[11.5px] font-bold text-ink-onDark">{user.name}</div>
          <div className="truncate text-[9.5px] text-ink-onDarkMuted">{config.auth_required ? user.email : "本地模式"}</div>
        </div>
        {config.auth_required ? (
          <button
            type="button"
            aria-label="退出登录"
            title="退出登录"
            onClick={() => void logout().then(() => navigate("/login", { replace: true }))}
            className="hidden h-8 w-8 shrink-0 items-center justify-center rounded-lg text-ink-onDarkMuted transition hover:bg-sidebar-hover hover:text-white md:flex"
          >
            <LogOut className="h-3.5 w-3.5" />
          </button>
        ) : null}
      </div>
    </div>
  );
}

function FooterButton({ to, label, icon }: { to: string; label: string; icon: React.ReactNode }) {
  return (
    <NavLink
      to={to}
      aria-label={label}
      title={label}
      className={({ isActive }) =>
        classNames(
          "h-10 rounded-lg flex items-center justify-center transition-colors",
          isActive ? "bg-sidebar-active text-white" : "text-ink-onDark hover:bg-sidebar-hover",
        )
      }
    >
      {icon}
    </NavLink>
  );
}
