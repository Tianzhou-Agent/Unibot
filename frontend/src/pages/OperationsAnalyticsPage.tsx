import { useEffect, useMemo, useState } from "react";
import { Activity, Bot, CalendarDays, Gauge, MousePointerClick, Repeat2, UsersRound } from "lucide-react";
import { BarMeter, LineChart, MetricCard, SectionCard } from "@/components/analytics/DashboardPrimitives";
import { Topbar } from "@/components/layout/Topbar";
import { apiErrorMessage } from "@/lib/api";
import {
  getOperationsOverview,
  type OperationsCohortRow,
  type OperationsOverview,
  type OperationsRange,
} from "@/lib/operationsApi";
import { classNames } from "@/lib/utils";

const RANGE_OPTIONS: Array<{ value: OperationsRange; label: string }> = [
  { value: "week", label: "最近 7 天" },
  { value: "month", label: "最近 30 天" },
  { value: "quarter", label: "最近 90 天" },
];

export default function OperationsAnalyticsPage() {
  const [range, setRange] = useState<OperationsRange>("week");
  const [cohortMode, setCohortMode] = useState<"week" | "month">("week");
  const [overview, setOverview] = useState<OperationsOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    void getOperationsOverview(range)
      .then((result) => {
        if (active) setOverview(result);
      })
      .catch((reason) => {
        if (active) setError(apiErrorMessage(reason));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [range]);

  const summary = overview?.summary;
  const trend = overview?.trend ?? [];
  const retention = overview?.retention;
  const cohortRows = overview?.cohorts[cohortMode] ?? [];
  const asOf = useMemo(
    () => overview ? new Date(overview.context.as_of).toLocaleString("zh-CN", { hour12: false }) : "—",
    [overview],
  );

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-app-bg">
      <Topbar title="运营增长" badge={{ label: "管理员视图", tone: "info" }} />
      <div className="min-h-0 flex-1 overflow-y-auto p-4 md:p-5">
        <div className="mx-auto max-w-[1500px] space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <div className="flex rounded-lg bg-app-soft p-0.5" aria-label="运营时间范围">
              {RANGE_OPTIONS.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => setRange(option.value)}
                  className={classNames(
                    "rounded-md px-2.5 py-1.5 text-[11px] font-bold transition-colors",
                    range === option.value ? "bg-white text-accent shadow-sm" : "text-ink-muted hover:text-ink",
                  )}
                >
                  {option.label}
                </button>
              ))}
            </div>
            <select aria-label="部门筛选" className="h-8 rounded-lg border border-line bg-app-soft px-2.5 text-[11.5px] text-ink-subtle" disabled>
              <option>部门数据源未接入</option>
            </select>
            <select aria-label="用户类型筛选" className="h-8 rounded-lg border border-line bg-app-soft px-2.5 text-[11.5px] text-ink-subtle" disabled>
              <option>用户类型数据源未接入</option>
            </select>
            <span className="ml-auto text-[10.5px] text-ink-subtle">
              {overview?.context.version ?? "v1"} · {overview?.context.timezone ?? "Asia/Shanghai"} · 更新于 {asOf}
            </span>
          </div>

          {error ? <div className="rounded-lg bg-danger-soft px-3 py-2 text-[11.5px] text-danger-deep">{error}</div> : null}
          {!loading && overview && !overview.availability.operations ? (
            <div className="rounded-lg border border-warning-ring bg-warning-soft px-3 py-2 text-[11.5px] text-warning-deep">
              OBS 可靠采集未启用，当前环境暂无可查询的运营事实。
            </div>
          ) : null}

          <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6" aria-label="运营核心指标">
            <MetricCard label="DAU" value={metric(summary?.dau, loading)} hint="今日有效请求用户" icon={<Activity />} />
            <MetricCard label="WAU" value={metric(summary?.wau, loading)} hint="近 7 个自然日去重" icon={<UsersRound />} tone="green" />
            <MetricCard label="MAU" value={metric(summary?.mau, loading)} hint={`DAU / MAU ${percentage(summary?.dau_mau)}`} icon={<CalendarDays />} tone="slate" />
            <MetricCard label="请求数" value={metric(summary?.request_count, loading)} hint={summary?.requests_per_active_user == null ? "暂无有效请求" : `人均 ${summary.requests_per_active_user} 次`} icon={<MousePointerClick />} />
            <MetricCard label="平台渗透率" value={percentage(summary?.platform_penetration)} hint="权限用户分母尚未接入" icon={<Gauge />} tone="amber" />
            <MetricCard label="D7 留存" value={percentage(summary?.d7_retention)} hint={retention ? `成熟 Cohort ${retention.d7.cohort_users} 人` : "—"} icon={<Repeat2 />} tone="green" />
          </section>

          <div className="grid gap-4 xl:grid-cols-[minmax(0,1.6fr)_minmax(320px,0.8fr)]">
            <SectionCard title="活跃与请求趋势" description="活跃仅由有效 Chat 请求产生，不包含页面访问">
              <LineChart
                values={trend.map((point) => point.active_users)}
                secondaryValues={trend.map((point) => point.requests)}
                labels={trend.map((point) => point.date.slice(5))}
                primaryLabel="活跃用户"
                secondaryLabel="请求数"
              />
            </SectionCard>
            <SectionCard title="留存概览" description="平台首次有效使用 · Asia/Shanghai 自然日口径">
              <div className="space-y-4 p-4">
                <RetentionBar label="D1 留存" metric={retention?.d1} tone="green" />
                <RetentionBar label="D7 留存" metric={retention?.d7} />
                <RetentionBar label="D30 留存" metric={retention?.d30} tone="amber" />
                <div className="rounded-lg border border-line bg-app-soft p-3 text-[11px] leading-5 text-ink-muted">
                  仅统计观察窗口已经成熟的首次使用用户；没有成熟用户时显示“数据不足”，不会计为流失。
                </div>
              </div>
            </SectionCard>
          </div>

          <SectionCard title="Agent 使用与体验关联" description="使用人数和请求数来自 OBS；权限用户分母接入前不计算渗透率">
            <div className="overflow-x-auto">
              <table className="w-full min-w-[850px] text-left text-[11.5px]">
                <thead className="bg-app-soft text-ink-muted">
                  <tr><th className="px-4 py-2.5 font-semibold">Agent</th><th className="px-3 py-2.5 font-semibold">权限用户</th><th className="px-3 py-2.5 font-semibold">实际使用</th><th className="px-3 py-2.5 font-semibold">渗透率</th><th className="px-3 py-2.5 font-semibold">请求数</th><th className="px-3 py-2.5 font-semibold">点赞率</th><th className="px-4 py-2.5 font-semibold">D7 留存</th></tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {(overview?.agents ?? []).map((row) => (
                    <tr key={row.agent_id} className="hover:bg-app-soft/70">
                      <td className="px-4 py-3 font-semibold text-ink"><span className="inline-flex items-center gap-2"><span className="flex h-7 w-7 items-center justify-center rounded-lg bg-accent-soft text-accent"><Bot className="h-3.5 w-3.5" /></span><span>{row.agent_id}{row.agent_version ? <small className="ml-1 font-mono text-ink-subtle">v{row.agent_version}</small> : null}</span></span></td>
                      <td className="px-3 py-3 text-ink-muted">{nullableNumber(row.eligible_users)}</td>
                      <td className="px-3 py-3 text-ink">{row.active_users.toLocaleString()}</td>
                      <td className="px-3 py-3 font-semibold text-ink-muted">{percentage(row.penetration)}</td>
                      <td className="px-3 py-3 text-ink-muted">{row.requests.toLocaleString()}</td>
                      <td className="px-3 py-3 font-semibold text-ink-muted">{percentage(row.positive_rate)}</td>
                      <td className="px-4 py-3 text-ink-muted">{percentage(row.d7_retention)}</td>
                    </tr>
                  ))}
                  {!loading && !overview?.agents.length ? <tr><td colSpan={7} className="px-4 py-8 text-center text-ink-subtle">当前时间范围内暂无 Agent 使用数据</td></tr> : null}
                </tbody>
              </table>
            </div>
          </SectionCard>

          <SectionCard
            title="Cohort 留存"
            description="分母在首次使用周期形成后固定；空白表示观察周期尚未成熟"
            actions={<div className="flex rounded-lg bg-app-soft p-0.5"><button type="button" onClick={() => setCohortMode("week")} className={classNames("rounded-md px-2.5 py-1.5 text-[11px] font-bold", cohortMode === "week" ? "bg-white text-accent shadow-sm" : "text-ink-muted")}>周 Cohort</button><button type="button" onClick={() => setCohortMode("month")} className={classNames("rounded-md px-2.5 py-1.5 text-[11px] font-bold", cohortMode === "month" ? "bg-white text-accent shadow-sm" : "text-ink-muted")}>月 Cohort</button></div>}
          >
            <CohortTable mode={cohortMode} rows={cohortRows} />
          </SectionCard>
        </div>
      </div>
    </div>
  );
}

function RetentionBar({ label, metric, tone = "blue" }: { label: string; metric?: { rate: number | null; cohort_users: number }; tone?: "blue" | "green" | "amber" }) {
  return <BarMeter label={`${label}${metric ? ` · ${metric.cohort_users} 人` : ""}`} value={metric?.rate ?? 0} displayValue={metric?.rate == null ? "数据不足" : `${metric.rate}%`} tone={tone} />;
}

function CohortTable({ mode, rows }: { mode: "week" | "month"; rows: OperationsCohortRow[] }) {
  return (
    <div className="overflow-x-auto p-4">
      <table className="w-full min-w-[700px] border-separate border-spacing-1 text-center text-[11px]">
        <thead><tr className="text-ink-muted"><th className="px-3 py-2 text-left font-semibold">{mode === "week" ? "首次使用周" : "首次使用月"}</th><th className="px-3 py-2 font-semibold">用户数</th><th className="px-3 py-2 font-semibold">首期</th><th className="px-3 py-2 font-semibold">第 1 期</th><th className="px-3 py-2 font-semibold">第 2 期</th><th className="px-3 py-2 font-semibold">第 3 期</th><th className="px-3 py-2 font-semibold">第 4 期</th></tr></thead>
        <tbody>{rows.map((row) => <tr key={row.cohort}><th className="rounded-md bg-app-soft px-3 py-3 text-left font-semibold text-ink">{mode === "week" ? `周 ${row.cohort}` : row.cohort.slice(0, 7)}</th><td className="rounded-md bg-app-soft px-3 py-3 text-ink-muted">{row.users}</td>{row.retention.map((value, index) => <td key={index} className={classNames("rounded-md px-3 py-3 font-bold", cohortCellClass(value))}>{value === null ? "—" : `${value}%`}</td>)}</tr>)}</tbody>
      </table>
    </div>
  );
}

function metric(value: number | undefined, loading: boolean) {
  if (loading && value === undefined) return "…";
  return (value ?? 0).toLocaleString();
}

function nullableNumber(value: number | null) {
  return value == null ? "—" : value.toLocaleString();
}

function percentage(value: number | null | undefined) {
  return value == null ? "—" : `${value}%`;
}

function cohortCellClass(value: number | null) {
  if (value === null) return "bg-app-soft text-ink-subtle";
  if (value >= 80) return "bg-blue-700 text-white";
  if (value >= 60) return "bg-blue-500 text-white";
  if (value >= 45) return "bg-blue-300 text-blue-950";
  return "bg-blue-100 text-blue-800";
}
