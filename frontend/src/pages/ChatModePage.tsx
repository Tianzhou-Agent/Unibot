import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowUp,
  Bot,
  Check,
  RotateCcw,
  Trash2,
  Wrench,
  X,
} from "lucide-react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ApprovalCard } from "@/components/chat/ApprovalCard";
import { AssistantMessage, UserMessage } from "@/components/chat/MessageBubble";
import { ModelSelector } from "@/components/chat/ModelSelector";
import { ConversationObsDrawer } from "@/components/observability/ConversationObsDrawer";
import { notifyConversationsChanged } from "@/components/layout/Sidebar";
import { Topbar } from "@/components/layout/Topbar";
import { SessionWidgetRenderer } from "@/components/widgets/SessionWidgetRenderer";
import { TaskTreeWidget } from "@/components/tasks/TaskTreeWidget";
import { api, apiErrorMessage, streamChat, type StreamEvent } from "@/lib/api";
import { useDebugMode } from "@/lib/debugMode";
import { loadAllPersonalLlmCalls } from "@/lib/obsData";
import { useMockSession } from "@/lib/mockSession";
import { classNames, uid } from "@/lib/utils";
import type {
  AinaCanvasResponse,
  ApprovalRecord,
  BackendMessage,
  ChatResponse,
  ConversationRecord,
  LLMCallRecord,
  TraceRecord,
  TraceSpan,
} from "@/types";

interface MessageFailure {
  kind: "llm" | "capability";
  name: string;
  error: string;
  callId?: string;
}

export default function ChatModePage() {
  const { conversationId } = useParams<{ conversationId: string }>();
  const navigate = useNavigate();
  const { debugMode } = useDebugMode();
  const { profile } = useMockSession();
  const actor = useMemo(() => ({ user_id: profile.actorUserId, tenant_id: profile.tenantId }), [profile.actorUserId, profile.tenantId]);
  const [llmCalls, setLlmCalls] = useState<LLMCallRecord[]>([]);
  const [traces, setTraces] = useState<TraceRecord[]>([]);
  const [conversation, setConversation] = useState<ConversationRecord | null>(null);
  const [loading, setLoading] = useState(Boolean(conversationId));
  const [sending, setSending] = useState(false);
  const [optimisticUser, setOptimisticUser] = useState<BackendMessage | null>(null);
  const [streamText, setStreamText] = useState("");
  const [activity, setActivity] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [approval, setApproval] = useState<ApprovalRecord | null>(null);
  const [lastRun, setLastRun] = useState<ChatResponse | null>(null);
  const [renaming, setRenaming] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleted, setDeleted] = useState(false);
  const [obsOpen, setObsOpen] = useState(false);
  const [searchParams, setSearchParams] = useSearchParams();
  const endRef = useRef<HTMLDivElement | null>(null);
  const activeConversationIdRef = useRef<string | null>(conversationId ?? null);
  const localRunConversationIdRef = useRef<string | null>(null);
  const loadRequestRef = useRef(0);
  activeConversationIdRef.current = conversationId ?? null;
  useEffect(() => {
    if (!conversationId) return;
    let active = true;
    const actorQuery = `tenant_id=${encodeURIComponent(profile.tenantId)}&user_id=${encodeURIComponent(profile.actorUserId)}`;
    const querySuffix = `?${actorQuery}`;
    void Promise.all([
      loadAllPersonalLlmCalls(actorQuery),
      api.get<TraceRecord[]>(`/traces${querySuffix}`),
    ]).then(([calls, traceData]) => {
      if (active) {
        setLlmCalls(calls);
        setTraces(traceData);
      }
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
      push(call.trace_id, { kind: "llm", name: call.model, error: call.error ?? "模型调用失败", callId: call.call_id });
    }
    for (const trace of traces) {
      for (const span of trace.spans ?? []) {
        if (span.kind !== "tool" && span.kind !== "aina") continue;
        if (span.status !== "failed" && !span.error) continue;
        const err = span.error;
        const message = typeof err === "string" ? err : (err as Record<string, unknown> | null)?.message;
        push(trace.trace_id, { kind: "capability", name: span.target_id || span.name, error: typeof message === "string" ? message : "能力调用失败" });
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
      setConversation(record);
      setApproval(pendingApprovals[0] ?? null);
      setTitleDraft(record.title);
      setDeleted(false);
      if (localRunConversationIdRef.current !== id) {
        setSending(record.run_status === "running");
        setActivity(record.run_status === "running" ? "正在处理，请稍候…" : null);
      }
      setError(record.run_error ?? null);
    } catch (loadError) {
      if (requestId !== loadRequestRef.current || activeConversationIdRef.current !== id) return;
      setError(apiErrorMessage(loadError));
    } finally {
      if (!silent && requestId === loadRequestRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const continuingLocalRun = Boolean(
      conversationId && localRunConversationIdRef.current === conversationId,
    );
    setConversation((current) => (current?.id === conversationId ? current : null));
    if (!continuingLocalRun) {
      setOptimisticUser(null);
      setStreamText("");
      setActivity(null);
      setSending(false);
    }
    setApproval(null);
    setLastRun(null);
    setError(null);
    setDeleted(false);
    if (conversationId) {
      void loadConversation(conversationId);
    } else {
      loadRequestRef.current += 1;
      setConversation(null);
      setLoading(false);
    }
  }, [conversationId, loadConversation]);

  useEffect(() => {
    if (!conversationId || conversation?.run_status !== "running") return;
    const timer = window.setInterval(() => {
      void loadConversation(conversationId, true).then(notifyConversationsChanged);
    }, 600);
    return () => window.clearInterval(timer);
  }, [conversation?.run_status, conversationId, loadConversation]);

  useEffect(() => {
    const reset = () => {
      setConversation(null);
      setApproval(null);
      setLastRun(null);
      setError(null);
      setDeleted(false);
      setOptimisticUser(null);
      setStreamText("");
      setActivity(null);
      setSending(false);
    };
    window.addEventListener("unibot:new-conversation", reset);
    return () => window.removeEventListener("unibot:new-conversation", reset);
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: sending ? "smooth" : "auto" });
  }, [conversation?.messages, optimisticUser, streamText, activity, approval, sending]);

  async function sendMessage(text: string) {
    if (sending || deleted) return;
    const localMessage: BackendMessage = {
      id: uid("local"),
      role: "user",
      content: text,
      content_type: "text",
      widgets: [],
      created_at: new Date().toISOString(),
    };
    setOptimisticUser(localMessage);
    setStreamText("");
    setActivity(debugMode ? "正在连接模型…" : "正在处理，请稍候…");
    setError(null);
    setApproval(null);
    setSending(true);
    let runConversationId = conversation?.id ?? null;
    localRunConversationIdRef.current = runConversationId;
    let completion: ChatResponse | null = null;
    let streamFailure: string | null = null;
    try {
      let targetConversation = conversation;
      if (!targetConversation) {
        targetConversation = await api.post<ConversationRecord>("/conversations", {
          ...actor,
          title: text.length > 24 ? `${text.slice(0, 24)}…` : text,
          category: "general",
        });
        setConversation(targetConversation);
        setTitleDraft(targetConversation.title);
        runConversationId = targetConversation.id;
        localRunConversationIdRef.current = targetConversation.id;
        activeConversationIdRef.current = targetConversation.id;
        notifyConversationsChanged();
        navigate(`/chat/${targetConversation.id}`, { replace: true });
      }
      runConversationId = targetConversation.id;
      localRunConversationIdRef.current = targetConversation.id;
      await streamChat(
        {
          message: text,
          conversation_id: targetConversation.id,
          ...actor,
        },
        (event: StreamEvent) => {
          if (event.type === "message.completed") completion = event.response;
          if (event.type === "error") {
            streamFailure = event.error?.message ?? event.code ?? "流式调用失败";
          }
          if (activeConversationIdRef.current !== targetConversation.id) return;
          if (event.type === "message.delta") setStreamText((current) => current + event.delta);
          if (event.type === "tool.requested") {
            if (debugMode) {
              const kindLabel = event.kind === "aina" ? "AINA" : event.kind === "builtin" ? "内置工具" : "远程工具";
              setActivity(`正在调用 ${kindLabel}：${event.id}`);
            } else {
              setActivity("正在处理，请稍候…");
            }
          }
          if (event.type === "tool.completed") {
            setActivity(debugMode ? `${event.id} 已完成，正在整理结果…` : "正在整理结果…");
          }
          if (event.type === "approval.required") setActivity("等待你的授权确认");
          if (event.type === "error") {
            setError(streamFailure);
          }
        },
      );
      if (!completion) throw new Error(streamFailure ?? "智能体流程结束前没有返回完成事件。");
      const completed = completion as ChatResponse;
      if (["New conversation", "新对话"].includes(targetConversation.title)) {
        await api.patch(`/conversations/${completed.conversation_id}`, {
          title: text.length > 24 ? `${text.slice(0, 24)}…` : text,
        });
      }
      notifyConversationsChanged();
      if (activeConversationIdRef.current !== completed.conversation_id) return;
      setLastRun(completed);
      setApproval(completed.approval ?? null);
      const openAction = completed.widgets
        .flatMap((widget) => widget.actions)
        .find((action) => action.kind === "open_aina" && action.aina_id);
      if (openAction?.aina_id) {
        await openAina(openAction.aina_id, completed.conversation_id);
        return;
      }
      if (conversationId !== completed.conversation_id) {
        navigate(`/chat/${completed.conversation_id}`, { replace: true });
      }
      await loadConversation(completed.conversation_id);
    } catch (sendError) {
      if (runConversationId && activeConversationIdRef.current === runConversationId) {
        setError(apiErrorMessage(sendError));
        await loadConversation(runConversationId, true);
      }
    } finally {
      if (localRunConversationIdRef.current === runConversationId) {
        localRunConversationIdRef.current = null;
      }
      if (runConversationId && activeConversationIdRef.current === runConversationId) {
        setOptimisticUser(null);
        setStreamText("");
        setActivity(null);
        setSending(false);
      }
    }
  }

  async function resolveApproval(action: "confirm" | "deny") {
    if (!approval) return;
    setSending(true);
    setError(null);
    setActivity(action === "confirm" ? "正在执行已授权的调用…" : "正在取消调用…");
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

  async function openAina(ainaId: string, targetConversationId = conversation?.id) {
    setError(null);
    try {
      const canvas = await api.post<AinaCanvasResponse>(`/ainas/${ainaId}/open`, {
        ...actor,
        conversation_id: targetConversationId,
      });
      navigate(canvas.route, { state: { canvas } });
    } catch (openError) {
      setError(apiErrorMessage(openError));
    }
  }

  const messages = useMemo(
    () => [...(conversation?.messages ?? []), ...(optimisticUser ? [optimisticUser] : [])],
    [conversation?.messages, optimisticUser],
  );

  const title = conversation?.title === "New conversation" ? "新对话" : conversation?.title ?? "新对话";
  const badge = deleted
    ? ({ label: "已删除", tone: "warning" } as const)
    : sending
      ? ({ label: "运行中", tone: "thinking" } as const)
      : ({ label: "已就绪", tone: "success" } as const);

  return (
    <>
    <div className="h-full flex flex-col bg-app-bg">
      <Topbar
        title={title}
        badge={badge}
        actions={conversation?.id && !deleted ? (
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
            aria-label="查看当前对话观测数据"
          >
            <Activity className="h-3.5 w-3.5" />OBS
          </button>
        ) : null}
      />

      {renaming ? (
        <div className="border-b border-line bg-white px-5 py-2.5 flex items-center gap-2">
          <input
            value={titleDraft}
            onChange={(event) => setTitleDraft(event.target.value)}
            className="input-soft max-w-md h-9"
            aria-label="对话标题"
            autoFocus
          />
          <button type="button" onClick={() => void saveTitle()} className="btn-primary h-9">
            <Check className="w-4 h-4" />保存
          </button>
          <button type="button" onClick={() => setRenaming(false)} className="btn-ghost h-9">
            <X className="w-4 h-4" />取消
          </button>
        </div>
      ) : null}

      {confirmDelete ? (
        <div className="border-b border-danger-ring bg-danger-soft px-5 py-3 flex items-center gap-3">
          <AlertTriangle className="w-4 h-4 text-danger" />
          <span className="text-[13px] text-danger-deep">删除后会从列表隐藏，你可以立即恢复。</span>
          <span className="flex-1" />
          <button type="button" onClick={() => setConfirmDelete(false)} className="btn-outline h-8">
            取消
          </button>
          <button type="button" onClick={() => void deleteConversation()} className="btn-danger-outline h-8">
            确认删除
          </button>
        </div>
      ) : null}

      <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
        <div className="flex-1 min-h-0 overflow-y-auto px-5 py-5" aria-live="polite">
          <div className="max-w-4xl mx-auto space-y-3">
              <TaskTreeWidget sessionId={conversation?.id ?? conversationId ?? null} />
              {loading ? <ChatSkeleton /> : null}
              {!loading && deleted ? (
                <DeletedConversation title={title} onRestore={() => void restoreConversation()} />
              ) : null}
              {!loading && !deleted && messages.length === 0 ? <WelcomePanel /> : null}
              {!deleted
                ? messages.map((message) => (
                    <ConversationMessage
                      key={message.id}
                      message={message}
                      onOpenAina={(ainaId) => void openAina(ainaId)}
                      onPrompt={sendMessage}
                      debugMode={debugMode}
                      conversationId={conversation?.id ?? ""}
                      failures={message.trace_id ? failuresByTrace.get(message.trace_id) ?? [] : []}
                    />
                  ))
                : null}
              {streamText ? (
                <AssistantMessage
                  message={{
                    id: "streaming",
                    role: "assistant",
                    content: streamText,
                    createdAt: new Date().toISOString(),
                    runState: "running",
                  }}
                />
              ) : null}
              {activity ? <ActivityBubble text={activity} /> : null}
              {approval && !deleted ? (
                <ApprovalCard
                  approval={approval}
                  disabled={sending}
                  debugMode={debugMode}
                  onConfirm={() => void resolveApproval("confirm")}
                  onDeny={() => void resolveApproval("deny")}
                />
              ) : null}
              {error ? <ErrorNotice message={error} detailsHref={errorLogHref} onDismiss={() => setError(null)} /> : null}
              {debugMode && lastRun && !sending ? <RunSummary response={lastRun} /> : null}
              <div ref={endRef} />
            </div>
          </div>
          {!deleted ? (
            <ChatComposer
              disabled={sending || loading}
              onSend={sendMessage}
            />
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
  failures,
}: {
  message: BackendMessage;
  onOpenAina: (ainaId: string) => void;
  onPrompt: (prompt: string) => void;
  debugMode: boolean;
  conversationId: string;
  failures: MessageFailure[];
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
  if (message.role === "tool") return debugMode ? <ToolResultMessage message={message} /> : null;
  const hasToolCalls = Boolean(message.tool_calls?.length);
  return (
    <div className="space-y-2">
      {debugMode && hasToolCalls ? <ToolCallMessage message={message} /> : null}
      {message.content && (!hasToolCalls || debugMode) ? (
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
      {message.widgets?.map((widget) => (
        <SessionWidgetRenderer
          key={widget.id}
          widget={widget}
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
    <div className="flex items-center gap-2 rounded-lg border border-danger-ring bg-danger-soft px-3 py-2">
      <AlertTriangle className="h-4 w-4 shrink-0 text-danger" />
      <span className="min-w-0 flex-1 truncate text-[12px] text-danger-deep">
        {failure.kind === "capability" ? `能力调用失败：${failure.name} · ${failure.error}` : `调用失败：${failure.error}`}
      </span>
      <Link
        to={logHref}
        className="shrink-0 text-[11.5px] font-bold text-danger-deep hover:underline"
      >
        查看原始日志
      </Link>
    </div>
  );
}

function ToolCallMessage({ message }: { message: BackendMessage }) {
  return (
    <div className="rounded-lg border border-accent-ring bg-accent-soft px-3.5 py-3">
      <div className="flex items-center gap-2 text-[12.5px] font-bold text-accent-hover">
        <Wrench className="w-4 h-4" />
        智能体请求调用能力
      </div>
      <div className="mt-2 space-y-1.5">
        {message.tool_calls?.map((call) => (
          <div key={call.id} className="rounded-md border border-accent-ring/70 bg-white px-2.5 py-2">
            <div className="font-mono text-[11.5px] text-ink">{call.function.name}</div>
            <div className="mt-1 font-mono text-[10.5px] text-ink-muted break-all">
              {call.function.arguments}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function ToolResultMessage({ message }: { message: BackendMessage }) {
  const isError = toolResultIsError(message.content);
  return (
    <details className="rounded-lg border border-line bg-white px-3.5 py-2.5">
      <summary className="cursor-pointer text-[12px] font-bold text-ink-muted">
        {isError ? "能力调用失败" : "能力调用结果"} · {message.name}
      </summary>
      <pre className="mt-2 whitespace-pre-wrap break-all text-[10.5px] leading-relaxed text-ink-muted">
        {formatJson(message.content)}
      </pre>
    </details>
  );
}

function toolResultIsError(content: string): boolean {
  try {
    const payload: unknown = JSON.parse(content);
    return typeof payload === "object" && payload !== null && Object.prototype.hasOwnProperty.call(payload, "error");
  } catch {
    return false;
  }
}

function ActivityBubble({ text }: { text: string }) {
  return (
    <div className="rounded-lg border border-accent-ring bg-accent-soft h-11 px-3.5 flex items-center gap-2.5">
      <span className="w-4 h-4 rounded-full border-2 border-accent border-t-transparent animate-spin" />
      <span className="text-accent-hover text-[12.5px] font-semibold">{text}</span>
    </div>
  );
}

function RunSummary({ response }: { response: ChatResponse }) {
  return (
    <div className="flex items-center justify-end gap-2 text-[10.5px] text-ink-muted">
      <span>{response.iterations} 次模型迭代</span>
      <span>·</span>
      <span>{response.usage.input_tokens + response.usage.output_tokens} Tokens</span>
      <span>·</span>
      <Link to={`/obs?sessionId=${encodeURIComponent(response.conversation_id)}`} className="text-accent hover:underline">
        查看调用记录
      </Link>
    </div>
  );
}

function ErrorNotice({ message, detailsHref, onDismiss }: { message: string; detailsHref: string | null; onDismiss: () => void }) {
  return (
    <div className="rounded-lg border border-danger-ring bg-danger-soft p-3 flex items-center gap-2.5">
      <AlertTriangle className="w-4 h-4 text-danger" />
      <span className="flex-1 text-[12.5px] text-danger-deep">{message}</span>
      {detailsHref ? <Link to={detailsHref} className="shrink-0 text-[11.5px] font-bold text-danger-deep hover:underline">查看原始日志</Link> : null}
      <button type="button" onClick={onDismiss} aria-label="关闭错误">
        <X className="w-4 h-4 text-danger" />
      </button>
    </div>
  );
}

function ChatComposer({
  disabled,
  onSend,
}: {
  disabled: boolean;
  onSend: (text: string) => void;
}) {
  const [text, setText] = useState("");

  function submit(event: FormEvent) {
    event.preventDefault();
    const value = text.trim();
    if (!value || disabled) return;
    onSend(value);
    setText("");
  }

  return (
    <form onSubmit={submit} className="border-t border-line bg-white px-5 py-3">
      <div className="max-w-4xl mx-auto rounded-xl border border-line-strong bg-white p-2.5 shadow-soft focus-within:border-accent">
        <textarea
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              submit(event);
            }
          }}
          disabled={disabled}
          rows={2}
          placeholder="给 Unibot 发消息；Enter 发送，Shift+Enter 换行"
          aria-label="消息"
          className="w-full bg-transparent px-1 text-[13px] leading-[1.5] text-ink placeholder:text-ink-muted outline-none resize-none disabled:opacity-60"
        />
        <div className="mt-2 flex items-center justify-between gap-2">
          <ModelSelector disabled={disabled} />
          <button
            type="submit"
            disabled={disabled || !text.trim()}
            className={classNames(
              "w-9 h-9 rounded-lg flex items-center justify-center text-white transition-colors",
              !disabled && text.trim()
                ? "bg-accent hover:bg-accent-hover"
                : "bg-ink-subtle cursor-not-allowed",
            )}
            aria-label="发送消息"
          >
            <ArrowUp className="w-4 h-4" />
          </button>
        </div>
      </div>
    </form>
  );
}

function WelcomePanel() {
  return (
    <div className="min-h-[420px] flex items-center justify-center">
      <div className="max-w-lg text-center">
        <div className="mx-auto w-14 h-14 rounded-2xl bg-accent-soft text-accent flex items-center justify-center">
          <Bot className="w-7 h-7" />
        </div>
        <h2 className="mt-4 text-[22px] font-extrabold font-display text-ink">开始新对话</h2>
        <p className="mt-2 text-[13px] leading-relaxed text-ink-muted">
          Unibot 会保留多轮上下文、根据目标自动组合合适的能力，并在高风险操作前等待你的确认。
        </p>
        <div className="mt-5 grid grid-cols-3 gap-2 text-left">
          {[
            ["多轮上下文", "会话历史自动恢复"],
            ["能力调度", "按任务自动组合"],
            ["安全确认", "高风险操作可控"],
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
  return (
    <div className="min-h-[420px] flex items-center justify-center">
      <div className="text-center">
        <Trash2 className="mx-auto w-10 h-10 text-ink-subtle" />
        <h2 className="mt-3 text-[17px] font-bold text-ink">“{title}”已删除</h2>
        <p className="mt-1 text-[12.5px] text-ink-muted">恢复后会重新出现在对话列表中。</p>
        <button type="button" onClick={onRestore} className="btn-primary mt-4">
          <RotateCcw className="w-4 h-4" />恢复对话
        </button>
      </div>
    </div>
  );
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

function formatJson(value: string): string {
  try {
    return JSON.stringify(JSON.parse(value), null, 2);
  } catch {
    return value;
  }
}
