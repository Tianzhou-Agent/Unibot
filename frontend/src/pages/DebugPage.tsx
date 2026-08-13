import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
import { useLocation, useSearchParams } from "react-router-dom";
import { Topbar } from "@/components/layout/Topbar";
import { PersonalObservabilityView } from "@/components/observability/PersonalObservabilityView";
import { api, apiErrorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { classNames, timeAgo } from "@/lib/utils";
import { useDebugMode } from "@/lib/debugMode";
import {
  getAdminObsSession,
  getObsOverview,
  getObsSession,
  loadLegacyAdminObsSession,
  loadLegacyPersonalObsData,
  loadLegacyPersonalObsSession,
} from "@/lib/obsData";
import type { ObsOverview, ObsSessionDetail } from "@/lib/obsData";
import { adaptSessionDetail } from "@/lib/obsAdapter";
import { useMockSession } from "@/lib/mockSession";
import type {
  AdminSummary,
  ConversationRecord,
  LLMCallRecord,
  TraceEvent,
  TraceRecord,
  TraceSpan,
} from "@/types";

export default function DebugPage() {
  const { debugMode, setDebugMode } = useDebugMode();
  const { user, config } = useAuth();
  const { isAdmin: mockIsAdmin, profile } = useMockSession();
  const isAdmin = config.auth_required ? Boolean(user?.is_admin) : mockIsAdmin;
  const location = useLocation();
  const showAdminView = isAdmin && location.pathname.startsWith("/admin/");
  const [searchParams] = useSearchParams();
  const requestedSessionId = searchParams.get("sessionId");
  const requestedTrace = searchParams.get("trace");
  const [health, setHealth] = useState<"checking" | "ok" | "error">("checking");
  const [summary, setSummary] = useState<AdminSummary | null>(null);
  const [traces, setTraces] = useState<TraceRecord[]>([]);
  const [llmCalls, setLlmCalls] = useState<LLMCallRecord[]>([]);
  const [conversations, setConversations] = useState<ConversationRecord[]>([]);
  const [obsOverview, setObsOverview] = useState<ObsOverview | null>(null);
  const [obsSession, setObsSession] = useState<ObsSessionDetail | null>(null);
  const [overviewPeriod, setOverviewPeriod] = useState<"day" | "week" | "month">("week");
  const [selectedTrace, setSelectedTrace] = useState<string | null>(requestedTrace);
  const [selectedLlmCall, setSelectedLlmCall] = useState<string | null>(null);
  const [traceDetailView, setTraceDetailView] = useState<"trace" | "llm">("trace");
  const [expandedTraceGroups, setExpandedTraceGroups] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const adminSessionRequestRef = useRef(0);
  const autoLoadedSessionsRef = useRef<Set<string>>(new Set());

  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      const actorQuery = config.auth_required
        ? ""
        : `tenant_id=${encodeURIComponent(profile.tenantId)}&user_id=${encodeURIComponent(profile.actorUserId)}`;
      const querySuffix = actorQuery ? `?${actorQuery}` : "";
      const adminData = showAdminView;
      if (adminData) {
        // 管理员视图：统计来自 /admin/summary + /admin/obs/overview；
        // Trace 按会话从 /admin/obs/sessions 按需加载（phase four）
        const [healthData, summaryData, overviewData, conversationData] = await Promise.all([
          api.get<{ status: string }>("/health"),
          api.get<AdminSummary>(`/admin/summary${querySuffix}`),
          api.get<ObsOverview>("/admin/obs/overview?range=week"),
          api.get<ConversationRecord[]>("/admin/conversations"),
        ]);
        const llmCallCount = (overviewData?.per_model ?? []).reduce((sum, row) => sum + row.call_count, 0);
        setHealth(healthData.status === "ok" ? "ok" : "error");
        setSummary({
          tools: summaryData?.tools ?? 0,
          skills: summaryData?.skills ?? 0,
          ainas: summaryData?.ainas ?? 0,
          installations: summaryData?.installations ?? 0,
          memories: summaryData?.memories ?? 0,
          conversations: conversationData.length,
          traces: overviewData?.trace_count ?? 0,
          llm_calls: llmCallCount,
        });
        setTraces([]);
        setLlmCalls([]);
        adminSessionRequestRef.current += 1;
        autoLoadedSessionsRef.current.clear();
        setConversations(conversationData);
        setError(null);
      } else {
        // 普通用户优先使用后端聚合 DTO；仅在 OBS 未启用、查询失败或会话尚未迁移时读取旧数据。
        const healthData: { status: string; obs?: { enabled?: boolean } } = await api.get<{
          status: string;
          obs?: { enabled?: boolean };
        }>("/health")
          .catch(() => ({ status: "error" as const }));
        const sessionId = requestedSessionId ?? null;
        let overviewData: ObsOverview | null = null;
        let sessionData: ObsSessionDetail | null = null;
        let legacyTraces: TraceRecord[] = [];
        let legacyCalls: LLMCallRecord[] = [];
        if (healthData.obs?.enabled !== false) {
          try {
            if (sessionId) {
              sessionData = await getObsSession(sessionId);
            } else {
              overviewData = await getObsOverview(overviewPeriod);
            }
          } catch {
            overviewData = null;
            sessionData = null;
          }
        }
        if (sessionId && !sessionData) {
          const legacy = await loadLegacyPersonalObsSession(sessionId, actorQuery).catch(() => null);
          legacyTraces = legacy?.traces ?? [];
          legacyCalls = legacy?.calls ?? [];
        } else if (!sessionId && (!overviewData || overviewData.trace_count === 0)) {
          const legacy = await loadLegacyPersonalObsData(actorQuery).catch(() => null);
          legacyTraces = legacy?.traces ?? [];
          legacyCalls = legacy?.calls ?? [];
          if (legacyTraces.length > 0 || legacyCalls.length > 0) {
            overviewData = null;
          }
        }
        const conversationData = await api.get<ConversationRecord[]>(`/conversations${querySuffix}`).catch(() => []);
        setHealth(healthData.status === "ok" ? "ok" : "error");
        setConversations(conversationData);
        setTraces(legacyTraces);
        setLlmCalls(legacyCalls);
        setObsOverview(overviewData);
        setObsSession(sessionData);
        setError(null);
      }
    } catch (loadError) {
      setHealth("error");
      setError(apiErrorMessage(loadError));
    } finally {
      setRefreshing(false);
    }
  }, [config.auth_required, profile.actorUserId, profile.tenantId, requestedTrace, requestedSessionId, showAdminView, overviewPeriod]);

  useEffect(() => {
    void load();
  }, [load]);

  const selected = useMemo(
    () => traces.find((trace) => trace.trace_id === selectedTrace) ?? null,
    [selectedTrace, traces],
  );
  const selectedTraceCalls = useMemo(
    () => llmCalls
      .filter((call) => call.trace_id === selected?.trace_id)
      .sort((left, right) => left.created_at.localeCompare(right.created_at)),
    [llmCalls, selected?.trace_id],
  );
  const selectedCall = useMemo(
    () => selectedTraceCalls.find((call) => call.call_id === selectedLlmCall) ?? null,
    [selectedLlmCall, selectedTraceCalls],
  );
  const conversationsById = useMemo(
    () => new Map(conversations.map((conversation) => [conversation.id, conversation])),
    [conversations],
  );
  const traceGroups = useMemo(
    () => {
      const fromTraces = groupTracesByConversation(traces, conversationsById);
      if (!showAdminView) return fromTraces;
      // Phase four: admin 视图以会话为驱动，Trace 按需从 /admin/obs/sessions 加载
      const keys = new Set(fromTraces.map((group) => group.key));
      if (requestedSessionId && !keys.has(requestedSessionId)) {
        fromTraces.push({
          key: requestedSessionId,
          conversationId: requestedSessionId,
          title: conversationsById.get(requestedSessionId)?.title.trim() || "已删除或不可用的会话",
          traces: [],
        });
        keys.add(requestedSessionId);
      }
      for (const conversation of conversations) {
        if (keys.has(conversation.id)) continue;
        fromTraces.push({
          key: conversation.id,
          conversationId: conversation.id,
          title: conversation.title.trim() || "未命名会话",
          traces: [],
        });
      }
      return fromTraces;
    },
    [conversations, conversationsById, requestedSessionId, showAdminView, traces],
  );

  const loadAdminSession = useCallback(async (conversationId: string) => {
    const requestId = ++adminSessionRequestRef.current;
    try {
      const session = await getAdminObsSession(conversationId).catch(() => null);
      const data = session
        ? adaptSessionDetail(session)
        : await loadLegacyAdminObsSession(conversationId);
      if (requestId !== adminSessionRequestRef.current) return;
      setTraces(data.traces);
      setLlmCalls(data.calls);
    } catch (loadError) {
      if (requestId !== adminSessionRequestRef.current) return;
      setError(apiErrorMessage(loadError));
    }
  }, []);

  // 已尝试自动加载过的会话：防止空 traces 响应导致自动加载 effect 无限重取
  const toggleTraceGroup = (conversationId: string | null, hasTraces: boolean) => {
    setExpandedTraceGroups((current) => {
      const key = traceGroupKey(conversationId);
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
    if (showAdminView && conversationId && !hasTraces) {
      void loadAdminSession(conversationId);
    }
  };

  // Admin 视图默认展开第一个会话组：自动加载其 trace 一次（同一会话不重取，避免空响应时无限循环）
  useEffect(() => {
    const targetGroup = requestedSessionId
      ? traceGroups.find((group) => group.conversationId === requestedSessionId)
      : traceGroups[0];
    if (showAdminView && targetGroup && targetGroup.traces.length === 0 && targetGroup.conversationId
      && !autoLoadedSessionsRef.current.has(targetGroup.conversationId)) {
      autoLoadedSessionsRef.current.add(targetGroup.conversationId);
      void loadAdminSession(targetGroup.conversationId);
    }
  }, [showAdminView, traceGroups, loadAdminSession, requestedSessionId]);

  useEffect(() => {
    const selectedRecord = traces.find((trace) => trace.trace_id === selectedTrace);
    const requestedGroup = showAdminView && requestedSessionId
      ? traceGroups.find((group) => group.conversationId === requestedSessionId)
      : null;
    const groupKey = selectedRecord
      ? traceGroupKey(selectedRecord.conversation_id)
      : requestedGroup?.key ?? traceGroups[0]?.key;
    if (!groupKey) return;
    setExpandedTraceGroups((current) => {
      if (current.has(groupKey) || (!selectedRecord && !requestedGroup && current.size > 0)) return current;
      const next = new Set(current);
      next.add(groupKey);
      return next;
    });
  }, [requestedSessionId, selectedTrace, showAdminView, traceGroups, traces]);

  useEffect(() => {
    setTraceDetailView("trace");
  }, [selectedTrace]);

  useEffect(() => {
    if (selectedTraceCalls.length === 0) {
      setSelectedLlmCall(null);
      setTraceDetailView("llm");
      return;
    }
    setSelectedLlmCall((current) => (
      current && selectedTraceCalls.some((call) => call.call_id === current)
        ? current
        : selectedTraceCalls[0].call_id
    ));
  }, [selectedTraceCalls]);

  const selectedConversationTitle = selected?.conversation_id
    ? conversationsById.get(selected.conversation_id)?.title ?? "已删除或不可用的会话"
    : "未关联会话";

  if (!showAdminView) {
    return (
      <PersonalObservabilityView
        error={error}
        sessionId={requestedSessionId}
        conversations={conversations}
        traces={traces}
        llmCalls={llmCalls}
        obsOverview={obsOverview}
        obsSession={obsSession}
        onOverviewPeriodChange={setOverviewPeriod}
      />
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-app-bg">
      <Topbar
        title="OBS"
        badge={health === "ok" ? undefined : {
          label: health === "checking" ? "检查中" : "后端异常",
          tone: health === "checking" ? "thinking" : "warning",
        }}
        actions={
          <div className="flex items-center gap-2">
            <div className="flex rounded-lg bg-app-soft p-0.5">
              <button
                type="button"
                onClick={() => setDebugMode(false)}
                className={classNames(
                  "rounded-md px-3 py-1.5 text-[12px] font-bold",
                  !debugMode ? "bg-white text-accent shadow-sm" : "text-ink-muted",
                )}
              >
                关闭
              </button>
              <button
                type="button"
                onClick={() => setDebugMode(true)}
                className={classNames(
                  "rounded-md px-3 py-1.5 text-[12px] font-bold",
                  debugMode ? "bg-white text-accent shadow-sm" : "text-ink-muted",
                )}
              >
                开启
              </button>
            </div>
            <button type="button" onClick={() => void load()} disabled={refreshing} className="btn-outline h-8">
              <RefreshCw className={classNames("w-3.5 h-3.5", refreshing && "animate-spin")} />刷新
            </button>
          </div>
        }
      />
      <div className="flex min-h-0 flex-1 overflow-hidden p-2.5 md:p-3">
        <div className="flex h-full min-h-0 w-full flex-col gap-2.5 md:gap-3">
          {error ? (
            <div className="shrink-0 rounded-lg border border-danger-ring bg-danger-soft p-3 text-[12.5px] text-danger-deep">
              {error}
            </div>
          ) : null}

          <section
            className="grid shrink-0 grid-flow-col auto-cols-[minmax(112px,1fr)] gap-2 overflow-x-auto xl:grid-flow-row xl:grid-cols-8"
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

          {debugMode ? <section className="grid min-h-0 flex-1 grid-cols-1 grid-rows-[minmax(0,0.8fr)_minmax(0,1.2fr)] overflow-hidden rounded-xl border border-line lg:grid-cols-[minmax(0,1fr)_minmax(0,4fr)] lg:grid-rows-1">
            <div className="flex min-h-0 flex-col overflow-hidden lg:border-r lg:border-line">
              <div className="flex shrink-0 items-center gap-2 border-b border-line px-3 py-2">
                <Activity className="h-3.5 w-3.5 text-accent" />
                <span className="text-[12px] font-bold text-ink">调用记录</span>
                <span className="ml-auto text-[11.5px] text-ink-muted">{traces.length} 条 Trace</span>
              </div>
              <div className="min-h-0 flex-1 overflow-y-auto" aria-label="Trace 列表">
                {traceGroups.length === 0 ? (
                  <div className="py-20 text-center text-[12px] text-ink-muted">完成一次对话后，调用记录会显示在这里。</div>
                ) : (
                  traceGroups.map((group) => (
                    <ConversationTraceGroup
                      key={group.key}
                      group={group}
                      expanded={expandedTraceGroups.has(group.key)}
                      selectedTrace={selectedTrace}
                      onToggle={() => toggleTraceGroup(group.conversationId, group.traces.length > 0)}
                      onSelectTrace={setSelectedTrace}
                    />
                  ))
                )}
              </div>
            </div>

            <div className="min-h-0 overflow-hidden">
              {selected ? (
                <TraceDetail
                  trace={selected}
                  conversationTitle={selectedConversationTitle}
                  calls={selectedTraceCalls}
                  selectedCall={selectedCall}
                  view={traceDetailView}
                  onViewChange={setTraceDetailView}
                  onSelectCall={setSelectedLlmCall}
                />
              ) : <NoTraceSelected />}
            </div>
          </section> : (
            <section className="px-6 py-10 text-center" aria-label="调试模式说明">
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
    <div className="flex items-center gap-2.5 rounded-xl border border-line bg-white px-2.5 py-1.5 shadow-card">
      <div className={classNames("flex h-7 w-7 shrink-0 items-center justify-center rounded-lg [&>svg]:h-3.5 [&>svg]:w-3.5", colors[tone])}>
        {icon}
      </div>
      <div className="min-w-0">
        <div className="text-[17px] font-extrabold leading-none text-ink">{value ?? "—"}</div>
        <div className="mt-0.5 truncate text-[10.5px] font-semibold text-ink-muted">{label}</div>
      </div>
    </div>
  );
}

interface ConversationTraceGroupData {
  key: string;
  conversationId: string | null;
  title: string;
  traces: TraceRecord[];
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
            "flex w-full items-start gap-2.5 px-3 py-2 pr-11 text-left transition-colors hover:bg-app-soft",
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

function TraceRow({ trace, active, onClick }: { trace: TraceRecord; active: boolean; onClick: () => void }) {
  return (
    <div className="relative">
      <button
        type="button"
        onClick={onClick}
        className={classNames(
          "w-full border-l-2 py-2 pl-3 pr-10 text-left transition-colors",
          active ? "border-accent bg-accent-soft" : "border-transparent hover:bg-app-soft",
        )}
      >
        <div className="flex items-center gap-2">
          <TraceStatus status={trace.status} />
          <span className="min-w-0 flex-1 truncate font-mono text-[12px] text-ink">{trace.trace_id}</span>
        </div>
        <div className="mt-1 flex items-center gap-2 text-[11.5px] text-ink-muted">
          <span>{trace.spans?.length ?? 0} 个 Span</span>
          <span>·</span>
          <span>{trace.events.length} 个事件</span>
          <span>·</span>
          <span>{timeAgo(trace.created_at)}</span>
        </div>
      </button>
      <CopyIdButton
        value={trace.trace_id}
        label={`复制 Trace ID ${trace.trace_id}`}
        compact
        className="absolute right-2 top-2.5"
      />
    </div>
  );
}

function LLMCallRow({
  call,
  sequence,
  active,
  onClick,
}: {
  call: LLMCallRecord;
  sequence: number;
  active: boolean;
  onClick: () => void;
}) {
  const performance = llmCallPerformance(call);
  return (
    <button
      type="button"
      onClick={onClick}
      className={classNames(
        "w-full border-b border-l-2 border-b-line px-2.5 py-2 text-left transition-colors last:border-b-0",
        active ? "border-l-accent bg-accent-soft" : "border-l-transparent hover:bg-app-soft",
      )}
    >
      <div className="flex items-center justify-between gap-1.5">
        <strong className="truncate text-[12px] text-ink">请求 {sequence}</strong>
        <LLMCallStatus status={call.status} compact />
      </div>
      <div className="mt-1 truncate font-mono text-[11px] text-ink-muted">{call.model}</div>
      <div className="mt-1 flex items-center justify-between gap-1 text-[10.5px] text-ink-subtle">
        <span className="truncate font-mono">{call.duration_ms != null ? formatDuration(call.duration_ms) : "请求中"}</span>
        <span className="shrink-0 font-mono">{formatTokenCount(performance.totalTokens)} Token</span>
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
  const performance = llmCallPerformance(call);
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="shrink-0 border-b border-line bg-app-soft px-3 py-1.5">
        <div className="flex items-center gap-2">
          <LLMCallStatus status={call.status} />
          <h2 className="min-w-0 flex-1 truncate font-mono text-[12px] font-bold text-ink">{call.call_id}</h2>
          <CopyIdButton value={call.call_id} label="复制模型调用 ID" />
        </div>
        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-ink-muted">
          <span className="min-w-0 basis-full truncate" title={call.endpoint}>接口：<span className="font-mono">{call.endpoint}</span></span>
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
        </div>
      </div>

      <div className="flex shrink-0 items-center gap-2 border-b border-line px-3 py-1.5">
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

      <div className="min-h-0 flex-1 overflow-auto bg-[#0f172a] p-3" aria-label={payloadView === "request" ? "模型请求 JSON" : "模型响应 JSON"}>
        {payload ? (
          <HighlightedJson key={`${call.call_id}-${payloadView}`} value={payload} />
        ) : (
          <div className="flex h-full min-h-48 items-center justify-center text-[12.5px] text-slate-400">
            {call.status === "running" ? "请求仍在处理中，刷新后查看返回值。" : "没有可用的响应体。"}
          </div>
        )}
      </div>
      {call.error ? (
        <div className="shrink-0 border-t border-danger-ring bg-danger-soft px-3 py-1.5 text-[12px] text-danger-deep">
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

function TraceDetail({
  trace,
  conversationTitle,
  calls,
  selectedCall,
  view,
  onViewChange,
  onSelectCall,
}: {
  trace: TraceRecord;
  conversationTitle: string;
  calls: LLMCallRecord[];
  selectedCall: LLMCallRecord | null;
  view: "trace" | "llm";
  onViewChange: (view: "trace" | "llm") => void;
  onSelectCall: (callId: string) => void;
}) {
  const performance = summarizeLlmCalls(calls);
  const spans = trace.spans ?? [];
  const callsBySpanId = new Map(
    calls.filter((call) => call.span_id).map((call) => [call.span_id as string, call]),
  );
  const userRequest = trace.events.find((event) => event.kind === "user.request");
  const finalResponse = [...trace.events].reverse().find((event) => event.kind === "final.response");
  return (
    <div className="flex h-full min-h-0 flex-col">
      {view === "trace" ? <div className="shrink-0 border-b border-line bg-app-soft px-3 py-2.5">
        <div className="grid gap-x-4 gap-y-1 text-[12px] text-ink-muted sm:grid-cols-2 lg:grid-cols-4">
          <div className="flex min-w-0 items-center gap-1.5">
            <span className="shrink-0">会话：</span>
            <span className="truncate font-medium text-ink">{conversationTitle}</span>
          </div>
          <div className="flex min-w-0 items-center gap-1.5">
            <span className="shrink-0">会话 ID：</span>
            <span className="truncate font-mono">{trace.conversation_id ?? "—"}</span>
            {trace.conversation_id ? (
              <CopyIdButton value={trace.conversation_id} label="复制 Conversation ID" compact />
            ) : null}
          </div>
          <div className="flex min-w-0 items-center gap-1.5">
            <span className="shrink-0">主体：</span>
            <span className="truncate font-mono">{trace.tenant_id}/{trace.user_id}</span>
          </div>
          <div className="flex min-w-0 items-center gap-1.5">
            <span className="shrink-0">开始：</span>
            <span className="truncate">{new Date(trace.created_at).toLocaleString("zh-CN")}</span>
          </div>
        </div>
      </div> : null}
      <div className="flex shrink-0 items-center gap-2 border-b border-line px-3 py-1.5">
        <div className="flex rounded-lg bg-app-soft p-0.5">
          <button
            type="button"
            onClick={() => onViewChange("trace")}
            className={classNames(
              "rounded-md px-3 py-1.5 text-[12px] font-bold",
              view === "trace" ? "bg-white text-accent shadow-sm" : "text-ink-muted",
            )}
          >
            调用链
          </button>
          <button
            type="button"
            onClick={() => onViewChange("llm")}
            className={classNames(
              "rounded-md px-3 py-1.5 text-[12px] font-bold",
              view === "llm" ? "bg-white text-accent shadow-sm" : "text-ink-muted",
            )}
          >
            模型请求 {calls.length}
          </button>
        </div>
        {view === "llm" ? (
          <span className="ml-auto hidden text-[11px] text-ink-subtle xl:block">
            总耗时 {formatDuration(performance.totalDurationMs)}
            {" · "}
            {formatTokenCount(performance.totalTokens)} Token
            {" · "}
            {formatOutputTokenRate(performance.outputTokensPerSecond)}
          </span>
        ) : (
          <span className="ml-auto text-[11px] text-ink-subtle">
            {spans.length} 个 Span · {trace.events.length} 个事件
          </span>
        )}
      </div>
      {view === "trace" ? (
        <div className="min-h-0 flex-1 overflow-y-auto p-3">
          <section aria-label="Span 调用树">
            <div className="mb-3 flex items-center gap-2">
              <Route className="h-4 w-4 text-accent" />
              <h3 className="text-[13px] font-bold text-ink">Span 调用树</h3>
              <span className="text-[11px] text-ink-subtle">{spans.length}</span>
            </div>
            {spans.length > 0 ? (
              <div className="space-y-2">
                {buildSpanRows(spans, trace.root_span_id).map(({ span, depth }) => {
                  const llmCall = callsBySpanId.get(span.span_id);
                  const root = span.span_id === trace.root_span_id;
                  return (
                    <SpanRow
                      key={span.span_id}
                      span={span}
                      depth={depth}
                      fallbackInput={span.kind === "model" ? llmCall?.request : root ? userRequest?.details : undefined}
                      fallbackOutput={span.kind === "model" ? llmCall?.response : root ? finalResponse?.details : undefined}
                    />
                  );
                })}
              </div>
            ) : (
              <div className="rounded-lg border border-dashed border-line px-3 py-4 text-center text-[12px] text-ink-muted">
                此 Trace 没有 Span 数据，可能由旧版本产生。
              </div>
            )}
          </section>
          <section className="mt-6 border-t border-line pt-4" aria-label="Trace 原始事件">
            <div className="mb-3 flex items-center gap-2">
              <Activity className="h-4 w-4 text-ink-muted" />
              <h3 className="text-[13px] font-bold text-ink">原始事件</h3>
              <span className="text-[11px] text-ink-subtle">{trace.events.length}</span>
            </div>
            <div className="space-y-0">
              {trace.events.map((event, index) => (
                <EventRow key={`${event.timestamp}-${index}`} event={event} last={index === trace.events.length - 1} />
              ))}
            </div>
          </section>
        </div>
      ) : (
        <div className="grid min-h-0 flex-1 grid-rows-[minmax(150px,0.55fr)_minmax(0,1.45fr)] lg:grid-cols-[minmax(0,1fr)_minmax(0,4fr)] lg:grid-rows-1">
          <div className="min-h-0 overflow-y-auto border-b border-line lg:border-b-0 lg:border-r" aria-label="当前 Trace 的模型请求">
            {calls.map((call, index) => (
              <LLMCallRow
                key={call.call_id}
                call={call}
                sequence={index + 1}
                active={call.call_id === selectedCall?.call_id}
                onClick={() => onSelectCall(call.call_id)}
              />
            ))}
          </div>
          <div className="min-h-0 overflow-hidden">
            {selectedCall ? <LLMCallDetail call={selectedCall} /> : <NoLLMCallSelected />}
          </div>
        </div>
      )}
    </div>
  );
}

function SpanRow({
  span,
  depth,
  fallbackInput,
  fallbackOutput,
}: {
  span: TraceSpan;
  depth: number;
  fallbackInput?: unknown;
  fallbackOutput?: unknown;
}) {
  const [collapsed, setCollapsed] = useState(true);
  const failed = span.status === "failed";
  const ttftMs = numberMetric(span.attributes.ttft_ms);
  const inputTokens = numberMetric(span.attributes.input_tokens);
  const outputTokens = numberMetric(span.attributes.output_tokens);
  const input = span.input ?? span.attributes.arguments ?? fallbackInput;
  const output = span.output ?? span.attributes.result ?? fallbackOutput;
  const attributes = Object.fromEntries(
    Object.entries(span.attributes).filter(([key]) => key !== "arguments" && key !== "result"),
  );
  const hasDetails = Boolean(
    span.target_id
    || span.target_version
    || span.logical_call_id
    || span.first_output_at
    || input != null
    || output != null
    || Object.keys(attributes).length
    || span.error,
  );

  return (
    <div
      className={classNames(
        "rounded-lg border bg-white",
        failed ? "border-danger/30" : "border-line",
      )}
      style={{ marginLeft: Math.min(depth, 6) * 24 }}
    >
      <button
        type="button"
        onClick={() => hasDetails && setCollapsed((current) => !current)}
        aria-expanded={hasDetails ? !collapsed : undefined}
        className={classNames(
          "flex w-full min-w-0 items-center gap-2 px-2.5 py-2 text-left",
          hasDetails && "cursor-pointer",
        )}
      >
        {hasDetails ? (
          collapsed
            ? <ChevronRight className="h-3.5 w-3.5 shrink-0 text-ink-subtle" />
            : <ChevronDown className="h-3.5 w-3.5 shrink-0 text-accent" />
        ) : <span className="w-3.5 shrink-0" />}
        <SpanKindIcon kind={span.kind} />
        <span className="shrink-0 rounded bg-app-soft px-1.5 py-0.5 text-[10.5px] font-bold uppercase text-ink-muted">
          {spanKindLabel(span.kind)}
        </span>
        <span className="min-w-0 truncate text-[13px] font-bold text-ink">{span.name}</span>
        <span className={classNames(
          "shrink-0 rounded px-1.5 py-0.5 text-[10.5px] font-bold",
          spanStatusTone(span.status),
        )}>
          {spanStatusLabel(span.status)}
        </span>
        {ttftMs != null ? (
          <span className="hidden shrink-0 text-[11px] text-ink-muted sm:inline">TTFT {formatDuration(ttftMs)}</span>
        ) : null}
        {inputTokens != null || outputTokens != null ? (
          <span className="hidden shrink-0 text-[11px] text-ink-muted xl:inline">
            {formatTokenCount(inputTokens)} / {formatTokenCount(outputTokens)} Token
          </span>
        ) : null}
        <span className="ml-auto shrink-0 font-mono text-[11px] text-ink-subtle">
          {span.duration_ms != null ? formatDuration(span.duration_ms) : "进行中"}
        </span>
      </button>
      {!collapsed && hasDetails ? (
        <div className="border-t border-line px-3 py-3 text-[11px] text-ink-muted">
          <div className="grid gap-x-4 gap-y-1 md:grid-cols-2 xl:grid-cols-4">
            <div><span className="text-ink-subtle">Span ID：</span><span className="font-mono break-all">{span.span_id}</span></div>
            <div><span className="text-ink-subtle">父 Span：</span><span className="font-mono break-all">{span.parent_span_id ?? "—"}</span></div>
            <div><span className="text-ink-subtle">目标：</span><span className="font-mono break-all">{span.target_id ?? "—"}</span></div>
            <div><span className="text-ink-subtle">版本：</span><span className="font-mono break-all">{span.target_version ?? "—"}</span></div>
            <div><span className="text-ink-subtle">逻辑调用：</span><span className="font-mono break-all">{span.logical_call_id ?? "—"}</span></div>
            <div><span className="text-ink-subtle">Attempt：</span>{span.attempt_no}</div>
            <div><span className="text-ink-subtle">开始：</span>{new Date(span.started_at).toLocaleString("zh-CN")}</div>
            <div><span className="text-ink-subtle">首输出：</span>{span.first_output_at ? new Date(span.first_output_at).toLocaleString("zh-CN") : "—"}</div>
          </div>
          {input != null || output != null ? (
            <div className="mt-3 grid gap-3 xl:grid-cols-2">
              {input != null ? <SpanPayload label="输入" value={input} spanName={span.name} /> : null}
              {output != null ? <SpanPayload label="输出" value={output} spanName={span.name} /> : null}
            </div>
          ) : null}
          {Object.keys(attributes).length > 0 ? (
            <div className="mt-3">
              <div className="mb-1 font-bold text-ink">属性</div>
              <pre className="max-h-80 overflow-auto rounded-md bg-app-soft p-2 whitespace-pre-wrap break-all leading-relaxed">{JSON.stringify(attributes, null, 2)}</pre>
            </div>
          ) : null}
          {span.error ? (
            <div className="mt-3">
              <div className="mb-1 font-bold text-danger">错误</div>
              <pre className="rounded-md bg-danger-soft p-2 whitespace-pre-wrap break-all leading-relaxed text-danger">{JSON.stringify(span.error, null, 2)}</pre>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function SpanPayload({ label, value, spanName }: { label: "输入" | "输出"; value: unknown; spanName: string }) {
  return (
    <section aria-label={`${spanName} ${label}`}>
      <div className="mb-1 font-bold text-ink">{label}</div>
      <pre className="max-h-96 overflow-auto rounded-md bg-slate-950 p-3 whitespace-pre-wrap break-all font-mono text-[11px] leading-relaxed text-slate-200">
        {JSON.stringify(value, null, 2) ?? String(value)}
      </pre>
    </section>
  );
}

function SpanKindIcon({ kind }: { kind: TraceSpan["kind"] }) {
  const iconClass = "h-4 w-4 shrink-0 text-accent";
  if (kind === "agent") return <Bot className={iconClass} />;
  if (kind === "model") return <Brain className={iconClass} />;
  if (kind === "tool") return <Wrench className={iconClass} />;
  if (kind === "aina") return <AppWindow className={iconClass} />;
  return <Activity className={iconClass} />;
}

function buildSpanRows(
  spans: TraceSpan[],
  rootSpanId: string | null | undefined,
): Array<{ span: TraceSpan; depth: number }> {
  const byId = new Map(spans.map((span) => [span.span_id, span]));
  const children = new Map<string, TraceSpan[]>();
  for (const span of spans) {
    if (!span.parent_span_id || !byId.has(span.parent_span_id)) continue;
    const siblings = children.get(span.parent_span_id) ?? [];
    siblings.push(span);
    children.set(span.parent_span_id, siblings);
  }
  const byStartTime = (left: TraceSpan, right: TraceSpan) =>
    left.started_at.localeCompare(right.started_at);
  for (const siblings of children.values()) siblings.sort(byStartTime);

  const roots = spans
    .filter((span) => !span.parent_span_id || !byId.has(span.parent_span_id))
    .sort((left, right) => {
      if (left.span_id === rootSpanId) return -1;
      if (right.span_id === rootSpanId) return 1;
      return byStartTime(left, right);
    });
  const rows: Array<{ span: TraceSpan; depth: number }> = [];
  const visited = new Set<string>();
  const visit = (span: TraceSpan, depth: number) => {
    if (visited.has(span.span_id)) return;
    visited.add(span.span_id);
    rows.push({ span, depth });
    for (const child of children.get(span.span_id) ?? []) visit(child, depth + 1);
  };
  for (const root of roots) visit(root, 0);
  for (const span of [...spans].sort(byStartTime)) visit(span, 0);
  return rows;
}

function EventRow({ event, last }: { event: TraceEvent; last: boolean }) {
  const failed = event.status === "failed";
  const discovery = event.kind === "capability.discovery" ? parseCapabilityDiscovery(event.details) : null;
  const hasDetail = Boolean(event.target_id || discovery || Object.keys(event.details).length);
  const [collapsed, setCollapsed] = useState(true);
  return (
    <div className="grid grid-cols-[20px_1fr] gap-2">
      <div className="flex flex-col items-center">
        <span className={classNames("mt-1 w-2.5 h-2.5 rounded-full border-2", failed ? "border-danger bg-danger-soft" : "border-accent bg-accent-soft")} />
        {!last ? <span className="w-px flex-1 min-h-10 bg-line" /> : null}
      </div>
      <div className="pb-4">
        <button
          type="button"
          onClick={() => hasDetail && setCollapsed((c) => !c)}
          className={classNames("flex items-center gap-2 w-full text-left", hasDetail && "cursor-pointer")}
        >
          {hasDetail ? (
            collapsed ? <ChevronRight className="h-3.5 w-3.5 shrink-0 text-ink-subtle" /> : <ChevronDown className="h-3.5 w-3.5 shrink-0 text-accent" />
          ) : <span className="w-3.5 shrink-0" />}
          <span className="text-[13px] font-bold text-ink">{event.kind}</span>
          <span className={classNames("rounded px-1.5 py-0.5 text-[10.5px] font-bold", failed ? "bg-danger-soft text-danger" : "bg-app-soft text-ink-muted")}>{eventStatusLabel(event.status)}</span>
          {event.duration_ms != null ? <span className="ml-auto text-[11px] text-ink-subtle">{event.duration_ms.toFixed(1)} ms</span> : null}
        </button>
        {!collapsed && hasDetail ? (
          <div className="mt-1.5">
            {event.target_id ? <div className="font-mono text-[11px] text-ink-muted break-all">{event.target_type}:{event.target_id}</div> : null}
            {discovery ? (
              <CapabilityDiscoveryView details={discovery} />
            ) : Object.keys(event.details).length ? (
              <pre className="mt-1.5 rounded-md bg-app-soft p-2 whitespace-pre-wrap break-all text-[11px] leading-relaxed text-ink-muted">{JSON.stringify(event.details, null, 2)}</pre>
            ) : null}
          </div>
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

function spanKindLabel(value: TraceSpan["kind"]): string {
  if (value === "agent") return "Agent";
  if (value === "model") return "Model";
  if (value === "tool") return "Tool";
  if (value === "aina") return "AINA";
  return "Internal";
}

function spanStatusLabel(value: TraceSpan["status"]): string {
  if (value === "completed") return "已完成";
  if (value === "failed") return "失败";
  if (value === "cancelled") return "已取消";
  if (value === "approval_required") return "等待确认";
  return "运行中";
}

function spanStatusTone(value: TraceSpan["status"]): string {
  if (value === "completed") return "bg-success-soft text-success";
  if (value === "failed") return "bg-danger-soft text-danger";
  if (value === "cancelled") return "bg-app-soft text-ink-muted";
  if (value === "approval_required") return "bg-warning-soft text-warning";
  return "bg-accent-soft text-accent";
}
