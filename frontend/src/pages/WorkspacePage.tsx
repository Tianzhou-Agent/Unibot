import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowRight, FileText, Folder, FolderOpen, MessageSquarePlus, Search } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { CONVERSATIONS_CHANGED_EVENT } from "@/components/layout/Sidebar";
import { Topbar } from "@/components/layout/Topbar";
import { api, apiErrorMessage } from "@/lib/api";
import { useMockSession } from "@/lib/mockSession";
import { timeAgo } from "@/lib/utils";
import { useWorkspace, workspaceCanvasPath, workspaceChatPath } from "@/lib/workspace";
import type { ConversationRecord, DocumentTreeResponse } from "@/types";

export default function WorkspacePage() {
  const { workspaceId = "" } = useParams<{ workspaceId: string }>();
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

  const filtered = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return conversations;
    return conversations.filter((conversation) => {
      const preview = conversation.messages.at(-1)?.content ?? "";
      return `${conversation.title} ${preview}`.toLowerCase().includes(normalized);
    });
  }, [conversations, query]);
  const documentEntries = useMemo(() => [
    ...documentTree.folders.map((folder) => ({ kind: "folder" as const, path: folder.path, name: folder.name })),
    ...documentTree.documents.map((document) => ({ kind: "document" as const, path: document.name, name: document.name })),
  ], [documentTree.documents, documentTree.folders]);

  if (!workspaceLoading && !activeWorkspace) {
    return (
      <div className="flex h-full items-center justify-center bg-app-bg p-6">
        <div className="panel-bordered max-w-md p-6 text-center">
          <FolderOpen className="mx-auto h-9 w-9 text-ink-subtle" />
          <h1 className="mt-3 text-[17px] font-extrabold text-ink">工作区不存在或不可访问</h1>
          <p className="mt-1 text-[12px] text-ink-muted">请从左侧选择其他工作区。</p>
        </div>
      </div>
    );
  }

  const title = activeWorkspace?.name ?? "工作区";
  return (
    <div className="flex h-full flex-col bg-app-bg">
      <Topbar title={title} badge={{ label: "工作区", tone: "info" }} />
      <div className="min-h-0 flex-1 overflow-y-auto p-4 md:p-6">
        <div className="mx-auto max-w-5xl space-y-4">
          <section className="panel-bordered p-4 md:p-5">
            <div className="flex flex-wrap items-start gap-3">
              <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-accent-soft text-accent">
                <FolderOpen className="h-5 w-5" />
              </span>
              <div className="min-w-0 flex-1">
                <h2 className="text-[17px] font-extrabold text-ink">{title}</h2>
                <p className="mt-1 text-[12px] leading-relaxed text-ink-muted">
                  {activeWorkspace?.description || "工作区中的对话和产物共享同一份 NAS 存储空间。"}
                </p>
              </div>
              <Link to={workspaceChatPath(workspaceId)} className="btn-primary">
                <MessageSquarePlus className="h-4 w-4" />新建会话
              </Link>
            </div>
          </section>

          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_280px]">
            <section className="panel-bordered min-w-0 overflow-hidden">
              <header className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-3">
                <div className="min-w-0 flex-1">
                  <h2 className="text-[14px] font-extrabold text-ink">会话</h2>
                  <p className="text-[10.5px] text-ink-muted">{conversations.length} 个工作区会话</p>
                </div>
                <label className="flex h-9 w-full items-center gap-2 rounded-lg border border-line bg-app-soft px-3 focus-within:border-accent sm:w-64">
                  <Search className="h-3.5 w-3.5 text-ink-muted" />
                  <input
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    type="search"
                    placeholder="搜索会话"
                    aria-label="搜索工作区会话"
                    className="min-w-0 flex-1 bg-transparent text-[12px] text-ink outline-none placeholder:text-ink-subtle"
                  />
                </label>
              </header>
              <div className="p-3">
                {loading ? <WorkspaceConversationSkeleton /> : null}
                {!loading && conversationError ? <p className="rounded-lg border border-danger-ring bg-danger-soft p-3 text-[12px] text-danger-deep">{conversationError}</p> : null}
                {!loading && !conversationError && !filtered.length ? (
                  <div className="py-12 text-center text-[12px] text-ink-muted">{query ? "没有匹配的会话" : "这个工作区还没有会话"}</div>
                ) : null}
                {!loading && !conversationError && filtered.length ? (
                  <div className="space-y-2" aria-label="工作区会话列表">
                    {filtered.map((conversation) => (
                      <Link
                        key={conversation.id}
                        to={workspaceChatPath(workspaceId, conversation.id)}
                        className="group flex items-center gap-3 rounded-lg border border-line bg-white px-3.5 py-3 transition hover:border-accent-ring hover:bg-accent-soft"
                      >
                        <span className="h-2 w-2 shrink-0 rounded-full bg-success" />
                        <span className="min-w-0 flex-1">
                          <strong className="block truncate text-[12.5px] text-ink">{conversation.title === "New conversation" ? "新对话" : conversation.title}</strong>
                          <span className="mt-0.5 block truncate text-[10.5px] text-ink-muted">{conversation.messages.at(-1)?.content || "等待第一条消息"}</span>
                        </span>
                        <span className="shrink-0 text-[10px] text-ink-subtle">{timeAgo(conversation.updated_at)}</span>
                        <ArrowRight className="h-3.5 w-3.5 shrink-0 text-ink-subtle transition-transform group-hover:translate-x-0.5 group-hover:text-accent" />
                      </Link>
                    ))}
                  </div>
                ) : null}
              </div>
            </section>

            <section className="panel-bordered flex min-h-48 flex-col overflow-hidden">
              <header className="flex items-center gap-2.5 border-b border-line px-3.5 py-3">
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent-soft text-accent"><FolderOpen className="h-4 w-4" /></span>
                <span className="min-w-0 flex-1">
                  <strong className="block text-[13px] text-ink">NAS 产物与文件夹</strong>
                  <span className="block text-[9.5px] text-ink-muted">{documentEntries.length} 项</span>
                </span>
                <Link to={workspaceCanvasPath(workspaceId, "unibot-documents")} className="flex h-7 w-7 items-center justify-center rounded-md text-ink-muted hover:bg-app-soft hover:text-accent" aria-label="打开文件工作区" title="打开文件工作区">
                  <ArrowRight className="h-3.5 w-3.5" />
                </Link>
              </header>
              <div className="flex-1 p-2">
                {loading ? <div className="h-24 animate-pulse rounded-lg bg-line/60" /> : null}
                {!loading && documentError ? <p className="rounded-lg border border-danger-ring bg-danger-soft p-3 text-[10.5px] text-danger-deep">{documentError}</p> : null}
                {!loading && !documentError && !documentEntries.length ? <p className="px-2 py-8 text-center text-[10.5px] text-ink-muted">暂无产物或文件夹</p> : null}
                {!loading && !documentError ? documentEntries.slice(0, 8).map((entry) => (
                  <Link key={`${entry.kind}:${entry.path}`} to={workspaceCanvasPath(workspaceId, "unibot-documents")} className="group flex min-h-9 items-center gap-2 rounded-md px-2 py-1.5 text-[11px] text-ink-muted hover:bg-accent-soft hover:text-ink" title={entry.path}>
                    {entry.kind === "folder" ? <Folder className="h-3.5 w-3.5 shrink-0 text-warning" /> : <FileText className="h-3.5 w-3.5 shrink-0 text-accent" />}
                    <span className="min-w-0 flex-1 truncate">{entry.name}</span>
                    <ArrowRight className="h-3 w-3 shrink-0 opacity-0 transition group-hover:opacity-100" />
                  </Link>
                )) : null}
              </div>
              <Link to={workspaceCanvasPath(workspaceId, "unibot-documents")} className="flex h-9 items-center justify-center gap-1 border-t border-line text-[10.5px] font-bold text-accent hover:bg-accent-soft">
                查看全部<ArrowRight className="h-3 w-3" />
              </Link>
            </section>
          </div>
        </div>
      </div>
    </div>
  );
}

function WorkspaceConversationSkeleton() {
  return (
    <div className="space-y-2" aria-label="正在加载工作区会话">
      {[0, 1, 2].map((item) => <div key={item} className="h-16 animate-pulse rounded-lg bg-line/60" />)}
    </div>
  );
}
