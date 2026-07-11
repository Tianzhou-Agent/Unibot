import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  AppWindow,
  Bot,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock3,
  Code2,
  Database,
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
import type { AdminSummary, TraceEvent, TraceRecord } from "@/types";

export default function SettingsPage() {
  const [searchParams] = useSearchParams();
  const requestedTrace = searchParams.get("trace");
  const [health, setHealth] = useState<"checking" | "ok" | "error">("checking");
  const [summary, setSummary] = useState<AdminSummary | null>(null);
  const [traces, setTraces] = useState<TraceRecord[]>([]);
  const [selectedTrace, setSelectedTrace] = useState<string | null>(requestedTrace);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      const [healthData, summaryData, traceData] = await Promise.all([
        api.get<{ status: string }>("/health"),
        api.get<AdminSummary>("/admin/summary"),
        api.get<TraceRecord[]>("/traces?user_id=anonymous&tenant_id=default"),
      ]);
      setHealth(healthData.status === "ok" ? "ok" : "error");
      setSummary(summaryData);
      setTraces(traceData);
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

  return (
    <div className="h-full flex flex-col bg-app-bg">
      <Topbar
        title="运行中心"
        badge={{
          label: health === "ok" ? "后端在线" : health === "checking" ? "检查中" : "后端异常",
          tone: health === "ok" ? "success" : health === "checking" ? "thinking" : "warning",
        }}
        actions={
          <button type="button" onClick={() => void load()} disabled={refreshing} className="btn-outline h-8">
            <RefreshCw className={classNames("w-3.5 h-3.5", refreshing && "animate-spin")} />刷新
          </button>
        }
      />
      <div className="flex-1 min-h-0 overflow-y-auto p-4">
        <div className="mx-auto max-w-6xl space-y-4">
          {error ? (
            <div className="rounded-lg border border-danger-ring bg-danger-soft p-3 text-[12.5px] text-danger-deep">
              {error}
            </div>
          ) : null}

          <section className="grid grid-cols-6 gap-3" aria-label="运行统计">
            <SummaryCard icon={<Bot />} label="对话" value={summary?.conversations} tone="blue" />
            <SummaryCard icon={<Wrench />} label="Tools" value={summary?.tools} tone="indigo" />
            <SummaryCard icon={<Code2 />} label="Skills" value={summary?.skills} tone="slate" />
            <SummaryCard icon={<AppWindow />} label="AINA" value={summary?.ainas} tone="green" />
            <SummaryCard icon={<Database />} label="安装" value={summary?.installations} tone="amber" />
            <SummaryCard icon={<Route />} label="Traces" value={summary?.traces} tone="blue" />
          </section>

          <section className="grid grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)] gap-4 min-h-[520px]">
            <div className="rounded-xl border border-line bg-white shadow-card overflow-hidden">
              <div className="h-13 px-4 py-3 border-b border-line flex items-center gap-2">
                <Activity className="w-4 h-4 text-accent" />
                <h2 className="text-[13px] font-extrabold text-ink">调用 Trace</h2>
                <span className="ml-auto text-[10.5px] text-ink-muted">最近 {traces.length} 条</span>
              </div>
              <div className="max-h-[620px] overflow-y-auto divide-y divide-line">
                {traces.length === 0 ? (
                  <div className="py-20 text-center text-[12px] text-ink-muted">完成一次对话后，Trace 会显示在这里。</div>
                ) : (
                  traces.map((trace) => (
                    <TraceRow
                      key={trace.trace_id}
                      trace={trace}
                      active={trace.trace_id === selectedTrace}
                      onClick={() => setSelectedTrace(trace.trace_id)}
                    />
                  ))
                )}
              </div>
            </div>

            <div className="rounded-xl border border-line bg-white shadow-card overflow-hidden">
              {selected ? <TraceDetail trace={selected} /> : <NoTraceSelected />}
            </div>
          </section>
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

function TraceRow({ trace, active, onClick }: { trace: TraceRecord; active: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={classNames("w-full px-4 py-3 text-left transition-colors", active ? "bg-accent-soft" : "hover:bg-app-soft")}
    >
      <div className="flex items-center gap-2">
        <TraceStatus status={trace.status} />
        <span className="min-w-0 flex-1 truncate font-mono text-[10.5px] text-ink">{trace.trace_id}</span>
        {active ? <ChevronDown className="w-3.5 h-3.5 text-accent" /> : <ChevronRight className="w-3.5 h-3.5 text-ink-subtle" />}
      </div>
      <div className="mt-1.5 flex items-center gap-2 text-[10px] text-ink-muted">
        <span>{trace.events.length} events</span>
        <span>·</span>
        <span>{timeAgo(trace.created_at)}</span>
      </div>
    </button>
  );
}

function TraceDetail({ trace }: { trace: TraceRecord }) {
  return (
    <div className="h-full flex flex-col">
      <div className="px-4 py-3 border-b border-line bg-app-soft">
        <div className="flex items-center gap-2">
          <TraceStatus status={trace.status} />
          <h2 className="font-mono text-[11px] font-bold text-ink break-all">{trace.trace_id}</h2>
        </div>
        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[10.5px] text-ink-muted">
          <span>会话：{trace.conversation_id ?? "—"}</span>
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
  return (
    <div className="grid grid-cols-[20px_1fr] gap-2">
      <div className="flex flex-col items-center">
        <span className={classNames("mt-1 w-2.5 h-2.5 rounded-full border-2", failed ? "border-danger bg-danger-soft" : "border-accent bg-accent-soft")} />
        {!last ? <span className="w-px flex-1 min-h-10 bg-line" /> : null}
      </div>
      <div className="pb-4">
        <div className="flex items-center gap-2">
          <span className="text-[11.5px] font-bold text-ink">{event.kind}</span>
          <span className={classNames("rounded px-1.5 py-0.5 text-[9px] font-bold", failed ? "bg-danger-soft text-danger" : "bg-app-soft text-ink-muted")}>{event.status}</span>
          {event.duration_ms != null ? <span className="ml-auto text-[9.5px] text-ink-subtle">{event.duration_ms.toFixed(1)} ms</span> : null}
        </div>
        {event.target_id ? <div className="mt-1 font-mono text-[9.5px] text-ink-muted break-all">{event.target_type}:{event.target_id}</div> : null}
        {Object.keys(event.details).length ? (
          <pre className="mt-1.5 rounded-md bg-app-soft p-2 whitespace-pre-wrap break-all text-[9.5px] leading-relaxed text-ink-muted">{JSON.stringify(event.details, null, 2)}</pre>
        ) : null}
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
      {status}
    </span>
  );
}

function NoTraceSelected() {
  return (
    <div className="h-full min-h-[520px] flex items-center justify-center text-center">
      <div>
        <ShieldCheck className="mx-auto w-10 h-10 text-ink-subtle" />
        <h2 className="mt-3 text-[14px] font-bold text-ink">选择一条 Trace</h2>
        <p className="mt-1 text-[11.5px] text-ink-muted">查看模型、Tool、AINA、授权和最终响应的完整链路。</p>
      </div>
    </div>
  );
}
