import { Activity, Bell, CheckCircle2, CircleDashed } from "lucide-react";
import { classNames } from "@/lib/utils";

export type RunBadgeTone = "info" | "success" | "warning" | "neutral" | "thinking";

export function Topbar({
  title,
  badge,
}: {
  title: string;
  badge?: { label: string; tone: RunBadgeTone };
}) {
  return (
    <div className="h-16 px-5 flex items-center gap-3 border-b border-line bg-white">
      <h1 className="text-[18px] font-bold text-ink font-display">{title}</h1>
      <div className="flex-1" />
      {badge ? <RunBadge {...badge} /> : null}
    </div>
  );
}

export function RunBadge({ label, tone }: { label: string; tone: RunBadgeTone }) {
  const styles = badgeStyles(tone);
  const Icon = badgeIcon(tone);
  return (
    <div
      className={classNames(
        "h-8 px-2.5 rounded-lg inline-flex items-center gap-1.5 text-[12.5px] font-bold",
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
