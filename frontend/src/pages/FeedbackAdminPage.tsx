import { useEffect, useMemo, useState } from "react";
import { CheckCircle2, Clock3, MessageSquareText, Search, ThumbsDown, ThumbsUp, UserRoundCheck } from "lucide-react";
import { Link } from "react-router-dom";
import { BarMeter, LineChart, MetricCard, SectionCard } from "@/components/analytics/DashboardPrimitives";
import { Topbar } from "@/components/layout/Topbar";
import { api, apiErrorMessage } from "@/lib/api";
import { classNames } from "@/lib/utils";
import type {
  FeedbackCaseStatus,
  FeedbackDetailRecord,
  FeedbackMetrics,
  FeedbackRating,
  FeedbackRecord,
  TraceRecord,
} from "@/types";

const RANGE_OPTIONS = [
  { value: 7, label: "最近 7 天" },
  { value: 30, label: "最近 30 天" },
  { value: 90, label: "最近 90 天" },
  { value: 0, label: "自定义日期" },
];

function toDateKey(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function defaultCustomRange(): { start: string; end: string } {
  const end = new Date();
  const start = new Date(end);
  start.setDate(start.getDate() - 29);
  return { start: toDateKey(start), end: toDateKey(end) };
}

export default function FeedbackAdminPage() {
  const [rangeDays, setRangeDays] = useState(30);
  const [customRange, setCustomRange] = useState(defaultCustomRange);
  const [records, setRecords] = useState<FeedbackRecord[]>([]);
  const [metrics, setMetrics] = useState<FeedbackMetrics | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [rating, setRating] = useState<"all" | FeedbackRating>("all");
  const [status, setStatus] = useState<"all" | FeedbackCaseStatus>("all");
  const [userQuery, setUserQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const range = useMemo(() => {
    if (rangeDays === 0) {
      if (!customRange.start || !customRange.end) return null;
      const from = new Date(`${customRange.start}T00:00:00`);
      const to = new Date(`${customRange.end}T23:59:59.999`);
      if (from > to) return null;
      return { from: from.toISOString(), to: to.toISOString() };
    }
    const to = new Date();
    const from = new Date(to);
    from.setDate(from.getDate() - (rangeDays - 1));
    from.setHours(0, 0, 0, 0);
    return { from: from.toISOString(), to: to.toISOString() };
  }, [rangeDays, customRange.end, customRange.start]);

  useEffect(() => {
    if (!range) return;
    const timer = window.setTimeout(() => {
      let active = true;
      setLoading(true);
      setError("");
      const params = new URLSearchParams({ from_at: range.from, to_at: range.to });
      if (rating !== "all") params.set("rating", rating);
      if (userQuery.trim()) params.set("user_query", userQuery.trim());
      const metricsParams = new URLSearchParams({ from_at: range.from, to_at: range.to });
      void Promise.all([
        api.get<FeedbackRecord[]>(`/admin/feedback?${params}`),
        api.get<FeedbackMetrics>(`/admin/feedback/metrics?${metricsParams}`),
      ]).then(([nextRecords, nextMetrics]) => {
        if (!active) return;
        setRecords(nextRecords);
        setMetrics(nextMetrics);
        setSelectedId((current) => nextRecords.some((item) => item.id === current) ? current : (nextRecords[0]?.id ?? ""));
      }).catch((reason) => {
        if (active) setError(apiErrorMessage(reason));
      }).finally(() => {
        if (active) setLoading(false);
      });
      return () => { active = false; };
    }, 250);
    return () => window.clearTimeout(timer);
  }, [range, rating, userQuery]);

  const filtered = useMemo(
    () => records.filter((record) => status === "all" || record.case_status === status),
    [records, status],
  );
  const selected = filtered.find((record) => record.id === selectedId) ?? filtered[0];

  function updateRecord(next: FeedbackRecord) {
    setRecords((current) => current.map((record) => record.id === next.id ? next : record));
  }

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-app-bg">
      <Topbar
        title="用户反馈"
        badge={{ label: "管理员视图", tone: "info" }}
      />
      <div className="min-h-0 flex-1 overflow-y-auto p-4 md:p-5">
        <div className="mx-auto max-w-[1500px] space-y-4">
          <div className="flex flex-wrap items-center gap-2 text-[10.5px] text-ink-subtle">
            <div className="flex rounded-lg bg-app-soft p-0.5" aria-label="反馈时间范围">
              {RANGE_OPTIONS.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => setRangeDays(option.value)}
                  className={classNames(
                    "rounded-md px-2.5 py-1.5 text-[11px] font-bold transition-colors",
                    rangeDays === option.value ? "bg-white text-accent shadow-sm" : "text-ink-muted hover:text-ink",
                  )}
                >
                  {option.label}
                </button>
              ))}
            </div>
            {rangeDays === 0 ? (
              <>
                <input
                  type="date"
                  aria-label="开始日期"
                  value={customRange.start}
                  max={customRange.end}
                  onChange={(event) => setCustomRange((current) => ({ ...current, start: event.target.value }))}
                  className="input h-8 text-[11px]"
                />
                <span>至</span>
                <input
                  type="date"
                  aria-label="结束日期"
                  value={customRange.end}
                  min={customRange.start}
                  onChange={(event) => setCustomRange((current) => ({ ...current, end: event.target.value }))}
                  className="input h-8 text-[11px]"
                />
              </>
            ) : null}
            <span className="ml-auto">{loading ? "正在更新…" : `更新于 ${formatDateTime(new Date().toISOString())}`}</span>
          </div>

          {error ? <div className="rounded-lg bg-danger-soft px-3 py-2 text-[11.5px] text-danger-deep">{error}</div> : null}

          <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" aria-label="反馈核心指标">
            <MetricCard label="反馈率" value={formatPercent(metrics?.feedback_rate)} change={formatPointChange(metrics?.feedback_rate_change)} hint={`${metrics?.feedback_count ?? 0} / ${metrics?.answer_count ?? 0}`} icon={<MessageSquareText />} />
            <MetricCard label="点赞率（Feedback）" value={formatPercent(metrics?.positive_feedback_rate)} change={formatPointChange(metrics?.positive_feedback_rate_change)} hint="点赞 / 有效 Feedback" icon={<ThumbsUp />} tone="green" />
            <MetricCard label="点赞率（回答）" value={formatPercent(metrics?.positive_answer_rate)} change={formatPointChange(metrics?.positive_answer_rate_change)} hint="点赞 / 可反馈回答" icon={<CheckCircle2 />} tone="slate" />
            <MetricCard label="待处理负评" value={String(metrics?.pending_negative_count ?? 0)} change={formatPercentChange(metrics?.pending_negative_change)} hint="待处理与处理中" icon={<ThumbsDown />} tone="red" />
          </section>

          <div className="grid gap-4 xl:grid-cols-[minmax(0,1.65fr)_minmax(300px,0.8fr)]">
            <SectionCard title="反馈与满意度趋势" description="随所选时间范围变化">
              <LineChart
                values={(metrics?.trend ?? []).map((point) => point.feedback_rate)}
                secondaryValues={(metrics?.trend ?? []).map((point) => point.positive_rate)}
                labels={chartLabels(metrics, rangeDays)}
                primaryLabel="反馈率"
                secondaryLabel="点赞率"
              />
            </SectionCard>
            <SectionCard title="点踩原因分布" description="所选时间范围内的负面原因">
              <div className="space-y-3.5 p-4">
                {(metrics?.reasons ?? []).map((item, index) => (
                  <BarMeter key={item.reason} label={item.reason} value={item.percentage} displayValue={`${item.count} · ${item.percentage}%`} tone={index === 0 ? "red" : index < 3 ? "amber" : "blue"} />
                ))}
                {!metrics?.reasons.length ? <EmptyState text="当前范围内暂无点踩反馈" /> : null}
              </div>
            </SectionCard>
          </div>

          <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1.65fr)_minmax(360px,0.85fr)]">
            <SectionCard
              title="Feedback 管理"
              description={`${filtered.length} 条真实反馈`}
              actions={(
                <div className="flex flex-wrap items-center gap-2">
                  <label className="flex h-8 items-center gap-2 rounded-lg border border-line bg-app-soft px-2.5">
                    <Search className="h-3.5 w-3.5 text-ink-subtle" />
                    <input aria-label="按用户名筛选" value={userQuery} onChange={(event) => setUserQuery(event.target.value)} placeholder="用户名或邮箱" className="w-36 bg-transparent text-[11px] outline-none" />
                  </label>
                  <select aria-label="评价筛选" value={rating} onChange={(event) => setRating(event.target.value as typeof rating)} className="h-8 rounded-lg border border-line bg-white px-2 text-[11px]"><option value="all">全部评价</option><option value="up">点赞</option><option value="down">点踩</option></select>
                  <select aria-label="状态筛选" value={status} onChange={(event) => setStatus(event.target.value as typeof status)} className="h-8 rounded-lg border border-line bg-white px-2 text-[11px]"><option value="all">全部状态</option><option value="pending">待处理</option><option value="in_progress">处理中</option><option value="resolved">已解决</option><option value="closed">已关闭</option></select>
                </div>
              )}
            >
              <div className="overflow-x-auto">
                <table className="w-full min-w-[820px] text-left text-[11.5px]">
                  <thead className="bg-app-soft text-ink-muted"><tr><th className="px-4 py-2.5 font-semibold">评价</th><th className="px-3 py-2.5 font-semibold">用户 / 内容</th><th className="px-3 py-2.5 font-semibold">Agent</th><th className="px-3 py-2.5 font-semibold">原因</th><th className="px-3 py-2.5 font-semibold">处理人</th><th className="px-3 py-2.5 font-semibold">Case</th><th className="px-4 py-2.5 font-semibold">时间</th></tr></thead>
                  <tbody className="divide-y divide-line">
                    {filtered.map((record) => (
                      <tr key={record.id} className={classNames("cursor-pointer hover:bg-app-soft/70", selected?.id === record.id && "bg-accent-soft/60")} onClick={() => setSelectedId(record.id)}>
                        <td className="px-4 py-3">{record.rating === "up" ? <ThumbsUp className="h-4 w-4 text-success" /> : <ThumbsDown className="h-4 w-4 text-danger" />}</td>
                        <td className="max-w-[300px] px-3 py-3"><div className="font-semibold text-ink">{record.user_name}</div><div className="text-[10px] text-ink-subtle">{record.user_email}</div><p className="mt-0.5 max-w-full truncate text-[10.5px] text-ink-muted">{record.comment || "未填写补充说明"}</p></td>
                        <td className="px-3 py-3"><div className="font-semibold text-ink">{record.agent_name}</div><div className="text-[10.5px] text-ink-subtle">{record.agent_version || "—"}</div></td>
                        <td className="px-3 py-3 text-ink-muted">{record.reason || "—"}</td>
                        <td className="px-3 py-3 font-semibold text-ink">{record.assignee || <span className="text-ink-subtle">未分配</span>}</td>
                        <td className="px-3 py-3"><StatusPill status={record.case_status} /></td>
                        <td className="px-4 py-3 text-ink-muted">{formatDateTime(record.created_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {!loading && !filtered.length ? <EmptyState text="没有符合筛选条件的反馈" /> : null}
              </div>
            </SectionCard>

            {selected ? <FeedbackDetail record={selected} onUpdate={updateRecord} /> : null}
          </div>
        </div>
      </div>
    </div>
  );
}

function FeedbackDetail({ record, onUpdate }: { record: FeedbackRecord; onUpdate: (record: FeedbackRecord) => void }) {
  const [detail, setDetail] = useState<FeedbackDetailRecord | null>(null);
  const [status, setStatus] = useState(record.case_status);
  const [assignee, setAssignee] = useState(record.assignee);
  const [conclusion, setConclusion] = useState(record.conclusion);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    setDetail(null);
    setError("");
    setStatus(record.case_status);
    setAssignee(record.assignee);
    setConclusion(record.conclusion);
    void api.get<FeedbackDetailRecord>(`/admin/feedback/${encodeURIComponent(record.id)}`)
      .then((next) => { if (active) setDetail(next); })
      .catch((reason) => { if (active) setError(apiErrorMessage(reason)); });
    return () => { active = false; };
  }, [record.id, record.assignee, record.case_status, record.conclusion]);

  async function save() {
    setSaving(true);
    setError("");
    try {
      const updated = await api.patch<FeedbackRecord>(`/admin/feedback/${encodeURIComponent(record.id)}/case`, { status, assignee, conclusion });
      onUpdate(updated);
      setConclusion("");
    } catch (reason) {
      setError(apiErrorMessage(reason));
    } finally {
      setSaving(false);
    }
  }

  const current = detail?.feedback ?? record;
  return (
    <SectionCard title="Feedback 详情" description={`反馈于 ${formatDateTime(current.created_at)}`} className="xl:sticky xl:top-0">
      <div className="max-h-[calc(100vh-180px)] space-y-4 overflow-y-auto p-4">
        {error ? <p className="rounded-lg bg-danger-soft px-3 py-2 text-[11px] text-danger-deep">{error}</p> : null}
        <div className="rounded-lg bg-app-soft p-3">
          <div className="flex items-center gap-2"><span className={classNames("flex h-7 w-7 items-center justify-center rounded-lg", current.rating === "up" ? "bg-success-soft text-success" : "bg-danger-soft text-danger")}>{current.rating === "up" ? <ThumbsUp className="h-3.5 w-3.5" /> : <ThumbsDown className="h-3.5 w-3.5" />}</span><div><strong className="text-[12px] text-ink">{current.user_name}</strong><p className="text-[10.5px] text-ink-subtle">{current.user_email}</p></div><StatusPill status={current.case_status} /></div>
          <p className="mt-3 text-[12px] leading-5 text-ink">{current.comment || "用户未填写补充说明"}</p>
          <p className="mt-2 text-[10.5px] text-ink-muted">原因：{current.reason || "—"}</p>
        </div>

        <div>
          <div className="flex items-center gap-2"><h3 className="text-[11.5px] font-bold text-ink">反馈时上下文</h3><span className="text-[10px] text-ink-subtle">仅展示反馈前的 Trace</span></div>
          <div className="mt-2 space-y-2">
            {detail?.context_traces.map((trace, index) => <TraceContext key={trace.trace_id} trace={trace} index={index} conversationId={current.conversation_id} />)}
            {detail && !detail.context_traces.length ? <EmptyState text="反馈发生前没有可用 Trace" /> : null}
            {!detail ? <EmptyState text="正在读取上下文…" /> : null}
          </div>
        </div>

        {current.rating === "down" ? (
          <div className="space-y-3 border-t border-line pt-4">
            <div className="grid grid-cols-2 gap-2">
              <label className="text-[10.5px] font-semibold text-ink-muted">状态<select aria-label="Case 状态" value={status} onChange={(event) => setStatus(event.target.value as FeedbackCaseStatus)} className="mt-1 h-9 w-full rounded-lg border border-line bg-white px-2 text-[11.5px] text-ink"><option value="pending">待处理</option><option value="in_progress">处理中</option><option value="resolved">已解决</option><option value="closed">已关闭</option></select></label>
              <label className="text-[10.5px] font-semibold text-ink-muted">负责人<input aria-label="Case 负责人" value={assignee} onChange={(event) => setAssignee(event.target.value.slice(0, 80))} placeholder="未分配" className="mt-1 h-9 w-full rounded-lg border border-line bg-white px-2 text-[11.5px] text-ink" /></label>
            </div>
            <label className="block text-[10.5px] font-semibold text-ink-muted">处理结论<textarea aria-label="处理结论" value={conclusion} onChange={(event) => setConclusion(event.target.value.slice(0, 1000))} rows={3} placeholder="记录原因、修复版本或后续动作" className="mt-1 w-full resize-none rounded-lg border border-line px-2.5 py-2 text-[11.5px] font-normal text-ink outline-none focus:border-accent" /></label>
            <button type="button" disabled={saving} onClick={() => void save()} className="btn-primary w-full"><UserRoundCheck className="h-4 w-4" />{saving ? "保存中…" : "保存处理结果"}</button>
          </div>
        ) : null}

        <div className="border-t border-line pt-4"><h3 className="text-[11.5px] font-bold text-ink">操作历史</h3><div className="mt-3 space-y-3">{current.history.map((item, index) => <div key={`${item.at}-${index}`} className="flex gap-2.5"><span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-app-soft text-ink-subtle"><Clock3 className="h-3 w-3" /></span><div><p className="text-[10.5px] leading-4 text-ink">{item.action}</p><p className="mt-0.5 text-[9.5px] text-ink-subtle">{item.actor_name} · {formatDateTime(item.at)}</p></div></div>)}</div></div>
      </div>
    </SectionCard>
  );
}

function TraceContext({ trace, index, conversationId }: { trace: TraceRecord; index: number; conversationId: string }) {
  const root = trace.spans.find((span) => span.span_id === trace.root_span_id) ?? trace.spans[0];
  const prompt = extractText(root?.input) || extractEventText(trace, "user");
  const response = extractText(root?.output) || extractEventText(trace, "final");
  const duration = root?.duration_ms ?? (trace.completed_at ? new Date(trace.completed_at).getTime() - new Date(trace.created_at).getTime() : null);
  return (
    <details className="rounded-lg bg-app-soft px-3 py-2.5" open={index === 0}>
      <summary className="cursor-pointer list-none">
        <div className="flex items-center gap-2"><strong className="text-[11px] text-ink">第 {index + 1} 轮</strong><span className="text-[10px] text-ink-subtle">{formatDateTime(trace.created_at)}</span><span className="ml-auto text-[10px] text-ink-muted">{trace.spans.length} Span · {duration === null ? "—" : formatDuration(duration)}</span></div>
        <p className="mt-1 truncate text-[10.5px] text-ink-muted">{prompt || "未采集到用户输入"}</p>
      </summary>
      <div className="mt-2 space-y-2 border-t border-line pt-2 text-[10.5px]">
        <ContextLine label="用户输入" text={prompt || "未采集"} />
        <ContextLine label="最终回复" text={response || "未采集"} />
        <Link to={`/admin/observability?sessionId=${encodeURIComponent(conversationId)}&traceId=${encodeURIComponent(trace.trace_id)}&tab=spans`} className="inline-flex font-semibold text-accent hover:text-accent-hover">查看完整 Trace</Link>
      </div>
    </details>
  );
}

function ContextLine({ label, text }: { label: string; text: string }) {
  return <div><span className="font-semibold text-ink">{label}</span><p className="mt-0.5 max-h-20 overflow-y-auto whitespace-pre-wrap break-words leading-4 text-ink-muted">{text}</p></div>;
}

function extractText(value: unknown): string {
  if (typeof value === "string") return value;
  if (!value || typeof value !== "object") return "";
  if (Array.isArray(value)) return value.map(extractText).filter(Boolean).join("\n");
  const record = value as Record<string, unknown>;
  for (const key of ["message", "prompt", "content", "text", "response", "output", "result"]) {
    const text = extractText(record[key]);
    if (text) return text;
  }
  return "";
}

function extractEventText(trace: TraceRecord, kind: string): string {
  const event = trace.events.find((item) => item.kind.toLowerCase().includes(kind));
  return extractText(event?.details);
}

function StatusPill({ status }: { status: FeedbackCaseStatus }) {
  const labels: Record<FeedbackCaseStatus, string> = { pending: "待处理", in_progress: "处理中", resolved: "已解决", closed: "已关闭" };
  return <span className={classNames("ml-auto inline-flex rounded-full px-2 py-0.5 text-[9.5px] font-bold", status === "pending" ? "bg-danger-soft text-danger" : status === "in_progress" ? "bg-warning-soft text-warning-deep" : status === "resolved" ? "bg-success-soft text-success-deep" : "bg-app-soft text-ink-muted")}>{labels[status]}</span>;
}

function EmptyState({ text }: { text: string }) {
  return <p className="px-4 py-6 text-center text-[11px] text-ink-subtle">{text}</p>;
}

function formatPercent(value?: number) {
  return `${(value ?? 0).toFixed(1)}%`;
}

function formatPointChange(value?: number) {
  const resolved = value ?? 0;
  return `${resolved >= 0 ? "+" : ""}${resolved.toFixed(1)}pp`;
}

function formatPercentChange(value?: number) {
  const resolved = value ?? 0;
  return `${resolved >= 0 ? "+" : ""}${resolved.toFixed(1)}%`;
}

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

function formatDuration(ms: number) {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`;
}

function chartLabels(metrics: FeedbackMetrics | null, rangeDays: number) {
  const trend = metrics?.trend ?? [];
  const step = rangeDays <= 7 ? 1 : Math.ceil(trend.length / 10);
  return trend.map((point, index) => index % step === 0 || index === trend.length - 1 ? point.date.slice(5) : "");
}
