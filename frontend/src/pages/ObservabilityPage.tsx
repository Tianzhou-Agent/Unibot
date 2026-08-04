import { useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  Bot,
  Brain,
  CheckCircle2,
  Clock3,
  RefreshCw,
  Route,
  Search,
  Wrench,
  XCircle,
} from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { BarMeter, LineChart, MetricCard, MockDataBadge, SectionCard } from "@/components/analytics/DashboardPrimitives";
import { Topbar } from "@/components/layout/Topbar";
import { classNames } from "@/lib/utils";
import { ERROR_SUMMARIES, METRIC_CONTEXT, PERFORMANCE, TRACE_SUMMARIES, TREND_POINTS } from "@/mocks/observability";

type PerformanceTab = "agent" | "model" | "tool";

export default function ObservabilityPage() {
  const [range, setRange] = useState("7d");
  const [performanceTab, setPerformanceTab] = useState<PerformanceTab>("agent");
  const [query, setQuery] = useState("");
  const [searchParams, setSearchParams] = useSearchParams();
  const focusedTraceId = searchParams.get("trace");
  const focusedTrace = TRACE_SUMMARIES.find((trace) => trace.id === focusedTraceId) ?? null;

  const traces = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return TRACE_SUMMARIES;
    return TRACE_SUMMARIES.filter((trace) => `${trace.id} ${trace.request} ${trace.agent}`.toLowerCase().includes(normalized));
  }, [query]);

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-app-bg">
      <Topbar
        title="可观测与质量"
        badge={{ label: "管理员视图", tone: "info" }}
        actions={
          <div className="flex items-center gap-2">
            <MockDataBadge />
            <Link to="/obs" className="btn-outline h-8"><Route className="h-3.5 w-3.5" />实时调用链</Link>
          </div>
        }
      />

      <div className="min-h-0 flex-1 overflow-y-auto p-4 md:p-5">
        <div className="mx-auto max-w-[1500px] space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <div className="flex rounded-lg border border-line bg-white p-0.5">
              {[{ id: "24h", label: "24 小时" }, { id: "7d", label: "7 天" }, { id: "30d", label: "30 天" }].map((item) => (
                <button key={item.id} type="button" onClick={() => setRange(item.id)} className={classNames("rounded-md px-3 py-1.5 text-[11.5px] font-bold", range === item.id ? "bg-accent text-white" : "text-ink-muted hover:bg-app-soft")}>{item.label}</button>
              ))}
            </div>
            <select aria-label="Agent 筛选" className="h-8 rounded-lg border border-line bg-white px-2.5 text-[11.5px] text-ink">
              <option>全部 Agent</option><option>数据分析助手</option><option>文档助手</option><option>客服助手</option>
            </select>
            <select aria-label="版本筛选" className="h-8 rounded-lg border border-line bg-white px-2.5 text-[11.5px] text-ink">
              <option>全部版本</option><option>当前版本</option><option>上一版本</option>
            </select>
            <span className="ml-auto text-[10.5px] text-ink-subtle">{METRIC_CONTEXT.version} · {METRIC_CONTEXT.timezone} · 更新于 {METRIC_CONTEXT.asOf}</span>
            <button type="button" className="btn-outline h-8" aria-label="刷新观测数据"><RefreshCw className="h-3.5 w-3.5" />刷新</button>
          </div>

          <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" aria-label="可观测核心指标">
            <MetricCard label="请求总量" value="12,842" change="+12.6%" hint="有效用户请求" icon={<Activity />} />
            <MetricCard label="成功率" value="96.8%" change="+1.4%" hint="失败 411 次" icon={<CheckCircle2 />} tone="green" />
            <MetricCard label="P95 总耗时" value="5.8 s" change="-8.2%" hint="P50 2.1 s · P99 9.3 s" icon={<Clock3 />} tone="amber" />
            <MetricCard label="平均 Token" value="3.8k" change="+3.1%" hint="输入 2.5k · 输出 1.3k" icon={<Brain />} tone="slate" />
          </section>

          {focusedTrace ? <TraceFocus trace={focusedTrace} onClose={() => setSearchParams({})} /> : null}

          <div className="grid gap-4 xl:grid-cols-[minmax(0,1.65fr)_minmax(300px,0.8fr)]">
            <SectionCard title="请求与错误趋势" description={`${METRIC_CONTEXT.window}，聚合延迟小于 5 分钟`}>
              <LineChart values={TREND_POINTS.map((point) => point.requests)} secondaryValues={TREND_POINTS.map((point) => point.errors * 20)} labels={TREND_POINTS.map((point) => point.label)} primaryLabel="请求数" secondaryLabel="错误数（×20）" />
            </SectionCard>
            <SectionCard title="错误来源" description="按错误事件去重后的来源分布">
              <div className="space-y-4 p-4">
                <BarMeter label="Tool / AINA" value={42} tone="red" />
                <BarMeter label="Model Provider" value={31} tone="amber" />
                <BarMeter label="Agent 编排" value={18} />
                <BarMeter label="平台与存储" value={9} tone="green" />
                <div className="rounded-lg bg-danger-soft p-3 text-[11.5px] leading-5 text-danger-deep">
                  <strong>需关注：</strong> contract-parser 的 P95 耗时在当前版本上升 28%。
                </div>
              </div>
            </SectionCard>
          </div>

          <SectionCard
            title="调用链运行中心"
            description="Mock Trace 摘要；点击后在本页定位模拟 Span"
            actions={
              <label className="flex h-8 items-center gap-2 rounded-lg border border-line bg-app-soft px-2.5">
                <Search className="h-3.5 w-3.5 text-ink-subtle" />
                <input aria-label="搜索 Trace" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Trace ID、请求或 Agent" className="w-44 bg-transparent text-[11.5px] outline-none placeholder:text-ink-subtle" />
              </label>
            }
          >
            <div className="overflow-x-auto">
              <table className="w-full min-w-[850px] text-left text-[11.5px]">
                <thead className="bg-app-soft text-ink-muted"><tr><th className="px-4 py-2.5 font-semibold">Trace</th><th className="px-3 py-2.5 font-semibold">请求</th><th className="px-3 py-2.5 font-semibold">Agent / 版本</th><th className="px-3 py-2.5 font-semibold">状态</th><th className="px-3 py-2.5 font-semibold">耗时</th><th className="px-3 py-2.5 font-semibold">Span</th><th className="px-3 py-2.5 font-semibold">Token</th><th className="px-4 py-2.5 font-semibold">时间</th></tr></thead>
                <tbody className="divide-y divide-line">
                  {traces.map((trace) => (
                    <tr key={trace.id} className="hover:bg-app-soft/70">
                      <td className="px-4 py-3"><Link to={`?trace=${trace.id}`} className="font-mono text-[10.5px] font-semibold text-accent hover:underline">{trace.id}</Link></td>
                      <td className="max-w-[300px] truncate px-3 py-3 text-ink">{trace.request}</td>
                      <td className="px-3 py-3"><div className="font-semibold text-ink">{trace.agent}</div><div className="text-[10.5px] text-ink-subtle">{trace.version}</div></td>
                      <td className="px-3 py-3">{trace.status === "completed" ? <span className="inline-flex items-center gap-1 font-semibold text-success"><CheckCircle2 className="h-3.5 w-3.5" />成功</span> : <span className="inline-flex items-center gap-1 font-semibold text-danger"><XCircle className="h-3.5 w-3.5" />失败</span>}</td>
                      <td className="px-3 py-3 font-mono text-ink">{trace.duration}</td><td className="px-3 py-3 text-ink-muted">{trace.spans}</td><td className="px-3 py-3 text-ink-muted">{trace.tokens}</td><td className="px-4 py-3 text-ink-muted">{trace.createdAt}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </SectionCard>

          <SectionCard
            title="性能与质量分析"
            description="统一展示 P50 / P95 / P99、成功率和 Token 口径"
            actions={<TabSwitcher value={performanceTab} onChange={setPerformanceTab} />}
          >
            <div className="overflow-x-auto">
              <table className="w-full min-w-[760px] text-left text-[11.5px]">
                <thead className="bg-app-soft text-ink-muted"><tr><th className="px-4 py-2.5 font-semibold">对象</th><th className="px-3 py-2.5 font-semibold">调用次数</th><th className="px-3 py-2.5 font-semibold">成功率</th><th className="px-3 py-2.5 font-semibold">P50</th><th className="px-3 py-2.5 font-semibold">P95</th><th className="px-3 py-2.5 font-semibold">P99</th><th className="px-4 py-2.5 font-semibold">平均 Token</th></tr></thead>
                <tbody className="divide-y divide-line">
                  {PERFORMANCE[performanceTab].map((row) => <tr key={row.name} className="hover:bg-app-soft/70"><td className="px-4 py-3"><div className="font-semibold text-ink">{row.name}</div><div className="text-[10.5px] text-ink-subtle">{row.detail}</div></td><td className="px-3 py-3 text-ink">{row.calls}</td><td className={classNames("px-3 py-3 font-semibold", row.successRate >= 97 ? "text-success" : row.successRate >= 94 ? "text-warning" : "text-danger")}>{row.successRate}%</td><td className="px-3 py-3 text-ink-muted">{row.p50}</td><td className="px-3 py-3 font-semibold text-ink">{row.p95}</td><td className="px-3 py-3 text-ink-muted">{row.p99}</td><td className="px-4 py-3 text-ink-muted">{row.tokens}</td></tr>)}
                </tbody>
              </table>
            </div>
          </SectionCard>

          <SectionCard title="错误中心" description="从错误指纹回溯 Trace 和异常 Span">
            <div className="grid gap-3 p-4 md:grid-cols-2">
              {ERROR_SUMMARIES.map((error) => (
                <article key={error.id} className="rounded-lg border border-line p-3.5">
                  <div className="flex items-start gap-3">
                    <span className={classNames("mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg", error.severity === "high" ? "bg-danger-soft text-danger" : error.severity === "medium" ? "bg-warning-soft text-warning" : "bg-app-soft text-ink-muted")}><AlertTriangle className="h-4 w-4" /></span>
                    <div className="min-w-0 flex-1"><div className="flex items-center gap-2"><strong className="font-mono text-[11.5px] text-ink">{error.code}</strong><span className="rounded bg-app-soft px-1.5 py-0.5 text-[10px] text-ink-muted">{error.source}</span><span className="ml-auto text-[10.5px] text-ink-subtle">{error.lastSeen}</span></div><p className="mt-1 text-[11.5px] leading-5 text-ink-muted">{error.message}</p><div className="mt-2 flex items-center gap-3 text-[10.5px]"><span className="text-ink-muted">{error.target} · {error.count} 次</span><Link to={`?trace=${error.traceId}`} className="font-semibold text-accent hover:underline">定位 {error.spanId}</Link></div></div>
                  </div>
                </article>
              ))}
            </div>
          </SectionCard>
        </div>
      </div>
    </div>
  );
}

function TabSwitcher({ value, onChange }: { value: PerformanceTab; onChange: (value: PerformanceTab) => void }) {
  const items: Array<{ id: PerformanceTab; label: string; icon: typeof Bot }> = [{ id: "agent", label: "Agent", icon: Bot }, { id: "model", label: "模型", icon: Brain }, { id: "tool", label: "工具", icon: Wrench }];
  return <div className="flex rounded-lg bg-app-soft p-0.5">{items.map(({ id, label, icon: Icon }) => <button key={id} type="button" onClick={() => onChange(id)} className={classNames("inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-[11px] font-bold", value === id ? "bg-white text-accent shadow-sm" : "text-ink-muted")}><Icon className="h-3.5 w-3.5" />{label}</button>)}</div>;
}

function TraceFocus({ trace, onClose }: { trace: (typeof TRACE_SUMMARIES)[number]; onClose: () => void }) {
  const spans = trace.status === "failed"
    ? [{ label: "agent.run", width: 100, tone: "blue" }, { label: "model.complete", width: 62, tone: "green" }, { label: "contract-parser", width: 78, tone: "red" }]
    : [{ label: "agent.run", width: 100, tone: "blue" }, { label: "model.complete", width: 54, tone: "green" }, { label: "knowledge-search", width: 31, tone: "amber" }];
  return <SectionCard title={`已定位 ${trace.id}`} description={`${trace.agent} ${trace.version} · ${trace.request}`} actions={<button type="button" onClick={onClose} className="text-[11.5px] font-semibold text-ink-muted hover:text-ink">关闭定位</button>}><div className="grid gap-4 p-4 lg:grid-cols-[minmax(0,1.5fr)_minmax(260px,0.8fr)]"><div className="space-y-2">{spans.map((span, index) => <div key={span.label} className="grid grid-cols-[130px_1fr] items-center gap-3"><span className="font-mono text-[10.5px] text-ink-muted">{span.label}</span><div className="h-7 rounded bg-app-soft p-1"><div className={classNames("flex h-full items-center rounded px-2 text-[9.5px] font-bold text-white", span.tone === "blue" ? "bg-accent" : span.tone === "green" ? "bg-success" : span.tone === "red" ? "bg-danger" : "bg-warning")} style={{ width: `${span.width}%`, marginLeft: `${index * 7}%` }}>{index === 0 ? trace.duration : index === 1 ? "1.84 s" : trace.status === "failed" ? "超时" : "620 ms"}</div></div></div>)}</div><div className="rounded-lg bg-slate-950 p-3 font-mono text-[10.5px] leading-5 text-slate-300"><div className="text-slate-500">关联日志 · 已脱敏</div><div><span className="text-sky-300">trace_id</span>={trace.id}</div><div><span className="text-emerald-300">status</span>={trace.status}</div>{trace.status === "failed" ? <div className="text-red-300">TOOL_TIMEOUT contract-parser exceeded 8s</div> : <div className="text-emerald-300">request completed successfully</div>}</div></div></SectionCard>;
}
