import { createPortal } from "react-dom";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Activity, Bot, Check, ChevronDown, ChevronRight, Folder, FolderOpen, LayoutGrid, ListChecks, LogOut, MessageSquare, MoreHorizontal, PanelLeftOpen, Pencil, Plus, Puzzle, Search, Settings as SettingsIcon, ShieldCheck, Trash2, UserRound, X } from "lucide-react";
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
  const { profile, toggleRole } = useMockSession();
  const { workspaces, activeWorkspaceId, loading: workspacesLoading, error: workspacesError, createWorkspace } = useWorkspace();
  const [conversations, setConversations] = useState<ConversationRecord[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [expandedWorkspaceIds, setExpandedWorkspaceIds] = useState<Set<string>>(new Set());
  const [workspaceDialogOpen, setWorkspaceDialogOpen] = useState(false);
  const [navigationOpen, setNavigationOpen] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const chatTabActive = isChatNavigationPath(location.pathname);
  const fileTabActive = isFileNavigationPath(location.pathname);
  const compactRoute = /^\/chat(?:\/|$)/.test(location.pathname)
    || /^\/canvas\//.test(location.pathname)
    || /^\/workspaces\/[^/]+\/(?:chat|canvas)(?:\/|$)/.test(location.pathname);

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
  const pendingDeleteConversation = conversations.find((conversation) => conversation.id === pendingDelete) ?? null;

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
    setDeleteError(null);
    try {
      await api.delete(`/conversations/${conversation.id}`);
      setPendingDelete(null);
      if (location.pathname === workspaceChatPath(conversation.workspace_id, conversation.id)) {
        navigate(workspaceChatPath(conversation.workspace_id));
      }
      await load();
    } catch (deleteError) {
      setDeleteError(apiErrorMessage(deleteError));
    } finally {
      setDeleting(false);
    }
  }

  if (compactRoute && !navigationOpen) {
    return (
      <>
      <IconRail
        activeWorkspaceId={activeWorkspaceId}
        initials={profile.initials}
        roleLabel={profile.roleLabel}
        onExpand={() => setNavigationOpen(true)}
        onNewTask={() => startConversation(activeWorkspaceId)}
        onNewWorkspace={() => setWorkspaceDialogOpen(true)}
        onToggleRole={toggleRole}
      />
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
      </>
    );
  }

  return (
    <aside className="relative z-20 flex h-full w-[264px] shrink-0 flex-col border-r border-sidebar-border bg-sidebar-bg text-ink">
      <div className="flex items-center justify-between gap-2 px-[18px] pb-2 pt-[18px]">
        <Brand />
        {navigationOpen ? (
          <button type="button" onClick={() => setNavigationOpen(false)} className="flex h-7 w-7 items-center justify-center rounded-lg text-ink-subtle hover:bg-sidebar-hover hover:text-ink" aria-label="收起导航">
            <PanelLeftOpen className="h-4 w-4 rotate-180" />
          </button>
        ) : null}
      </div>

      <div className="px-3 pb-2">
        <button type="button" onClick={() => startConversation(activeWorkspaceId)} className="flex h-9 w-full items-center gap-2 rounded-lg bg-ink px-2.5 text-[13px] font-medium text-white hover:bg-black">
          <Plus className="h-3.5 w-3.5" />新任务<span className="ml-auto rounded-[5px] bg-white/15 px-1.5 py-0.5 font-mono text-[10px] text-white/70">⌘K</span>
        </button>
      </div>

      <nav className="space-y-0.5 px-3 pb-2" aria-label="主导航">
        <SidebarNavLink to="/chat" label="对话" active={chatTabActive} icon={<MessageSquare className="h-4 w-4" />} />
        <SidebarNavLink to="/schedules" label="任务" icon={<ListChecks className="h-4 w-4" />} />
        <SidebarNavLink to={activeWorkspaceId ? workspaceHomePath(activeWorkspaceId) : "/chat"} label="文件" active={fileTabActive} icon={<Folder className="h-4 w-4" />} />
        <SidebarNavLink to="/apps" label="插件" icon={<Puzzle className="h-4 w-4" />} />
      </nav>

      <div className="px-3 pb-2">
        <label className="flex h-8 items-center gap-2 rounded-lg border border-sidebar-border bg-white px-2.5 focus-within:border-line-strong">
          <Search className="w-3.5 h-3.5 text-ink-subtle" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            type="search"
            placeholder="搜索对话"
            aria-label="搜索对话"
            className="flex-1 min-w-0 bg-transparent text-[12px] text-ink outline-none placeholder:text-ink-subtle"
          />
        </label>
      </div>

      <nav className="flex min-h-0 flex-1 flex-col px-3 pb-3" aria-label="对话列表">
        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto">
          {loading ? <SkeletonList /> : null}
          {!loading && error ? (
            <div className="rounded-lg border border-danger-ring bg-danger-soft p-3 text-[11.5px] text-danger-deep">
              <p>无法连接后端</p>
              <button type="button" onClick={() => void load()} className="mt-2 font-bold underline">
                重试
              </button>
            </div>
          ) : null}
          {!loading && !error && filtered.length === 0 && workspaces.length === 0 ? (
            <div className="px-2 py-8 text-center text-[11.5px] text-ink-subtle">
              {query ? "没有匹配的对话" : "还没有对话"}
            </div>
          ) : null}
          <section aria-label="工作区列表">
            <div className="mb-1 flex items-center gap-2 px-2 text-[11px] font-medium text-ink-subtle">
              <span>工作区</span>
              <span>{workspaces.length}</span>
              <button
                type="button"
                onClick={() => setWorkspaceDialogOpen(true)}
                className="ml-auto flex h-6 w-6 items-center justify-center rounded-md text-ink-subtle transition hover:bg-sidebar-hover hover:text-ink"
                aria-label="创建工作区"
                title="创建工作区"
              >
                <Plus className="h-3.5 w-3.5" />
              </button>
            </div>
            {workspacesLoading ? <div className="mx-1 h-9 animate-pulse rounded-lg bg-sidebar-hover" /> : null}
            {!workspacesLoading && workspacesError ? <p className="px-1.5 py-2 text-[10px] text-danger">{workspacesError}</p> : null}
            <div className="space-y-0.5">
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
                  onRequestDelete={(conversationId) => {
                    setDeleteError(null);
                    setPendingDelete(conversationId);
                  }}
                />
              ))}
              {!workspacesLoading && !workspacesError && !workspaces.length ? (
                <button type="button" onClick={() => setWorkspaceDialogOpen(true)} className="w-full rounded-lg border border-dashed border-line-strong px-3 py-3 text-left text-[10.5px] text-ink-subtle hover:bg-sidebar-hover">
                  创建第一个工作区
                </button>
              ) : null}
            </div>
          </section>

          <section aria-label="独立对话">
            <div className="mb-1 flex items-center gap-2 px-2 text-[11px] font-medium text-ink-subtle">
              <span>独立对话</span>
              <span>{independentConversations.length}</span>
              <button type="button" onClick={() => startConversation(null)} className="ml-auto flex h-6 w-6 items-center justify-center rounded-md text-ink-subtle transition hover:bg-sidebar-hover hover:text-ink" aria-label="新建独立对话" title="新建独立对话">
                <Plus className="h-3.5 w-3.5" />
              </button>
            </div>
            <div className="space-y-0.5">
              {independentConversations.map((conversation) => (
                <ConversationLink
                  key={conversation.id}
                  conversation={conversation}
                  onRequestDelete={() => {
                    setDeleteError(null);
                    setPendingDelete(conversation.id);
                  }}
                />
              ))}
              {!loading && !independentConversations.length ? <p className="px-2 py-2 text-[10px] text-ink-subtle">暂无独立对话</p> : null}
            </div>
          </section>
        </div>
      </nav>

      <AccountFooter />
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
      {pendingDeleteConversation ? (
        <ConversationDeleteDialog
          conversation={pendingDeleteConversation}
          deleting={deleting}
          error={deleteError}
          onClose={() => {
            setDeleteError(null);
            setPendingDelete(null);
          }}
          onConfirm={() => void deleteConversation(pendingDeleteConversation)}
        />
      ) : null}
    </aside>
  );
}

function Brand() {
  return (
    <NavLink to="/chat" className="flex items-center gap-2.5" aria-label="Unibot 首页">
      <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-accent text-white"><Bot className="h-4 w-4" /></span>
      <span className="text-[14px] font-semibold tracking-tight text-ink">Unibot</span>
    </NavLink>
  );
}

function SidebarNavLink({ to, label, icon, active }: { to: string; label: string; icon: React.ReactNode; active?: boolean }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) => classNames(
        "flex h-8 items-center gap-2.5 rounded-lg px-2.5 text-[13px] transition-colors",
        (active ?? isActive) ? "bg-sidebar-active font-medium text-ink" : "text-ink-muted hover:bg-sidebar-hover hover:text-ink",
      )}
    >
      <span className="text-ink-subtle">{icon}</span>{label}
    </NavLink>
  );
}

function IconRail({
  activeWorkspaceId,
  initials,
  roleLabel,
  onExpand,
  onNewTask,
  onNewWorkspace,
  onToggleRole,
}: {
  activeWorkspaceId: string | null;
  initials: string;
  roleLabel: string;
  onExpand: () => void;
  onNewTask: () => void;
  onNewWorkspace: () => void;
  onToggleRole: () => void;
}) {
  const filePath = activeWorkspaceId ? workspaceHomePath(activeWorkspaceId) : "/chat";
  const location = useLocation();
  const chatActive = isChatNavigationPath(location.pathname);
  const fileActive = isFileNavigationPath(location.pathname);
  return (
    <aside className="relative z-20 flex h-full w-16 shrink-0 flex-col items-center gap-2 border-r border-sidebar-border bg-sidebar-bg py-4" aria-label="快捷导航">
      <button type="button" onClick={onNewTask} className="flex h-9 w-9 items-center justify-center rounded-[10px] bg-accent text-white" aria-label="新任务" title="新任务">
        <Bot className="h-5 w-5" />
      </button>
      <span className="h-3" />
      <RailLink to="/chat" label="对话" active={chatActive} icon={<MessageSquare className="h-[19px] w-[19px]" />} />
      <RailLink to="/schedules" label="任务" icon={<ListChecks className="h-[19px] w-[19px]" />} />
      <RailLink to={filePath} label="文件" active={fileActive} icon={<Folder className="h-[19px] w-[19px]" />} />
      <RailLink to="/apps" label="插件" icon={<Puzzle className="h-[19px] w-[19px]" />} />
      <button type="button" onClick={onNewWorkspace} className="flex h-10 w-10 items-center justify-center rounded-[10px] text-ink-subtle hover:bg-sidebar-hover hover:text-ink" aria-label="创建工作区" title="创建工作区">
        <Plus className="h-[19px] w-[19px]" />
      </button>
      <button type="button" onClick={onExpand} className="flex h-10 w-10 items-center justify-center rounded-[10px] text-ink-subtle hover:bg-sidebar-hover hover:text-ink" aria-label="展开导航" title="展开导航">
        <PanelLeftOpen className="h-[19px] w-[19px]" />
      </button>
      <span className="flex-1" />
      <RailLink to="/settings" label="设置" icon={<SettingsIcon className="h-[19px] w-[19px]" />} />
      <button type="button" onClick={onToggleRole} className="flex h-[30px] w-[30px] items-center justify-center rounded-full border border-line-strong bg-[#D9DEE7] text-[10px] font-semibold text-ink-muted" aria-label={`切换身份，当前${roleLabel}`} title={roleLabel}>{initials}</button>
    </aside>
  );
}

function isChatNavigationPath(pathname: string) {
  return /^\/chat(?:\/|$)/.test(pathname)
    || /^\/workspaces\/[^/]+\/chat(?:\/|$)/.test(pathname);
}

function isFileNavigationPath(pathname: string) {
  return /^\/canvas(?:\/|$)/.test(pathname)
    || /^\/workspaces\/[^/]+\/?$/.test(pathname)
    || /^\/workspaces\/[^/]+\/canvas(?:\/|$)/.test(pathname);
}

function RailLink({ to, label, icon, active }: { to: string; label: string; icon: React.ReactNode; active?: boolean }) {
  return (
    <NavLink
      to={to}
      aria-label={label}
      title={label}
      className={({ isActive }) => classNames(
        "flex h-10 w-10 items-center justify-center rounded-[10px] transition-colors",
        (active ?? isActive) ? "bg-sidebar-active text-ink" : "text-ink-subtle hover:bg-sidebar-hover hover:text-ink",
      )}
    >
      {icon}
    </NavLink>
  );
}

function WorkspaceConversationGroup({
  workspaceId,
  name,
  conversations,
  expanded,
  onToggle,
  onNewConversation,
  onRequestDelete,
}: {
  workspaceId: string;
  name: string;
  conversations: ConversationRecord[];
  expanded: boolean;
  onToggle: () => void;
  onNewConversation: () => void;
  onRequestDelete: (conversationId: string) => void;
}) {
  const location = useLocation();
  const active = location.pathname.startsWith(`${workspaceHomePath(workspaceId)}/`) || location.pathname === workspaceHomePath(workspaceId);
  return (
    <div>
      <div className={classNames("group flex h-9 items-center gap-1 rounded-lg px-1.5", active && "bg-sidebar-hover")}>
        <button type="button" onClick={onToggle} className="flex h-7 w-6 shrink-0 items-center justify-center rounded text-ink-subtle hover:bg-white hover:text-ink" aria-label={`${expanded ? "收起" : "展开"}${name}`}>
          {expanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
        </button>
        <NavLink to={workspaceHomePath(workspaceId)} className="flex min-w-0 flex-1 items-center gap-1.5 text-[13px] font-medium text-ink" title={name}>
          {active ? <FolderOpen className="h-3.5 w-3.5 shrink-0 text-ink" /> : <Folder className="h-3.5 w-3.5 shrink-0 text-ink-muted" />}
          <span className="truncate">{name}</span>
          <span className="ml-auto shrink-0 font-mono text-[10px] font-normal text-ink-subtle">{conversations.length}</span>
        </NavLink>
        <button type="button" onClick={onNewConversation} className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-ink-subtle opacity-0 transition hover:bg-white hover:text-ink group-hover:opacity-100 focus-visible:opacity-100" aria-label={`在 ${name} 新建对话`} title="新建对话">
          <Plus className="h-3.5 w-3.5" />
        </button>
      </div>
      {expanded ? (
        <div className="space-y-0.5 px-1.5 py-1">
          {conversations.map((conversation) => (
            <ConversationLink
              key={conversation.id}
              conversation={conversation}
              compact
              onRequestDelete={() => onRequestDelete(conversation.id)}
            />
          ))}
          {!conversations.length ? <p className="px-8 py-1.5 text-[9.5px] text-ink-subtle">暂无对话</p> : null}
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

function ConversationDeleteDialog({
  conversation,
  deleting,
  error,
  onClose,
  onConfirm,
}: {
  conversation: ConversationRecord;
  deleting: boolean;
  error: string | null;
  onClose: () => void;
  onConfirm: () => void;
}) {
  const title = conversation.title === "New conversation" ? "新对话" : conversation.title;

  useEffect(() => {
    const onEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !deleting) onClose();
    };
    document.addEventListener("keydown", onEscape);
    return () => document.removeEventListener("keydown", onEscape);
  }, [deleting, onClose]);

  return createPortal(
    <div
      className="fixed inset-0 z-[80] flex items-center justify-center bg-slate-950/35 p-4 backdrop-blur-[1px]"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !deleting) onClose();
      }}
    >
      <section
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="delete-conversation-title"
        aria-describedby="delete-conversation-description"
        className="w-full max-w-[420px] overflow-hidden rounded-2xl border border-line-strong bg-white text-ink shadow-[0_18px_50px_rgba(15,17,21,0.16)]"
      >
        <div className="px-6 pb-5 pt-6">
          <div className="flex items-start gap-4">
            <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-danger-soft text-danger">
              <Trash2 className="h-5 w-5" />
            </span>
            <div className="min-w-0 flex-1">
              <h2 id="delete-conversation-title" className="text-[16px] font-semibold tracking-[-0.01em]">删除会话</h2>
              <p id="delete-conversation-description" className="mt-1.5 text-[12px] leading-5 text-ink-muted">
                删除后，此会话及消息将不再出现在对话列表中。
              </p>
            </div>
            <button
              type="button"
              onClick={onClose}
              disabled={deleting}
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-ink-subtle hover:bg-app-soft hover:text-ink disabled:opacity-40"
              aria-label="关闭删除会话"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          <div className="mt-5 flex items-center gap-3 rounded-xl border border-line bg-app-soft px-3.5 py-3">
            <MessageSquare className="h-4 w-4 shrink-0 text-ink-subtle" />
            <span className="min-w-0 flex-1 truncate text-[13px] font-medium" title={title}>{title}</span>
          </div>

          <p className="mt-3 rounded-lg bg-accent-softer px-3 py-2 text-[11px] leading-5 text-ink-muted">
            工作区中已经生成的文件和其他产物不会受到影响。
          </p>

          {error ? (
            <p className="mt-3 rounded-lg border border-danger-ring bg-danger-soft px-3 py-2 text-[11.5px] text-danger-deep" role="alert">
              {error}
            </p>
          ) : null}
        </div>

        <div className="flex justify-end gap-2 border-t border-line bg-app-soft/60 px-6 py-4">
          <button type="button" onClick={onClose} disabled={deleting} autoFocus className="btn-outline disabled:opacity-50">
            取消
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={deleting}
            aria-label={`确认删除 ${title}`}
            className="btn bg-danger text-white hover:bg-danger-deep disabled:cursor-not-allowed disabled:opacity-50"
          >
            {deleting ? "删除中…" : "删除会话"}
          </button>
        </div>
      </section>
    </div>,
    document.body,
  );
}

function ConversationLink({
  conversation,
  onRequestDelete,
  compact = false,
}: {
  conversation: ConversationRecord;
  onRequestDelete: () => void;
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
          classNames("block rounded-lg border border-transparent pr-10 transition-colors", compact ? "py-2 pl-8" : "px-2.5 py-2"),
          isActive ? "bg-sidebar-active" : "hover:bg-sidebar-hover",
        )}
      >
        {({ isActive }) => (
          <>
            <div className="flex items-center gap-1.5">
              {!compact ? <span className={classNames("h-1.5 w-1.5 shrink-0 rounded-full", conversation.run_status === "running" ? "animate-pulse bg-warning" : "bg-success")} /> : null}
              <div className="min-w-0 flex-1 truncate text-[12.5px] font-medium text-ink">{renaming ? "" : title}</div>
              <span className="shrink-0 text-[10px] text-ink-subtle transition-opacity group-hover:opacity-0 group-focus-within:opacity-0">
                {timeAgo(conversation.updated_at)}
              </span>
            </div>
            {renaming ? (
              <div className="mt-1 flex items-center gap-1" onClick={(e) => e.preventDefault()}>
                <input
                  value={titleDraft}
                  aria-label="对话标题"
                  onChange={(e) => setTitleDraft(e.target.value)}
                  className="flex-1 min-w-0 rounded border border-line-strong bg-white px-2 py-0.5 text-[12px] text-ink outline-none focus:border-accent"
                  autoFocus
                  onKeyDown={(e) => {
                    if (e.key === "Enter") { e.preventDefault(); void saveTitle(); }
                    if (e.key === "Escape") { e.preventDefault(); setRenaming(false); }
                  }}
                />
                <button type="button" onClick={() => void saveTitle()} className="rounded p-0.5 text-success hover:bg-white"><Check className="h-3.5 w-3.5" /></button>
                <button type="button" onClick={() => setRenaming(false)} className="rounded p-0.5 text-ink-subtle hover:bg-white"><X className="h-3.5 w-3.5" /></button>
              </div>
            ) : (
              !compact ? <div className="mt-1 truncate text-[10.5px] text-ink-subtle">{preview}</div> : null
            )}
          </>
        )}
      </NavLink>

      <button
        ref={menuBtnRef}
        type="button"
        onClick={openMenu}
        className={classNames(
          "absolute right-2 top-1/2 z-10 flex h-7 w-7 -translate-y-1/2 items-center justify-center rounded-md text-ink-subtle transition-[color,background-color,opacity] hover:bg-white hover:text-ink focus-visible:pointer-events-auto focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-ring group-hover:pointer-events-auto group-hover:opacity-100 group-focus-within:pointer-events-auto group-focus-within:opacity-100",
          menuOpen ? "pointer-events-auto opacity-100" : "pointer-events-none opacity-0",
        )}
        aria-label="更多操作"
        aria-haspopup="menu"
        aria-expanded={menuOpen}
      >
        <MoreHorizontal className="h-4 w-4" />
      </button>

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
        <div key={item} className="rounded-lg p-3">
          <div className="h-3 w-28 rounded bg-sidebar-hover" />
          <div className="mt-2 h-2.5 w-36 rounded bg-sidebar-hover" />
        </div>
      ))}
    </div>
  );
}

function AccountFooter() {
  const { profile, isAdmin, toggleRole } = useMockSession();
  const { user, config, logout } = useAuth();
  const navigate = useNavigate();
  const [menuOpen, setMenuOpen] = useState(false);
  const footerRef = useRef<HTMLDivElement | null>(null);
  const canAccessAdmin = config.auth_required ? Boolean(user?.is_admin) : true;

  useEffect(() => {
    if (!menuOpen) return;
    const closeMenu = (event: MouseEvent) => {
      if (!footerRef.current?.contains(event.target as Node)) setMenuOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenuOpen(false);
    };
    document.addEventListener("mousedown", closeMenu);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("mousedown", closeMenu);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [menuOpen]);

  const displayName = config.auth_required ? user?.name : profile.name;
  const detail = config.auth_required ? user?.email : `Mock · ${profile.roleLabel}`;

  return (
    <div ref={footerRef} className="relative border-t border-sidebar-border p-3">
      <div className="flex h-11 items-center gap-2 rounded-xl px-2 transition-colors hover:bg-sidebar-hover">
        {user?.avatar_url ? (
          <img src={user.avatar_url} alt="" className="h-8 w-8 shrink-0 rounded-full object-cover" referrerPolicy="no-referrer" />
        ) : config.auth_required ? (
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[#D9DEE7] text-ink-muted">
            <UserRound className="h-4 w-4" />
          </span>
        ) : (
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[#D9DEE7] text-[10px] font-bold text-ink-muted">
            {profile.initials}
          </span>
        )}
        <div className="min-w-0 flex-1">
          <div className="truncate text-[11.5px] font-bold text-ink">{displayName}</div>
          <div className="truncate text-[9.5px] text-ink-subtle">{detail}</div>
        </div>
        <button
          type="button"
          onClick={() => setMenuOpen((current) => !current)}
          aria-label="打开用户菜单"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          className={classNames(
            "flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-ink-subtle transition-colors hover:bg-white hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-ring",
            menuOpen && "bg-white text-ink shadow-sm",
          )}
        >
          <MoreHorizontal className="h-4 w-4" />
        </button>
      </div>

      {menuOpen ? (
        <div
          role="menu"
          aria-label="用户菜单"
          className="absolute bottom-[calc(100%+8px)] left-3 right-3 z-50 overflow-hidden rounded-xl border border-line bg-white p-1.5 shadow-[0_12px_32px_rgba(15,17,21,0.14)]"
        >
          <FooterMenuLink to="/apps" label="应用" icon={<LayoutGrid className="h-4 w-4" />} onSelect={() => setMenuOpen(false)} />
          <FooterMenuLink to="/settings" label="设置" icon={<SettingsIcon className="h-4 w-4" />} onSelect={() => setMenuOpen(false)} />
          <FooterMenuLink to="/obs" label="OBS" icon={<Activity className="h-4 w-4" />} onSelect={() => setMenuOpen(false)} />
          {canAccessAdmin ? (
            <FooterMenuLink to="/admin/observability" label="管理" icon={<ShieldCheck className="h-4 w-4" />} onSelect={() => setMenuOpen(false)} />
          ) : null}
          <div className="my-1 border-t border-line" />
          {!config.auth_required ? (
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setMenuOpen(false);
                toggleRole();
              }}
              className="flex h-9 w-full items-center gap-2.5 rounded-lg px-2.5 text-left text-[11.5px] text-ink transition-colors hover:bg-app-soft"
            >
              {isAdmin ? <UserRound className="h-4 w-4 text-ink-subtle" /> : <ShieldCheck className="h-4 w-4 text-accent" />}
              切换为{isAdmin ? "普通用户" : "管理员"}
            </button>
          ) : (
            <button
              type="button"
              role="menuitem"
              onClick={() => void logout().then(() => navigate("/login", { replace: true }))}
              className="flex h-9 w-full items-center gap-2.5 rounded-lg px-2.5 text-left text-[11.5px] text-ink transition-colors hover:bg-app-soft"
            >
              <LogOut className="h-4 w-4 text-ink-subtle" />
              退出登录
            </button>
          )}
        </div>
      ) : null}
    </div>
  );
}

function FooterMenuLink({
  to,
  label,
  icon,
  onSelect,
}: {
  to: string;
  label: string;
  icon: React.ReactNode;
  onSelect: () => void;
}) {
  return (
    <NavLink
      to={to}
      role="menuitem"
      onClick={onSelect}
      className={({ isActive }) => classNames(
        "flex h-9 items-center gap-2.5 rounded-lg px-2.5 text-[11.5px] transition-colors",
        isActive ? "bg-sidebar-active font-semibold text-ink" : "text-ink hover:bg-app-soft",
      )}
    >
      <span className="text-ink-subtle">{icon}</span>
      {label}
    </NavLink>
  );
}
