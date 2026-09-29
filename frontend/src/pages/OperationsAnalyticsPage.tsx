import { useTranslation } from "react-i18next";
import i18n, { currentLocale } from "@/i18n";
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
  { value: "week", get label() { return i18n.t("ops:range.week"); } },
  { value: "month", get label() { return i18n.t("ops:range.month"); } },
  { value: "quarter", get label() { return i18n.t("ops:range.quarter"); } },
];

export default function OperationsAnalyticsPage() {
  const { t } = useTranslation("ops");
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
    () => overview ? new Date(overview.context.as_of).toLocaleString(currentLocale(), { hour12: false }) : "—",
    [overview],
  );

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-app-bg">
      <Topbar title={t("title")} badge={{ label: t("badge"), tone: "info" }} />
      <div className="min-h-0 flex-1 overflow-y-auto p-4 md:p-5">
        <div className="mx-auto max-w-[1500px] space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <div className="flex rounded-lg bg-app-soft p-0.5" aria-label={t("rangeAria")}>
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
            <select aria-label={t("deptAria")} className="h-8 rounded-lg border border-line bg-app-soft px-2.5 text-[11.5px] text-ink-subtle" disabled>
              <option>{t("deptNone")}</option>
            </select>
            <select aria-label={t("userTypeAria")} className="h-8 rounded-lg border border-line bg-app-soft px-2.5 text-[11.5px] text-ink-subtle" disabled>
              <option>{t("userTypeNone")}</option>
            </select>
            <span className="ml-auto text-[10.5px] text-ink-subtle">
              {t("updatedAt", { version: overview?.context.version ?? "v1", tz: overview?.context.timezone ?? "Asia/Shanghai", asOf })}
            </span>
          </div>

          {error ? <div className="rounded-lg bg-danger-soft px-3 py-2 text-[11.5px] text-danger-deep">{error}</div> : null}
          {!loading && overview && !overview.availability.operations ? (
            <div className="rounded-lg border border-warning-ring bg-warning-soft px-3 py-2 text-[11.5px] text-warning-deep">
              {t("obsOff")}
            </div>
          ) : null}

          <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6" aria-label={t("coreAria")}>
            <MetricCard label="DAU" value={metric(summary?.dau, loading)} hint={t("dauHint")} icon={<Activity />} />
            <MetricCard label="WAU" value={metric(summary?.wau, loading)} hint={t("wauHint")} icon={<UsersRound />} tone="green" />
            <MetricCard label="MAU" value={metric(summary?.mau, loading)} hint={`DAU / MAU ${percentage(summary?.dau_mau)}`} icon={<CalendarDays />} tone="slate" />
            <MetricCard label={t("requests")} value={metric(summary?.request_count, loading)} hint={summary?.requests_per_active_user == null ? t("noRequests") : t("perUser", { n: summary.requests_per_active_user })} icon={<MousePointerClick />} />
            <MetricCard label={t("penetration")} value={percentage(summary?.platform_penetration)} hint={t("penetrationHint")} icon={<Gauge />} tone="amber" />
            <MetricCard label={t("d7")} value={percentage(summary?.d7_retention)} hint={retention ? t("cohortUsers", { count: retention.d7.cohort_users }) : "—"} icon={<Repeat2 />} tone="green" />
          </section>

          <div className="grid gap-4 xl:grid-cols-[minmax(0,1.6fr)_minmax(320px,0.8fr)]">
            <SectionCard title={t("trendTitle")} description={t("trendDesc")}>
              <LineChart
                values={trend.map((point) => point.active_users)}
                secondaryValues={trend.map((point) => point.requests)}
                labels={trend.map((point) => point.date.slice(5))}
                primaryLabel={t("activeUsers")}
                secondaryLabel={t("requests")}
              />
            </SectionCard>
            <SectionCard title={t("retentionTitle")} description={t("retentionDesc")}>
              <div className="space-y-4 p-4">
                <RetentionBar label={t("d1")} metric={retention?.d1} tone="green" />
                <RetentionBar label={t("d7")} metric={retention?.d7} />
                <RetentionBar label={t("d30")} metric={retention?.d30} tone="amber" />
                <div className="rounded-lg border border-line bg-app-soft p-3 text-[11px] leading-5 text-ink-muted">
                  {t("retentionNote")}
                </div>
              </div>
            </SectionCard>
          </div>

          <SectionCard title={t("agentTitle")} description={t("agentDesc")}>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[850px] text-left text-[11.5px]">
                <thead className="bg-app-soft text-ink-muted">
                  <tr><th className="px-4 py-2.5 font-semibold">Agent</th><th className="px-3 py-2.5 font-semibold">{t("col.eligible")}</th><th className="px-3 py-2.5 font-semibold">{t("col.used")}</th><th className="px-3 py-2.5 font-semibold">{t("col.penetration")}</th><th className="px-3 py-2.5 font-semibold">{t("col.requests")}</th><th className="px-3 py-2.5 font-semibold">{t("col.likeRate")}</th><th className="px-4 py-2.5 font-semibold">{t("d7")}</th></tr>
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
                  {!loading && !overview?.agents.length ? <tr><td colSpan={7} className="px-4 py-8 text-center text-ink-subtle">{t("noAgents")}</td></tr> : null}
                </tbody>
              </table>
            </div>
          </SectionCard>

          <SectionCard
            title={t("cohortTitle")}
            description={t("cohortDesc")}
            actions={<div className="flex rounded-lg bg-app-soft p-0.5"><button type="button" onClick={() => setCohortMode("week")} className={classNames("rounded-md px-2.5 py-1.5 text-[11px] font-bold", cohortMode === "week" ? "bg-white text-accent shadow-sm" : "text-ink-muted")}>{t("weekCohort")}</button><button type="button" onClick={() => setCohortMode("month")} className={classNames("rounded-md px-2.5 py-1.5 text-[11px] font-bold", cohortMode === "month" ? "bg-white text-accent shadow-sm" : "text-ink-muted")}>{t("monthCohort")}</button></div>}
          >
            <CohortTable mode={cohortMode} rows={cohortRows} />
          </SectionCard>
        </div>
      </div>
    </div>
  );
}

function RetentionBar({ label, metric, tone = "blue" }: { label: string; metric?: { rate: number | null; cohort_users: number }; tone?: "blue" | "green" | "amber" }) {
  const { t } = useTranslation("ops");
  return <BarMeter label={`${label}${metric ? ` · ${t("people", { count: metric.cohort_users })}` : ""}`} value={metric?.rate ?? 0} displayValue={metric?.rate == null ? t("insufficient") : `${metric.rate}%`} tone={tone} />;
}

function CohortTable({ mode, rows }: { mode: "week" | "month"; rows: OperationsCohortRow[] }) {
  const { t } = useTranslation("ops");
  return (
    <div className="overflow-x-auto p-4">
      <table className="w-full min-w-[700px] border-separate border-spacing-1 text-center text-[11px]">
        <thead><tr className="text-ink-muted"><th className="px-3 py-2 text-left font-semibold">{mode === "week" ? t("firstWeek") : t("firstMonth")}</th><th className="px-3 py-2 font-semibold">{t("userCount")}</th><th className="px-3 py-2 font-semibold">{t("period0")}</th><th className="px-3 py-2 font-semibold">{t("period1")}</th><th className="px-3 py-2 font-semibold">{t("period2")}</th><th className="px-3 py-2 font-semibold">{t("period3")}</th><th className="px-3 py-2 font-semibold">{t("period4")}</th></tr></thead>
        <tbody>{rows.map((row) => <tr key={row.cohort}><th className="rounded-md bg-app-soft px-3 py-3 text-left font-semibold text-ink">{mode === "week" ? t("weekLabel", { cohort: row.cohort }) : row.cohort.slice(0, 7)}</th><td className="rounded-md bg-app-soft px-3 py-3 text-ink-muted">{row.users}</td>{row.retention.map((value, index) => <td key={index} className={classNames("rounded-md px-3 py-3 font-bold", cohortCellClass(value))}>{value === null ? "—" : `${value}%`}</td>)}</tr>)}</tbody>
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
