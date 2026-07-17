import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { ArrowLeft, ArrowUp, Bot, Loader2, MessageSquareText, PanelRightOpen, Wrench } from "lucide-react";
import { useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { AssistantMessage, UserMessage } from "@/components/chat/MessageBubble";
import { Topbar } from "@/components/layout/Topbar";
import { MainWidgetRenderer } from "@/components/widgets/MainWidgetRenderer";
import { SessionWidgetRenderer } from "@/components/widgets/SessionWidgetRenderer";
import { api, apiErrorMessage, streamChat, type StreamEvent } from "@/lib/api";
import { useDebugMode } from "@/lib/debugMode";
import { classNames, uid } from "@/lib/utils";
import type { AinaCanvasResponse, BackendMessage, ChatResponse, ConversationRecord } from "@/types";

const ACTOR = { user_id: "anonymous", tenant_id: "default" };

export default function CanvasModePage() {
  const { ainaId = "" } = useParams<{ ainaId: string }>();
  const [searchParams] = useSearchParams();
  const location = useLocation();
  const navigate = useNavigate();
  const { debugMode } = useDebugMode();
  const stateCanvas = (location.state as { canvas?: AinaCanvasResponse } | null)?.canvas;
  const [canvas, setCanvas] = useState<AinaCanvasResponse | null>(
    stateCanvas?.aina_id === ainaId ? stateCanvas : null,
  );
  const [conversationId, setConversationId] = useState<string | null>(
    searchParams.get("conversation") ?? stateCanvas?.conversation_id ?? null,
  );
  const [messages, setMessages] = useState<BackendMessage[]>([]);
  const [loading, setLoading] = useState(!canvas);
  const [sending, setSending] = useState(false);
  const [streamText, setStreamText] = useState("");
  const [activity, setActivity] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastRun, setLastRun] = useState<ChatResponse | null>(null);
  const [recoveringRun, setRecoveringRun] = useState(false);
  const [mobilePane, setMobilePane] = useState<"chat" | "app">("app");
  const endRef = useRef<HTMLDivElement | null>(null);
  const localRunRef = useRef(false);

  const loadConversation = useCallback(async (id: string) => {
    const record = await api.get<ConversationRecord>(`/conversations/${id}`);
    setMessages(record.messages);
    if (!localRunRef.current) {
      const running = record.run_status === "running";
      setRecoveringRun(running);
      setSending(running);
      setActivity(running ? "正在处理，请稍候…" : null);
    }
    setError(record.run_error ?? null);
    return record;
  }, []);

  useEffect(() => {
    let cancelled = false;
    const supplied = stateCanvas?.aina_id === ainaId ? stateCanvas : null;
    if (supplied) {
      setCanvas(supplied);
      setConversationId(searchParams.get("conversation") ?? supplied.conversation_id ?? null);
      setLoading(false);
    } else {
      setLoading(true);
      setMessages([]);
    }
    api
      .post<AinaCanvasResponse>(`/ainas/${ainaId}/open`, {
        ...ACTOR,
        conversation_id: searchParams.get("conversation"),
      })
      .then((opened) => {
        if (!cancelled) {
          setCanvas(opened);
          setConversationId(searchParams.get("conversation") ?? opened.conversation_id ?? null);
          setError(null);
        }
      })
      .catch((openError) => !cancelled && setError(apiErrorMessage(openError)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [ainaId, searchParams, stateCanvas]);

  useEffect(() => {
    if (!conversationId) {
      setMessages([]);
      return;
    }
    void loadConversation(conversationId).catch((loadError) => setError(apiErrorMessage(loadError)));
  }, [conversationId, loadConversation]);

  useEffect(() => {
    if (!conversationId || !recoveringRun) return;
    const timer = window.setInterval(() => {
      void loadConversation(conversationId);
    }, 600);
    return () => window.clearInterval(timer);
  }, [conversationId, loadConversation, recoveringRun]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: sending ? "smooth" : "auto" });
  }, [messages, streamText, activity, sending]);

  async function sendMessage(text: string) {
    const prompt = text.trim();
    if (!prompt || sending) return;
    const optimistic: BackendMessage = {
      id: uid("canvas-user"),
      role: "user",
      content: prompt,
      content_type: "text",
      widgets: [],
      created_at: new Date().toISOString(),
    };
    setMessages((current) => [...current, optimistic]);
    setSending(true);
    localRunRef.current = true;
    setStreamText("");
    setActivity(debugMode ? "正在连接 AINA…" : "正在处理，请稍候…");
    setError(null);
    let completion: ChatResponse | null = null;
    let streamFailure: string | null = null;
    try {
      let targetConversationId = conversationId;
      if (!targetConversationId) {
        const created = await api.post<ConversationRecord>("/conversations", {
          ...ACTOR,
          title: `${canvas?.name ?? ainaId} 对话`,
          category: "general",
        });
        targetConversationId = created.id;
        setConversationId(created.id);
        navigate(`/canvas/${ainaId}?conversation=${created.id}`, {
          replace: true,
          state: canvas ? { canvas: { ...canvas, conversation_id: created.id } } : undefined,
        });
      }
      await streamChat(
        {
          message: prompt,
          conversation_id: targetConversationId,
          ...ACTOR,
          capability: ainaId === "unibot-assistant" ? undefined : `aina:${ainaId}`,
        },
        (event: StreamEvent) => {
          if (event.type === "message.delta") setStreamText((current) => current + event.delta);
          if (event.type === "tool.requested") setActivity(debugMode ? `正在调用 ${event.id}…` : "正在处理，请稍候…");
          if (event.type === "tool.completed") setActivity(debugMode ? "调用完成，正在整理结果…" : "正在整理结果…");
          if (event.type === "routing.started") setActivity(debugMode ? "正在匹配 AINA…" : "正在处理，请稍候…");
          if (event.type === "error") {
            streamFailure = event.error?.message ?? event.code ?? "AINA 调用失败";
            setError(streamFailure);
          }
          if (event.type === "message.completed") completion = event.response;
        },
      );
      if (!completion) throw new Error(streamFailure ?? "AINA 会话没有返回完成事件。");
      const completed = completion as ChatResponse;
      setLastRun(completed);
      setConversationId(completed.conversation_id);
      await loadConversation(completed.conversation_id);
      navigate(`/canvas/${ainaId}?conversation=${completed.conversation_id}`, {
        replace: true,
        state: canvas ? { canvas: { ...canvas, conversation_id: completed.conversation_id } } : undefined,
      });
    } catch (sendError) {
      setMessages((current) => current.filter((message) => message.id !== optimistic.id));
      setError(apiErrorMessage(sendError));
    } finally {
      localRunRef.current = false;
      setSending(false);
      setStreamText("");
      setActivity(null);
    }
  }

  async function openAina(targetAinaId: string) {
    try {
      const opened = await api.post<AinaCanvasResponse>(`/ainas/${targetAinaId}/open`, {
        ...ACTOR,
        conversation_id: conversationId,
      });
      navigate(opened.route, { state: { canvas: opened } });
    } catch (openError) {
      setError(apiErrorMessage(openError));
    }
  }

  const visibleMessages = useMemo(
    () => messages.filter((message) => message.role !== "system"),
    [messages],
  );

  return (
    <div className="flex h-full flex-col bg-app-bg">
      <Topbar
        title={canvas?.name ?? "AINA 画布"}
        badge={{ label: sending ? "运行中" : "画布", tone: sending ? "thinking" : "info" }}
        actions={
          <div className="flex items-center gap-2">
            <div className="flex h-8 items-center rounded-md border border-line bg-app-soft p-0.5 lg:hidden" aria-label="画布移动端视图">
              <button
                type="button"
                onClick={() => setMobilePane("chat")}
                className={classNames(
                  "flex h-6 items-center gap-1 rounded px-2 text-[10px] font-bold",
                  mobilePane === "chat" ? "bg-white text-accent shadow-sm" : "text-ink-muted",
                )}
                aria-label="显示对话"
              >
                <MessageSquareText className="h-3.5 w-3.5" />对话
              </button>
              <button
                type="button"
                onClick={() => setMobilePane("app")}
                className={classNames(
                  "flex h-6 items-center gap-1 rounded px-2 text-[10px] font-bold",
                  mobilePane === "app" ? "bg-white text-accent shadow-sm" : "text-ink-muted",
                )}
                aria-label="显示应用"
              >
                <PanelRightOpen className="h-3.5 w-3.5" />应用
              </button>
            </div>
            <button
              type="button"
              onClick={() => navigate(conversationId ? `/chat/${conversationId}` : "/chat")}
              className="btn-outline h-8"
            >
              <ArrowLeft className="h-3.5 w-3.5" />退出画布
            </button>
          </div>
        }
      />

      <div className="min-h-0 flex-1">
        {loading ? <CanvasSkeleton /> : null}
        {!loading && canvas ? (
          <div className="grid h-full min-h-0 grid-cols-1 lg:grid-cols-[minmax(320px,380px)_minmax(0,1fr)]">
            <section className={classNames(
              "min-h-0 flex-col overflow-hidden bg-white lg:flex lg:border-r lg:border-line",
              mobilePane === "chat" ? "flex" : "hidden",
            )}>
              <header className="flex items-center gap-3 border-b border-line px-4 py-3">
                <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-accent-soft text-accent">
                  <Bot className="h-4.5 w-4.5" />
                </span>
                <div className="min-w-0">
                  <h2 className="truncate text-[13.5px] font-extrabold text-ink">与 {canvas.name} 对话</h2>
                  <p className="truncate text-[10.5px] text-ink-muted">描述需求，也可在右侧应用直接操作</p>
                </div>
              </header>

              <div className="min-h-0 flex-1 space-y-3 overflow-y-auto bg-app-bg p-3" aria-live="polite">
                {!visibleMessages.length && !sending ? (
                  <div className="flex min-h-[260px] items-center justify-center text-center">
                    <div>
                      <Bot className="mx-auto h-8 w-8 text-ink-subtle" />
                      <p className="mt-2 text-[12px] font-semibold text-ink-muted">开始描述你要完成的任务</p>
                    </div>
                  </div>
                ) : null}
                {visibleMessages.map((message) => (
                  <CanvasMessage
                    key={message.id}
                    message={message}
                    onOpenAina={(id) => void openAina(id)}
                    onPrompt={(prompt) => void sendMessage(prompt)}
                    debugMode={debugMode}
                  />
                ))}
                {streamText ? (
                  <AssistantMessage
                    message={{
                      id: "canvas-stream",
                      role: "assistant",
                      content: streamText,
                      createdAt: new Date().toISOString(),
                      runState: "running",
                    }}
                  />
                ) : null}
                {activity ? (
                  <div className="flex items-center gap-2 rounded-lg border border-accent-ring bg-accent-soft px-3 py-2.5 text-[11.5px] font-semibold text-accent-hover">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />{activity}
                  </div>
                ) : null}
                {error ? <p className="rounded-lg border border-danger-ring bg-danger-soft p-3 text-[11.5px] text-danger-deep">{error}</p> : null}
                <div ref={endRef} />
              </div>
              <CanvasComposer disabled={sending} onSend={(text) => void sendMessage(text)} />
            </section>

            <section className={classNames(
              "relative min-h-0 flex-col overflow-hidden bg-white lg:flex",
              mobilePane === "app" ? "flex" : "hidden",
            )}>
              <div className="min-h-0 flex-1 overflow-hidden">
                <MainWidgetRenderer
                  key={`${canvas.main_widget.id}:${lastRun?.trace_id ?? "initial"}`}
                  ainaId={canvas.aina_id}
                  widget={canvas.main_widget}
                  disabled={sending}
                  onOpenAina={(id) => void openAina(id)}
                  onPrompt={(prompt) => void sendMessage(prompt)}
                />
              </div>
              {debugMode && lastRun ? (
                <p className="pointer-events-none absolute bottom-2 right-3 rounded bg-white/90 px-2 py-1 text-[10px] text-ink-subtle shadow-sm">
                  {lastRun.iterations} 次模型迭代 · {lastRun.usage.input_tokens + lastRun.usage.output_tokens} Tokens
                </p>
              ) : null}
            </section>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function CanvasMessage({
  message,
  onOpenAina,
  onPrompt,
  debugMode,
}: {
  message: BackendMessage;
  onOpenAina: (ainaId: string) => void;
  onPrompt: (prompt: string) => void;
  debugMode: boolean;
}) {
  if (message.role === "user") return <UserMessage content={message.content} />;
  if (message.role === "tool") {
    if (!debugMode) return null;
    return (
      <details className="rounded-lg border border-line bg-white px-3 py-2 text-[10.5px] text-ink-muted">
        <summary className="flex cursor-pointer items-center gap-1.5 font-bold">
          <Wrench className="h-3 w-3" />{message.name ?? "能力调用"}
        </summary>
        <pre className="mt-2 whitespace-pre-wrap break-all">{formatJson(message.content)}</pre>
      </details>
    );
  }
  const hasToolCalls = Boolean(message.tool_calls?.length);
  return (
    <div className="space-y-2">
      {message.content && (!hasToolCalls || debugMode) ? (
        <AssistantMessage
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
        <SessionWidgetRenderer key={widget.id} widget={widget} onOpenAina={onOpenAina} onPrompt={onPrompt} />
      ))}
    </div>
  );
}

function CanvasComposer({ disabled, onSend }: { disabled: boolean; onSend: (text: string) => void }) {
  const [text, setText] = useState("");

  function submit(event: FormEvent) {
    event.preventDefault();
    const value = text.trim();
    if (!value || disabled) return;
    onSend(value);
    setText("");
  }

  return (
    <form onSubmit={submit} className="border-t border-line bg-white p-3">
      <div className="rounded-xl border border-line-strong p-2 focus-within:border-accent">
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
          placeholder="向当前 AINA 描述需求"
          aria-label="画布消息"
          className="w-full resize-none bg-transparent px-1 text-[12.5px] outline-none placeholder:text-ink-muted"
        />
        <div className="mt-1 flex justify-end">
          <button
            type="submit"
            disabled={disabled || !text.trim()}
            className={classNames(
              "flex h-8 w-8 items-center justify-center rounded-lg text-white",
              disabled || !text.trim() ? "cursor-not-allowed bg-ink-subtle" : "bg-accent hover:bg-accent-hover",
            )}
            aria-label="发送画布消息"
          >
            <ArrowUp className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
    </form>
  );
}

function CanvasSkeleton() {
  return (
    <div className="grid h-full grid-cols-1 lg:grid-cols-[380px_1fr]">
      <div className="animate-pulse border-r border-line bg-line/60" />
      <div className="animate-pulse bg-line/60" />
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
