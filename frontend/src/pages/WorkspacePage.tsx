import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { ArrowRight, ArrowUp, FileText, Folder, FolderOpen, Plus, Search } from "lucide-react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { CONVERSATIONS_CHANGED_EVENT } from "@/components/layout/Sidebar";
import { Topbar } from "@/components/layout/Topbar";
import { api, apiErrorMessage } from "@/lib/api";
import { useMockSession } from "@/lib/mockSession";
import { timeAgo } from "@/lib/utils";
import { useWorkspace, workspaceCanvasPath, workspaceChatPath } from "@/lib/workspace";
import type { ConversationRecord, DocumentTreeResponse } from "@/types";

export default function WorkspacePage() {
  const { workspaceId = "" } = useParams<{ workspaceId: string }>();
  const navigate = useNavigate();
  const { profile } = useMockSession();
  const { activeWorkspace, loading: workspaceLoading } = useWorkspace();
  const [conversations, setConversations] = useState<ConversationRecord[]>([]);
  const [documentTree, setDocumentTree] = useState<DocumentTreeResponse>({ folders: [], documents: [] });
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [conversationError, setConversationError] = useState<string | null>(null);
  const [documentError, setDocumentError] = useState<string | null>(null);
  const loadRequestRef = useRef(0);

  const load = useCallback(async () => {
    const requestId = ++loadRequestRef.current;
    setLoading(true);
    const params = new URLSearchParams({
      workspace_id: workspaceId,
      user_id: profile.actorUserId,
      tenant_id: profile.tenantId,
    });
    const [conversationResult, documentResult] = await Promise.allSettled([
      api.get<ConversationRecord[]>(`/conversations?${params}`),
      api.get<DocumentTreeResponse>(`/documents/tree?${params}`),
    ]);
    if (requestId !== loadRequestRef.current) return;
    if (conversationResult.status === "fulfilled") {
      setConversations(conversationResult.value);
      setConversationError(null);
    } else {
      setConversationError(apiErrorMessage(conversationResult.reason));
    }
    if (documentResult.status === "fulfilled") {
      setDocumentTree(documentResult.value);
      setDocumentError(null);
    } else {
      setDocumentError(apiErrorMessage(documentResult.reason));
    }
    setLoading(false);
  }, [profile.actorUserId, profile.tenantId, workspaceId]);

  useEffect(() => {
    void load();
    const refresh = () => void load();
    window.addEventListener(CONVERSATIONS_CHANGED_EVENT, refresh);
    return () => {
      window.removeEventListener(CONVERSATIONS_CHANGED_EVENT, refresh);
      loadRequestRef.current += 1;
    };
  }, [load]);

  const normalizedQuery = query.trim().toLowerCase();
  const filteredConversations = useMemo(() => {
    if (!normalizedQuery) return conversations;
    return conversations.filter((conversation) => {
      const preview = conversation.messages.at(-1)?.content ?? "";
      return `${conversation.title} ${preview}`.toLowerCase().includes(normalizedQuery);
    });
  }, [conversations, normalizedQuery]);
  const documentEntries = useMemo(() => [
    ...documentTree.folders.map((folder) => ({ kind: "folder" as const, path: folder.path, name: folder.name })),
    ...documentTree.documents.map((document) => ({ kind: "document" as const, path: document.name, name: document.name })),
  ].filter((entry) => !normalizedQuery || `${entry.name} ${entry.path}`.toLowerCase().includes(normalizedQuery)), [documentTree.documents, documentTree.folders, normalizedQuery]);

  function startTask(event: FormEvent) {
    event.preventDefault();
    const initialPrompt = query.trim();
    if (!initialPrompt) return;
    navigate(`${workspaceChatPath(workspaceId)}?prompt=${encodeURIComponent(initialPrompt)}`);
  }

  if (!workspaceLoading && !activeWorkspace) {
    return (
      <div className="flex h-full items-center justify-center bg-app-bg p-6">
        <div className="max-w-md text-center">
          <FolderOpen className="mx-auto h-9 w-9 text-ink-subtle" />
          <h1 className="mt-3 text-[17px] font-semibold text-ink">工作区不存在或不可访问</h1>
          <p className="mt-1 text-[12px] text-ink-muted">请从左侧选择其他工作区。</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col bg-app-bg">
      <Topbar
        title="文件夹"
        actions={(
          <Link to={workspaceCanvasPath(workspaceId, "unibot-documents")} className="btn-outline h-8 text-[12px] font-medium">
            <Plus className="h-3.5 w-3.5" />新建文件夹
          </Link>
        )}
      />
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-8 md:px-6 md:py-10">
        <div className="mx-auto w-full max-w-[760px] space-y-7">
          <form onSubmit={startTask} className="rounded-2xl border border-line-strong bg-white px-4 py-3 shadow-soft" aria-label="工作区任务输入">
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="告诉我你想完成什么…"
              aria-label="搜索或创建任务"
              className="h-8 w-full bg-transparent text-[14px] text-ink outline-none placeholder:text-ink-subtle"
            />
            <div className="mt-1 flex items-center gap-2">
              <Search className="h-3.5 w-3.5 text-ink-subtle" />
              <span className="text-[11px] text-ink-subtle">搜索工作区，或直接创建一个任务</span>
              <button type="submit" disabled={!query.trim()} className="ml-auto flex h-8 w-8 items-center justify-center rounded-full bg-ink text-white disabled:opacity-40" aria-label="开始任务">
                <ArrowUp className="h-4 w-4" />
              </button>
            </div>
          </form>

          <WorkspaceSection title="文件夹" count={documentEntries.length} error={documentError} loading={loading}>
            {!loading && !documentError && documentEntries.length === 0 ? <EmptyRow text={normalizedQuery ? "没有匹配的文件" : "暂无产物或文件夹"} /> : null}
            {!loading && !documentError ? documentEntries.map((entry) => (
              <Link key={`${entry.kind}:${entry.path}`} to={workspaceCanvasPath(workspaceId, "unibot-documents")} className="group flex h-9 items-center gap-2.5 rounded-lg px-2.5 text-[12.5px] text-ink-muted hover:bg-sidebar-hover hover:text-ink" title={entry.path}>
                {entry.kind === "folder" ? <Folder className="h-3.5 w-3.5 text-ink-subtle" /> : <FileText className="h-3.5 w-3.5 text-ink-subtle" />}
                <span className="min-w-0 flex-1 truncate">{entry.name}</span>
                <span className="font-mono text-[9.5px] text-ink-subtle">NAS</span>
                <ArrowRight className="h-3.5 w-3.5 text-ink-subtle opacity-0 transition-opacity group-hover:opacity-100" />
              </Link>
            )) : null}
          </WorkspaceSection>

          <WorkspaceSection title="会话" count={filteredConversations.length} error={conversationError} loading={loading}>
            {!loading && !conversationError && filteredConversations.length === 0 ? <EmptyRow text={normalizedQuery ? "没有匹配的会话" : "这个工作区还没有会话"} /> : null}
            {!loading && !conversationError ? filteredConversations.map((conversation) => (
              <Link key={conversation.id} to={workspaceChatPath(workspaceId, conversation.id)} className="group flex min-h-9 items-center gap-2.5 rounded-lg px-2.5 py-2 text-[12.5px] text-ink-muted hover:bg-sidebar-hover hover:text-ink">
                <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-success" />
                <span className="min-w-0 flex-1 truncate">{conversation.title === "New conversation" ? "新对话" : conversation.title}</span>
                <span className="shrink-0 font-mono text-[9.5px] text-ink-subtle">{timeAgo(conversation.updated_at)}</span>
                <ArrowRight className="h-3.5 w-3.5 text-ink-subtle opacity-0 transition-opacity group-hover:opacity-100" />
              </Link>
            )) : null}
          </WorkspaceSection>
        </div>
      </div>
    </div>
  );
}

function WorkspaceSection({ title, count, error, loading, children }: { title: string; count: number; error: string | null; loading: boolean; children: React.ReactNode }) {
  return (
    <section aria-label={title}>
      <header className="mb-1 flex h-7 items-center gap-2 px-2.5">
        <h2 className="text-[11px] font-medium text-ink-subtle">{title}</h2>
        <span className="font-mono text-[9.5px] text-ink-subtle">{count}</span>
      </header>
      {loading ? <div className="space-y-1"><div className="h-9 animate-pulse rounded-lg bg-app-soft" /><div className="h-9 animate-pulse rounded-lg bg-app-soft" /></div> : null}
      {!loading && error ? <p className="rounded-lg border border-danger-ring bg-danger-soft px-3 py-2 text-[11px] text-danger-deep">{error}</p> : null}
      {children}
    </section>
  );
}

function EmptyRow({ text }: { text: string }) {
  return <p className="rounded-lg px-2.5 py-4 text-center text-[11px] text-ink-subtle">{text}</p>;
}
