import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  AppWindow,
  Bot,
  Brain,
  Braces,
  Bug,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock3,
  Code2,
  Copy,
  Database,
  MessageSquareText,
  RefreshCw,
  Route,
  Server,
  ShieldCheck,
  Wrench,
  XCircle,
} from "lucide-react";
import { useSearchParams } from "react-router-dom";
import { Topbar } from "@/components/layout/Topbar";
import { api, apiErrorMessage } from "@/lib/api";
import { classNames, timeAgo } from "@/lib/utils";
import { useDebugMode } from "@/lib/debugMode";
import type { AdminSummary, ConversationRecord, LLMCallRecord, TraceEvent, TraceRecord } from "@/types";

export default function DebugPage() {
  const { debugMode, setDebugMode } = useDebugMode();
  const [searchParams] = useSearchParams();
  const requestedTrace = searchParams.get("trace");
  const [health, setHealth] = useState<"checking" | "ok" | "error">("checking");
  const [summary, setSummary] = useState<AdminSummary | null>(null);
  const [traces, setTraces] = useState<TraceRecord[]>([]);
  const [llmCalls, setLlmCalls] = useState<LLMCallRecord[]>([]);
  const [conversations, setConversations] = useState<ConversationRecord[]>([]);
  const [selectedTrace, setSelectedTrace] = useState<string | null>(requestedTrace);
  const [selectedLlmCall, setSelectedLlmCall] = useState<string | null>(null);
  const [recordView, setRecordView] = useState<"traces" | "llm">("traces");
  const [expandedTraceGroups, setExpandedTraceGroups] = useState<Set<string>>(new Set());
  const [expandedLlmCallGroups, setExpandedLlmCallGroups] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      const [healthData, summaryData, traceData, llmCallData, conversationData] = await Promise.all([
        api.get<{ status: string }>("/health"),
        api.get<AdminSummary>("/admin/summary"),
        api.get<TraceRecord[]>("/traces?user_id=anonymous&tenant_id=default"),
        api.get<LLMCallRecord[]>("/llm-calls?limit=200"),
        api.get<ConversationRecord[]>("/conversations?user_id=anonymous&tenant_id=default"),
      ]);
      setHealth(healthData.status === "ok" ? "ok" : "error");
      setSummary(summaryData);
      setTraces(traceData);
      setLlmCalls(llmCallData);
      setConversations(conversationData);
      setSelectedLlmCall((current) => (
        current && llmCallData.some((call) => call.call_id === current)
          ? current
          : llmCallData[0]?.call_id ?? null
      ));
      setError(null);
      if (requestedTrace && traceData.some((trace) => trace.trace_id === requestedTrace)) {
        setSelectedTrace(requestedTrace);
      }
    } catch (loadError) {
      setHealth("error");
      setError(apiErrorMessage(loadError));
    } finally {
      setRefreshing(false);
    }
  }, [requestedTrace]);

  useEffect(() => {
    void load();
  }, [load]);

  const selected = useMemo(
    () => traces.find((trace) => trace.trace_id === selectedTrace) ?? null,
    [selectedTrace, traces],
  );
  const selectedCall = useMemo(
    () => llmCalls.find((call) => call.call_id === selectedLlmCall) ?? null,
    [llmCalls, selectedLlmCall],
  );
  const conversationsById = useMemo(
    () => new Map(conversations.map((conversation) => [conversation.id, conversation])),
    [conversations],
  );
  const tracesById = useMemo(
    () => new Map(traces.map((trace) => [trace.trace_id, trace])),
    [traces],
  );
  const traceGroups = useMemo(
    () => groupTracesByConversation(traces, conversationsById),
    [conversationsById, traces],
  );
  const llmCallGroups = useMemo(
    () => groupLlmCallsByConversation(llmCalls, tracesById, conversationsById),
    [conversationsById, llmCalls, tracesById],
  );

  useEffect(() => {
    const selectedRecord = traces.find((trace) => trace.trace_id === selectedTrace);
    const groupKey = selectedRecord ? traceGroupKey(selectedRecord.conversation_id) : traceGroups[0]?.key;
    if (!groupKey) return;
    setExpandedTraceGroups((current) => {
      if (current.has(groupKey) || (!selectedRecord && current.size > 0)) return current;
      const next = new Set(current);
      next.add(groupKey);
      return next;
    });
  }, [selectedTrace, traceGroups, traces]);

  useEffect(() => {
    const selectedRecord = llmCalls.find((call) => call.call_id === selectedLlmCall);
    const groupKey = selectedRecord
      ? traceGroupKey(llmCallConversationId(selectedRecord, tracesById))
      : llmCallGroups[0]?.key;
    if (!groupKey) return;
    setExpandedLlmCallGroups((current) => {
      if (current.has(groupKey) || (!selectedRecord && current.size > 0)) return current;
      const next = new Set(current);
      next.add(groupKey);
      return next;
    });
  }, [llmCallGroups, llmCalls, selectedLlmCall, tracesById]);

  const selectedConversationTitle = selected?.conversation_id
    ? conversationsById.get(selected.conversation_id)?.title ?? "已删除或不可用的会话"
    : "未关联会话";

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-app-bg">
      <Topbar
        title="Debug"
        badge={{
          label: health === "ok" ? "后端在线" : health === "checking" ? "检查中" : "后端异常",
          tone: health === "ok" ? "success" : health === "checking" ? "thinking" : "warning",
        }}
        actions={
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setDebugMode(!debugMode)}
              aria-pressed={debugMode}
              className={classNames("h-8 rounded-lg border px-3 text-[13px] font-bold", debugMode ? "border-accent bg-accent-soft text-accent" : "border-line bg-white text-ink-muted")}
            >
              <Bug className="mr-1.5 inline h-3.5 w-3.5" />调试模式{debugMode ? "已开启" : "已关闭"}
            </button>
            <button type="button" onClick={() => void load()} disabled={refreshing} className="btn-outline h-8">
              <RefreshCw className={classNames("w-3.5 h-3.5", refreshing && "animate-spin")} />刷新
            </button>
          </div>
        }
      />
      <div className="flex min-h-0 flex-1 overflow-hidden p-3 md:p-4">
        <div className="flex h-full min-h-0 w-full flex-col gap-3 md:gap-4">
          {error ? (
            <div className="shrink-0 rounded-lg border border-danger-ring bg-danger-soft p-3 text-[12.5px] text-danger-deep">
              {error}
            </div>
          ) : null}

          <section
            className="grid shrink-0 grid-flow-col auto-cols-[minmax(112px,1fr)] gap-2 overflow-x-auto md:gap-3 xl:grid-flow-row xl:grid-cols-8"
            aria-label="运行统计"
          >
            <SummaryCard icon={<Bot />} label="对话" value={summary?.conversations} tone="blue" />
            <SummaryCard icon={<Wrench />} label="工具" value={summary?.tools} tone="indigo" />
            <SummaryCard icon={<Code2 />} label="技能" value={summary?.skills} tone="slate" />
            <SummaryCard icon={<AppWindow />} label="AINA" value={summary?.ainas} tone="green" />
            <SummaryCard icon={<Database />} label="安装" value={summary?.installations} tone="amber" />
            <SummaryCard icon={<Brain />} label="记忆" value={summary?.memories} tone="indigo" />
            {debugMode ? <SummaryCard icon={<Route />} label="调用记录" value={summary?.traces} tone="blue" /> : null}
            {debugMode ? <SummaryCard icon={<Braces />} label="模型请求" value={summary?.llm_calls} tone="slate" /> : null}
          </section>

          {debugMode ? <section className="grid min-h-0 flex-1 grid-cols-1 grid-rows-[minmax(0,0.8fr)_minmax(0,1.2fr)] gap-3 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)] xl:grid-rows-1 xl:gap-4">
            <div className="flex min-h-0 flex-col overflow-hidden rounded-xl border border-line bg-white shadow-card">
              <div className="flex shrink-0 items-center gap-2 border-b border-line px-3 py-2.5">
                <div className="flex rounded-lg border border-line bg-app-soft p-0.5">
                  <button
                    type="button"
                    onClick={() => setRecordView("traces")}
                    className={classNames(
                      "flex h-7 items-center gap-1.5 rounded-md px-2.5 text-[12px] font-bold",
                      recordView === "traces" ? "bg-white text-accent shadow-sm" : "text-ink-muted",
                    )}
                  >
                    <Activity className="h-3.5 w-3.5" />调用链
                  </button>
                  <button
                    type="button"
                    onClick={() => setRecordView("llm")}
                    className={classNames(
                      "flex h-7 items-center gap-1.5 rounded-md px-2.5 text-[12px] font-bold",
                      recordView === "llm" ? "bg-white text-accent shadow-sm" : "text-ink-muted",
                    )}
                  >
                    <Braces className="h-3.5 w-3.5" />模型请求
                  </button>
                </div>
                <span className="ml-auto text-[11.5px] text-ink-muted">
                  {recordView === "traces" ? `${traces.length} 条 Trace` : `${llmCalls.length} 次请求`}
                </span>
              </div>
              <div className="min-h-0 flex-1 overflow-y-auto" aria-label={recordView === "traces" ? "Trace 列表" : "模型请求列表"}>
                {recordView === "traces" ? traces.length === 0 ? (
                  <div className="py-20 text-center text-[12px] text-ink-muted">完成一次对话后，调用记录会显示在这里。</div>
                ) : (
                  traceGroups.map((group) => (
                    <ConversationTraceGroup
                      key={group.key}
                      group={group}
                      expanded={expandedTraceGroups.has(group.key)}
                      selectedTrace={selectedTrace}
                      onToggle={() => setExpandedTraceGroups((current) => {
                        const next = new Set(current);
                        if (next.has(group.key)) next.delete(group.key);
                        else next.add(group.key);
                        return next;
                      })}
                      onSelectTrace={setSelectedTrace}
                    />
                  ))
                ) : llmCalls.length === 0 ? (
                  <div className="py-20 text-center text-[12px] text-ink-muted">发起模型请求后，入参与模型原始返回值会显示在这里。</div>
                ) : (
                  llmCallGroups.map((group) => (
                    <ConversationLLMCallGroup
                      key={group.key}
                      group={group}
                      expanded={expandedLlmCallGroups.has(group.key)}
                      selectedCall={selectedLlmCall}
                      onToggle={() => setExpandedLlmCallGroups((current) => {
                        const next = new Set(current);
                        if (next.has(group.key)) next.delete(group.key);
                        else next.add(group.key);
                        return next;
                      })}
                      onSelectCall={setSelectedLlmCall}
                    />
                  ))
                )}
              </div>
            </div>

            <div className="min-h-0 overflow-hidden rounded-xl border border-line bg-white shadow-card">
              {recordView === "traces"
                ? selected
                  ? <TraceDetail trace={selected} conversationTitle={selectedConversationTitle} />
                  : <NoTraceSelected />
                : selectedCall
                  ? <LLMCallDetail call={selectedCall} />
                  : <NoLLMCallSelected />}
            </div>
          </section> : (
            <section className="rounded-xl border border-line bg-white px-6 py-16 text-center shadow-card" aria-label="调试模式说明">
              <Bug className="mx-auto h-8 w-8 text-ink-subtle" />
              <h2 className="mt-3 text-[15px] font-extrabold text-ink">调试模式已关闭</h2>
              <p className="mx-auto mt-2 max-w-lg text-[12px] leading-relaxed text-ink-muted">
                普通使用界面不会显示工具调用、模型迭代、Token 数或调用记录。需要排查问题时，可在页面右上角临时开启。
              </p>
            </section>
          )}
        </div>
      </div>
    </div>
  );
}

function SummaryCard({
  icon,
  label,
  value,
  tone,
}: {
  icon: React.ReactNode;
  label: string;
  value?: number;
  tone: "blue" | "green" | "indigo" | "amber" | "slate";
}) {
  const colors = {
    blue: "bg-accent-soft text-accent",
    green: "bg-success-soft text-success",
    indigo: "bg-indigo-50 text-indigo-600",
    amber: "bg-warning-soft text-warning",
    slate: "bg-app-soft text-ink-muted",
  };
  return (
    <div className="rounded-xl border border-line bg-white p-3.5 shadow-card">
      <div className={classNames("w-8 h-8 rounded-lg flex items-center justify-center [&>svg]:w-4 [&>svg]:h-4", colors[tone])}>
        {icon}
      </div>
      <div className="mt-3 text-[22px] font-extrabold text-ink">{value ?? "—"}</div>
      <div className="text-[12px] font-semibold text-ink-muted">{label}</div>
    </div>
  );
}

interface ConversationTraceGroupData {
  key: string;
  conversationId: string | null;
  title: string;
  traces: TraceRecord[];
}

interface ConversationLLMCallGroupData {
  key: string;
  conversationId: string | null;
  title: string;
  calls: LLMCallRecord[];
}

function ConversationTraceGroup({
  group,
  expanded,
  selectedTrace,
  onToggle,
  onSelectTrace,
}: {
  group: ConversationTraceGroupData;
  expanded: boolean;
  selectedTrace: string | null;
  onToggle: () => void;
  onSelectTrace: (traceId: string) => void;
}) {
  const containsSelected = group.traces.some((trace) => trace.trace_id === selectedTrace);
  return (
    <section className="border-b border-line last:border-b-0">
      <div className="relative">
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={expanded}
          className={classNames(
            "flex w-full items-start gap-2.5 px-4 py-3 pr-11 text-left transition-colors hover:bg-app-soft",
            containsSelected && "bg-accent-soft/60",
          )}
        >
          <span className={classNames(
            "mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md",
            containsSelected ? "bg-white text-accent shadow-sm" : "bg-app-soft text-ink-muted",
          )}>
            <MessageSquareText className="h-3.5 w-3.5" />
          </span>
          <span className="min-w-0 flex-1">
            <span className="flex items-center gap-2">
              <strong className="min-w-0 flex-1 truncate text-[13px] text-ink">{group.title}</strong>
              <span className="shrink-0 rounded bg-white px-1.5 py-0.5 text-[10.5px] font-bold text-ink-muted shadow-sm">
                {group.traces.length} Trace
              </span>
            </span>
            <span className="mt-1 block truncate pr-7 font-mono text-[11px] text-ink-subtle">
              {group.conversationId ?? "无 Conversation ID"}
            </span>
          </span>
          {expanded ? <ChevronDown className="mt-1 h-3.5 w-3.5 shrink-0 text-accent" /> : <ChevronRight className="mt-1 h-3.5 w-3.5 shrink-0 text-ink-subtle" />}
        </button>
        {group.conversationId ? (
          <CopyIdButton
            value={group.conversationId}
            label={`复制 Conversation ID ${group.conversationId}`}
            compact
            className="absolute bottom-2 right-10"
          />
        ) : null}
      </div>
      {expanded ? (
        <div className="border-t border-line bg-white">
          {group.traces.map((trace) => (
            <TraceRow
              key={trace.trace_id}
              trace={trace}
              active={trace.trace_id === selectedTrace}
              onClick={() => onSelectTrace(trace.trace_id)}
            />
          ))}
        </div>
      ) : null}
    </section>
  );
}

function ConversationLLMCallGroup({
  group,
  expanded,
  selectedCall,
  onToggle,
  onSelectCall,
}: {
  group: ConversationLLMCallGroupData;
  expanded: boolean;
  selectedCall: string | null;
  onToggle: () => void;
  onSelectCall: (callId: string) => void;
}) {
  const containsSelected = group.calls.some((call) => call.call_id === selectedCall);
  const performance = summarizeLlmCalls(group.calls);
  return (
    <section className="border-b border-line last:border-b-0">
      <div className="relative">
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={expanded}
          className={classNames(
            "flex w-full items-start gap-2.5 px-4 py-3 pr-11 text-left transition-colors hover:bg-app-soft",
            containsSelected && "bg-accent-soft/60",
          )}
        >
          <span className={classNames(
            "mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md",
            containsSelected ? "bg-white text-accent shadow-sm" : "bg-app-soft text-ink-muted",
          )}>
            <Braces className="h-3.5 w-3.5" />
          </span>
          <span className="min-w-0 flex-1">
            <span className="flex items-center gap-2">
              <strong className="min-w-0 flex-1 truncate text-[13px] text-ink">{group.title}</strong>
              <span className="shrink-0 rounded bg-white px-1.5 py-0.5 text-[10.5px] font-bold text-ink-muted shadow-sm">
                {group.calls.length} 次
              </span>
            </span>
            <span className="mt-1 block truncate pr-7 font-mono text-[11px] text-ink-subtle">
              {group.conversationId ?? "无 Conversation ID"}
            </span>
            <span className="mt-1 flex flex-wrap items-center gap-x-1.5 text-[10.5px] text-ink-muted">
              <span>模型总耗时 {formatDuration(performance.totalDurationMs)}</span>
              <span>·</span>
              <span>总 Token {formatTokenCount(performance.totalTokens)}</span>
              <span>·</span>
              <span>输出速率 {formatOutputTokenRate(performance.outputTokensPerSecond)}</span>
            </span>
          </span>
          {expanded
            ? <ChevronDown className="mt-1 h-3.5 w-3.5 shrink-0 text-accent" />
            : <ChevronRight className="mt-1 h-3.5 w-3.5 shrink-0 text-ink-subtle" />}
        </button>
        {group.conversationId ? (
          <CopyIdButton
            value={group.conversationId}
            label={`复制 Conversation ID ${group.conversationId}`}
            compact
            className="absolute right-10 top-9"
          />
        ) : null}
      </div>
      {expanded ? (
        <div className="border-t border-line bg-white">
          {group.calls.map((call) => (
            <LLMCallRow
              key={call.call_id}
              call={call}
              active={call.call_id === selectedCall}
              onClick={() => onSelectCall(call.call_id)}
            />
          ))}
        </div>
      ) : null}
    </section>
  );
}

function TraceRow({ trace, active, onClick }: { trace: TraceRecord; active: boolean; onClick: () => void }) {
  return (
    <div className="relative">
      <button
        type="button"
        onClick={onClick}
        className={classNames(
          "w-full border-l-2 py-2.5 pl-[50px] pr-12 text-left transition-colors",
          active ? "border-accent bg-accent-soft" : "border-transparent hover:bg-app-soft",
        )}
      >
        <div className="flex items-center gap-2">
          <TraceStatus status={trace.status} />
          <span className="min-w-0 flex-1 truncate font-mono text-[12px] text-ink">{trace.trace_id}</span>
        </div>
        <div className="mt-1.5 flex items-center gap-2 text-[11.5px] text-ink-muted">
          <span>{trace.events.length} 个事件</span>
          <span>·</span>
          <span>{timeAgo(trace.created_at)}</span>
        </div>
      </button>
      <CopyIdButton
        value={trace.trace_id}
        label={`复制 Trace ID ${trace.trace_id}`}
        compact
        className="absolute right-3 top-2.5"
      />
    </div>
  );
}

function LLMCallRow({ call, active, onClick }: { call: LLMCallRecord; active: boolean; onClick: () => void }) {
  const performance = llmCallPerformance(call);
  return (
    <button
      type="button"
      onClick={onClick}
      className={classNames(
        "w-full border-b border-l-2 border-b-line py-3 pl-[50px] pr-4 text-left transition-colors last:border-b-0",
        active ? "border-l-accent bg-accent-soft" : "border-l-transparent hover:bg-app-soft",
      )}
    >
      <div className="flex items-center gap-2">
        <LLMCallStatus status={call.status} compact />
        <strong className="min-w-0 flex-1 truncate text-[12.5px] text-ink">{call.model}</strong>
        {call.duration_ms != null ? (
          <span className="shrink-0 font-mono text-[11px] text-ink-subtle">{formatDuration(call.duration_ms)}</span>
        ) : null}
      </div>
      <div className="mt-1.5 truncate font-mono text-[11px] text-ink-muted">{call.endpoint}</div>
      <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-ink-subtle">
        <span>{new Date(call.created_at).toLocaleString("zh-CN")}</span>
        {call.context_type ? (
          <>
            <span>·</span>
            <span className="truncate">{llmContextLabel(call)}</span>
          </>
        ) : null}
        <span className="ml-auto shrink-0 font-mono text-ink-muted">
          {formatTokenCount(performance.totalTokens)} Token · {formatOutputTokenRate(performance.outputTokensPerSecond)}
        </span>
      </div>
    </button>
  );
}

function CopyIdButton({
  value,
  label,
  compact = false,
  className,
}: {
  value: string;
  label: string;
  compact?: boolean;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  };

  return (
    <button
      type="button"
      onClick={() => void copy()}
      aria-label={label}
      title={copied ? "已复制" : label}
      className={classNames(
        "shrink-0 items-center justify-center rounded-md border bg-white transition-colors",
        compact ? "flex h-6 w-6" : "flex h-7 w-7",
        copied ? "border-success/30 text-success" : "border-line text-ink-muted hover:border-accent hover:text-accent",
        className,
      )}
    >
      {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
    </button>
  );
}

function LLMCallDetail({ call }: { call: LLMCallRecord }) {
  const [payloadView, setPayloadView] = useState<"request" | "response">("request");

  useEffect(() => {
    setPayloadView("request");
  }, [call.call_id]);

  const payload = payloadView === "request" ? call.request : call.response;
  const conversationId = call.context_type === "conversation" ? call.context_id : null;
  const performance = llmCallPerformance(call);
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="shrink-0 border-b border-line bg-app-soft px-4 py-3">
        <div className="flex items-center gap-2">
          <LLMCallStatus status={call.status} />
          <h2 className="min-w-0 flex-1 truncate font-mono text-[12.5px] font-bold text-ink">{call.call_id}</h2>
          <CopyIdButton value={call.call_id} label="复制模型调用 ID" />
        </div>
        <div className="mt-2 grid gap-1 text-[11.5px] text-ink-muted sm:grid-cols-2">
          <span className="truncate" title={call.endpoint}>接口：<span className="font-mono">{call.endpoint}</span></span>
          <span>模型：<span className="font-mono text-ink">{call.model}</span></span>
          <span>时间：{new Date(call.created_at).toLocaleString("zh-CN")}</span>
          <span>耗时：{call.duration_ms != null ? formatDuration(call.duration_ms) : "请求中"}</span>
          <span>
            Token：{formatTokenCount(performance.totalTokens)}
            {performance.inputTokens != null || performance.outputTokens != null
              ? `（输入 ${formatTokenCount(performance.inputTokens)} / 输出 ${formatTokenCount(performance.outputTokens)}）`
              : ""}
          </span>
          <span>输出速率：{formatOutputTokenRate(performance.outputTokensPerSecond)}</span>
          <span className="flex min-w-0 items-center gap-1.5">
            <span className="truncate">上下文：{llmContextLabel(call)}</span>
            {conversationId ? (
              <CopyIdButton value={conversationId} label="复制 Conversation ID" compact />
            ) : null}
          </span>
          <span className="flex min-w-0 items-center gap-1.5">
            <span className="truncate">Trace：<span className="font-mono">{call.trace_id ?? "—"}</span></span>
            {call.trace_id ? <CopyIdButton value={call.trace_id} label="复制 Trace ID" compact /> : null}
          </span>
        </div>
      </div>

      <div className="flex shrink-0 items-center gap-2 border-b border-line px-4 py-2">
        <div className="flex rounded-lg bg-app-soft p-0.5">
          <button
            type="button"
            onClick={() => setPayloadView("request")}
            className={classNames(
              "rounded-md px-3 py-1.5 text-[12px] font-bold",
              payloadView === "request" ? "bg-white text-accent shadow-sm" : "text-ink-muted",
            )}
          >
            请求
          </button>
          <button
            type="button"
            onClick={() => setPayloadView("response")}
            className={classNames(
              "rounded-md px-3 py-1.5 text-[12px] font-bold",
              payloadView === "response" ? "bg-white text-accent shadow-sm" : "text-ink-muted",
            )}
          >
            响应
          </button>
        </div>
        <span className="ml-auto text-[11px] text-ink-subtle">
          {payloadView === "request" ? "POST 请求体" : call.status === "running" ? "等待模型响应" : "模型原始响应体"}
        </span>
      </div>

      <div className="min-h-0 flex-1 overflow-auto bg-[#0f172a] p-4" aria-label={payloadView === "request" ? "模型请求 JSON" : "模型响应 JSON"}>
        {payload ? (
          <HighlightedJson key={`${call.call_id}-${payloadView}`} value={payload} />
        ) : (
          <div className="flex h-full min-h-48 items-center justify-center text-[12.5px] text-slate-400">
            {call.status === "running" ? "请求仍在处理中，刷新后查看返回值。" : "没有可用的响应体。"}
          </div>
        )}
      </div>
      {call.error ? (
        <div className="shrink-0 border-t border-danger-ring bg-danger-soft px-4 py-2 text-[12px] text-danger-deep">
          {call.error}
        </div>
      ) : null}
    </div>
  );
}

function HighlightedJson({ value }: { value: Record<string, unknown> }) {
  const [collapsedPaths, setCollapsedPaths] = useState<Set<string>>(new Set());

  const togglePath = (path: string) => {
    setCollapsedPaths((current) => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  return (
    <pre className="whitespace-pre-wrap break-words text-[12px] leading-relaxed text-slate-200 [overflow-wrap:anywhere]">
      <code>
        <JsonTreeNode
          value={value}
          path="$"
          depth={0}
          trailingComma={false}
          collapsedPaths={collapsedPaths}
          onToggle={togglePath}
        />
      </code>
    </pre>
  );
}

function JsonTreeNode({
  value,
  path,
  depth,
  propertyKey,
  itemIndex,
  trailingComma,
  collapsedPaths,
  onToggle,
}: {
  value: unknown;
  path: string;
  depth: number;
  propertyKey?: string;
  itemIndex?: number;
  trailingComma: boolean;
  collapsedPaths: Set<string>;
  onToggle: (path: string) => void;
}) {
  const prefix = propertyKey !== undefined ? (
    <>
      <span className="text-sky-300">{JSON.stringify(propertyKey)}</span>
      <span className="text-slate-400">: </span>
    </>
  ) : null;
  const comma = trailingComma ? "," : "";

  if (!isJsonComposite(value)) {
    return (
      <span className="block min-w-0" style={{ paddingLeft: depth * 16 }}>
        <span className="inline-block w-5" />
        {prefix}
        <JsonPrimitive value={value} />
        <span className="text-slate-400">{comma}</span>
      </span>
    );
  }

  const entries = Array.isArray(value)
    ? value.map((item, index) => [String(index), item] as const)
    : Object.entries(value);
  const opening = Array.isArray(value) ? "[" : "{";
  const closing = Array.isArray(value) ? "]" : "}";
  const collapsed = collapsedPaths.has(path);
  const nodeName = propertyKey ?? (itemIndex !== undefined ? `第 ${itemIndex + 1} 项` : "根节点");
  const countLabel = Array.isArray(value) ? `${entries.length} 项` : `${entries.length} 个字段`;

  if (entries.length === 0) {
    return (
      <span className="block min-w-0" style={{ paddingLeft: depth * 16 }}>
        <span className="inline-block w-5" />
        {prefix}
        <span className="text-slate-200">{opening}{closing}</span>
        <span className="text-slate-400">{comma}</span>
      </span>
    );
  }

  return (
    <>
      <span className="block min-w-0" style={{ paddingLeft: depth * 16 }}>
        <button
          type="button"
          onClick={() => onToggle(path)}
          aria-label={`${collapsed ? "展开" : "折叠"} ${nodeName}`}
          title={`${collapsed ? "展开" : "折叠"} ${nodeName}`}
          className="mr-1 inline-flex h-5 w-5 align-middle items-center justify-center rounded text-slate-400 transition-colors hover:bg-white/10 hover:text-white"
        >
          {collapsed ? <ChevronRight className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
        </button>
        {prefix}
        <span className="text-slate-200">{opening}</span>
        {collapsed ? (
          <>
            <span className="px-1.5 italic text-slate-500">… {countLabel}</span>
            <span className="text-slate-200">{closing}</span>
            <span className="text-slate-400">{comma}</span>
          </>
        ) : null}
      </span>
      {!collapsed ? (
        <>
          {entries.map(([key, item], index) => {
            const arrayItem = Array.isArray(value);
            return (
              <JsonTreeNode
                key={`${path}/${key}`}
                value={item}
                path={`${path}/${arrayItem ? `[${key}]` : JSON.stringify(key)}`}
                depth={depth + 1}
                propertyKey={arrayItem ? undefined : key}
                itemIndex={arrayItem ? index : undefined}
                trailingComma={index < entries.length - 1}
                collapsedPaths={collapsedPaths}
                onToggle={onToggle}
              />
            );
          })}
          <span className="block min-w-0" style={{ paddingLeft: depth * 16 }}>
            <span className="inline-block w-5" />
            <span className="text-slate-200">{closing}</span>
            <span className="text-slate-400">{comma}</span>
          </span>
        </>
      ) : null}
    </>
  );
}

function JsonPrimitive({ value }: { value: unknown }) {
  if (value === null) return <span className="text-rose-300">null</span>;
  if (typeof value === "string") return <span className="text-emerald-300">{JSON.stringify(value)}</span>;
  if (typeof value === "number") return <span className="text-amber-300">{String(value)}</span>;
  if (typeof value === "boolean") return <span className="text-violet-300">{String(value)}</span>;
  return <span className="text-slate-400">{JSON.stringify(value) ?? String(value)}</span>;
}

function isJsonComposite(value: unknown): value is Record<string, unknown> | unknown[] {
  return value !== null && typeof value === "object";
}

function TraceDetail({ trace, conversationTitle }: { trace: TraceRecord; conversationTitle: string }) {
  return (
    <div className="h-full flex flex-col">
      <div className="px-4 py-3 border-b border-line bg-app-soft">
        <div className="flex items-center gap-2">
          <TraceStatus status={trace.status} />
          <h2 className="font-mono text-[12.5px] font-bold text-ink break-all">{trace.trace_id}</h2>
          <CopyIdButton value={trace.trace_id} label="复制 Trace ID" />
        </div>
        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-ink-muted">
          <span>会话：{conversationTitle}</span>
          <span className="flex items-center gap-1.5 font-mono">
            {trace.conversation_id ?? "—"}
            {trace.conversation_id ? (
              <CopyIdButton value={trace.conversation_id} label="复制 Conversation ID" compact />
            ) : null}
          </span>
          <span>主体：{trace.tenant_id}/{trace.user_id}</span>
          <span>开始：{new Date(trace.created_at).toLocaleString("zh-CN")}</span>
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-4" aria-label="Trace 事件">
        <div className="space-y-0">
          {trace.events.map((event, index) => (
            <EventRow key={`${event.timestamp}-${index}`} event={event} last={index === trace.events.length - 1} />
          ))}
        </div>
      </div>
    </div>
  );
}

function EventRow({ event, last }: { event: TraceEvent; last: boolean }) {
  const failed = event.status === "failed";
  const discovery = event.kind === "capability.discovery" ? parseCapabilityDiscovery(event.details) : null;
  return (
    <div className="grid grid-cols-[20px_1fr] gap-2">
      <div className="flex flex-col items-center">
        <span className={classNames("mt-1 w-2.5 h-2.5 rounded-full border-2", failed ? "border-danger bg-danger-soft" : "border-accent bg-accent-soft")} />
        {!last ? <span className="w-px flex-1 min-h-10 bg-line" /> : null}
      </div>
      <div className="pb-4">
        <div className="flex items-center gap-2">
          <span className="text-[13px] font-bold text-ink">{event.kind}</span>
          <span className={classNames("rounded px-1.5 py-0.5 text-[10.5px] font-bold", failed ? "bg-danger-soft text-danger" : "bg-app-soft text-ink-muted")}>{eventStatusLabel(event.status)}</span>
          {event.duration_ms != null ? <span className="ml-auto text-[11px] text-ink-subtle">{event.duration_ms.toFixed(1)} ms</span> : null}
        </div>
        {event.target_id ? <div className="mt-1 font-mono text-[11px] text-ink-muted break-all">{event.target_type}:{event.target_id}</div> : null}
        {discovery ? (
          <CapabilityDiscoveryView details={discovery} />
        ) : Object.keys(event.details).length ? (
          <pre className="mt-1.5 rounded-md bg-app-soft p-2 whitespace-pre-wrap break-all text-[11px] leading-relaxed text-ink-muted">{JSON.stringify(event.details, null, 2)}</pre>
        ) : null}
      </div>
    </div>
  );
}

function traceGroupKey(conversationId: string | null | undefined): string {
  return conversationId ?? "__unassigned__";
}

function groupTracesByConversation(
  traces: TraceRecord[],
  conversationsById: Map<string, ConversationRecord>,
): ConversationTraceGroupData[] {
  const groups = new Map<string, ConversationTraceGroupData>();
  for (const trace of traces) {
    const conversationId = trace.conversation_id ?? null;
    const key = traceGroupKey(conversationId);
    const existing = groups.get(key);
    if (existing) {
      existing.traces.push(trace);
      continue;
    }
    const conversation = conversationId ? conversationsById.get(conversationId) : null;
    groups.set(key, {
      key,
      conversationId,
      title: conversation?.title.trim() || (conversationId ? "已删除或不可用的会话" : "未关联会话"),
      traces: [trace],
    });
  }
  return [...groups.values()]
    .map((group) => ({
      ...group,
      traces: [...group.traces].sort((left, right) => right.created_at.localeCompare(left.created_at)),
    }))
    .sort((left, right) => right.traces[0].created_at.localeCompare(left.traces[0].created_at));
}

function llmCallConversationId(
  call: LLMCallRecord,
  tracesById: Map<string, TraceRecord>,
): string | null {
  if (call.trace_id) {
    const traceConversationId = tracesById.get(call.trace_id)?.conversation_id;
    if (traceConversationId) return traceConversationId;
  }
  return call.context_type === "conversation" ? call.context_id ?? null : null;
}

function groupLlmCallsByConversation(
  calls: LLMCallRecord[],
  tracesById: Map<string, TraceRecord>,
  conversationsById: Map<string, ConversationRecord>,
): ConversationLLMCallGroupData[] {
  const groups = new Map<string, ConversationLLMCallGroupData>();
  for (const call of calls) {
    const conversationId = llmCallConversationId(call, tracesById);
    const key = traceGroupKey(conversationId);
    const existing = groups.get(key);
    if (existing) {
      existing.calls.push(call);
      continue;
    }
    const conversation = conversationId ? conversationsById.get(conversationId) : null;
    groups.set(key, {
      key,
      conversationId,
      title: conversation?.title.trim() || (conversationId ? "已删除或不可用的会话" : "未关联会话"),
      calls: [call],
    });
  }
  return [...groups.values()]
    .map((group) => ({
      ...group,
      calls: [...group.calls].sort((left, right) => right.created_at.localeCompare(left.created_at)),
    }))
    .sort((left, right) => right.calls[0].created_at.localeCompare(left.calls[0].created_at));
}

interface DiscoveryCapability {
  id: string;
  kind: string;
  name?: string;
  display_name?: string;
  description?: string;
  model_exposed?: boolean;
  function_name?: string | null;
  requires_confirmation?: boolean;
}

interface DiscoveryAina {
  id: string;
  name: string;
  version: string;
  runtime: string;
  availability: string;
  routing_candidate: boolean;
  entrypoint?: DiscoveryCapability | null;
  capabilities: {
    skills: DiscoveryCapability[];
    tools: DiscoveryCapability[];
    ui: DiscoveryCapability[];
  };
}

interface CapabilityDiscoveryDetails {
  aina_graph: {
    available_count: number;
    counts?: { builtin_aina: number; remote_aina: number };
    available: DiscoveryAina[];
    excluded: Array<{
      id: string;
      name: string;
      runtime: string;
      reason: string;
      missing_permissions: string[];
    }>;
  };
  model_scope: {
    counts: {
      remote_tool?: number;
      remote_aina?: number;
      builtin_capability?: number;
      tool?: number;
      aina?: number;
      builtin?: number;
    };
    forced?: string | null;
    by_aina: Array<{ aina_id: string; capabilities: DiscoveryCapability[] }>;
    standalone: DiscoveryCapability[];
  };
}

function parseCapabilityDiscovery(details: Record<string, unknown>): CapabilityDiscoveryDetails | null {
  const graph = details.aina_graph;
  const scope = details.model_scope;
  if (!graph || typeof graph !== "object" || !scope || typeof scope !== "object") return null;
  const typedGraph = graph as CapabilityDiscoveryDetails["aina_graph"];
  const typedScope = scope as CapabilityDiscoveryDetails["model_scope"];
  if (!Array.isArray(typedGraph.available) || !Array.isArray(typedGraph.excluded) || !Array.isArray(typedScope.by_aina)) {
    return null;
  }
  return { aina_graph: typedGraph, model_scope: typedScope };
}

function CapabilityDiscoveryView({ details }: { details: CapabilityDiscoveryDetails }) {
  const { aina_graph: graph, model_scope: scope } = details;
  const builtinAinaCount = graph.counts?.builtin_aina ?? graph.available.filter((item) => item.runtime === "builtin").length;
  const remoteAinaCount = graph.counts?.remote_aina ?? graph.available.filter((item) => item.runtime === "remote").length;
  const remoteToolCount = scope.counts.remote_tool ?? scope.counts.tool ?? 0;
  const remoteScopeAinaCount = scope.counts.remote_aina ?? scope.counts.aina ?? 0;
  const builtinCapabilityCount = scope.counts.builtin_capability ?? scope.counts.builtin ?? 0;
  return (
    <div className="mt-2 space-y-2 text-[11.5px] text-ink-muted">
      <div className="flex items-center gap-2">
        <AppWindow className="h-3.5 w-3.5 text-accent" />
        <span className="font-bold text-ink">可用 AINA</span>
        <span className="rounded bg-app-soft px-1.5 py-0.5 font-bold text-ink-muted">内置 AINA {builtinAinaCount}</span>
        <span className="rounded bg-accent-soft px-1.5 py-0.5 font-bold text-accent">远程 AINA {remoteAinaCount}</span>
      </div>

      <div className="space-y-2">
        {graph.available.map((aina) => (
          <section key={aina.id} className="overflow-hidden rounded-md border border-line bg-white">
            <div className="flex items-start gap-2 border-b border-line bg-app-soft px-2.5 py-2">
              <AppWindow className="mt-0.5 h-3.5 w-3.5 shrink-0 text-accent" />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="font-bold text-ink">{aina.name}</span>
                  <span className="font-mono text-[10.5px] text-ink-subtle">{aina.id}</span>
                </div>
                <div className="mt-0.5 flex flex-wrap gap-x-2 text-[10.5px] text-ink-subtle">
                  <span>{runtimeLabel(aina.runtime)}</span>
                  <span>v{aina.version}</span>
                  <span>{availabilityLabel(aina.availability)}</span>
                  {aina.routing_candidate ? <span>可参与路由</span> : null}
                </div>
              </div>
            </div>
            <div className="divide-y divide-line px-2.5">
              <CapabilityGroup icon={<Wrench />} label="工具" items={aina.capabilities.tools} />
              <CapabilityGroup icon={<Code2 />} label="技能" items={aina.capabilities.skills} />
              <CapabilityGroup icon={<AppWindow />} label="界面" items={aina.capabilities.ui} />
            </div>
          </section>
        ))}
      </div>

      <section className="rounded-md border border-line bg-app-soft px-2.5 py-2">
        <div className="flex flex-wrap items-center gap-2">
          <Bot className="h-3.5 w-3.5 text-ink-muted" />
          <span className="font-bold text-ink">模型可用范围</span>
          <span>远程工具 {remoteToolCount}</span>
          <span>远程 AINA {remoteScopeAinaCount}</span>
          <span>内置能力 {builtinCapabilityCount}</span>
          {scope.forced ? <span className="font-mono text-warning-deep">强制指定：{scope.forced}</span> : null}
        </div>
        <div className="mt-1.5 space-y-1">
          {scope.by_aina.map((group) => (
            <div key={group.aina_id} className="grid grid-cols-[minmax(110px,0.8fr)_minmax(0,1.2fr)] gap-2">
              <span className="truncate font-mono text-ink">{group.aina_id}</span>
              <span className="break-words">{group.capabilities.map((item) => item.id).join(", ") || "无"}</span>
            </div>
          ))}
          {scope.standalone.length ? (
            <div className="grid grid-cols-[minmax(110px,0.8fr)_minmax(0,1.2fr)] gap-2">
              <span className="font-mono text-ink">独立能力</span>
              <span className="break-words">{scope.standalone.map((item) => item.id).join(", ")}</span>
            </div>
          ) : null}
        </div>
      </section>

      {graph.excluded.length ? (
        <section className="rounded-md border border-warning-ring bg-warning-soft px-2.5 py-2">
          <div className="font-bold text-warning-deep">不可用 AINA</div>
          {graph.excluded.map((aina) => (
            <div key={aina.id} className="mt-1 flex flex-wrap gap-x-2">
              <span className="font-mono text-ink">{aina.id}</span>
              <span>{availabilityLabel(aina.reason)}</span>
              {aina.missing_permissions.length ? <span>{aina.missing_permissions.join(", ")}</span> : null}
            </div>
          ))}
        </section>
      ) : null}

      <details>
        <summary className="cursor-pointer font-semibold text-ink-muted">原始 JSON</summary>
        <pre className="mt-1.5 rounded-md bg-app-soft p-2 whitespace-pre-wrap break-all text-[11px] leading-relaxed text-ink-muted">{JSON.stringify(details, null, 2)}</pre>
      </details>
    </div>
  );
}

function CapabilityGroup({ icon, label, items }: { icon: React.ReactNode; label: string; items: DiscoveryCapability[] }) {
  if (!items.length) return null;
  return (
    <div className="grid grid-cols-[72px_1fr] gap-2 py-2">
      <div className="flex items-center gap-1 font-bold text-ink-muted [&>svg]:h-3 [&>svg]:w-3">
        {icon}
        {label}
      </div>
      <div className="space-y-1">
        {items.map((item) => (
          <div key={`${item.kind}-${item.id}`} className="flex min-w-0 items-start gap-1.5">
            <span className="min-w-0 flex-1 break-all font-mono text-ink">{item.id}</span>
            {item.model_exposed ? <span className="shrink-0 rounded bg-success-soft px-1 py-0.5 text-[10px] font-bold text-success-deep">模型可见</span> : null}
            {item.requires_confirmation ? <span className="shrink-0 rounded bg-warning-soft px-1 py-0.5 text-[10px] font-bold text-warning-deep">需确认</span> : null}
          </div>
        ))}
      </div>
    </div>
  );
}

function TraceStatus({ status }: { status: TraceRecord["status"] }) {
  const success = status === "completed";
  const failed = status === "failed";
  return (
    <span className={classNames("inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-bold", success ? "bg-success-soft text-success-deep" : failed ? "bg-danger-soft text-danger" : "bg-warning-soft text-warning-deep")}>
      {success ? <CheckCircle2 className="w-3 h-3" /> : failed ? <XCircle className="w-3 h-3" /> : <Clock3 className="w-3 h-3" />}
      {traceStatusLabel(status)}
    </span>
  );
}

function LLMCallStatus({ status, compact = false }: { status: LLMCallRecord["status"]; compact?: boolean }) {
  const completed = status === "completed";
  const failed = status === "failed";
  return (
    <span className={classNames(
      "inline-flex shrink-0 items-center gap-1 rounded-md font-bold",
      compact ? "px-1.5 py-0.5 text-[10px]" : "px-2 py-1 text-[11px]",
      completed ? "bg-success-soft text-success-deep" : failed ? "bg-danger-soft text-danger" : "bg-warning-soft text-warning-deep",
    )}>
      {completed ? <CheckCircle2 className="h-3 w-3" /> : failed ? <XCircle className="h-3 w-3" /> : <Clock3 className="h-3 w-3" />}
      {completed ? "成功" : failed ? "失败" : "请求中"}
    </span>
  );
}

interface LLMCallPerformance {
  inputTokens: number | null;
  outputTokens: number | null;
  totalTokens: number | null;
  outputTokensPerSecond: number | null;
}

interface LLMAggregatePerformance {
  totalDurationMs: number | null;
  totalTokens: number | null;
  outputTokensPerSecond: number | null;
}

function llmCallPerformance(call: LLMCallRecord): LLMCallPerformance {
  const usage = isRecord(call.response?.usage) ? call.response.usage : null;
  const inputTokens = numberMetric(usage?.prompt_tokens);
  const outputTokens = numberMetric(usage?.completion_tokens);
  const reportedTotal = numberMetric(usage?.total_tokens);
  const totalTokens = reportedTotal ?? (
    inputTokens != null || outputTokens != null
      ? (inputTokens ?? 0) + (outputTokens ?? 0)
      : null
  );
  const outputTokensPerSecond = outputTokens != null && call.duration_ms != null && call.duration_ms > 0
    ? outputTokens / (call.duration_ms / 1000)
    : null;
  return { inputTokens, outputTokens, totalTokens, outputTokensPerSecond };
}

function summarizeLlmCalls(calls: LLMCallRecord[]): LLMAggregatePerformance {
  let totalDurationMs = 0;
  let measuredDurationMs = 0;
  let totalTokens = 0;
  let totalOutputTokens = 0;
  let hasDuration = false;
  let hasTokens = false;
  let hasOutputTokens = false;

  for (const call of calls) {
    if (call.duration_ms != null) {
      totalDurationMs += call.duration_ms;
      hasDuration = true;
    }
    const performance = llmCallPerformance(call);
    if (performance.totalTokens != null) {
      totalTokens += performance.totalTokens;
      hasTokens = true;
    }
    if (performance.outputTokens != null) {
      totalOutputTokens += performance.outputTokens;
      hasOutputTokens = true;
      if (call.duration_ms != null && call.duration_ms > 0) measuredDurationMs += call.duration_ms;
    }
  }

  return {
    totalDurationMs: hasDuration ? totalDurationMs : null,
    totalTokens: hasTokens ? totalTokens : null,
    outputTokensPerSecond: hasOutputTokens && measuredDurationMs > 0
      ? totalOutputTokens / (measuredDurationMs / 1000)
      : null,
  };
}

function numberMetric(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function formatDuration(value: number | null): string {
  if (value == null) return "—";
  if (value < 1000) return `${value.toFixed(0)} ms`;
  if (value < 60_000) return `${(value / 1000).toFixed(2)} s`;
  const minutes = Math.floor(value / 60_000);
  const seconds = (value % 60_000) / 1000;
  return `${minutes}m ${seconds.toFixed(1)}s`;
}

function formatTokenCount(value: number | null): string {
  return value == null ? "—" : Math.round(value).toLocaleString("zh-CN");
}

function formatOutputTokenRate(value: number | null): string {
  if (value == null) return "—";
  return `${value < 1 ? value.toFixed(2) : value.toFixed(1)} Output Token/s`;
}

function llmContextLabel(call: LLMCallRecord): string {
  if (call.context_type === "conversation") return call.context_id ? `会话 ${call.context_id}` : "会话";
  if (call.context_type === "document_edit_task") return call.context_id ? `文档任务 ${call.context_id}` : "文档任务";
  return call.context_id ?? "未关联";
}

function NoTraceSelected() {
  return (
    <div className="h-full min-h-[520px] flex items-center justify-center text-center">
      <div>
        <ShieldCheck className="mx-auto w-10 h-10 text-ink-subtle" />
        <h2 className="mt-3 text-[15px] font-bold text-ink">选择一条调用记录</h2>
        <p className="mt-1 text-[13px] text-ink-muted">查看模型、工具、AINA、授权和最终响应的完整链路。</p>
      </div>
    </div>
  );
}

function NoLLMCallSelected() {
  return (
    <div className="flex h-full min-h-[520px] items-center justify-center text-center">
      <div>
        <Server className="mx-auto h-10 w-10 text-ink-subtle" />
        <h2 className="mt-3 text-[15px] font-bold text-ink">选择一次模型请求</h2>
        <p className="mt-1 text-[13px] text-ink-muted">查看发送到 Chat Completions 接口的入参与模型原始返回值。</p>
      </div>
    </div>
  );
}

function runtimeLabel(value: string): string {
  return value === "builtin" ? "内置" : value === "remote" ? "远程" : value;
}

function availabilityLabel(value: string): string {
  const labels: Record<string, string> = {
    builtin: "系统内置",
    available: "可用",
    installed: "已安装",
    unavailable: "不可用",
    disabled: "已停用",
    not_installed: "未安装",
    installation_disabled: "安装已停用",
    disabled_for_conversation: "当前对话未启用",
    missing_permissions: "缺少权限",
  };
  return labels[value] ?? value;
}

function traceStatusLabel(value: TraceRecord["status"]): string {
  if (value === "completed") return "已完成";
  if (value === "failed") return "失败";
  if (value === "approval_required") return "等待确认";
  return "运行中";
}

function eventStatusLabel(value: TraceEvent["status"]): string {
  if (value === "completed") return "已完成";
  if (value === "failed") return "失败";
  if (value === "pending") return "等待中";
  return value === "started" ? "已开始" : value;
}
