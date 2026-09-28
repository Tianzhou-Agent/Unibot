import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowRight, FileText, Folder, Search, UserRound } from "lucide-react";
import { Link } from "react-router-dom";
import { Topbar } from "@/components/layout/Topbar";
import { api, apiErrorMessage } from "@/lib/api";
import { useMockSession } from "@/lib/mockSession";
import { documentCanvasPath, useWorkspace } from "@/lib/workspace";
import type { DocumentTreeResponse } from "@/types";

interface FileScope {
  key: string;
  label: string;
  workspaceId: string | null;
  tree: DocumentTreeResponse;
  error: string | null;
}

export default function FilesPage() {
  const { profile } = useMockSession();
  const { workspaces, loading: workspacesLoading, error: workspacesError } = useWorkspace();
  const [scopes, setScopes] = useState<FileScope[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const loadRequestRef = useRef(0);

  const load = useCallback(async () => {
    if (workspacesLoading) return;
    const requestId = ++loadRequestRef.current;
    setLoading(true);
    const definitions = [
      { key: "user", label: "用户文件", workspaceId: null },
      ...workspaces.map((workspace) => ({ key: workspace.id, label: workspace.name, workspaceId: workspace.id })),
    ];
    const results = await Promise.all(definitions.map(async (scope): Promise<FileScope> => {
      const params = new URLSearchParams({
        user_id: profile.actorUserId,
        tenant_id: profile.tenantId,
        ...(scope.workspaceId ? { workspace_id: scope.workspaceId } : {}),
      });
      try {
        return { ...scope, tree: await api.get<DocumentTreeResponse>(`/documents/tree?${params}`), error: null };
      } catch (loadError) {
        return { ...scope, tree: { folders: [], documents: [] }, error: apiErrorMessage(loadError) };
      }
    }));
    if (requestId !== loadRequestRef.current) return;
    setScopes(results);
    setLoading(false);
  }, [profile.actorUserId, profile.tenantId, workspaces, workspacesLoading]);

  useEffect(() => {
    void load();
    return () => {
      loadRequestRef.current += 1;
    };
  }, [load]);

  const filteredScopes = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return scopes.map((scope) => {
      const scopeMatches = scope.label.toLowerCase().includes(normalized);
      const matches = (value: string) => !normalized || scopeMatches || value.toLowerCase().includes(normalized);
      return {
        ...scope,
        tree: {
          folders: scope.tree.folders.filter((folder) => matches(`${folder.name} ${folder.path}`)),
          documents: scope.tree.documents.filter((document) => matches(document.name)),
        },
      };
    }).filter((scope) => !normalized || scope.error || scope.tree.folders.length || scope.tree.documents.length);
  }, [query, scopes]);

  const userScope = filteredScopes.find((scope) => scope.workspaceId === null);
  const workspaceScopes = filteredScopes.filter((scope) => scope.workspaceId !== null);

  return (
    <div className="flex h-full flex-col bg-app-bg">
      <Topbar title="文件" />
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-7 md:px-6 md:py-9">
        <div className="mx-auto w-full max-w-[820px] space-y-7">
          <label className="flex h-10 items-center gap-2.5 rounded-xl border border-line-strong bg-white px-3.5 shadow-soft focus-within:border-accent">
            <Search className="h-4 w-4 text-ink-subtle" />
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索所有层级的文件"
              aria-label="搜索所有文件"
              className="min-w-0 flex-1 bg-transparent text-[13px] text-ink outline-none placeholder:text-ink-subtle"
            />
          </label>

          {workspacesError ? <p className="rounded-lg border border-danger-ring bg-danger-soft px-3 py-2 text-[11px] text-danger-deep">{workspacesError}</p> : null}
          {loading || workspacesLoading ? <FileScopeSkeleton /> : null}

          {!loading && !workspacesLoading ? (
            <>
              {userScope ? (
                <ScopeGroup title="用户层级">
                  <FileScopeCard scope={userScope} />
                </ScopeGroup>
              ) : null}
              {workspaceScopes.length ? (
                <ScopeGroup title="Workspace 层级">
                  {workspaceScopes.map((scope) => <FileScopeCard key={scope.key} scope={scope} />)}
                </ScopeGroup>
              ) : null}
              {!filteredScopes.length ? <p className="py-10 text-center text-[12px] text-ink-subtle">没有匹配的文件</p> : null}
              {!query && !workspaceScopes.length && !workspacesError ? <p className="text-[11px] text-ink-subtle">还没有 Workspace。</p> : null}
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function ScopeGroup({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section aria-label={title} className="space-y-2">
      <h2 className="px-1 text-[11px] font-medium text-ink-subtle">{title}</h2>
      {children}
    </section>
  );
}

function FileScopeCard({ scope }: { scope: FileScope }) {
  const total = scope.tree.documents.length;
  const scopePath = documentCanvasPath(scope.workspaceId);
  return (
    <section aria-label={`${scope.label}文件层级`} className="overflow-hidden rounded-xl border border-line bg-white shadow-soft">
      <Link to={scopePath} className="group flex items-center gap-3 border-b border-line px-4 py-3 hover:bg-app-soft" aria-label={`打开${scope.label}文件`}>
        <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-app-soft text-ink-muted">
          {scope.workspaceId ? <Folder className="h-4 w-4" /> : <UserRound className="h-4 w-4" />}
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-[12.5px] font-semibold text-ink">{scope.label}</h3>
          <p className="text-[10px] text-ink-subtle">{scope.workspaceId ? "Workspace" : "User"} · {total} 个文件 · {scope.tree.folders.length} 个文件夹</p>
        </div>
        <span className="flex h-8 items-center gap-1 rounded-lg px-2.5 text-[11px] font-medium text-ink-muted group-hover:text-ink">
          打开<ArrowRight className="h-3.5 w-3.5" />
        </span>
      </Link>
      {scope.error ? <p className="m-3 rounded-lg border border-danger-ring bg-danger-soft px-3 py-2 text-[11px] text-danger-deep">{scope.error}</p> : null}
      {!scope.error && !scope.tree.folders.length && !scope.tree.documents.length ? <p className="px-4 py-5 text-center text-[11px] text-ink-subtle">暂无文件</p> : null}
      {!scope.error ? (
        <div className="divide-y divide-line">
          {scope.tree.folders.map((folder) => (
            <Link key={folder.path} to={scopePath} className="group flex min-h-10 items-center gap-2.5 px-4 py-2 text-[12px] text-ink-muted hover:bg-app-soft hover:text-ink">
              <Folder className="h-4 w-4 shrink-0 text-ink-subtle" />
              <span className="min-w-0 flex-1 truncate">{folder.path}</span>
              <ArrowRight className="h-3.5 w-3.5 text-ink-subtle opacity-0 group-hover:opacity-100" />
            </Link>
          ))}
          {scope.tree.documents.map((document) => (
            <Link key={document.name} to={documentCanvasPath(scope.workspaceId, document.name)} className="group flex min-h-10 items-center gap-2.5 px-4 py-2 text-[12px] text-ink-muted hover:bg-app-soft hover:text-ink">
              <FileText className="h-4 w-4 shrink-0 text-ink-subtle" />
              <span className="min-w-0 flex-1 truncate">{document.name}</span>
              <span className="shrink-0 font-mono text-[9.5px] text-ink-subtle">{formatBytes(document.size_bytes)}</span>
              <ArrowRight className="h-3.5 w-3.5 text-ink-subtle opacity-0 group-hover:opacity-100" />
            </Link>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function FileScopeSkeleton() {
  return <div className="space-y-3"><div className="h-28 animate-pulse rounded-xl bg-app-soft" /><div className="h-36 animate-pulse rounded-xl bg-app-soft" /></div>;
}

function formatBytes(value: number): string {
  return value < 1024 ? `${value} B` : `${(value / 1024).toFixed(value < 10 * 1024 ? 1 : 0)} KB`;
}
