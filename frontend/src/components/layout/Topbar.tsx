import { Activity, Bell, CheckCircle2, ChevronRight, CircleDashed } from "lucide-react";
import { classNames } from "@/lib/utils";
import { useWorkspace } from "@/lib/workspace";

export type RunBadgeTone = "info" | "success" | "warning" | "neutral" | "thinking";

export function Topbar({
  title,
  badge,
  actions,
}: {
  title: string;
  badge?: { label: string; tone: RunBadgeTone };
  actions?: React.ReactNode;
}) {
  const { activeWorkspace } = useWorkspace();
  return (
    <div className="flex h-14 shrink-0 items-center gap-3 border-b border-line bg-white px-4 md:px-6">
      <span className="hidden text-[13px] font-normal text-ink-subtle sm:inline">Unibot</span>
      {activeWorkspace ? (
        <>
          <ChevronRight className="hidden h-3.5 w-3.5 text-ink-subtle sm:block" />
          <span className="hidden max-w-48 truncate text-[13px] font-medium text-ink-muted sm:inline">{activeWorkspace.name}</span>
        </>
      ) : null}
      <ChevronRight className="hidden h-3.5 w-3.5 text-ink-subtle sm:block" />
      <h1 className="truncate text-[13px] font-semibold text-ink">{title}</h1>
      <div className="flex-1" />
      {badge ? <RunBadge {...badge} /> : null}
      {actions}
    </div>
  );
}

export function RunBadge({ label, tone }: { label: string; tone: RunBadgeTone }) {
  const styles = badgeStyles(tone);
  const Icon = badgeIcon(tone);
  return (
    <div
      className={classNames(
        "inline-flex h-7 items-center gap-1.5 rounded-full px-2.5 text-[11px] font-medium",
        styles.bg,
        styles.fg,
      )}
    >
      <Icon className={classNames("w-3.5 h-3.5", tone === "thinking" ? "animate-spin" : "")} />
      {label}
    </div>
  );
}

function badgeStyles(tone: RunBadgeTone) {
  switch (tone) {
    case "success":
      return { bg: "bg-success-soft", fg: "text-success-deep" };
    case "warning":
      return { bg: "bg-warning-soft", fg: "text-warning-deep" };
    case "info":
      return { bg: "bg-accent-soft", fg: "text-accent-hover" };
    default:
      return { bg: "bg-app-soft", fg: "text-ink-muted" };
  }
}

function badgeIcon(tone: RunBadgeTone) {
  switch (tone) {
    case "success":
      return CheckCircle2;
    case "warning":
      return Bell;
    case "info":
      return Activity;
    default:
      return CircleDashed;
  }
}
