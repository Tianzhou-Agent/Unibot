import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useMatch } from "react-router-dom";
import { api, apiErrorMessage } from "@/lib/api";
import { useMockSession } from "@/lib/mockSession";
import type { WorkspaceRecord } from "@/types";

interface WorkspaceContextValue {
  workspaces: WorkspaceRecord[];
  activeWorkspaceId: string | null;
  activeWorkspace: WorkspaceRecord | null;
  loading: boolean;
  error: string | null;
  reload: () => Promise<void>;
  createWorkspace: (input: { name: string; description?: string }) => Promise<WorkspaceRecord>;
}

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const { profile } = useMockSession();
  const workspaceMatch = useMatch("/workspaces/:workspaceId/*");
  const activeWorkspaceId = workspaceMatch?.params.workspaceId ?? null;
  const [workspaces, setWorkspaces] = useState<WorkspaceRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const reloadRequestRef = useRef(0);
  const actorKey = `${profile.tenantId}\u0000${profile.actorUserId}`;
  const activeActorKeyRef = useRef(actorKey);
  activeActorKeyRef.current = actorKey;

  const reload = useCallback(async () => {
    const requestId = ++reloadRequestRef.current;
    const expectedActorKey = actorKey;
    setLoading(true);
    try {
      const query = new URLSearchParams({
        user_id: profile.actorUserId,
        tenant_id: profile.tenantId,
      });
      const records = await api.get<WorkspaceRecord[]>(`/workspaces?${query}`);
      if (requestId !== reloadRequestRef.current || activeActorKeyRef.current !== expectedActorKey) return;
      setWorkspaces(records);
      setError(null);
    } catch (loadError) {
      if (requestId === reloadRequestRef.current && activeActorKeyRef.current === expectedActorKey) setError(apiErrorMessage(loadError));
    } finally {
      if (requestId === reloadRequestRef.current && activeActorKeyRef.current === expectedActorKey) setLoading(false);
    }
  }, [actorKey, profile.actorUserId, profile.tenantId]);

  useEffect(() => {
    void reload();
    return () => {
      reloadRequestRef.current += 1;
    };
  }, [reload]);

  const value = useMemo<WorkspaceContextValue>(() => ({
    workspaces,
    activeWorkspaceId,
    activeWorkspace: workspaces.find((workspace) => workspace.id === activeWorkspaceId) ?? null,
    loading,
    error,
    reload,
    createWorkspace: async (input) => {
      const expectedActorKey = actorKey;
      const created = await api.post<WorkspaceRecord>("/workspaces", {
        ...input,
        user_id: profile.actorUserId,
        tenant_id: profile.tenantId,
      });
      if (activeActorKeyRef.current !== expectedActorKey) {
        throw new Error("当前用户已切换，请重新创建工作区。");
      }
      reloadRequestRef.current += 1;
      setLoading(false);
      setWorkspaces((current) => [created, ...current.filter((workspace) => workspace.id !== created.id)]);
      return created;
    },
  }), [activeWorkspaceId, actorKey, error, loading, profile.actorUserId, profile.tenantId, reload, workspaces]);

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

export function useWorkspace(): WorkspaceContextValue {
  const value = useContext(WorkspaceContext);
  if (!value) throw new Error("useWorkspace must be used inside WorkspaceProvider");
  return value;
}

export function workspaceHomePath(workspaceId: string): string {
  return `/workspaces/${encodeURIComponent(workspaceId)}`;
}

export function workspaceChatPath(workspaceId: string | null | undefined, conversationId?: string | null): string {
  if (!workspaceId) return conversationId ? `/chat/${encodeURIComponent(conversationId)}` : "/chat";
  const root = `${workspaceHomePath(workspaceId)}/chat`;
  return conversationId ? `${root}/${encodeURIComponent(conversationId)}` : root;
}

export function workspaceCanvasPath(
  workspaceId: string | null | undefined,
  ainaId: string,
  conversationId?: string | null,
): string {
  const root = workspaceId
    ? `${workspaceHomePath(workspaceId)}/canvas/${encodeURIComponent(ainaId)}`
    : `/canvas/${encodeURIComponent(ainaId)}`;
  return conversationId ? `${root}?conversation=${encodeURIComponent(conversationId)}` : root;
}
