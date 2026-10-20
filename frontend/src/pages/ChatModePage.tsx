import { useTranslation } from "react-i18next";
import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowUp,
  Bot,
  Check,
  CirclePause,
  Loader2,
  MessageSquareText,
  PanelRightClose,
  Paperclip,
  PanelRightOpen,
  Play,
  Plus,
  RotateCcw,
  Sparkles,
  Square,
  Trash2,
  X,
} from "lucide-react";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { AinaLauncher } from "@/components/canvas/AinaLauncher";
import { CanvasTabs, NEW_CANVAS_TAB } from "@/components/canvas/CanvasTabs";
import { ApprovalCard, type EditedArguments } from "@/components/chat/ApprovalCard";
import { AssistantMessage, UserMessage } from "@/components/chat/MessageBubble";
import { ModelSelector } from "@/components/chat/ModelSelector";
import { applyLiveEvent, LiveTurn, type LiveItem } from "@/components/chat/LiveTurn";
import { isToolSequenceContinuation, toolLabel, ToolCallList, ToolResultCard } from "@/components/chat/ToolCallCard";
import { ReasoningBlock } from "@/components/chat/ReasoningBlock";
import { completedAnswerIds, finishedTurnProcesses, TurnProcessToggle } from "@/components/chat/TurnProcess";
import { ConversationObsDrawer } from "@/components/observability/ConversationObsDrawer";
import { announceConversationRunning, notifyConversationsChanged } from "@/components/layout/Sidebar";
import { Topbar } from "@/components/layout/Topbar";
import { MainWidgetRenderer } from "@/components/widgets/MainWidgetRenderer";
import { isClarificationWidget, SessionWidgetRenderer } from "@/components/widgets/SessionWidgetRenderer";
import { AinaTaskList } from "@/components/tasks/AinaTaskList";
import { useCanvasViewer } from "@/components/canvas/useCanvasViewer";
import { TaskTreeWidget } from "@/components/tasks/TaskTreeWidget";
import { api, ApiError, apiErrorMessage, streamChat, streamResume, type StreamEvent } from "@/lib/api";
import { useDebugMode } from "@/lib/debugMode";
import { getObsSession, loadLegacyPersonalObsSession } from "@/lib/obsData";
import { adaptSessionDetail } from "@/lib/obsAdapter";
import { useMockSession } from "@/lib/mockSession";
import { classNames, uid } from "@/lib/utils";
import { canvasQuery, readCanvasRoute, workspaceCanvasPath, workspaceChatPath, type CanvasRoute } from "@/lib/workspace";
import type {
  AinaCanvasResponse,
  ApprovalRecord,
  BackendMessage,
  ChatResponse,
  ConversationRecord,
  DocumentTaskContext,
  LLMCallRecord,
  MessageAttachment,
  TraceRecord,
  TraceSpan,
  WidgetDefinition,
} from "@/types";

interface MessageFailure {
  kind: "llm" | "capability";
  name: string;
  error: string;
  code?: string;
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
  const loadingConversation = loading && !conversation;
  const [sending, setSending] = useState(false);
  const [composerVersion, setComposerVersion] = useState(0);
  const composerConversationIdRef = useRef<string | null>(conversationId ?? null);
  const [optimisticUser, setOptimisticUser] = useState<BackendMessage | null>(null);
  // Archived message count when the optimistic message was sent; a later archived copy replaces it.
  // Archive length when the local streamed turn began; null when no such turn is running.
  const optimisticBaselineRef = useRef<number | null>(null);
  const [liveItems, setLiveItems] = useState<LiveItem[]>([]);
  const [activity, setActivity] = useState<string | null>(null);
  const [errorInfo, setErrorInfo] = useState<{ message: string; code: string | null } | null>(null);
  const error = errorInfo?.message ?? null;
  const setError = useCallback((message: string | null, code: string | null = null) => {
    setErrorInfo(message ? { message, code } : null);
  }, []);
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
  // AINAs opened in this conversation since it was loaded; the loaded record lists the earlier ones.
  const [usedAinaIds, setUsedAinaIds] = useState<string[]>([]);
  const [mobilePane, setMobilePane] = useState<"chat" | "app">("app");
  const [documentTaskContext, setDocumentTaskContext] = useState<DocumentTaskContext | null>(null);
  const canvasRouteRef = useRef(canvasRoute);
  canvasRouteRef.current = canvasRoute;
  const canvasesRef = useRef(canvases);
  canvasesRef.current = canvases;
  const activeCanvas = activeAinaId ? canvases[activeAinaId] : undefined;
  // A browser-style "new tab" in the canvas: it shows the app launcher until the user picks an app.
  const [newTab, setNewTab] = useState<"active" | "background" | null>(null);
  const [newTabIndex, setNewTabIndex] = useState<number | null>(null);
  const [canvasExpanded, setCanvasExpanded] = useState(false);
  const canvasOpen = ainaIds.length > 0 || newTab !== null;
  // The title-bar toggle hides the canvas without closing its tabs, so their state survives.
  const [canvasHidden, setCanvasHidden] = useState(false);
  const canvasVisible = canvasOpen && !canvasHidden;
  const newTabShown = newTab === "active" || (newTab !== null && !activeAinaId);
  const canvasTabs = ainaIds.map((id) => ({ id, label: canvases[id]?.name ?? id, kind: canvases[id]?.main_widget.kind }));
  if (newTab) canvasTabs.splice(Math.min(newTabIndex ?? ainaIds.length, ainaIds.length), 0, { id: NEW_CANVAS_TAB, label: tCanvas("launcher.newTab"), kind: "panel" });
  const endRef = useRef<HTMLDivElement | null>(null);
  const scrollerRef = useRef<HTMLDivElement | null>(null);
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
      // A failed attempt can be corrected by the model without failing the turn.
      const trace = traces.find((item) => item.trace_id === traceId);
      if (trace && trace.status !== "failed") return;
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
        push(trace.trace_id, { kind: "capability", name: span.target_id || span.name, error: typeof message === "string" ? message : t("err.capabilityFailed"), code: typeof err?.code === "string" ? err.code : undefined });
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
  const recordedError = traces.find((trace) => trace.trace_id === errorTraceId)?.spans
    .find((span) => span.kind === "agent" && span.status === "failed")?.error;
  const errorCode = errorInfo?.code ?? (typeof recordedError?.code === "string" ? recordedError.code : null);

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
      optimisticBaselineRef.current = null;
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
    setUsedAinaIds([]);
    setNewTab(null);
    setNewTabIndex(null);
    setCanvasExpanded(false);
    setCanvasHidden(false);
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
      optimisticBaselineRef.current = null;
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
    };
    window.addEventListener("unibot:new-conversation", reset);
    return () => window.removeEventListener("unibot:new-conversation", reset);
  }, []);

  /** Navigates to the conversation with `route`'s canvases open beside it. */
  function navigateCanvas(route: CanvasRoute, targetConversationId: string | null, replace = false) {
    const query = canvasQuery(route);
    navigate(`${workspaceChatPath(routeWorkspaceId, targetConversationId)}${query ? `?${query}` : ""}`, { replace });
  }

  // Canvas tool calls (AINA 2.0 §9.5) reach the canvas of this tab while it shows the conversation.
  useCanvasViewer(conversation?.id ?? null, (id) => openAinas([id]));

  /** Opens `ids` as tabs beside the conversation, activating the first; open tabs are kept. */
  function openAinas(ids: string[], targetConversationId = conversation?.id ?? conversationId ?? null) {
    if (!ids.length) return;
    setMobilePane("app");
    setCanvasHidden(false);
    setNewTab((current) => current === "active" ? "background" : current);
    const route = canvasRouteRef.current;
    navigateCanvas({
      ...route,
      ainaIds: [...route.ainaIds, ...ids.filter((id) => !route.ainaIds.includes(id))],
      activeAinaId: ids[0],
    }, targetConversationId);
  }

  /** Hides or shows the canvas; with nothing open it brings back this conversation's apps, or a new tab if it has none. */
  function toggleCanvas() {
    if (canvasVisible) {
      setCanvasHidden(true);
      return;
    }
    setCanvasHidden(false);
    setMobilePane("app");
    if (canvasOpen) return;
    const previous = [...new Set([...(conversation?.active_aina_ids ?? []), ...usedAinaIds])];
    if (previous.length) openAinas(previous);
    else setNewTab("active");
  }

  function selectAina(id: string) {
    setNewTab((current) => current === "active" ? "background" : current);
    navigateCanvas({ ...canvasRouteRef.current, activeAinaId: id }, conversation?.id ?? conversationId ?? null, true);
  }

  function reorderCanvasTabs(ids: string[]) {
    const route = canvasRouteRef.current;
    const reordered = ids.filter((id) => id !== NEW_CANVAS_TAB);
    setNewTabIndex(newTab ? ids.indexOf(NEW_CANVAS_TAB) : null);
    if (reordered.every((id, index) => id === route.ainaIds[index])) return;
    navigateCanvas({ ...route, ainaIds: reordered }, conversation?.id ?? conversationId ?? null, true);
  }

  useEffect(() => {
    if (!newTab) setNewTabIndex(null);
  }, [newTab]);

  function closeAinas(ids: string[], replace = false) {
    const route = canvasRouteRef.current;
    setNewTabIndex((index) => index === null ? null : index - route.ainaIds.slice(0, index).filter((id) => ids.includes(id)).length);
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

  useEffect(() => {
    const id = conversation?.id;
    if (!id || !sending) return;
    announceConversationRunning(id, true);
    return () => announceConversationRunning(id, false);
  }, [conversation?.id, sending]);

  // Opening or closing a canvas resizes the message list and re-wraps its text, which leaves the old
  // scroll offset short of the bottom. Stay pinned to the latest message if the user was already there.
  useEffect(() => {
    const scroller = scrollerRef.current;
    if (!scroller) return;
    let pinned = true;
    const onScroll = () => {
      pinned = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 48;
    };
    const observer = new ResizeObserver(() => {
      if (pinned) scroller.scrollTop = scroller.scrollHeight;
    });
    scroller.addEventListener("scroll", onScroll, { passive: true });
    observer.observe(scroller);
    return () => {
      scroller.removeEventListener("scroll", onScroll);
      observer.disconnect();
    };
  }, []);

  const sendMessage = (text: string, attachments: MessageAttachment[] = []) => runTurn(text, attachments);

  /** Runs a turn for `text` with uploaded `attachments`, or continues the conversation's stopped turn when `text` is null. */
  async function runTurn(text: string | null, attachments: MessageAttachment[] = []) {
    if (sending || deleted) return;
    setClarificationWidgets([]);
    const draftTitle = text === null ? null : text.length > 24 ? `${text.slice(0, 24)}…` : text;
    const localMessage: BackendMessage | null = text === null ? null : {
      id: uid("local"),
      role: "user",
      content: text,
      content_type: "text",
      widgets: [],
      attachments,
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
    let streamFailureCode: string | null = null;
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
        // Capability errors are tool feedback for the model; terminal errors come from the stream producer.
        if (event.type === "error" && event.source === "capability") return;
        if (event.type === "message.completed") completion = event.response;
        if (event.type === "error") {
          streamFailure = event.error?.message ?? event.code ?? t("err.streamFailed");
          streamFailureCode = event.error?.code ?? event.code ?? null;
        }
        if (!isActiveRun()) return;
        setLiveItems((current) => applyLiveEvent(current, event));
        if (event.type === "approval.required") setActivity(t("activity.awaitingApproval"));
        if (event.type === "error") {
          setError(streamFailure, streamFailureCode);
        }
      };
      if (text === null) {
        await streamResume(targetConversation.id, actor, onEvent, streamController.signal);
      } else {
        await streamChat(
          {
            message: text,
            attachments: attachments.length ? attachments.map((item) => item.file_ref) : undefined,
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
      const openedAppId = completed.opened_app_id ?? openAction?.aina_id;
      // Only an explicit open_app call opens the canvas; suggestions remain chips under the reply.
      if (openedAppId && isActiveRun()) openAinas([openedAppId], completed.conversation_id);
      return true;
    } catch (sendError) {
      if (isActiveRun()) {
        if (runConversationId) await loadConversation(runConversationId, true);
        if (isActiveRun()) {
          const body = sendError instanceof ApiError ? sendError.body as { error?: { code?: string } } | null : null;
          setError(apiErrorMessage(sendError), streamFailureCode ?? body?.error?.code ?? null);
        }
        return Boolean(completion);
      }
    } finally {
      const activeRun = isActiveRun();
      if (runGenerationRef.current === runGeneration) {
        optimisticBaselineRef.current = null;
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

  async function resolveApproval(action: "confirm" | "deny", edited: EditedArguments = {}) {
    if (!approval) return;
    const decided = approval;
    // The decision is made: drop the card now rather than after the approved calls finish running.
    setApproval(null);
    setSending(true);
    setError(null);
    setActivity(action === "confirm" ? t("activity.runningApproved") : t("activity.cancelling"));
    try {
      if (action === "confirm") {
        const response = await api.post<ChatResponse>(`/approvals/${decided.id}/confirm`, { ...actor, edited_arguments: edited });
        setLastRun(response);
      } else {
        await api.post(`/approvals/${decided.id}/deny`, actor);
      }
      await loadConversation(decided.conversation_id);
      notifyConversationsChanged();
    } catch (approvalError) {
      // Reload so a still-pending approval comes back to be decided again.
      await loadConversation(decided.conversation_id, true);
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
    const baseline = optimisticBaselineRef.current;
    if (!sending || baseline === null) return archived;
    // The run archives its messages as it goes and the page reloads the conversation meanwhile, but the live turn
    // already shows those steps: until it ends, show the archive only up to the turn and its user message.
    const echoed = archived
      .slice(baseline)
      .find((message) => message.role === "user" && message.content === optimisticUser?.content);
    const user = echoed ?? optimisticUser;
    return user ? [...archived.slice(0, baseline), user] : archived.slice(0, baseline);
  }, [conversation?.messages, optimisticUser, sending]);
  const hasRecordedRunError = messages.some((message) => (
    message.role === "user" && message.trace_id === errorTraceId && failuresByTrace.has(message.trace_id ?? "")
  ));
  const toolResultsByCallId = useMemo(() => new Map(
    messages
      .filter((message) => message.role === "tool" && message.tool_call_id)
      .map((message) => [message.tool_call_id as string, message]),
  ), [messages]);
  const requestedToolCallIds = useMemo(() => new Set(
    messages.flatMap((message) => message.tool_calls?.map((call) => call.id) ?? []),
  ), [messages]);
  // Finished turns fold their intermediate steps behind one "Completed in …" toggle; stopped or failed turns stay open.
  const turnProcesses = useMemo(
    () => finishedTurnProcesses(messages, !sending && conversation?.run_status === "idle"),
    [messages, sending, conversation?.run_status],
  );
  const answerIds = useMemo(
    () => completedAnswerIds(messages, !sending && conversation?.run_status === "idle"),
    [messages, sending, conversation?.run_status],
  );
  const [openTurnIds, setOpenTurnIds] = useState<ReadonlySet<string>>(() => new Set());
  const activeTurnStart = sending ? messages.map((message) => message.role).lastIndexOf("user") : -1;
  const activeTurnId = activeTurnStart >= 0 ? messages[activeTurnStart].id : "live";
  const activeProcessOpen = openTurnIds.has(activeTurnId);
  const activeSteps = useMemo(() => {
    if (!sending) return [];
    const following = messages.slice(activeTurnStart + 1);
    const end = following.findIndex((message) => message.id.startsWith("msg_aina_task_"));
    return (end < 0 ? following : following.slice(0, end)).filter((message) => message.role !== "system");
  }, [messages, sending, activeTurnStart]);
  const activeStepIds = new Set(activeSteps.map((message) => message.id));
  const latestLiveItem = liveItems[liveItems.length - 1];
  const latestStep = activeSteps[activeSteps.length - 1];
  const latestCall = latestStep?.tool_calls?.slice(-1)[0];
  const activeType = activity ? "activity" : latestLiveItem?.kind
    ?? (latestCall || latestStep?.role === "tool" ? "tool" : latestStep?.content ? "text" : "reasoning");
  const activePreview = activity || (latestLiveItem
    ? latestLiveItem.kind === "tool" ? toolLabel(latestLiveItem.name) : latestLiveItem.text
    : latestCall ? toolLabel(latestCall.function.name)
      : latestStep?.role === "tool" ? toolLabel(latestStep.name ?? "")
        : latestStep?.content || latestStep?.reasoning);
  const foldedStepIds = useMemo(() => new Set(
    [...turnProcesses].flatMap(([turnId, process]) => openTurnIds.has(turnId) ? [] : [...process.stepIds]),
  ), [turnProcesses, openTurnIds]);
  const foldedReasoningIds = useMemo(() => new Set(
    [...turnProcesses].flatMap(([turnId, process]) => openTurnIds.has(turnId) ? [] : [process.answerId]),
  ), [turnProcesses, openTurnIds]);
  function toggleTurn(turnId: string) {
    setOpenTurnIds((current) => {
      const next = new Set(current);
      if (!next.delete(turnId)) next.add(turnId);
      return next;
    });
  }

  const title = conversation?.title === "New conversation" ? t("newConversation") : conversation?.title ?? t("newConversation");
  return (
    <>
    <div className="flex h-full flex-col bg-app-bg">
      <Topbar
        title={title}
        actions={!deleted ? (
          <div className="flex items-center gap-1">
            {canvasVisible ? <CanvasPaneToggle pane={mobilePane} onChange={(pane) => { setMobilePane(pane); setCanvasExpanded(false); }} /> : null}
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
                className={TITLE_ICON_BUTTON}
                aria-label={t("viewObsAria")}
                title={t("viewObsAria")}
              >
                <Activity className="h-4 w-4" />
              </button>
            ) : null}
            <button
              type="button"
              onClick={toggleCanvas}
              className={classNames(TITLE_ICON_BUTTON, canvasVisible && "bg-app-soft text-ink")}
              aria-label={canvasVisible ? t("canvasHide") : t("canvasShow")}
              aria-pressed={canvasVisible}
              title={canvasVisible ? t("canvasHide") : t("canvasShow")}
            >
              {canvasVisible ? <PanelRightClose className="h-4 w-4" /> : <PanelRightOpen className="h-4 w-4" />}
            </button>
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
        canvasVisible ? classNames("grid grid-cols-1", !canvasExpanded && "lg:grid-cols-[400px_minmax(0,1fr)]") : "flex flex-col",
      )}>
      <div className={classNames(
        "min-h-0 flex-1 flex-col overflow-hidden",
        canvasVisible ? canvasExpanded ? "hidden" : classNames("lg:flex", mobilePane === "chat" ? "flex" : "hidden") : "flex",
      )}>
        {/* `relative` keeps absolutely positioned descendants (sr-only labels) inside this scroller; otherwise they
            stretch the document and scrollIntoView scrolls the whole page, dragging the composer up. */}
        <div ref={scrollerRef} className="relative min-h-0 flex-1 overflow-y-auto px-4 py-8 md:px-6 md:py-10" aria-live="polite">
          <div className="mx-auto max-w-[760px] space-y-7">
              {loadingConversation ? <ChatSkeleton /> : null}
              {!loadingConversation && deleted ? (
                <DeletedConversation title={title} onRestore={() => void restoreConversation()} />
              ) : null}
              {!loadingConversation && !deleted && messages.length === 0 ? <WelcomePanel /> : null}
              {!deleted
                ? messages.map((message, index) => {
                    if (message.role === "tool" && message.tool_call_id && requestedToolCallIds.has(message.tool_call_id)) return null;
                    if (foldedStepIds.has(message.id)) return null;
                    if (sending && !activeProcessOpen && activeStepIds.has(message.id)) return null;
                    const continuesToolSequence = isToolSequenceContinuation(messages, index);
                    const process = turnProcesses.get(message.id);
                    return (
                      <Fragment key={message.id}>
                        <div className={continuesToolSequence ? "!mt-2" : undefined}>
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
                            showReasoning={!foldedReasoningIds.has(message.id)}
                            showActions={answerIds.has(message.id)}
                          />
                        </div>
                        {sending && message.id === activeTurnId ? (
                          <TurnProcessToggle
                            durationMs={null}
                            active
                            activityType={activeType}
                            preview={activePreview}
                            open={activeProcessOpen}
                            onToggle={() => toggleTurn(activeTurnId)}
                          />
                        ) : process ? (
                          <TurnProcessToggle
                            durationMs={process.durationMs}
                            open={openTurnIds.has(message.id)}
                            onToggle={() => toggleTurn(message.id)}
                          />
                        ) : null}
                      </Fragment>
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
              {sending && activeTurnStart < 0 ? (
                <TurnProcessToggle durationMs={null} active activityType={activeType} preview={activePreview} open={activeProcessOpen} onToggle={() => toggleTurn(activeTurnId)} />
              ) : null}
              {!sending || activeProcessOpen ? <LiveTurn items={liveItems} debugMode={debugMode} /> : null}
              {conversation?.run_status === "stopped" && !sending && !deleted ? (
                <StoppedNotice onResume={() => void runTurn(null)} />
              ) : null}
              {error && !hasRecordedRunError ? <ErrorNotice message={error} code={errorCode} detailsHref={errorLogHref} onDismiss={() => setError(null)} /> : null}
              {debugMode && lastRun && !sending ? <RunSummary response={lastRun} /> : null}
              <div ref={endRef} />
            </div>
          </div>
          {!deleted ? (
            <ChatComposer
              key={`${profile.tenantId}:${profile.actorUserId}:${routeWorkspaceId}:${composerVersion}`}
              disabled={sending || loadingConversation}
              running={sending && Boolean(conversation?.id)}
              initialText={initialPrompt}
              sessionId={conversation?.id ?? conversationId ?? null}
              approval={approval ? {
                id: approval.id,
                content: <ApprovalCard
                  key={approval.id}
                  approval={approval}
                  disabled={sending}
                  debugMode={debugMode}
                  onConfirm={(edited) => void resolveApproval("confirm", edited)}
                  onDeny={() => void resolveApproval("deny")}
                />,
              } : undefined}
              messageCount={conversation?.messages.length ?? 0}
              context={activeCanvas?.main_widget.kind === "document" ? documentTaskContext : null}
              onSend={sendMessage}
              onStop={() => void stopRun()}
              onBackgroundTaskSettled={() => { if (conversation?.id) void loadConversation(conversation.id, true); }}
            />
          ) : null}
        </div>
        {canvasOpen ? (
          <section className={classNames(
            "min-h-0 min-w-0 flex-col border-l border-line bg-white",
            canvasHidden ? "hidden" : classNames("lg:flex", mobilePane === "app" ? "flex" : "hidden"),
          )}>
            <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
              <CanvasTabs
                tabs={canvasTabs}
                activeId={newTabShown ? NEW_CANVAS_TAB : activeAinaId}
                expanded={canvasExpanded}
                onSelect={(id) => id === NEW_CANVAS_TAB ? setNewTab("active") : selectAina(id)}
                onClose={(id) => id === NEW_CANVAS_TAB ? setNewTab(null) : closeAinas([id])}
                onReorder={reorderCanvasTabs}
                onNew={() => setNewTab("active")}
                onExpand={() => { setCanvasExpanded((expanded) => !expanded); setMobilePane("app"); }}
              />
              <div className="relative min-h-0 flex-1">
                {ainaIds.map((id) => {
                  const opened = canvases[id];
                  // Inactive tabs stay mounted so switching back keeps their state.
                  return (
                    <div key={id} role="tabpanel" id={`canvas-panel-${id}`} aria-labelledby={`canvas-tab-${id}`} className={classNames("absolute inset-0 overflow-hidden", (newTabShown || id !== activeAinaId) && "hidden")}>
                      {opened ? (
                        <MainWidgetRenderer
                          key={`${profile.tenantId}:${profile.actorUserId}:${routeWorkspaceId ?? "independent"}:${opened.main_widget.id}`}
                          ainaId={opened.aina_id}
                          widget={opened.main_widget}
                          workspaceId={routeWorkspaceId}
                          documentName={documentName}
                          disabled={false}
                          refreshToken={lastRun?.trace_id}
                          conversationId={conversation?.id ?? null}
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
                {newTab ? (
                  <div role="tabpanel" id={`canvas-panel-${NEW_CANVAS_TAB}`} aria-labelledby={`canvas-tab-${NEW_CANVAS_TAB}`} aria-label={tCanvas("launcher.newTab")} className={classNames("absolute inset-0 overflow-y-auto", !newTabShown && "hidden")}>
                    <AinaLauncher
                      conversationAinaIds={[...(conversation?.active_aina_ids ?? []), ...usedAinaIds]}
                      openAinaIds={ainaIds}
                      onSelect={(id) => {
                        openAinas([id]);
                        setNewTab(null);
                      }}
                    />
                  </div>
                ) : null}
              </div>
            </div>
          </section>
        ) : null}
      </div>
    </div>
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
  showReasoning = true,
  showActions,
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
  showReasoning?: boolean;
  showActions: boolean;
}) {
  const failure = failures[0] ?? null;
  if (message.role === "system") return null;
  if (message.role === "user") {
    return (
      <div className="space-y-2">
        <UserMessage
          content={message.content}
          files={message.attachments?.map((item) => ({
            id: item.file_ref || item.name,
            name: item.name,
            sizeBytes: item.size_bytes,
            mimeType: item.content_type,
            to: item.document
              ? workspaceCanvasPath(workspaceId, "unibot-documents", conversationId || null, item.document)
              : undefined,
          }))}
        />
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
      {showReasoning && message.reasoning ? <ReasoningBlock text={message.reasoning} /> : null}
      {message.content ? (
        <AssistantMessage
          conversationId={conversationId}
          showActions={showActions}
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
        <ToolCallList calls={message.tool_calls ?? []} resultsByCallId={toolResultsByCallId} debugMode={debugMode} />
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
  const logHref = failure.kind === "capability"
    ? `/obs?sessionId=${encodeURIComponent(conversationId)}&tab=logs&traceId=${encodeURIComponent(traceId)}`
    : `/obs?sessionId=${encodeURIComponent(conversationId)}&tab=logs&traceId=${encodeURIComponent(traceId)}&logId=${encodeURIComponent(failure.callId ?? "")}`;
  return (
    <ErrorNotice
      message={`${failure.name}: ${failure.error}`}
      code={failure.code}
      detailsHref={logHref}
    />
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

function ErrorNotice({ message, code, detailsHref, onDismiss }: {
  message: string;
  code?: string | null;
  detailsHref: string | null;
  onDismiss?: () => void;
}) {
  const { t } = useTranslation("chatPage");
  return (
    <section role="alert" className="py-4">
      <div className="flex items-center justify-between gap-3 border-b border-line-strong pb-3">
        <h3 className="text-[14px] font-normal text-ink-subtle">{t("failure.title")}</h3>
        {onDismiss ? (
          <button type="button" onClick={onDismiss} aria-label={t("dismissError")} className="rounded p-1 text-ink-subtle hover:bg-app-soft hover:text-ink">
            <X className="h-3.5 w-3.5" />
          </button>
        ) : null}
      </div>
      <div className="flex min-h-7 items-center gap-1.5 pt-5 text-[13px]">
        <span aria-hidden="true" className="h-1.5 w-1.5 shrink-0 rounded-full bg-danger" />
        <strong className="shrink-0 font-semibold text-danger">{t("failure.type")}</strong>
        <span aria-hidden="true" className="mx-1 h-0.5 w-0.5 shrink-0 rounded-full bg-ink-subtle" />
        <span className="min-w-0 flex-1 truncate text-ink-muted" title={message}>
          {message.replace(/\s+/g, " ").trim()}
        </span>
        {code ? <span className="hidden max-w-[140px] truncate font-mono text-[11px] text-ink-subtle sm:block" title={code}>{code}</span> : null}
        {detailsHref ? <Link to={detailsHref} className="shrink-0 text-[11.5px] text-ink-subtle hover:text-accent hover:underline">{t("viewRawLogs")}</Link> : null}
      </div>
    </section>
  );
}

function ChatComposer({
  disabled,
  running,
  initialText,
  sessionId,
  approval,
  messageCount,
  context,
  onSend,
  onStop,
  onBackgroundTaskSettled,
}: {
  disabled: boolean;
  running: boolean;
  initialText: string;
  sessionId: string | null;
  approval?: { id: string; content: ReactNode };
  messageCount: number;
  context: DocumentTaskContext | null;
  onSend: (text: string, attachments: MessageAttachment[]) => Promise<boolean | undefined>;
  onStop: () => void;
  onBackgroundTaskSettled: () => void;
}) {
  const { t } = useTranslation("chatPage");
  const { t: tCanvas } = useTranslation("canvas");
  const { profile } = useMockSession();
  const [text, setText] = useState(initialText);
  const [sendFailed, setSendFailed] = useState(false);
  const [files, setFiles] = useState<PickedFile[]>([]);
  const [fileError, setFileError] = useState<string | null>(null);
  const composingRef = useRef(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const uploadQueueRef = useRef<PickedFile[]>([]);
  const activeUploadsRef = useRef(0);
  // Files removed while still uploading: their upload is discarded once it lands.
  const removedRef = useRef(new Set<string>());
  const actorQuery = new URLSearchParams({ user_id: profile.actorUserId, tenant_id: profile.tenantId }).toString();
  const filesReady = files.every((item) => item.status === "ready");

  function updateFile(key: string, change: Partial<PickedFile>) {
    setFiles((current) => current.map((item) => item.key === key ? { ...item, ...change } : item));
  }

  // Each file is uploaded when it is picked, a few at a time; the message then names it by file_ref.
  function queueUpload(item: PickedFile) {
    uploadQueueRef.current.push(item);
    startUploads();
  }

  function startUploads() {
    while (activeUploadsRef.current < MAX_PARALLEL_UPLOADS && uploadQueueRef.current.length) {
      const next = uploadQueueRef.current.shift()!;
      if (removedRef.current.delete(next.key)) continue;
      activeUploadsRef.current += 1;
      void upload(next).finally(() => {
        activeUploadsRef.current -= 1;
        startUploads();
      });
    }
  }

  async function upload(item: PickedFile) {
    const form = new FormData();
    form.append("file", item.file);
    try {
      const attachment = await api.postForm<MessageAttachment>(`/attachments?${actorQuery}`, form);
      if (removedRef.current.delete(item.key)) void discardUpload(attachment);
      else updateFile(item.key, { status: "ready", attachment });
    } catch (uploadError) {
      if (removedRef.current.delete(item.key)) return;
      updateFile(item.key, { status: "failed" });
      setFileError(t("attachmentFailed", { name: item.file.name, error: apiErrorMessage(uploadError) }));
    }
  }

  function discardUpload(attachment: MessageAttachment) {
    // A failed discard is harmless: the server deletes an upload no message names within a day.
    return api.delete(`/attachments/${attachment.file_ref}?${actorQuery}`).catch(() => undefined);
  }

  function addFiles(picked: FileList | null) {
    const chosen = Array.from(picked ?? []);
    const unreadable = chosen.find((file) => !isReadableFile(file.name));
    const tooLarge = chosen.find((file) => file.size > MAX_ATTACHMENT_MB * 1024 * 1024);
    const accepted = chosen.filter((file) => isReadableFile(file.name) && file.size <= MAX_ATTACHMENT_MB * 1024 * 1024);
    const room = Math.max(0, MAX_ATTACHMENTS - files.length);
    const added = accepted.slice(0, room).map((file): PickedFile => ({ key: uid("file"), file, status: "uploading" }));
    setFileError(
      unreadable ? t("attachmentUnsupported", { name: unreadable.name })
        : tooLarge ? t("attachmentTooLarge", { name: tooLarge.name, limit: MAX_ATTACHMENT_MB })
          : accepted.length > room ? t("tooManyAttachments", { limit: MAX_ATTACHMENTS })
            : null,
    );
    setFiles((current) => [...current, ...added]);
    added.forEach(queueUpload);
  }

  function removeFile(item: PickedFile) {
    setFiles((current) => current.filter((entry) => entry.key !== item.key));
    if (item.status === "uploading") removedRef.current.add(item.key);
    else if (item.attachment) void discardUpload(item.attachment);
  }

  function retryFile(item: PickedFile) {
    updateFile(item.key, { status: "uploading" });
    setFileError(null);
    queueUpload(item);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const value = text.trim();
    if (!value || disabled || !filesReady) return;
    setSendFailed(false);
    const sent = await onSend(value, files.flatMap((item) => item.attachment ? [item.attachment] : []));
    if (sent) {
      setText((current) => current === text ? "" : current);
      setFiles([]);
      setFileError(null);
    } else if (sent === false) setSendFailed(true);
  }

  return (
    <div className="bg-white px-4 pb-5 pt-3 md:px-6">
      <div className="mx-auto max-w-[760px] space-y-2">
        <AinaTaskList conversationId={sessionId} refreshKey={messageCount} onSettled={onBackgroundTaskSettled} />
        <TaskTreeWidget key={sessionId} sessionId={sessionId} approval={approval} />
        {sendFailed ? <p role="status" className="text-[11.5px] text-ink-muted">{t("sendFailed")}</p> : null}
        {fileError ? <p role="status" className="text-[11.5px] text-ink-muted">{fileError}</p> : null}
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
          {files.length ? (
            <div className="mb-2 flex flex-wrap gap-1.5">
              {files.map((item) => (
                <span
                  key={item.key}
                  data-status={item.status}
                  className={classNames(
                    "inline-flex h-7 max-w-[240px] items-center gap-1.5 rounded-lg border bg-app-soft px-2 text-[12px] font-semibold",
                    item.status === "failed" ? "border-danger-ring text-danger" : "border-line text-ink-muted",
                  )}
                >
                  {item.status === "uploading" ? (
                    <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" role="img" aria-label={t("attachmentUploading", { name: item.file.name })} />
                  ) : item.status === "failed" ? (
                    <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden />
                  ) : (
                    <Paperclip className="h-3.5 w-3.5 shrink-0" aria-hidden />
                  )}
                  <span className="truncate">{item.file.name}</span>
                  {item.status === "failed" ? (
                    <button
                      type="button"
                      onClick={() => retryFile(item)}
                      className="shrink-0 hover:text-ink"
                      aria-label={t("retryAttachment", { name: item.file.name })}
                    >
                      <RotateCcw className="h-3.5 w-3.5" />
                    </button>
                  ) : null}
                  <button
                    type="button"
                    onClick={() => removeFile(item)}
                    className="shrink-0 hover:text-ink"
                    aria-label={t("removeAttachment", { name: item.file.name })}
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </span>
              ))}
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
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={disabled}
                className="flex h-8 w-8 items-center justify-center rounded-md text-ink-muted transition-colors hover:bg-app-soft hover:text-ink disabled:opacity-60"
                aria-label={t("attachAria")}
              >
                <Paperclip className="h-4 w-4" />
              </button>
              <input
                ref={fileInputRef}
                type="file"
                multiple
                hidden
                accept={READABLE_EXTENSIONS.join(",")}
                data-testid="composer-files"
                onChange={(event) => { addFiles(event.target.files); event.target.value = ""; }}
              />
              <ModelSelector disabled={disabled} />
            </div>
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
                disabled={disabled || !text.trim() || !filesReady}
                className={classNames(
                  "flex h-8 w-8 items-center justify-center rounded-full text-white transition-colors",
                  !disabled && text.trim() && filesReady
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

// The backend's limits (conversations/attachments.py), checked here so the user learns before sending.
const MAX_ATTACHMENTS = 10;
const MAX_ATTACHMENT_MB = 20;
const MAX_PARALLEL_UPLOADS = 3;
// The files the backend can read (conversations/conversion.py); it refuses any other.
const READABLE_EXTENSIONS = [
  ".md", ".markdown", ".txt", ".text", ".log", ".rst",
  ".csv", ".tsv", ".json", ".jsonl", ".yaml", ".yml", ".xml", ".html", ".htm", ".toml", ".ini", ".cfg", ".conf",
  ".sql", ".py", ".ipynb", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".vue", ".svelte", ".css", ".scss",
  ".java", ".kt", ".scala", ".go", ".rs", ".c", ".h", ".cpp", ".cc", ".hpp", ".cs", ".rb", ".php", ".swift",
  ".dart", ".lua", ".r", ".sh", ".bash", ".ps1", ".bat", ".diff", ".patch",
  ".pdf", ".docx", ".xlsx",
];

function isReadableFile(name: string): boolean {
  const dot = name.lastIndexOf(".");
  return dot > 0 && READABLE_EXTENSIONS.includes(name.slice(dot).toLowerCase());
}

/** A file picked in the composer: uploaded right away, then sent by its file_ref. */
interface PickedFile {
  key: string;
  file: File;
  status: "uploading" | "ready" | "failed";
  attachment?: MessageAttachment;
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

const TITLE_ICON_BUTTON = "flex h-8 w-8 items-center justify-center rounded-md text-ink-muted transition-colors hover:bg-app-soft hover:text-ink";

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
  const { t } = useTranslation("chatPage");
  return (
    <div role="status" aria-label={t("loadingConversation")} className="space-y-3 py-6">
      {["w-2/3", "w-1/2", "w-3/4"].map((width) => (
        <div key={width} className={classNames("h-16 rounded-lg bg-line/60 animate-pulse", width)} />
      ))}
    </div>
  );
}
