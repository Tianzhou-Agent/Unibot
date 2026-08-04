import type { ReactNode } from "react";
import { DatabaseZap } from "lucide-react";
import { classNames } from "@/lib/utils";

export function MockDataBadge() {
  return (
    <span className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-warning-ring bg-warning-soft px-2.5 text-[11.5px] font-bold text-warning-deep">
      <DatabaseZap className="h-3.5 w-3.5" />Mock 数据
    </span>
  );
}

export function MetricCard({
  label,
  value,
  change,
  hint,
  icon,
  tone = "blue",
}: {
  label: string;
  value: string;
  change?: string;
  hint?: string;
  icon: ReactNode;
  tone?: "blue" | "green" | "amber" | "red" | "slate";
}) {
  const toneStyles = {
    blue: "bg-accent-soft text-accent",
    green: "bg-success-soft text-success",
    amber: "bg-warning-soft text-warning",
    red: "bg-danger-soft text-danger",
    slate: "bg-app-soft text-ink-muted",
  }[tone];

  return (
    <article className="rounded-xl border border-line bg-white p-4 shadow-card">
      <div className="flex items-start gap-3">
        <span className={classNames("flex h-9 w-9 shrink-0 items-center justify-center rounded-lg [&>svg]:h-4 [&>svg]:w-4", toneStyles)}>
          {icon}
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-[11.5px] font-semibold text-ink-muted">{label}</p>
          <div className="mt-1 flex items-baseline gap-2">
            <strong className="text-[22px] font-extrabold tracking-tight text-ink">{value}</strong>
            {change ? <span className={classNames("text-[11px] font-bold", change.startsWith("-") ? "text-danger" : "text-success")}>{change}</span> : null}
          </div>
          {hint ? <p className="mt-1 truncate text-[10.5px] text-ink-subtle">{hint}</p> : null}
        </div>
      </div>
    </article>
  );
}

export function SectionCard({
  title,
  description,
  actions,
  children,
  className,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={classNames("rounded-xl border border-line bg-white shadow-card", className)}>
      <header className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-3.5">
        <div>
          <h2 className="text-sm font-bold text-ink">{title}</h2>
          {description ? <p className="mt-0.5 text-[11px] text-ink-muted">{description}</p> : null}
        </div>
        <div className="flex-1" />
        {actions}
      </header>
      {children}
    </section>
  );
}

export function LineChart({
  values,
  labels,
  secondaryValues,
  primaryLabel,
  secondaryLabel,
}: {
  values: number[];
  labels: string[];
  secondaryValues?: number[];
  primaryLabel: string;
  secondaryLabel?: string;
}) {
  const width = 620;
  const height = 190;
  const padding = 18;
  const allValues = [...values, ...(secondaryValues ?? [])];
  const max = Math.max(...allValues, 1);
  const min = Math.min(...allValues, 0);
  const range = Math.max(max - min, 1);
  const points = chartPoints(values, width, height, padding, min, range);
  const secondaryPoints = secondaryValues ? chartPoints(secondaryValues, width, height, padding, min, range) : "";

  return (
    <div className="p-4">
      <div className="mb-3 flex items-center gap-4 text-[11px] text-ink-muted">
        <span className="inline-flex items-center gap-1.5"><i className="h-2 w-2 rounded-full bg-accent" />{primaryLabel}</span>
        {secondaryLabel ? <span className="inline-flex items-center gap-1.5"><i className="h-2 w-2 rounded-full bg-warning" />{secondaryLabel}</span> : null}
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} className="h-48 w-full overflow-visible" role="img" aria-label={`${primaryLabel}趋势图`}>
        {[0, 1, 2, 3].map((line) => {
          const y = padding + ((height - padding * 2) / 3) * line;
          return <line key={line} x1={padding} x2={width - padding} y1={y} y2={y} stroke="#E2E8F0" strokeDasharray="4 5" />;
        })}
        <polyline points={points} fill="none" stroke="#2563EB" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
        {secondaryValues ? <polyline points={secondaryPoints} fill="none" stroke="#D97706" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" /> : null}
        {values.map((value, index) => {
          const [x, y] = pointAt(index, value, values.length, width, height, padding, min, range);
          return <circle key={`${labels[index]}-${value}`} cx={x} cy={y} r="3.5" fill="#FFFFFF" stroke="#2563EB" strokeWidth="2" />;
        })}
      </svg>
      <div className="grid text-[10.5px] text-ink-subtle" style={{ gridTemplateColumns: `repeat(${labels.length}, minmax(0, 1fr))` }}>
        {labels.map((label) => <span key={label} className="text-center">{label}</span>)}
      </div>
    </div>
  );
}

export function BarMeter({ label, value, displayValue, tone = "blue" }: { label: string; value: number; displayValue?: string; tone?: "blue" | "green" | "amber" | "red" }) {
  const bar = { blue: "bg-accent", green: "bg-success", amber: "bg-warning", red: "bg-danger" }[tone];
  return (
    <div>
      <div className="mb-1.5 flex items-center gap-2 text-[11.5px]">
        <span className="min-w-0 flex-1 truncate text-ink-muted">{label}</span>
        <strong className="text-ink">{displayValue ?? `${value}%`}</strong>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-app-soft">
        <div className={classNames("h-full rounded-full", bar)} style={{ width: `${Math.max(0, Math.min(value, 100))}%` }} />
      </div>
    </div>
  );
}

function chartPoints(values: number[], width: number, height: number, padding: number, min: number, range: number) {
  return values.map((value, index) => pointAt(index, value, values.length, width, height, padding, min, range).join(",")).join(" ");
}

function pointAt(index: number, value: number, length: number, width: number, height: number, padding: number, min: number, range: number): [number, number] {
  const x = padding + (index / Math.max(length - 1, 1)) * (width - padding * 2);
  const y = height - padding - ((value - min) / range) * (height - padding * 2);
  return [x, y];
}
