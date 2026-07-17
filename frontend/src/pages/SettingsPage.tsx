import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  AppWindow,
  Bot,
  Brain,
  Bug,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock3,
  Code2,
  Database,
  MessageSquareText,
  RefreshCw,
  Route,
  ShieldCheck,
  Wrench,
  XCircle,
} from "lucide-react";
import { useSearchParams } from "react-router-dom";
import { Topbar } from "@/components/layout/Topbar";
import { api, apiErrorMessage } from "@/lib/api";
import { classNames, timeAgo } from "@/lib/utils";
import { useDebugMode } from "@/lib/debugMode";
import type { AdminSummary, ConversationRecord, TraceEvent, TraceRecord } from "@/types";

export default function SettingsPage() {
  const { debugMode, setDebugMode } = useDebugMode();
  const [searchParams] = useSearchParams();
  const requestedTrace = searchParams.get("trace");
  const [health, setHealth] = useState<"checking" | "ok" | "error">("checking");
  const [summary, setSummary] = useState<AdminSummary | null>(null);
  const [traces, setTraces] = useState<TraceRecord[]>([]);
  const [conversations, setConversations] = useState<ConversationRecord[]>([]);
  const [selectedTrace, setSelectedTrace] = useState<string | null>(requestedTrace);
  const [expandedTraceGroups, setExpandedTraceGroups] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      const [healthData, summaryData, traceData, conversationData] = await Promise.all([
        api.get<{ status: string }>("/health"),
        api.get<AdminSummary>("/admin/summary"),
        api.get<TraceRecord[]>("/traces?user_id=anonymous&tenant_id=default"),
        api.get<ConversationRecord[]>("/conversations?user_id=anonymous&tenant_id=default"),
      ]);
      setHealth(healthData.status === "ok" ? "ok" : "error");
      setSummary(summaryData);
      setTraces(traceData);
      setConversations(conversationData);
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
  const conversationsById = useMemo(
    () => new Map(conversations.map((conversation) => [conversation.id, conversation])),
    [conversations],
  );
  const traceGroups = useMemo(
    () => groupTracesByConversation(traces, conversationsById),
    [conversationsById, traces],
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

  const selectedConversationTitle = selected?.conversation_id
    ? conversationsById.get(selected.conversation_id)?.title ?? "已删除或不可用的会话"
    : "未关联会话";

  return (
    <div className="h-full flex flex-col bg-app-bg">
      <Topbar
        title="运行中心"
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
              className={classNames("h-8 rounded-lg border px-3 text-[11.5px] font-bold", debugMode ? "border-accent bg-accent-soft text-accent" : "border-line bg-white text-ink-muted")}
            >
              <Bug className="mr-1.5 inline h-3.5 w-3.5" />调试模式{debugMode ? "已开启" : "已关闭"}
            </button>
            <button type="button" onClick={() => void load()} disabled={refreshing} className="btn-outline h-8">
              <RefreshCw className={classNames("w-3.5 h-3.5", refreshing && "animate-spin")} />刷新
            </button>
          </div>
        }
      />
      <div className="flex-1 min-h-0 overflow-y-auto p-2 md:p-4">
        <div className="mx-auto max-w-6xl space-y-4">
          {error ? (
            <div className="rounded-lg border border-danger-ring bg-danger-soft p-3 text-[12.5px] text-danger-deep">
              {error}
            </div>
          ) : null}

          <section className="grid grid-cols-2 gap-2 sm:grid-cols-3 md:gap-3 xl:grid-cols-7" aria-label="运行统计">
            <SummaryCard icon={<Bot />} label="对话" value={summary?.conversations} tone="blue" />
            <SummaryCard icon={<Wrench />} label="工具" value={summary?.tools} tone="indigo" />
            <SummaryCard icon={<Code2 />} label="技能" value={summary?.skills} tone="slate" />
            <SummaryCard icon={<AppWindow />} label="AINA" value={summary?.ainas} tone="green" />
            <SummaryCard icon={<Database />} label="安装" value={summary?.installations} tone="amber" />
            <SummaryCard icon={<Brain />} label="记忆" value={summary?.memories} tone="indigo" />
            {debugMode ? <SummaryCard icon={<Route />} label="调用记录" value={summary?.traces} tone="blue" /> : null}
          </section>

          {debugMode ? <section className="grid grid-cols-1 gap-3 min-h-[520px] xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)] xl:gap-4">
            <div className="rounded-xl border border-line bg-white shadow-card overflow-hidden">
              <div className="h-13 px-4 py-3 border-b border-line flex items-center gap-2">
                <Activity className="w-4 h-4 text-accent" />
                <h2 className="text-[13px] font-extrabold text-ink">调用记录</h2>
                <span className="ml-auto text-[10.5px] text-ink-muted">{traceGroups.length} 个会话 · {traces.length} 条 Trace</span>
              </div>
              <div className="max-h-[280px] xl:max-h-[620px] overflow-y-auto">
                {traces.length === 0 ? (
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
                )}
              </div>
            </div>

            <div className="rounded-xl border border-line bg-white shadow-card overflow-hidden">
              {selected ? <TraceDetail trace={selected} conversationTitle={selectedConversationTitle} /> : <NoTraceSelected />}
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
      <div className="text-[10.5px] font-semibold text-ink-muted">{label}</div>
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
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={expanded}
        className={classNames(
          "flex w-full items-start gap-2.5 px-4 py-3 text-left transition-colors hover:bg-app-soft",
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
            <strong className="min-w-0 flex-1 truncate text-[11.5px] text-ink">{group.title}</strong>
            <span className="shrink-0 rounded bg-white px-1.5 py-0.5 text-[9px] font-bold text-ink-muted shadow-sm">
              {group.traces.length} Trace
            </span>
          </span>
          <span className="mt-1 block truncate font-mono text-[9.5px] text-ink-subtle">
            {group.conversationId ?? "无 Conversation ID"}
          </span>
        </span>
        {expanded ? <ChevronDown className="mt-1 h-3.5 w-3.5 shrink-0 text-accent" /> : <ChevronRight className="mt-1 h-3.5 w-3.5 shrink-0 text-ink-subtle" />}
      </button>
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
    <button
      type="button"
      onClick={onClick}
      className={classNames(
        "w-full border-l-2 px-4 py-2.5 pl-[50px] text-left transition-colors",
        active ? "border-accent bg-accent-soft" : "border-transparent hover:bg-app-soft",
      )}
    >
      <div className="flex items-center gap-2">
        <TraceStatus status={trace.status} />
        <span className="min-w-0 flex-1 truncate font-mono text-[10.5px] text-ink">{trace.trace_id}</span>
      </div>
      <div className="mt-1.5 flex items-center gap-2 text-[10px] text-ink-muted">
        <span>{trace.events.length} 个事件</span>
        <span>·</span>
        <span>{timeAgo(trace.created_at)}</span>
      </div>
    </button>
  );
}

function TraceDetail({ trace, conversationTitle }: { trace: TraceRecord; conversationTitle: string }) {
  return (
    <div className="h-full flex flex-col">
      <div className="px-4 py-3 border-b border-line bg-app-soft">
        <div className="flex items-center gap-2">
          <TraceStatus status={trace.status} />
          <h2 className="font-mono text-[11px] font-bold text-ink break-all">{trace.trace_id}</h2>
        </div>
        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[10.5px] text-ink-muted">
          <span>会话：{conversationTitle}</span>
          <span className="font-mono">{trace.conversation_id ?? "—"}</span>
          <span>主体：{trace.tenant_id}/{trace.user_id}</span>
          <span>开始：{new Date(trace.created_at).toLocaleString("zh-CN")}</span>
        </div>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto p-4">
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
          <span className="text-[11.5px] font-bold text-ink">{event.kind}</span>
          <span className={classNames("rounded px-1.5 py-0.5 text-[9px] font-bold", failed ? "bg-danger-soft text-danger" : "bg-app-soft text-ink-muted")}>{eventStatusLabel(event.status)}</span>
          {event.duration_ms != null ? <span className="ml-auto text-[9.5px] text-ink-subtle">{event.duration_ms.toFixed(1)} ms</span> : null}
        </div>
        {event.target_id ? <div className="mt-1 font-mono text-[9.5px] text-ink-muted break-all">{event.target_type}:{event.target_id}</div> : null}
        {discovery ? (
          <CapabilityDiscoveryView details={discovery} />
        ) : Object.keys(event.details).length ? (
          <pre className="mt-1.5 rounded-md bg-app-soft p-2 whitespace-pre-wrap break-all text-[9.5px] leading-relaxed text-ink-muted">{JSON.stringify(event.details, null, 2)}</pre>
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
    <div className="mt-2 space-y-2 text-[10px] text-ink-muted">
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
                  <span className="font-mono text-[9px] text-ink-subtle">{aina.id}</span>
                </div>
                <div className="mt-0.5 flex flex-wrap gap-x-2 text-[9px] text-ink-subtle">
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
        <pre className="mt-1.5 rounded-md bg-app-soft p-2 whitespace-pre-wrap break-all text-[9.5px] leading-relaxed text-ink-muted">{JSON.stringify(details, null, 2)}</pre>
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
            {item.model_exposed ? <span className="shrink-0 rounded bg-success-soft px-1 py-0.5 text-[8.5px] font-bold text-success-deep">模型可见</span> : null}
            {item.requires_confirmation ? <span className="shrink-0 rounded bg-warning-soft px-1 py-0.5 text-[8.5px] font-bold text-warning-deep">需确认</span> : null}
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
    <span className={classNames("inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[9.5px] font-bold", success ? "bg-success-soft text-success-deep" : failed ? "bg-danger-soft text-danger" : "bg-warning-soft text-warning-deep")}>
      {success ? <CheckCircle2 className="w-3 h-3" /> : failed ? <XCircle className="w-3 h-3" /> : <Clock3 className="w-3 h-3" />}
      {traceStatusLabel(status)}
    </span>
  );
}

function NoTraceSelected() {
  return (
    <div className="h-full min-h-[520px] flex items-center justify-center text-center">
      <div>
        <ShieldCheck className="mx-auto w-10 h-10 text-ink-subtle" />
        <h2 className="mt-3 text-[14px] font-bold text-ink">选择一条调用记录</h2>
        <p className="mt-1 text-[11.5px] text-ink-muted">查看模型、工具、AINA、授权和最终响应的完整链路。</p>
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
