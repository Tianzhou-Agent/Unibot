import { useTranslation } from "react-i18next";
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowUp,
  Bot,
  Check,
  CirclePause,
  MessageSquareText,
  PanelRightOpen,
  Play,
  RotateCcw,
  Sparkles,
  Square,
  Trash2,
  X,
} from "lucide-react";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { AinaChooserDialog } from "@/components/canvas/AinaChooserDialog";
import { AinaSwitcher } from "@/components/canvas/AinaSwitcher";
import { ApprovalCard } from "@/components/chat/ApprovalCard";
import { AssistantMessage, UserMessage } from "@/components/chat/MessageBubble";
import { ModelSelector } from "@/components/chat/ModelSelector";
import { applyLiveEvent, LiveTurn, ThinkingIndicator, type LiveItem } from "@/components/chat/LiveTurn";
import { isToolSequenceContinuation, toolSequenceCallCount, ToolCallList, ToolResultCard } from "@/components/chat/ToolCallCard";
import { ConversationObsDrawer } from "@/components/observability/ConversationObsDrawer";
import { notifyConversationsChanged } from "@/components/layout/Sidebar";
import { Topbar } from "@/components/layout/Topbar";
import { MainWidgetRenderer } from "@/components/widgets/MainWidgetRenderer";
import { isClarificationWidget, SessionWidgetRenderer } from "@/components/widgets/SessionWidgetRenderer";
import { TaskTreeWidget } from "@/components/tasks/TaskTreeWidget";
import { api, apiErrorMessage, streamChat, streamResume, type StreamEvent } from "@/lib/api";
import { useDebugMode } from "@/lib/debugMode";
import { getObsSession, loadLegacyPersonalObsSession } from "@/lib/obsData";
import { adaptSessionDetail } from "@/lib/obsAdapter";
import { useMockSession } from "@/lib/mockSession";
import { classNames, uid } from "@/lib/utils";
import { canvasQuery, readCanvasRoute, workspaceChatPath, type CanvasRoute } from "@/lib/workspace";
import type {
  AinaCanvasResponse,
  ApprovalRecord,
  BackendMessage,
  ChatResponse,
  ConversationRecord,
  DocumentTaskContext,
  LLMCallRecord,
  TraceRecord,
  TraceSpan,
  WidgetDefinition,
} from "@/types";

interface MessageFailure {
  kind: "llm" | "capability";
  name: string;
  error: string;
  callId?: string;
}

export default function ChatModePage() {
  const { t } = useTranslation("chatPage");
  const { t: tCanvas } = useTranslation("canvas");
  const { workspaceId, conversationId } = useParams<{ workspaceId?: string; conversationId?: string }>();
  const routeWorkspaceId = workspaceId ?? null;
  const navigate = useNavigate();
  const location = useLocation();
  const { debugMode } = useDebugMode();
  const { profile } = useMockSession();
  const actor = useMemo(() => ({ user_id: profile.actorUserId, tenant_id: profile.tenantId }), [profile.actorUserId, profile.tenantId]);
  const [llmCalls, setLlmCalls] = useState<LLMCallRecord[]>([]);
  const [traces, setTraces] = useState<TraceRecord[]>([]);
  const [conversation, setConversation] = useState<ConversationRecord | null>(null);
  const [loading, setLoading] = useState(Boolean(conversationId));
  const [sending, setSending] = useState(false);
  const [composerVersion, setComposerVersion] = useState(0);
  const composerConversationIdRef = useRef<string | null>(conversationId ?? null);
  const [optimisticUser, setOptimisticUser] = useState<BackendMessage | null>(null);
  // Archived message count when the optimistic message was sent; a later archived copy replaces it.
  const optimisticBaselineRef = useRef(0);
  const [liveItems, setLiveItems] = useState<LiveItem[]>([]);
  const [activity, setActivity] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [approval, setApproval] = useState<ApprovalRecord | null>(null);
  const [lastRun, setLastRun] = useState<ChatResponse | null>(null);
  const [clarificationWidgets, setClarificationWidgets] = useState<WidgetDefinition[]>([]);
  const [renaming, setRenaming] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleted, setDeleted] = useState(false);
  const [obsOpen, setObsOpen] = useState(false);
  const [searchParams, setSearchParams] = useSearchParams();
  // Canvases are optional add-ons of the conversation: AINAs opened as tabs via `?aina=`.
  const canvasRoute = useMemo(() => readCanvasRoute(searchParams), [searchParams]);
  const { ainaIds, activeAinaId, documentName } = canvasRoute;
  const ainaKey = ainaIds.join("\n");
  const [canvases, setCanvases] = useState<Record<string, AinaCanvasResponse>>({});
  const [chooser, setChooser] = useState<WidgetDefinition | null>(null);
  // AINAs opened in this conversation since it was loaded; the loaded record lists the earlier ones.
  const [usedAinaIds, setUsedAinaIds] = useState<string[]>([]);
  // AINAs the user declined to open in this conversation; they are not offered again.
  const declinedAinaIdsRef = useRef(new Set<string>());
  const [mobilePane, setMobilePane] = useState<"chat" | "app">("app");
  const [documentTaskContext, setDocumentTaskContext] = useState<DocumentTaskContext | null>(null);
  const canvasRouteRef = useRef(canvasRoute);
  canvasRouteRef.current = canvasRoute;
  const canvasesRef = useRef(canvases);
  canvasesRef.current = canvases;
  const activeCanvas = activeAinaId ? canvases[activeAinaId] : undefined;
  const endRef = useRef<HTMLDivElement | null>(null);
  const activeConversationIdRef = useRef<string | null>(conversationId ?? null);
  const activeWorkspaceIdRef = useRef<string | null>(routeWorkspaceId);
  const localRunConversationIdRef = useRef<string | null>(null);
  const localRunWorkspaceIdRef = useRef<string | null>(null);
  const loadRequestRef = useRef(0);
  const runGenerationRef = useRef(0);
  const streamAbortRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(true);
  activeConversationIdRef.current = conversationId ?? null;
  activeWorkspaceIdRef.current = routeWorkspaceId;
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      loadRequestRef.current += 1;
      runGenerationRef.current += 1;
      streamAbortRef.current?.abort();
      streamAbortRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!conversationId) return;
    let active = true;
    const actorQuery = `tenant_id=${encodeURIComponent(profile.tenantId)}&user_id=${encodeURIComponent(profile.actorUserId)}`;
    void getObsSession(conversationId).catch(() => null).then(async (session) => {
      if (!active) return;
      if (session) {
        const adapted = adaptSessionDetail(session);
        setLlmCalls(adapted.calls);
        setTraces(adapted.traces);
        return;
      }
      // OBS 未启用或会话尚未迁移：只在此时读取旧 Trace/LLM Call。
      const legacy = await loadLegacyPersonalObsSession(conversationId, actorQuery);
      if (!active) return;
      setLlmCalls(legacy.calls);
      setTraces(legacy.traces);
    }).catch(() => {
      if (!active) return;
      setLlmCalls([]);
      setTraces([]);
    });
    return () => { active = false; };
  }, [conversationId, profile.actorUserId, profile.tenantId]);

  const failuresByTrace = useMemo(() => {
    const map = new Map<string, MessageFailure[]>();
    const push = (traceId: string, failure: MessageFailure) => {
      const list = map.get(traceId) ?? [];
      list.push(failure);
      map.set(traceId, list);
    };
    for (const call of llmCalls) {
      if (call.status !== "failed" && !call.error) continue;
      if (!call.trace_id) continue;
      push(call.trace_id, { kind: "llm", name: call.model, error: call.error ?? t("err.llmFailed"), callId: call.call_id });
    }
    for (const trace of traces) {
      for (const span of trace.spans ?? []) {
        if (span.kind !== "tool" && span.kind !== "aina") continue;
        if (span.status !== "failed" && !span.error) continue;
        const err = span.error;
        const message = typeof err === "string" ? err : (err as Record<string, unknown> | null)?.message;
        push(trace.trace_id, { kind: "capability", name: span.target_id || span.name, error: typeof message === "string" ? message : t("err.capabilityFailed") });
      }
    }
    return map;
  }, [llmCalls, traces]);

  const errorTraceId = useMemo(() => (
    [...(conversation?.messages ?? [])].reverse().find((message) => message.trace_id)?.trace_id ?? null
  ), [conversation?.messages]);
  const errorLogHref = error && conversation?.id && errorTraceId
    ? `/obs?sessionId=${encodeURIComponent(conversation.id)}&tab=logs&traceId=${encodeURIComponent(errorTraceId)}`
    : null;

  const loadConversation = useCallback(async (id: string, silent = false) => {
    const requestId = ++loadRequestRef.current;
    if (!silent) setLoading(true);
    try {
      const [record, pendingApprovals] = await Promise.all([
        api.get<ConversationRecord>(`/conversations/${id}`),
        api.get<ApprovalRecord[]>(`/approvals?conversation_id=${id}&status=pending`),
      ]);
      if (requestId !== loadRequestRef.current || activeConversationIdRef.current !== id) return;
      if ((record.workspace_id ?? null) !== activeWorkspaceIdRef.current) {
        throw new Error(t("err.wrongWorkspace"));
      }
      setConversation(record);
      setApproval(pendingApprovals[0] ?? null);
      setTitleDraft(record.title);
      setDeleted(false);
      if (localRunConversationIdRef.current !== id) {
        setSending(record.run_status === "running");
        setActivity(null);
      }
      setError(record.run_error ?? null);
    } catch (loadError) {
      if (requestId !== loadRequestRef.current || activeConversationIdRef.current !== id) return;
      setError(apiErrorMessage(loadError));
    } finally {
      if (requestId === loadRequestRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const continuingLocalRun = Boolean(
      conversationId
      && localRunWorkspaceIdRef.current === routeWorkspaceId
      && localRunConversationIdRef.current === conversationId,
    );
    if (composerConversationIdRef.current !== (conversationId ?? null)) {
      composerConversationIdRef.current = conversationId ?? null;
      setComposerVersion((current) => current + 1);
    }
    setConversation((current) => (
      current && current.id === conversationId && (current.workspace_id ?? null) === routeWorkspaceId ? current : null
    ));
    if (!continuingLocalRun) {
      runGenerationRef.current += 1;
      streamAbortRef.current?.abort();
      streamAbortRef.current = null;
      localRunConversationIdRef.current = null;
      localRunWorkspaceIdRef.current = null;
      setOptimisticUser(null);
      setLiveItems([]);
      setActivity(null);
      setSending(false);
      setClarificationWidgets([]);
    }
    setApproval(null);
    setLastRun(null);
    setError(null);
    setDeleted(false);
    setChooser(null);
    declinedAinaIdsRef.current = new Set();
    setUsedAinaIds([]);
    if (conversationId) {
      void loadConversation(conversationId);
    } else {
      loadRequestRef.current += 1;
      setConversation(null);
      setLoading(false);
    }
  }, [conversationId, loadConversation, routeWorkspaceId]);

  useEffect(() => {
    if (!conversationId || conversation?.run_status !== "running") return;
    const timer = window.setInterval(() => {
      void loadConversation(conversationId, true).then(notifyConversationsChanged);
    }, 600);
    return () => window.clearInterval(timer);
  }, [conversation?.run_status, conversationId, loadConversation]);

  useEffect(() => {
    const reset = () => {
      setComposerVersion((current) => current + 1);
      runGenerationRef.current += 1;
      streamAbortRef.current?.abort();
      streamAbortRef.current = null;
      localRunConversationIdRef.current = null;
      localRunWorkspaceIdRef.current = null;
      setConversation(null);
      setApproval(null);
      setLastRun(null);
      setError(null);
      setDeleted(false);
      setOptimisticUser(null);
      setLiveItems([]);
      setActivity(null);
      setSending(false);
      setClarificationWidgets([]);
      setChooser(null);
      declinedAinaIdsRef.current = new Set();
    };
    window.addEventListener("unibot:new-conversation", reset);
    return () => window.removeEventListener("unibot:new-conversation", reset);
  }, []);

  /** Navigates to the conversation with `route`'s canvases open beside it. */
  function navigateCanvas(route: CanvasRoute, targetConversationId: string | null, replace = false) {
    const query = canvasQuery(route);
    navigate(`${workspaceChatPath(routeWorkspaceId, targetConversationId)}${query ? `?${query}` : ""}`, { replace });
  }

  /** Opens `ids` as tabs beside the conversation, activating the first; open tabs are kept. */
  function openAinas(ids: string[], targetConversationId = conversation?.id ?? conversationId ?? null) {
    if (!ids.length) return;
    setMobilePane("app");
    const route = canvasRouteRef.current;
    navigateCanvas({
      ...route,
      ainaIds: [...route.ainaIds, ...ids.filter((id) => !route.ainaIds.includes(id))],
      activeAinaId: ids[0],
    }, targetConversationId);
  }

  function selectAina(id: string) {
    navigateCanvas({ ...canvasRouteRef.current, activeAinaId: id }, conversation?.id ?? conversationId ?? null, true);
  }

  function closeAinas(ids: string[], replace = false) {
    const route = canvasRouteRef.current;
    const remaining = route.ainaIds.filter((id) => !ids.includes(id));
    let nextActive = route.activeAinaId;
    if (nextActive && ids.includes(nextActive)) {
      nextActive = remaining[Math.min(route.ainaIds.indexOf(nextActive), remaining.length - 1)] ?? null;
    }
    const keepsDocument = remaining.some((id) => canvasesRef.current[id]?.main_widget.kind === "document");
    navigateCanvas(
      { ainaIds: remaining, activeAinaId: nextActive, documentName: keepsDocument ? route.documentName : null },
      conversation?.id ?? conversationId ?? null,
      replace,
    );
  }

  useEffect(() => {
    setCanvases((current) => {
      const kept = Object.entries(current).filter(([id]) => ainaIds.includes(id));
      return kept.length === Object.keys(current).length ? current : Object.fromEntries(kept);
    });
    if (!ainaIds.some((id) => canvasesRef.current[id]?.main_widget.kind === "document")) setDocumentTaskContext(null);
    setUsedAinaIds((current) => {
      const added = ainaIds.filter((id) => !current.includes(id));
      return added.length ? [...current, ...added] : current;
    });
    // Opening with the conversation binds each AINA to it; reopen any not yet bound to this conversation.
    const targetConversationId = conversationId ?? null;
    const pending = ainaIds.filter((id) => {
      const loaded = canvasesRef.current[id];
      return !loaded || (loaded.conversation_id ?? null) !== targetConversationId;
    });
    let cancelled = false;
    for (const id of pending) {
      api
        .post<AinaCanvasResponse>(`/ainas/${encodeURIComponent(id)}/open`, {
          ...actor,
          workspace_id: routeWorkspaceId,
          conversation_id: targetConversationId,
        })
        .then((opened) => !cancelled && setCanvases((current) => ({ ...current, [id]: opened })))
        .catch((openError) => {
          if (cancelled) return;
          setError(apiErrorMessage(openError));
          closeAinas([id], true);
        });
    }
    return () => {
      cancelled = true;
    };
  }, [actor, ainaKey, conversationId, routeWorkspaceId]); // ainaKey stands for ainaIds; closeAinas only reads refs.

  /** The chat path for `id`, keeping whichever canvases are open beside it. */
  function conversationPath(id: string) {
    const query = canvasQuery(canvasRouteRef.current);
    return `${workspaceChatPath(routeWorkspaceId, id)}${query ? `?${query}` : ""}`;
  }

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: sending ? "smooth" : "auto" });
  }, [conversation?.messages, optimisticUser, liveItems, activity, approval, sending, clarificationWidgets]);

  const sendMessage = (text: string) => runTurn(text);

  /** Runs a turn for `text`, or continues the conversation's stopped turn when `text` is null. */
  async function runTurn(text: string | null) {
    if (sending || deleted) return;
    setClarificationWidgets([]);
    const draftTitle = text === null ? null : text.length > 24 ? `${text.slice(0, 24)}…` : text;
    const localMessage: BackendMessage | null = text === null ? null : {
      id: uid("local"),
      role: "user",
      content: text,
      content_type: "text",
      widgets: [],
      created_at: new Date().toISOString(),
    };
    optimisticBaselineRef.current = conversation?.messages.length ?? 0;
    setOptimisticUser(localMessage);
    setLiveItems([]);
    setActivity(null);
    setError(null);
    setApproval(null);
    setSending(true);
    let runConversationId = conversation?.id ?? null;
    localRunConversationIdRef.current = runConversationId;
    localRunWorkspaceIdRef.current = routeWorkspaceId;
    const runGeneration = ++runGenerationRef.current;
    streamAbortRef.current?.abort();
    const streamController = new AbortController();
    streamAbortRef.current = streamController;
    const isActiveRun = () => mountedRef.current
      && runGenerationRef.current === runGeneration
      && activeConversationIdRef.current === runConversationId
      && activeWorkspaceIdRef.current === routeWorkspaceId;
    let completion: ChatResponse | null = null;
    let streamFailure: string | null = null;
    try {
      let targetConversation = conversation;
      if (!targetConversation) {
        targetConversation = await api.post<ConversationRecord>("/conversations", {
          ...actor,
          workspace_id: routeWorkspaceId,
          title: draftTitle,
          category: "general",
          ...(ainaIds.length ? { active_aina_ids: ainaIds, primary_aina_id: activeAinaId } : {}),
        });
        if (!isActiveRun()) return;
        setConversation(targetConversation);
        setTitleDraft(targetConversation.title);
        runConversationId = targetConversation.id;
        composerConversationIdRef.current = targetConversation.id;
        localRunConversationIdRef.current = targetConversation.id;
        localRunWorkspaceIdRef.current = routeWorkspaceId;
        activeConversationIdRef.current = targetConversation.id;
        notifyConversationsChanged();
        navigate(conversationPath(targetConversation.id), { replace: true });
      }
      runConversationId = targetConversation.id;
      localRunConversationIdRef.current = targetConversation.id;
      const onEvent = (event: StreamEvent) => {
        if (event.type === "message.completed") completion = event.response;
        if (event.type === "error") {
          streamFailure = event.error?.message ?? event.code ?? t("err.streamFailed");
        }
        if (!isActiveRun()) return;
        setLiveItems((current) => applyLiveEvent(current, event));
        if (event.type === "approval.required") setActivity(t("activity.awaitingApproval"));
        if (event.type === "error") {
          setError(streamFailure);
        }
      };
      if (text === null) {
        await streamResume(targetConversation.id, actor, onEvent, streamController.signal);
      } else {
        await streamChat(
          {
            message: text,
            conversation_id: targetConversation.id,
            workspace_id: routeWorkspaceId,
            preferred_aina_id: canvasRouteRef.current.activeAinaId ?? undefined,
            ui_context: activeCanvasIsDocument() && documentTaskContext ? documentTaskUiContext(documentTaskContext) : undefined,
            ...actor,
          },
          onEvent,
          streamController.signal,
        );
      }
      if (!completion) throw new Error(streamFailure ?? t("err.noCompletion"));
      if (!isActiveRun()) return;
      const completed = completion as ChatResponse;
      if (draftTitle !== null && ["New conversation", "新对话"].includes(targetConversation.title)) {
        await api.patch(`/conversations/${completed.conversation_id}`, {
          title: draftTitle,
        });
      }
      if (!isActiveRun()) return;
      notifyConversationsChanged();
      setLastRun(completed);
      setClarificationWidgets(completed.widgets.filter(isClarificationWidget));
      setApproval(completed.approval ?? null);
      if (conversationId !== completed.conversation_id) {
        navigate(conversationPath(completed.conversation_id), { replace: true });
      }
      await loadConversation(completed.conversation_id);
      const openAction = completed.widgets
        .flatMap((widget) => widget.actions)
        .find((action) => action.kind === "open_aina" && action.aina_id);
      const suggestion = completed.widgets.find((widget) => widget.kind === "aina_chooser" && widget.apps.some(
        (app) => !canvasRouteRef.current.ainaIds.includes(app.aina_id) && !declinedAinaIdsRef.current.has(app.aina_id),
      ));
      if (openAction?.aina_id && isActiveRun()) openAinas([openAction.aina_id], completed.conversation_id);
      else if (suggestion && isActiveRun()) setChooser(suggestion);
      return true;
    } catch (sendError) {
      if (isActiveRun()) {
        if (runConversationId) await loadConversation(runConversationId, true);
        if (isActiveRun()) setError(apiErrorMessage(sendError));
        return Boolean(completion);
      }
    } finally {
      const activeRun = isActiveRun();
      if (runGenerationRef.current === runGeneration) {
        localRunConversationIdRef.current = null;
        localRunWorkspaceIdRef.current = null;
        if (streamAbortRef.current === streamController) streamAbortRef.current = null;
      }
      if (activeRun) {
        setOptimisticUser(null);
        setLiveItems([]);
        setActivity(null);
        setSending(false);
      }
    }
  }

  async function stopRun() {
    const id = localRunConversationIdRef.current ?? conversation?.id;
    if (!id) return;
    setActivity(t("activity.stopping"));
    try {
      await api.post(`/conversations/${id}/stop`, actor);
    } catch (stopError) {
      setError(apiErrorMessage(stopError));
    }
  }

  async function resolveApproval(action: "confirm" | "deny") {
    if (!approval) return;
    setSending(true);
    setError(null);
    setActivity(action === "confirm" ? t("activity.runningApproved") : t("activity.cancelling"));
    try {
      if (action === "confirm") {
        const response = await api.post<ChatResponse>(`/approvals/${approval.id}/confirm`, actor);
        setLastRun(response);
      } else {
        await api.post(`/approvals/${approval.id}/deny`, actor);
      }
      setApproval(null);
      await loadConversation(approval.conversation_id);
      notifyConversationsChanged();
    } catch (approvalError) {
      setError(apiErrorMessage(approvalError));
    } finally {
      setSending(false);
      setActivity(null);
    }
  }

  async function saveTitle() {
    if (!conversation || !titleDraft.trim()) return;
    try {
      const updated = await api.patch<ConversationRecord>(`/conversations/${conversation.id}`, {
        title: titleDraft.trim(),
      });
      setConversation(updated);
      setRenaming(false);
      notifyConversationsChanged();
    } catch (renameError) {
      setError(apiErrorMessage(renameError));
    }
  }

  async function deleteConversation() {
    if (!conversation) return;
    try {
      await api.delete(`/conversations/${conversation.id}`);
      setConfirmDelete(false);
      setDeleted(true);
      notifyConversationsChanged();
    } catch (deleteError) {
      setError(apiErrorMessage(deleteError));
    }
  }

  async function restoreConversation() {
    if (!conversation) return;
    try {
      const restored = await api.post<ConversationRecord>(`/conversations/${conversation.id}/restore`);
      setConversation(restored);
      setDeleted(false);
      notifyConversationsChanged();
    } catch (restoreError) {
      setError(apiErrorMessage(restoreError));
    }
  }

  function activeCanvasIsDocument() {
    const active = canvasRouteRef.current.activeAinaId;
    return Boolean(active && canvasesRef.current[active]?.main_widget.kind === "document");
  }

  const initialPrompt = (location.state as { initialPrompt?: string } | null)?.initialPrompt?.trim()
    || searchParams.get("prompt")?.trim()
    || "";

  const messages = useMemo(() => {
    const archived = conversation?.messages ?? [];
    // The run archives the user message when it starts, so a reload during the run already contains it.
    const echoed = optimisticUser !== null && archived
      .slice(optimisticBaselineRef.current)
      .some((message) => message.role === "user" && message.content === optimisticUser.content);
    return optimisticUser && !echoed ? [...archived, optimisticUser] : archived;
  }, [conversation?.messages, optimisticUser]);
  const toolResultsByCallId = useMemo(() => new Map(
    messages
      .filter((message) => message.role === "tool" && message.tool_call_id)
      .map((message) => [message.tool_call_id as string, message]),
  ), [messages]);
  const requestedToolCallIds = useMemo(() => new Set(
    messages.flatMap((message) => message.tool_calls?.map((call) => call.id) ?? []),
  ), [messages]);

  const title = conversation?.title === "New conversation" ? t("newConversation") : conversation?.title ?? t("newConversation");
  const badge = deleted
    ? ({ label: t("badge.deleted"), tone: "warning" } as const)
    : sending
      ? ({ label: t("badge.running"), tone: "thinking" } as const)
      : ({ label: t("badge.ready"), tone: "success" } as const);

  return (
    <>
    <div className="flex h-full flex-col bg-app-bg">
      <Topbar
        title={title}
        badge={badge}
        actions={!deleted && (ainaIds.length || conversation?.id) ? (
          <div className="flex items-center gap-2">
            {ainaIds.length ? <CanvasPaneToggle pane={mobilePane} onChange={setMobilePane} /> : null}
            {conversation?.id ? (
              <button
                type="button"
                onClick={() => {
                  const next = new URLSearchParams(searchParams);
                  next.delete("tab");
                  next.delete("traceId");
                  next.delete("logId");
                  setSearchParams(next, { replace: true });
                  setObsOpen(true);
                }}
                className="btn-outline h-8"
                aria-label={t("viewObsAria")}
              >
                <Activity className="h-3.5 w-3.5" />OBS
              </button>
            ) : null}
          </div>
        ) : null}
      />

      {renaming ? (
        <div className="border-b border-line bg-white px-5 py-2.5 flex items-center gap-2">
          <input
            value={titleDraft}
            onChange={(event) => setTitleDraft(event.target.value)}
            className="input-soft max-w-md h-9"
            aria-label={t("titleAria")}
            autoFocus
          />
          <button type="button" onClick={() => void saveTitle()} className="btn-primary h-9">
            <Check className="w-4 h-4" />{t("save")}
          </button>
          <button type="button" onClick={() => setRenaming(false)} className="btn-ghost h-9">
            <X className="w-4 h-4" />{t("cancel")}
          </button>
        </div>
      ) : null}

      {confirmDelete ? (
        <div className="border-b border-danger-ring bg-danger-soft px-5 py-3 flex items-center gap-3">
          <AlertTriangle className="w-4 h-4 text-danger" />
          <span className="text-[13px] text-danger-deep">{t("deleteNote")}</span>
          <span className="flex-1" />
          <button type="button" onClick={() => setConfirmDelete(false)} className="btn-outline h-8">
            {t("cancel")}
          </button>
          <button type="button" onClick={() => void deleteConversation()} className="btn-danger-outline h-8">
            {t("confirmDelete")}
          </button>
        </div>
      ) : null}

      <div className={classNames(
        "min-h-0 flex-1",
        ainaIds.length ? "grid grid-cols-1 lg:grid-cols-[400px_minmax(0,1fr)]" : "flex flex-col",
      )}>
      <div className={classNames(
        "min-h-0 flex-1 flex-col overflow-hidden",
        ainaIds.length ? classNames("lg:flex", mobilePane === "chat" ? "flex" : "hidden") : "flex",
      )}>
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-8 md:px-6 md:py-10" aria-live="polite">
          <div className="mx-auto max-w-[760px] space-y-7">
              {loading ? <ChatSkeleton /> : null}
              {!loading && deleted ? (
                <DeletedConversation title={title} onRestore={() => void restoreConversation()} />
              ) : null}
              {!loading && !deleted && messages.length === 0 ? <WelcomePanel /> : null}
              {!deleted
                ? messages.map((message, index) => {
                    if (message.role === "tool" && message.tool_call_id && requestedToolCallIds.has(message.tool_call_id)) return null;
                    const continuesToolSequence = isToolSequenceContinuation(messages, index);
                    return (
                      <div key={message.id} className={continuesToolSequence ? "!mt-2" : undefined}>
                        <ConversationMessage
                          message={message}
                          onOpenAina={(id) => openAinas([id])}
                          onPrompt={sendMessage}
                          debugMode={debugMode}
                          conversationId={conversation?.id ?? ""}
                          workspaceId={routeWorkspaceId}
                          failures={message.trace_id ? failuresByTrace.get(message.trace_id) ?? [] : []}
                          toolResultsByCallId={toolResultsByCallId}
                          requestedToolCallIds={requestedToolCallIds}
                          showToolHeader={!continuesToolSequence}
                          toolHeaderCount={toolSequenceCallCount(messages, index)}
                        />
                      </div>
                    );
                  })
                : null}
              {clarificationWidgets.map((widget) => (
                <SessionWidgetRenderer
                  key={widget.id}
                  widget={widget}
                  workspaceId={routeWorkspaceId}
                  onOpenAina={(id) => openAinas([id])}
                  onPrompt={sendMessage}
                />
              ))}
              <LiveTurn items={liveItems} debugMode={debugMode} />
              {sending ? <ThinkingIndicator label={activity} /> : null}
              {approval && !deleted ? (
                <ApprovalCard
                  approval={approval}
                  disabled={sending}
                  debugMode={debugMode}
                  onConfirm={() => void resolveApproval("confirm")}
                  onDeny={() => void resolveApproval("deny")}
                />
              ) : null}
              {conversation?.run_status === "stopped" && !sending && !deleted ? (
                <StoppedNotice onResume={() => void runTurn(null)} />
              ) : null}
              {error ? <ErrorNotice message={error} detailsHref={errorLogHref} onDismiss={() => setError(null)} /> : null}
              {debugMode && lastRun && !sending ? <RunSummary response={lastRun} /> : null}
              <div ref={endRef} />
            </div>
          </div>
          {!deleted ? (
            <ChatComposer
              key={`${profile.tenantId}:${profile.actorUserId}:${routeWorkspaceId}:${composerVersion}`}
              disabled={sending || loading}
              running={sending && Boolean(conversation?.id)}
              initialText={initialPrompt}
              sessionId={conversation?.id ?? conversationId ?? null}
              context={activeCanvas?.main_widget.kind === "document" ? documentTaskContext : null}
              onSend={sendMessage}
              onStop={() => void stopRun()}
            />
          ) : null}
        </div>
        {ainaIds.length ? (
          // Inset as a window inside the conversation, so it reads as a closable add-on; one tab per open AINA.
          <section className={classNames(
            "min-h-0 flex-col p-1.5 lg:flex lg:py-2 lg:pl-0 lg:pr-2",
            mobilePane === "app" ? "flex" : "hidden",
          )}>
            <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-line-strong bg-white shadow-soft">
              <div className="flex h-10 shrink-0 items-end gap-1 border-b border-line bg-app-soft px-2">
              <div role="tablist" aria-label={tCanvas("tabs")} className="flex h-full min-w-0 flex-1 items-end gap-1 overflow-x-auto">
                {ainaIds.map((id) => {
                  const name = canvases[id]?.name ?? id;
                  const active = id === activeAinaId;
                  return (
                    <div
                      key={id}
                      className={classNames(
                        "flex h-8 max-w-[220px] shrink-0 items-center gap-1 rounded-t-lg border border-b-0 pl-3 pr-1 text-[12px]",
                        active ? "border-line bg-white font-semibold text-ink" : "border-transparent text-ink-muted hover:bg-white/60",
                      )}
                    >
                      <button type="button" role="tab" aria-selected={active} onClick={() => selectAina(id)} className="min-w-0 truncate">
                        {name}
                      </button>
                      <button
                        type="button"
                        onClick={() => closeAinas([id])}
                        className="rounded p-1 text-ink-subtle hover:bg-line/60 hover:text-ink"
                        aria-label={tCanvas("close", { name })}
                      >
                        <X className="h-3 w-3" />
                      </button>
                    </div>
                  );
                })}
              </div>
              <AinaSwitcher
                conversationAinaIds={[...(conversation?.active_aina_ids ?? []), ...usedAinaIds]}
                openAinaIds={ainaIds}
                onSelect={(id) => openAinas([id])}
              />
              </div>
              <div className="relative min-h-0 flex-1">
                {ainaIds.map((id) => {
                  const opened = canvases[id];
                  // Inactive tabs stay mounted so switching back keeps their state.
                  return (
                    <div key={id} role="tabpanel" className={classNames("absolute inset-0 overflow-hidden", id !== activeAinaId && "hidden")}>
                      {opened ? (
                        <MainWidgetRenderer
                          key={`${profile.tenantId}:${profile.actorUserId}:${routeWorkspaceId ?? "independent"}:${opened.main_widget.id}`}
                          ainaId={opened.aina_id}
                          widget={opened.main_widget}
                          workspaceId={routeWorkspaceId}
                          documentName={documentName}
                          disabled={false}
                          refreshToken={lastRun?.trace_id}
                          onOpenAina={(next) => openAinas([next])}
                          onPrompt={(prompt) => void sendMessage(prompt)}
                          onDocumentTaskContextChange={setDocumentTaskContext}
                        />
                      ) : (
                        <div className="h-full animate-pulse bg-line/60" />
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          </section>
        ) : null}
      </div>
    </div>
      {chooser ? (
        <AinaChooserDialog
          widget={chooser}
          openAinaIds={ainaIds}
          onOpen={(ids) => {
            setChooser(null);
            openAinas(ids);
          }}
          onClose={() => {
            for (const app of chooser.apps) declinedAinaIdsRef.current.add(app.aina_id);
            setChooser(null);
          }}
        />
      ) : null}
      {conversation?.id && obsOpen ? (
        <ConversationObsDrawer
          sessionId={conversation.id}
          onClose={() => setObsOpen(false)}
        />
      ) : null}
    </>
  );
}

function ConversationMessage({
  message,
  onOpenAina,
  onPrompt,
  debugMode,
  conversationId,
  workspaceId,
  failures,
  toolResultsByCallId,
  requestedToolCallIds,
  showToolHeader,
  toolHeaderCount,
}: {
  message: BackendMessage;
  onOpenAina: (ainaId: string) => void;
  onPrompt: (prompt: string) => void;
  debugMode: boolean;
  conversationId: string;
  workspaceId: string | null;
  failures: MessageFailure[];
  toolResultsByCallId: ReadonlyMap<string, BackendMessage>;
  requestedToolCallIds: ReadonlySet<string>;
  showToolHeader: boolean;
  toolHeaderCount: number;
}) {
  const failure = failures[0] ?? null;
  if (message.role === "system") return null;
  if (message.role === "user") {
    return (
      <div className="space-y-2">
        <UserMessage content={message.content} />
        {failure && message.trace_id ? (
          <FailedCallNotice conversationId={conversationId} traceId={message.trace_id} failure={failure} />
        ) : null}
      </div>
    );
  }
  if (message.role === "tool") {
    if (message.tool_call_id && requestedToolCallIds.has(message.tool_call_id)) return null;
    return <ToolResultCard message={message} debugMode={debugMode} />;
  }
  const hasToolCalls = Boolean(message.tool_calls?.length);
  return (
    <div className="space-y-2">
      {message.content ? (
        <AssistantMessage
          conversationId={conversationId}
          message={{
            id: message.id,
            role: "assistant",
            content: message.content,
            createdAt: message.created_at,
            runState: "done",
          }}
        />
      ) : null}
      {hasToolCalls ? (
        <ToolCallList
          calls={message.tool_calls ?? []}
          resultsByCallId={toolResultsByCallId}
          debugMode={debugMode}
          showHeader={showToolHeader}
          headerCount={toolHeaderCount}
        />
      ) : null}
      {message.widgets?.filter((widget) => !isClarificationWidget(widget)).map((widget) => (
        <SessionWidgetRenderer
          key={widget.id}
          widget={widget}
          workspaceId={workspaceId}
          onOpenAina={onOpenAina}
          onPrompt={onPrompt}
        />
      ))}
    </div>
  );
}

function FailedCallNotice({ conversationId, traceId, failure }: { conversationId: string; traceId: string; failure: MessageFailure }) {
  const { t } = useTranslation("chatPage");
  const logHref = failure.kind === "capability"
    ? `/obs?sessionId=${encodeURIComponent(conversationId)}&tab=logs&traceId=${encodeURIComponent(traceId)}`
    : `/obs?sessionId=${encodeURIComponent(conversationId)}&tab=logs&traceId=${encodeURIComponent(traceId)}&logId=${encodeURIComponent(failure.callId ?? "")}`;
  return (
    <div className="flex items-center gap-2 rounded-lg border border-danger-ring bg-danger-soft px-3 py-2">
      <AlertTriangle className="h-4 w-4 shrink-0 text-danger" />
      <span className="min-w-0 flex-1 truncate text-[12px] text-danger-deep">
        {failure.kind === "capability" ? t("failure.capability", { name: failure.name, error: failure.error }) : t("failure.call", { error: failure.error })}
      </span>
      <Link
        to={logHref}
        className="shrink-0 text-[11.5px] font-bold text-danger-deep hover:underline"
      >
        {t("viewRawLogs")}
      </Link>
    </div>
  );
}

function RunSummary({ response }: { response: ChatResponse }) {
  const { t } = useTranslation("chatPage");
  return (
    <div className="flex items-center justify-end gap-2 text-[10.5px] text-ink-muted">
      <span>{t("iterations", { count: response.iterations })}</span>
      <span>·</span>
      <span>{response.usage.estimated ? "≈" : ""}{response.usage.input_tokens + response.usage.output_tokens} Tokens</span>
      <span>·</span>
      <Link to={`/obs?sessionId=${encodeURIComponent(response.conversation_id)}`} className="text-accent hover:underline">
        {t("viewCalls")}
      </Link>
    </div>
  );
}

function StoppedNotice({ onResume }: { onResume: () => void }) {
  const { t } = useTranslation("chatPage");
  return (
    <div className="flex items-center gap-2.5 rounded-lg border border-line bg-white p-3">
      <CirclePause className="h-4 w-4 text-ink-muted" />
      <span className="flex-1 text-[12.5px] text-ink-muted">{t("stopped")}</span>
      <button type="button" onClick={onResume} className="btn-outline h-8">
        <Play className="h-3.5 w-3.5" />{t("resume")}
      </button>
    </div>
  );
}

function ErrorNotice({ message, detailsHref, onDismiss }: { message: string; detailsHref: string | null; onDismiss: () => void }) {
  const { t } = useTranslation("chatPage");
  return (
    <div className="rounded-lg border border-danger-ring bg-danger-soft p-3 flex items-center gap-2.5">
      <AlertTriangle className="w-4 h-4 text-danger" />
      <span className="flex-1 text-[12.5px] text-danger-deep">{message}</span>
      {detailsHref ? <Link to={detailsHref} className="shrink-0 text-[11.5px] font-bold text-danger-deep hover:underline">{t("viewRawLogs")}</Link> : null}
      <button type="button" onClick={onDismiss} aria-label={t("dismissError")}>
        <X className="w-4 h-4 text-danger" />
      </button>
    </div>
  );
}

function ChatComposer({
  disabled,
  running,
  initialText,
  sessionId,
  context,
  onSend,
  onStop,
}: {
  disabled: boolean;
  running: boolean;
  initialText: string;
  sessionId: string | null;
  context: DocumentTaskContext | null;
  onSend: (text: string) => Promise<boolean | undefined>;
  onStop: () => void;
}) {
  const { t } = useTranslation("chatPage");
  const { t: tCanvas } = useTranslation("canvas");
  const [text, setText] = useState(initialText);
  const [sendFailed, setSendFailed] = useState(false);
  const composingRef = useRef(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const value = text.trim();
    if (!value || disabled) return;
    setSendFailed(false);
    const sent = await onSend(value);
    if (sent) setText((current) => current === text ? "" : current);
    else if (sent === false) setSendFailed(true);
  }

  return (
    <div className="bg-white px-4 pb-5 pt-3 md:px-6">
      <div className="mx-auto max-w-[760px] space-y-2">
        <TaskTreeWidget sessionId={sessionId} />
        {sendFailed ? <p role="alert" className="text-[11.5px] text-danger-deep">{t("sendFailed")}</p> : null}
        <form
          onSubmit={submit}
          className="rounded-2xl border border-line-strong bg-white px-4 py-3 shadow-soft focus-within:border-accent"
        >
          {context ? (
            <div className="mb-2 flex min-w-0 items-center gap-1.5 rounded-md bg-accent-soft px-2 py-1.5 text-[10.5px] text-accent">
              <Sparkles className="h-3 w-3 shrink-0" />
              <span className="truncate">{tCanvas("context", { task: context.taskTitle, section: context.sectionHeading })}</span>
            </div>
          ) : null}
          <textarea
            value={text}
            onChange={(event) => { setText(event.target.value); setSendFailed(false); }}
            onCompositionStart={() => { composingRef.current = true; }}
            onCompositionEnd={() => { composingRef.current = false; }}
            onKeyDown={(event) => {
              if (composingRef.current || event.nativeEvent.isComposing || event.keyCode === 229) return;
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                submit(event);
              }
            }}
            disabled={disabled}
            rows={1}
            placeholder={context ? tCanvas("placeholderEdit") : t("composerPlaceholder")}
            aria-label={t("messageAria")}
            className="w-full resize-none bg-transparent text-[14px] leading-[1.7] text-ink outline-none placeholder:text-ink-subtle disabled:opacity-60"
          />
          <div className="mt-2 flex items-center justify-between gap-2">
            <ModelSelector disabled={disabled} />
            {running ? (
              <button
                type="button"
                onClick={onStop}
                className="flex h-8 w-8 items-center justify-center rounded-full bg-ink text-white transition-colors hover:bg-ink-muted"
                aria-label={t("stopAria")}
              >
                <Square className="h-3 w-3 fill-current" />
              </button>
            ) : (
              <button
                type="submit"
                disabled={disabled || !text.trim()}
                className={classNames(
                  "flex h-8 w-8 items-center justify-center rounded-full text-white transition-colors",
                  !disabled && text.trim()
                    ? "bg-accent hover:bg-accent-hover"
                    : "bg-ink cursor-not-allowed opacity-80",
                )}
                aria-label={t("sendAria")}
              >
                <ArrowUp className="w-4 h-4" />
              </button>
            )}
          </div>
        </form>
      </div>
    </div>
  );
}

function WelcomePanel() {
  const { t } = useTranslation("chatPage");
  return (
    <div className="min-h-[420px] flex items-center justify-center">
      <div className="max-w-lg text-center">
        <div className="mx-auto w-14 h-14 rounded-2xl bg-accent-soft text-accent flex items-center justify-center">
          <Bot className="w-7 h-7" />
        </div>
        <h2 className="mt-4 text-[22px] font-extrabold font-display text-ink">{t("welcome.title")}</h2>
        <p className="mt-2 text-[13px] leading-relaxed text-ink-muted">
          {t("welcome.body")}
        </p>
        <div className="mt-5 grid grid-cols-3 gap-2 text-left">
          {[
            [t("welcome.f1.title"), t("welcome.f1.body")],
            [t("welcome.f2.title"), t("welcome.f2.body")],
            [t("welcome.f3.title"), t("welcome.f3.body")],
          ].map(([label, detail]) => (
            <div key={label} className="rounded-lg border border-line bg-white p-3">
              <div className="text-[12px] font-bold text-ink">{label}</div>
              <div className="mt-1 text-[10.5px] text-ink-muted">{detail}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function DeletedConversation({ title, onRestore }: { title: string; onRestore: () => void }) {
  const { t } = useTranslation("chatPage");
  return (
    <div className="min-h-[420px] flex items-center justify-center">
      <div className="text-center">
        <Trash2 className="mx-auto w-10 h-10 text-ink-subtle" />
        <h2 className="mt-3 text-[17px] font-bold text-ink">{t("deleted.title", { title })}</h2>
        <p className="mt-1 text-[12.5px] text-ink-muted">{t("deleted.body")}</p>
        <button type="button" onClick={onRestore} className="btn-primary mt-4">
          <RotateCcw className="w-4 h-4" />{t("deleted.restore")}
        </button>
      </div>
    </div>
  );
}

function CanvasPaneToggle({ pane, onChange }: { pane: "chat" | "app"; onChange: (pane: "chat" | "app") => void }) {
  const { t } = useTranslation("canvas");
  const options = [
    { value: "chat", label: t("chat"), aria: t("showChat"), Icon: MessageSquareText },
    { value: "app", label: t("app"), aria: t("showApp"), Icon: PanelRightOpen },
  ] as const;
  return (
    <div className="flex h-8 items-center rounded-md border border-line bg-app-soft p-0.5 lg:hidden" aria-label={t("mobileView")}>
      {options.map(({ value, label, aria, Icon }) => (
        <button
          key={value}
          type="button"
          onClick={() => onChange(value)}
          className={classNames(
            "flex h-6 items-center gap-1 rounded px-2 text-[10px] font-bold",
            pane === value ? "bg-white text-accent shadow-sm" : "text-ink-muted",
          )}
          aria-label={aria}
        >
          <Icon className="h-3.5 w-3.5" />{label}
        </button>
      ))}
    </div>
  );
}

function documentTaskUiContext(context: DocumentTaskContext): string {
  return [
    "The user is reviewing a section draft in the document editor. When the user says \"this task\" or \"this section\", use this exact context:",
    `Document: ${context.documentName}`,
    `Task title: ${context.taskTitle}`,
    `Task id: ${context.taskId}`,
    `Task status: ${context.taskStatus}`,
    `Section: ${context.sectionHeading}`,
    `Section id: ${context.sectionId}`,
    `Current draft revision: ${context.draftRevision}`,
    "To have the AI revise it further, call document.edit_task.ai_revise; never update the formal document directly.",
  ].join("\n");
}

function ChatSkeleton() {
  return (
    <div className="space-y-3 py-6">
      {["w-2/3", "w-1/2", "w-3/4"].map((width) => (
        <div key={width} className={classNames("h-16 rounded-lg bg-line/60 animate-pulse", width)} />
      ))}
    </div>
  );
}
