import { useState } from "react";
import { Activity, Bot, CalendarDays, Gauge, MousePointerClick, Repeat2, UsersRound } from "lucide-react";
import { BarMeter, LineChart, MetricCard, MockDataBadge, SectionCard } from "@/components/analytics/DashboardPrimitives";
import { Topbar } from "@/components/layout/Topbar";
import { classNames } from "@/lib/utils";
import { AGENT_ADOPTION, COHORT_ROWS, METRIC_CONTEXT, TREND_POINTS } from "@/mocks/observability";

export default function OperationsAnalyticsPage() {
  const [scope, setScope] = useState("platform");
  const [cohortMode, setCohortMode] = useState<"week" | "month">("week");

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-app-bg">
      <Topbar title="运营增长" badge={{ label: "管理员视图", tone: "info" }} actions={<MockDataBadge />} />
      <div className="min-h-0 flex-1 overflow-y-auto p-4 md:p-5">
        <div className="mx-auto max-w-[1500px] space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <div className="flex rounded-lg border border-line bg-white p-0.5">
              <button type="button" onClick={() => setScope("platform")} className={classNames("rounded-md px-3 py-1.5 text-[11.5px] font-bold", scope === "platform" ? "bg-accent text-white" : "text-ink-muted")}>平台</button>
              <button type="button" onClick={() => setScope("agent")} className={classNames("rounded-md px-3 py-1.5 text-[11.5px] font-bold", scope === "agent" ? "bg-accent text-white" : "text-ink-muted")}>Agent</button>
            </div>
            {scope === "agent" ? <select aria-label="运营 Agent 筛选" className="h-8 rounded-lg border border-line bg-white px-2.5 text-[11.5px]"><option>数据分析助手</option><option>文档助手</option><option>客服助手</option><option>任务助手</option></select> : null}
            <select aria-label="部门筛选" className="h-8 rounded-lg border border-line bg-white px-2.5 text-[11.5px]"><option>全部部门</option><option>研发中心</option><option>客户成功部</option><option>产品中心</option></select>
            <select aria-label="用户类型筛选" className="h-8 rounded-lg border border-line bg-white px-2.5 text-[11.5px]"><option>全部用户</option><option>正式员工</option><option>合作伙伴</option><option>匿名用户</option></select>
            <span className="ml-auto text-[10.5px] text-ink-subtle">{METRIC_CONTEXT.version} · {METRIC_CONTEXT.timezone} · {METRIC_CONTEXT.window}</span>
          </div>

          <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6" aria-label="运营核心指标">
            <MetricCard label="DAU" value="926" change="+4.8%" hint="昨日 884" icon={<Activity />} />
            <MetricCard label="WAU" value="3,842" change="+7.2%" hint="去重活跃用户" icon={<UsersRound />} tone="green" />
            <MetricCard label="MAU" value="8,961" change="+9.6%" hint="DAU / MAU 10.3%" icon={<CalendarDays />} tone="slate" />
            <MetricCard label="请求数" value="12,842" change="+12.6%" hint="人均 3.34 次" icon={<MousePointerClick />} />
            <MetricCard label="平台渗透率" value="68.7%" change="+3.1%" hint="权限用户 13,047" icon={<Gauge />} tone="amber" />
            <MetricCard label="D7 留存" value="58.7%" change="+2.4%" hint="已成熟 Cohort" icon={<Repeat2 />} tone="green" />
          </section>

          <div className="grid gap-4 xl:grid-cols-[minmax(0,1.6fr)_minmax(320px,0.8fr)]">
            <SectionCard title="活跃与请求趋势" description="活跃仅由有效用户请求产生，不包含页面访问">
              <LineChart values={TREND_POINTS.map((point) => point.activeUsers)} secondaryValues={TREND_POINTS.map((point) => point.requests / 2.5)} labels={TREND_POINTS.map((point) => point.label)} primaryLabel="活跃用户" secondaryLabel="请求数（缩放）" />
            </SectionCard>
            <SectionCard title="留存概览" description="平台首次有效使用 · 自然日口径">
              <div className="space-y-4 p-4">
                <BarMeter label="D1 留存" value={71.4} tone="green" />
                <BarMeter label="D7 留存" value={58.7} />
                <BarMeter label="D30 留存" value={42.1} tone="amber" />
                <div className="rounded-lg border border-line bg-app-soft p-3 text-[11px] leading-5 text-ink-muted">
                  D30 仅统计观察窗口已成熟用户；当前未成熟用户不会被计为流失。
                </div>
              </div>
            </SectionCard>
          </div>

          <SectionCard title="Agent 渗透与体验关联" description="分母来自周期权限用户快照；缺少分母时不展示百分比">
            <div className="overflow-x-auto">
              <table className="w-full min-w-[850px] text-left text-[11.5px]">
                <thead className="bg-app-soft text-ink-muted"><tr><th className="px-4 py-2.5 font-semibold">Agent</th><th className="px-3 py-2.5 font-semibold">权限用户</th><th className="px-3 py-2.5 font-semibold">实际使用</th><th className="px-3 py-2.5 font-semibold">渗透率</th><th className="px-3 py-2.5 font-semibold">请求数</th><th className="px-3 py-2.5 font-semibold">点赞率</th><th className="px-4 py-2.5 font-semibold">D7 留存</th></tr></thead>
                <tbody className="divide-y divide-line">{AGENT_ADOPTION.map((row) => <tr key={row.agent} className="hover:bg-app-soft/70"><td className="px-4 py-3 font-semibold text-ink"><span className="inline-flex items-center gap-2"><span className="flex h-7 w-7 items-center justify-center rounded-lg bg-accent-soft text-accent"><Bot className="h-3.5 w-3.5" /></span>{row.agent}</span></td><td className="px-3 py-3 text-ink-muted">{row.eligibleUsers}</td><td className="px-3 py-3 text-ink">{row.activeUsers}</td><td className="px-3 py-3"><div className="flex w-32 items-center gap-2"><div className="h-1.5 flex-1 overflow-hidden rounded-full bg-app-soft"><div className="h-full rounded-full bg-accent" style={{ width: `${row.penetration}%` }} /></div><strong className="w-10 text-right text-ink">{row.penetration}%</strong></div></td><td className="px-3 py-3 text-ink-muted">{row.requests}</td><td className={classNames("px-3 py-3 font-semibold", row.positiveRate >= 88 ? "text-success" : row.positiveRate >= 83 ? "text-warning" : "text-danger")}>{row.positiveRate}%</td><td className="px-4 py-3 text-ink-muted">{row.d7}%</td></tr>)}</tbody>
              </table>
            </div>
          </SectionCard>

          <SectionCard
            title="Cohort 留存"
            description="分母在 Cohort 形成后固定；空白代表观察窗口尚未成熟"
            actions={<div className="flex rounded-lg bg-app-soft p-0.5"><button type="button" onClick={() => setCohortMode("week")} className={classNames("rounded-md px-2.5 py-1.5 text-[11px] font-bold", cohortMode === "week" ? "bg-white text-accent shadow-sm" : "text-ink-muted")}>周 Cohort</button><button type="button" onClick={() => setCohortMode("month")} className={classNames("rounded-md px-2.5 py-1.5 text-[11px] font-bold", cohortMode === "month" ? "bg-white text-accent shadow-sm" : "text-ink-muted")}>月 Cohort</button></div>}
          >
            <div className="overflow-x-auto p-4">
              <table className="w-full min-w-[700px] border-separate border-spacing-1 text-center text-[11px]">
                <thead><tr className="text-ink-muted"><th className="px-3 py-2 text-left font-semibold">{cohortMode === "week" ? "首次使用周" : "首次使用月"}</th><th className="px-3 py-2 font-semibold">用户数</th><th className="px-3 py-2 font-semibold">首期</th><th className="px-3 py-2 font-semibold">第 1 期</th><th className="px-3 py-2 font-semibold">第 2 期</th><th className="px-3 py-2 font-semibold">第 3 期</th><th className="px-3 py-2 font-semibold">第 4 期</th></tr></thead>
                <tbody>{COHORT_ROWS.map((row) => <tr key={row.cohort}><th className="rounded-md bg-app-soft px-3 py-3 text-left font-semibold text-ink">{cohortMode === "week" ? row.cohort : row.cohort.slice(0, 5)}</th><td className="rounded-md bg-app-soft px-3 py-3 text-ink-muted">{row.users}</td>{row.retention.map((value, index) => <td key={index} className={classNames("rounded-md px-3 py-3 font-bold", cohortCellClass(value))}>{value === null ? "—" : `${value}%`}</td>)}</tr>)}</tbody>
              </table>
            </div>
          </SectionCard>
        </div>
      </div>
    </div>
  );
}

function cohortCellClass(value: number | null) {
  if (value === null) return "bg-app-soft text-ink-subtle";
  if (value >= 80) return "bg-blue-700 text-white";
  if (value >= 60) return "bg-blue-500 text-white";
  if (value >= 45) return "bg-blue-300 text-blue-950";
  return "bg-blue-100 text-blue-800";
}
